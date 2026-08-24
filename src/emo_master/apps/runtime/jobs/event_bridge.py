from __future__ import annotations

import queue
import threading


class EventBridge(threading.Thread):
    def __init__(self, supervisor, jobId: str, process, eventQueue, cancelEvent) -> None:
        super().__init__(name=f"runtime-event-bridge-{jobId}", daemon=True)
        self.supervisor = supervisor
        self.jobId = jobId
        self.process = process
        self.eventQueue = eventQueue
        self.cancelEvent = cancelEvent
        self.stopEvent = threading.Event()

    def requestStop(self) -> None:
        self.stopEvent.set()

    def run(self) -> None:
        while not self.stopEvent.is_set():
            try:
                event = self.eventQueue.get(timeout=0.2)
            except queue.Empty:
                event = None
            if self.stopEvent.is_set():
                break
            if isinstance(event, dict):
                try:
                    self.supervisor.consumeWorkerEvent(self.jobId, event)
                except BaseException as err:
                    self.supervisor.bridgeError(self.jobId, err)
                    break
            if not self.process.is_alive():
                self._drain()
                break
            self.supervisor.checkHeartbeat(self.jobId)
        if self.stopEvent.is_set():
            self.supervisor.bridgeStopped(self.jobId)
            return
        self.supervisor.processExited(self.jobId, self.process.exitcode)

    def _drain(self) -> None:
        while not self.stopEvent.is_set():
            try:
                event = self.eventQueue.get_nowait()
            except queue.Empty:
                return
            if isinstance(event, dict):
                try:
                    self.supervisor.consumeWorkerEvent(self.jobId, event)
                except BaseException as err:
                    self.supervisor.bridgeError(self.jobId, err)
                    return
