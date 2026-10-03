from __future__ import annotations

import json
import threading
import time
from collections.abc import Iterator
from typing import Callable

from emo_master.apps.runtime.events.models import RuntimeEvent


class EventStore:
    def __init__(self, persistence: object | None = None, retentionPerJob: int = 10000) -> None:
        self.persistence = persistence
        self.retentionPerJob = max(1, retentionPerJob)
        self._events: dict[str, list[RuntimeEvent]] = {}
        self._sequences: dict[str, int] = {}
        self._terminalJobs: set[str] = set()
        self._terminalSequences: dict[str, int] = {}
        self._sinks: list[Callable[[RuntimeEvent], object]] = []
        # follow() checks retained and persisted history while waiting on the
        # same condition.  A re-entrant lock avoids self-deadlocking there.
        self._condition = threading.Condition(threading.RLock())

    def append(
        self,
        jobId: str,
        eventType: str,
        message: str,
        level: str = "INFO",
        nodeId: str = "",
        code: str = "",
        payload: dict[str, object] | None = None,
        projectId: str = "",
        workflowId: str = "",
        workflowRunId: str = "",
        parentWorkflowRunId: str = "",
        nodeRunId: str = "",
        iterationPath: tuple[int, ...] = (),
        timestampMs: int | None = None,
        notifySinks: bool = True,
    ) -> RuntimeEvent:
        payloadJson = json.dumps(payload or {}, ensure_ascii=True, default=str)
        timestamp = int(time.time() * 1000) if timestampMs is None else timestampMs
        with self._condition:
            iterationPathJson = json.dumps(list(iterationPath), ensure_ascii=True)
            append = getattr(self.persistence, "appendJobEvent", None) if self.persistence is not None else None
            if self.persistence is not None:
                if callable(append):
                    sequence = int(append(
                        jobId=jobId,
                        nodeId=nodeId,
                        eventType=eventType,
                        level=level,
                        code=code,
                        message=message,
                        payloadJson=payloadJson,
                        projectId=projectId,
                        workflowId=workflowId,
                        workflowRunId=workflowRunId,
                        parentWorkflowRunId=parentWorkflowRunId,
                        nodeRunId=nodeRunId,
                        iterationPathJson=iterationPathJson,
                        timestamp=timestamp,
                        sequence=None,
                    ))
                else:
                    sequence = (self._sequences.get(jobId) or 0) + 1
            else:
                sequence = (self._sequences.get(jobId) or 0) + 1
            self._sequences[jobId] = sequence
            event = RuntimeEvent(
                jobId=jobId,
                eventType=eventType,
                message=message,
                level=level,
                nodeId=nodeId,
                code=code,
                payloadJson=payloadJson,
                sequence=sequence,
                timestampMs=timestamp,
                projectId=projectId,
                workflowId=workflowId,
                workflowRunId=workflowRunId,
                parentWorkflowRunId=parentWorkflowRunId,
                nodeRunId=nodeRunId,
                iterationPathJson=iterationPathJson,
            )
            bucket = self._events.setdefault(jobId, [])
            bucket.append(event)
            if len(bucket) > self.retentionPerJob:
                del bucket[: len(bucket) - self.retentionPerJob]
            if eventType in {"job.completed", "job.failed", "job.aborted"}:
                self._terminalJobs.add(jobId)
                self._terminalSequences[jobId] = max(
                    sequence,
                    self._terminalSequences.get(jobId, 0),
                )
            self._condition.notify_all()
            # Sinks are required to be non-blocking.  Dispatch while the append
            # order is still serialized so concurrent producers cannot put a
            # later SQLite sequence into JSONL before an earlier one.
            if notifySinks:
                for sink in tuple(self._sinks):
                    try:
                        sink(event)
                    except BaseException:
                        pass
        return event

    def addSink(self, sink: Callable[[RuntimeEvent], object]) -> None:
        with self._condition:
            if sink not in self._sinks:
                self._sinks.append(sink)

    def removeSink(self, sink: Callable[[RuntimeEvent], object]) -> None:
        with self._condition:
            if sink in self._sinks:
                self._sinks.remove(sink)

    def read(self, jobId: str, afterSequence: int = 0) -> list[RuntimeEvent]:
        with self._condition:
            return [event for event in self._events.get(jobId, []) if event.sequence > afterSequence]

    def readMerged(self, jobId: str, afterSequence: int = 0) -> list[RuntimeEvent]:
        """Read retained memory events and older persisted history without duplicates."""
        events = self.read(jobId, afterSequence)
        if self.persistence is not None:
            reader = getattr(self.persistence, "listJobEventsAfter", None)
            if callable(reader):
                events.extend(reader(jobId, afterSequence))
        unique = {event.sequence: event for event in events}
        return [unique[key] for key in sorted(unique)]

    def readFromPersistence(self, jobId: str, afterSequence: int = 0) -> list[RuntimeEvent]:
        if self.persistence is None:
            return []
        reader = getattr(self.persistence, "listJobEventsAfter", None)
        if not callable(reader):
            return []
        return list(reader(jobId, afterSequence))

    def terminalSequence(self, jobId: str) -> int:
        """Return the persisted terminal event sequence, including after restart."""

        with self._condition:
            sequence = self._terminalSequences.get(jobId, 0)
            if sequence > 0:
                return sequence
            reader = (
                getattr(self.persistence, "getTerminalJobEventSequence", None)
                if self.persistence is not None
                else None
            )
            if not callable(reader):
                return 0
            sequence = max(0, int(reader(jobId)))
            if sequence > 0:
                self._terminalSequences[jobId] = sequence
                self._terminalJobs.add(jobId)
            return sequence

    def follow(
        self,
        jobId: str,
        afterSequence: int = 0,
        cancellation: object | None = None,
        isTerminal: Callable[[str], bool] | None = None,
    ) -> Iterator[RuntimeEvent]:
        cursor = afterSequence
        while True:
            events = self.readMerged(jobId, cursor)
            for event in events:
                cursor = max(cursor, event.sequence)
                if event.eventType in {"job.completed", "job.failed", "job.aborted"}:
                    with self._condition:
                        self._terminalJobs.add(jobId)
                        self._terminalSequences[jobId] = max(
                            event.sequence,
                            self._terminalSequences.get(jobId, 0),
                        )
                yield event
            with self._condition:
                terminal = jobId in self._terminalJobs
                if isTerminal is not None:
                    terminal = terminal or isTerminal(jobId)
                elif not terminal:
                    terminal = self.terminalSequence(jobId) > 0
                if terminal and not self.readMerged(jobId, cursor):
                    return
                if _cancelled(cancellation):
                    return
                self._condition.wait(timeout=0.25)

    def markTerminal(self, jobId: str) -> None:
        with self._condition:
            self._terminalJobs.add(jobId)
            self._condition.notify_all()


def _cancelled(value: object | None) -> bool:
    if value is None:
        return False
    isActive = getattr(value, "is_active", None)
    if callable(isActive):
        return not bool(isActive())
    checker = getattr(value, "isCancellationRequested", None)
    if isinstance(checker, bool):
        return checker
    checkerMethod = getattr(value, "is_set", None)
    return bool(checkerMethod()) if callable(checkerMethod) else False
