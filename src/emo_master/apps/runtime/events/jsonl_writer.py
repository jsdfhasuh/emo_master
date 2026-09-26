from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import queue
import threading
import time
from typing import BinaryIO, Callable

from emo_master.apps.runtime.events.models import RuntimeEvent


FailureCallback = Callable[[RuntimeEvent | None, str], None]


@dataclass(frozen=True)
class _QueuedEvent:
    order: int
    event: RuntimeEvent


class RuntimeJsonlLogWriter:
    """Best-effort asynchronous operational log sink with bounded rotation."""

    def __init__(
        self,
        directory: Path,
        *,
        maximumFileBytes: int = 64 * 1024 * 1024,
        retentionDays: int = 14,
        maximumTotalBytes: int = 2 * 1024 * 1024 * 1024,
        normalQueueCapacity: int = 9000,
        priorityQueueCapacity: int = 1000,
        flushIntervalSeconds: float = 1.0,
        failureCallback: FailureCallback | None = None,
        clock: Callable[[], float] = time.monotonic,
        wallClock: Callable[[], float] = time.time,
    ) -> None:
        self.directory = directory
        self.maximumFileBytes = max(1024, int(maximumFileBytes))
        self.retentionDays = max(1, int(retentionDays))
        self.maximumTotalBytes = max(self.maximumFileBytes, int(maximumTotalBytes))
        self.flushIntervalSeconds = max(0.05, float(flushIntervalSeconds))
        self.failureCallback = failureCallback
        self.clock = clock
        self.wallClock = wallClock
        self._normal: queue.Queue[_QueuedEvent] = queue.Queue(
            maxsize=max(1, normalQueueCapacity)
        )
        self._priority: queue.Queue[_QueuedEvent] = queue.Queue(
            maxsize=max(1, priorityQueueCapacity)
        )
        self._stop = threading.Event()
        self._available = threading.Event()
        self._stateLock = threading.RLock()
        self._progressCondition = threading.Condition(self._stateLock)
        self._handle: BinaryIO | None = None
        self._activePath: Path | None = None
        self._activeBytes = 0
        self._fileIndex = 0
        self._lastFlush = self.clock()
        self._reportedFailure = ""
        self._dropped: dict[str, int] = {}
        self._processedSequences: dict[str, int] = {}
        self._lastWrittenEvent: RuntimeEvent | None = None
        self._enqueueOrder = 0
        self._enabled = True
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            self._cleanupClosedFiles()
        except OSError as err:
            self._enabled = False
            self._reportFailure(None, f"cannot initialize runtime log directory: {err}")
        self._thread = threading.Thread(
            target=self._run,
            name="runtime-jsonl-log-writer",
            daemon=True,
        )
        self._thread.start()

    @property
    def activePath(self) -> Path | None:
        with self._stateLock:
            return self._activePath

    @property
    def enabled(self) -> bool:
        with self._stateLock:
            return self._enabled

    @property
    def failureMessage(self) -> str:
        with self._stateLock:
            return self._reportedFailure

    def enqueue(self, event: RuntimeEvent) -> bool:
        if not self._enabled or self._stop.is_set() or not _isOperationalEvent(event):
            return False
        target = self._priority if _isPriorityEvent(event) else self._normal
        with self._stateLock:
            self._enqueueOrder += 1
            queued = _QueuedEvent(self._enqueueOrder, event)
        try:
            target.put_nowait(queued)
            self._available.set()
            return True
        except queue.Full:
            with self._stateLock:
                self._dropped[event.level] = self._dropped.get(event.level, 0) + 1
            if event.level == "ERROR" or event.eventType in {
                "job.completed",
                "job.failed",
                "job.aborted",
            }:
                self._reportFailure(
                    event,
                    "runtime JSONL priority queue is full; event was dropped",
                )
            self._markProcessed(event)
            return False

    def waitUntilProcessed(
        self,
        jobId: str,
        sequence: int,
        timeoutSeconds: float = 3.0,
    ) -> bool:
        if sequence <= 0:
            return True
        deadline = time.monotonic() + max(0.0, float(timeoutSeconds))
        with self._progressCondition:
            if not self._enabled:
                return False
            while self._processedSequences.get(jobId, 0) < sequence:
                remaining = deadline - time.monotonic()
                if remaining <= 0.0:
                    return False
                if not self._thread.is_alive() and self._queuesEmpty():
                    return False
                self._progressCondition.wait(timeout=min(0.1, remaining))
            return True

    def close(self, timeoutSeconds: float = 3.0) -> str | None:
        self._stop.set()
        self._available.set()
        self._thread.join(timeout=max(0.0, timeoutSeconds))
        if self._thread.is_alive():
            return "runtime JSONL writer did not stop before timeout"
        return None

    def _run(self) -> None:
        try:
            while not self._stop.is_set() or not self._queuesEmpty():
                event = self._nextEvent()
                if event is not None:
                    self._writeEvent(event)
                self._flushIfDue()
        finally:
            self._flush(sync=True, sourceEvent=self._lastWrittenEvent)
            self._closeHandle()

    def _nextEvent(self) -> RuntimeEvent | None:
        while True:
            priority = self._peek(self._priority)
            normal = self._peek(self._normal)
            target: queue.Queue[_QueuedEvent] | None = None
            if priority is not None and normal is not None:
                target = self._priority if priority.order < normal.order else self._normal
            elif priority is not None:
                target = self._priority
            elif normal is not None:
                target = self._normal
            if target is not None:
                try:
                    return target.get_nowait().event
                except queue.Empty:
                    continue
            if self._stop.is_set():
                return None
            self._available.clear()
            if not self._queuesEmpty():
                continue
            self._available.wait(timeout=0.1)

    @staticmethod
    def _peek(values: queue.Queue[_QueuedEvent]) -> _QueuedEvent | None:
        with values.mutex:
            return values.queue[0] if values.queue else None

    def _queuesEmpty(self) -> bool:
        return self._priority.empty() and self._normal.empty()

    def _writeEvent(self, event: RuntimeEvent) -> None:
        successful = False
        try:
            record = _projectEvent(event)
            encoded = (
                json.dumps(record, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
                + "\n"
            ).encode("utf-8")
            self._ensureHandle(len(encoded), event)
            if self._handle is None:
                return
            self._handle.write(encoded)
            self._activeBytes += len(encoded)
            with self._stateLock:
                self._lastWrittenEvent = event
            requiresSync = event.level == "ERROR" or event.eventType in {
                "job.completed",
                "job.failed",
                "job.aborted",
            }
            successful = True
            if requiresSync:
                successful = self._flush(sync=True, sourceEvent=event)
            if requiresSync and successful:
                self._clearReportedFailure()
        except (OSError, TypeError, ValueError) as err:
            self._reportFailure(event, f"runtime JSONL write failed: {err}")
            self._closeHandle()
        finally:
            self._markProcessed(event)

    def _ensureHandle(self, incomingBytes: int, sourceEvent: RuntimeEvent) -> None:
        if self._handle is not None and self._activeBytes + incomingBytes <= self.maximumFileBytes:
            return
        if self._handle is not None:
            self._flush(sync=True, sourceEvent=sourceEvent)
            self._closeHandle()
        self._cleanupClosedFiles(
            reserveBytes=min(
                self.maximumTotalBytes,
                max(self.maximumFileBytes, incomingBytes),
            ),
            sourceEvent=sourceEvent,
        )
        self.directory.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        while True:
            self._fileIndex += 1
            filename = (
                f"runtime-{stamp}-{os.getpid()}-{self._fileIndex:04d}.jsonl"
            )
            path = self.directory / filename
            if not path.exists():
                break
        handle = path.open("ab", buffering=0)
        with self._stateLock:
            self._handle = handle
            self._activePath = path
            self._activeBytes = path.stat().st_size

    def _flushIfDue(self) -> None:
        if self.clock() - self._lastFlush >= self.flushIntervalSeconds:
            if self._flush(sync=False, sourceEvent=self._lastWrittenEvent):
                self._clearReportedFailure()

    def _flush(
        self,
        *,
        sync: bool,
        sourceEvent: RuntimeEvent | None = None,
    ) -> bool:
        handle = self._handle
        self._lastFlush = self.clock()
        if handle is None:
            return True
        try:
            handle.flush()
            if sync:
                os.fsync(handle.fileno())
            return True
        except OSError as err:
            self._reportFailure(
                sourceEvent,
                f"runtime JSONL flush failed: {err}",
            )
            return False

    def _closeHandle(self) -> None:
        with self._stateLock:
            handle = self._handle
            self._handle = None
            self._activePath = None
            self._activeBytes = 0
        if handle is not None:
            try:
                handle.close()
            except OSError:
                pass

    def _cleanupClosedFiles(
        self,
        reserveBytes: int = 0,
        sourceEvent: RuntimeEvent | None = None,
    ) -> None:
        now = self.wallClock()
        cutoff = now - self.retentionDays * 86400
        active = self.activePath
        files = [
            path
            for path in self.directory.glob("runtime-*.jsonl")
            if path.is_file() and path != active
        ]
        for path in files:
            try:
                if path.stat().st_mtime < cutoff:
                    path.unlink()
            except OSError as err:
                self._reportFailure(
                    sourceEvent,
                    f"cannot remove expired runtime log: {err}",
                )
        files = sorted(
            (
                path
                for path in self.directory.glob("runtime-*.jsonl")
                if path.is_file() and path != active
            ),
            key=lambda path: (path.stat().st_mtime, path.name),
        )
        total = sum(path.stat().st_size for path in files)
        if active is not None and active.exists():
            total += active.stat().st_size
        sizeLimit = max(0, self.maximumTotalBytes - max(0, int(reserveBytes)))
        for path in files:
            if total <= sizeLimit:
                break
            try:
                size = path.stat().st_size
                path.unlink()
                total -= size
            except OSError as err:
                self._reportFailure(
                    sourceEvent,
                    f"cannot enforce runtime log size limit: {err}",
                )

    def _reportFailure(self, event: RuntimeEvent | None, message: str) -> None:
        with self._stateLock:
            if message == self._reportedFailure:
                return
            self._reportedFailure = message
            callback = self.failureCallback
        if callback is not None:
            try:
                callback(event, message)
            except BaseException:
                pass

    def _clearReportedFailure(self) -> None:
        with self._stateLock:
            self._reportedFailure = ""

    def _markProcessed(self, event: RuntimeEvent) -> None:
        with self._progressCondition:
            self._processedSequences[event.jobId] = max(
                event.sequence,
                self._processedSequences.get(event.jobId, 0),
            )
            self._progressCondition.notify_all()


def _isPriorityEvent(event: RuntimeEvent) -> bool:
    return event.level in {"WARN", "ERROR"} or event.eventType in {
        "job.completed",
        "job.failed",
        "job.aborted",
    }


def _isOperationalEvent(event: RuntimeEvent) -> bool:
    if event.eventType == "runtime.logfile.failed":
        return False
    return (
        event.eventType == "node.log"
        or event.eventType == "artifact.created"
        or event.eventType == "resource.cleanup.failed"
        or event.eventType.startswith("job.")
        or event.eventType.startswith("workflow.")
        or event.eventType.startswith("loop.")
        or event.eventType.startswith("node.")
    )


def _projectEvent(event: RuntimeEvent) -> dict[str, object]:
    try:
        payload = json.loads(event.payloadJson or "{}")
    except json.JSONDecodeError:
        payload = {"invalidPayloadJson": True}
    if not isinstance(payload, dict):
        payload = {"value": payload}
    if event.eventType.endswith(".completed"):
        payload = {
            key: payload[key]
            for key in (
                "status",
                "metrics",
                "diagnostics",
                "branch",
                "artifact",
                "artifacts",
            )
            if key in payload
        }
    iterationPath: object
    try:
        iterationPath = json.loads(event.iterationPathJson or "[]")
    except json.JSONDecodeError:
        iterationPath = []
    return {
        "schemaVersion": "1.0",
        "timestamp": datetime.fromtimestamp(
            event.timestampMs / 1000.0, timezone.utc
        ).isoformat(),
        "timestampMs": event.timestampMs,
        "sequence": event.sequence,
        "level": event.level,
        "eventType": event.eventType,
        "jobId": event.jobId,
        "projectId": event.projectId,
        "workflowId": event.workflowId,
        "workflowRunId": event.workflowRunId,
        "parentWorkflowRunId": event.parentWorkflowRunId,
        "nodeId": event.nodeId,
        "nodeRunId": event.nodeRunId,
        "iterationPath": iterationPath,
        "code": event.code,
        "message": event.message,
        "payload": _jsonValue(payload),
    }


def _jsonValue(value: object) -> object:
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else "<non-finite>"
    if isinstance(value, Mapping):
        return {str(key): _jsonValue(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonValue(item) for item in value]
    return f"<{type(value).__name__}>"
