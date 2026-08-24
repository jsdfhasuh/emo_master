from __future__ import annotations

import threading
import inspect
from typing import Callable


try:
    from PySide2.QtCore import QThread, Signal

    class RuntimeWorker(QThread):
        """Runs the long-lived runtime stream outside the Designer UI thread."""

        jobAccepted = Signal(object)
        eventReceived = Signal(object)
        statusChanged = Signal(object)
        failed = Signal(str)

        def __init__(
            self,
            runtimeClient,
            projectId: str,
            workflowId: str = "",
            inputs: dict[str, object] | None = None,
        ) -> None:
            super().__init__()
            self.runtimeClient = runtimeClient
            self.projectId = projectId
            self.workflowId = workflowId
            self.inputs = dict(inputs or {})

        def run(self) -> None:  # type: ignore[override]
            _runWorker(self)

except Exception:  # pragma: no cover

    class _Signal:
        def __init__(self) -> None:
            self._callbacks: list[Callable[..., object]] = []

        def connect(self, callback: Callable[..., object]) -> None:
            self._callbacks.append(callback)

        def emit(self, *args) -> None:
            for callback in list(self._callbacks):
                callback(*args)

    class RuntimeWorker:  # type: ignore[no-redef]
        """Threaded fallback used when Qt is unavailable in headless tooling."""

        def __init__(
            self,
            runtimeClient,
            projectId: str,
            workflowId: str = "",
            inputs: dict[str, object] | None = None,
        ) -> None:
            self.runtimeClient = runtimeClient
            self.projectId = projectId
            self.workflowId = workflowId
            self.inputs = dict(inputs or {})
            self.jobAccepted = _Signal()
            self.eventReceived = _Signal()
            self.statusChanged = _Signal()
            self.failed = _Signal()
            self.finished = _Signal()
            self._thread: threading.Thread | None = None

        def start(self) -> None:
            self._thread = threading.Thread(target=self.run, daemon=True)
            self._thread.start()

        def run(self) -> None:
            try:
                _runWorker(self)
            finally:
                self.finished.emit()

        def wait(self, timeoutMs: int = -1) -> None:
            if self._thread is not None:
                self._thread.join(None if timeoutMs < 0 else timeoutMs / 1000.0)

        def isRunning(self) -> bool:
            return self._thread is not None and self._thread.is_alive()


def _runWorker(worker: RuntimeWorker) -> None:
    try:
        reply = _startJobCompat(worker)
        if not bool(getattr(reply, "ok", False)):
            worker.failed.emit(str(getattr(reply, "message", "runtime job failed")))
            worker.statusChanged.emit(reply)
            return
        worker.jobAccepted.emit(reply)
        jobId = str(getattr(reply, "job_id", ""))
        if jobId == "":
            worker.failed.emit("runtime returned an empty job id")
            return
        events = _streamEventsCompat(worker.runtimeClient, jobId)
        for event in events:
            worker.eventReceived.emit(event)
        worker.statusChanged.emit(worker.runtimeClient.getJobStatus(jobId))
    except Exception as err:
        worker.failed.emit(str(err))


def _startJobCompat(worker: RuntimeWorker):
    method = worker.runtimeClient.startJob
    parameters = _parameters(method)
    acceptsKeywords = "workflowId" in parameters or "workflow_id" in parameters
    acceptsKeywords = acceptsKeywords or "inputs" in parameters or "inputs_json" in parameters
    acceptsKeywords = acceptsKeywords or any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in parameters.values()
    )
    if acceptsKeywords:
        return method(
            worker.projectId,
            workflowId=worker.workflowId,
            inputs=worker.inputs,
        )
    return method(worker.projectId)


def _streamEventsCompat(runtimeClient, jobId: str):
    method = runtimeClient.streamJobEvents
    parameters = _parameters(method)
    acceptsFollow = "follow" in parameters or any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in parameters.values()
    )
    if acceptsFollow:
        return method(jobId, follow=True)
    return method(jobId)


def _parameters(method) -> dict[str, inspect.Parameter]:
    try:
        return dict(inspect.signature(method).parameters)
    except (TypeError, ValueError):
        return {}
