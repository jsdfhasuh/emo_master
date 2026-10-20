from __future__ import annotations

import queue
import threading

from emo_master.apps.runtime.events.models import MAX_WORKER_EVENT_BATCH, batchableWorkerEvent


class EventBridge(threading.Thread):
    def __init__(self, supervisor, jobId: str, process, eventQueue, cancelEvent) -> None:
        super().__init__(name=f"runtime-event-bridge-{jobId}", daemon=True)
        self.supervisor = supervisor
        self.jobId = jobId
        self.process = process
        self.eventQueue = eventQueue
        self.cancelEvent = cancelEvent
        self.stopEvent = threading.Event()
        self._pendingEvent = None

    def requestStop(self) -> None:
        self.stopEvent.set()

    def run(self) -> None:
        try:
            self._run()
        finally:
            # This is the final action: after it the bridge never touches the
            # process or queue. Timed-out external joins retain ownership until
            # this hook can reacquire the Supervisor lock and finish retirement.
            self.supervisor.bridgeStopped(self.jobId)

    def _run(self) -> None:
        while not self.stopEvent.is_set():
            try:
                event = self._nextEvent()
            except queue.Empty:
                event = None
            if self.stopEvent.is_set():
                break
            if isinstance(event, dict):
                try:
                    self._consume(event)
                except BaseException as err:
                    self.supervisor.bridgeError(self.jobId, err)
                    break
            if self.stopEvent.is_set():
                break
            if not self._processIsAlive():
                self._drain()
                break
            if self.stopEvent.is_set():
                break
            self.supervisor.checkHeartbeat(self.jobId)
        if self.stopEvent.is_set():
            return
        self.supervisor.processExited(self.jobId, self._processExitCode())

    def _drain(self) -> None:
        while not self.stopEvent.is_set():
            try:
                event = self._nextEvent(nowait=True)
            except queue.Empty:
                return
            if isinstance(event, dict):
                try:
                    self._consume(event)
                except BaseException as err:
                    self.supervisor.bridgeError(self.jobId, err)
                    return

    def _nextEvent(self, *, nowait=False):
        if self._pendingEvent is not None:
            event, self._pendingEvent = self._pendingEvent, None
            return event
        return self.eventQueue.get_nowait() if nowait else self.eventQueue.get(timeout=0.2)

    def _consume(self, event):
        consumeMany = getattr(self.supervisor, "consumeWorkerEvents", None)
        def ordinary(item):
            return isinstance(item, dict) and batchableWorkerEvent(
                str(item.get("eventType", "process.event")))
        if not callable(consumeMany) or not ordinary(event):
            self.supervisor.consumeWorkerEvent(self.jobId, event)
            return
        batch = [event]
        while len(batch) < MAX_WORKER_EVENT_BATCH and not self.stopEvent.is_set():
            try:
                following = self.eventQueue.get_nowait()
            except queue.Empty:
                break
            if not ordinary(following):
                # The first barrier stays before every subsequent queue item,
                # including when the process exits and _drain takes over.
                self._pendingEvent = following
                break
            batch.append(following)
        if self.stopEvent.is_set():
            return
        if len(batch) == 1:
            self.supervisor.consumeWorkerEvent(self.jobId, event)
        else:
            consumeMany(self.jobId, batch)

    def _processIsAlive(self) -> bool:
        try:
            return bool(self.process.is_alive())
        except ValueError:
            return False

    def _processExitCode(self) -> int | None:
        try:
            return self.process.exitcode
        except ValueError:
            return None
