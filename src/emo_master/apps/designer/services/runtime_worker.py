from __future__ import annotations

import threading
import inspect
import json
import time
from typing import Any, Callable


try:
    from PySide2.QtCore import QThread, Signal

    class RuntimeWorker(QThread):
        """Runs the long-lived runtime stream outside the Designer UI thread."""

        jobAccepted: Any = Signal(object)
        eventReceived: Any = Signal(object)
        statusChanged: Any = Signal(object)
        failed: Any = Signal(str)

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
            self._stopEvent = threading.Event()
            self._streamLock = threading.RLock()
            self._stream = None
            self._jobId = ""

        def run(self) -> None:  # type: ignore[override]
            _runWorker(self)

        def requestStop(self) -> None:
            self._stopEvent.set()
            self.cancelSubscription()

        def requestInterruption(self) -> None:
            self.requestStop()

        def isInterruptionRequested(self) -> bool:
            return self.stopRequested()

        def cancelSubscription(self) -> None:
            _cancelSubscription(self)

        def stopRequested(self) -> bool:
            return self._stopEvent.is_set()

        def setActiveStream(self, stream) -> None:
            with self._streamLock:
                self._stream = stream

        def clearActiveStream(self) -> None:
            with self._streamLock:
                self._stream = None

        def setJobId(self, jobId: str) -> None:
            self._jobId = jobId

    class RuntimeStopWorker(QThread):
        """Executes a potentially blocking StopJob RPC off the UI thread."""

        replyReceived: Any = Signal(object)
        failed: Any = Signal(str)

        def __init__(self, runtimeClient, jobId: str, mode: str) -> None:
            super().__init__()
            self.runtimeClient = runtimeClient
            self.jobId = jobId
            self.mode = mode
            self.reply: object | None = None
            self.error: str | None = None
            self.resultHandled = False

        def run(self) -> None:  # type: ignore[override]
            _runStopWorker(self)

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
            self._stopEvent = threading.Event()
            self._streamLock = threading.RLock()
            self._stream = None
            self._jobId = ""

        def start(self) -> None:
            self._thread = threading.Thread(target=self.run, daemon=True)
            self._thread.start()

        def run(self) -> None:
            try:
                _runWorker(self)
            finally:
                self.finished.emit()

        def wait(self, timeoutMs: int = -1) -> bool:
            if self._thread is not None:
                self._thread.join(None if timeoutMs < 0 else timeoutMs / 1000.0)
                return not self._thread.is_alive()
            return True

        def isRunning(self) -> bool:
            return self._thread is not None and self._thread.is_alive()

        def requestStop(self) -> None:
            self._stopEvent.set()
            self.cancelSubscription()

        def requestInterruption(self) -> None:
            self.requestStop()

        def isInterruptionRequested(self) -> bool:
            return self.stopRequested()

        def cancelSubscription(self) -> None:
            _cancelSubscription(self)

        def stopRequested(self) -> bool:
            return self._stopEvent.is_set()

        def setActiveStream(self, stream) -> None:
            with self._streamLock:
                self._stream = stream

        def clearActiveStream(self) -> None:
            with self._streamLock:
                self._stream = None

        def setJobId(self, jobId: str) -> None:
            self._jobId = jobId

    class RuntimeStopWorker:  # type: ignore[no-redef]
        """Threaded fallback for headless Designer tests."""

        def __init__(self, runtimeClient, jobId: str, mode: str) -> None:
            self.runtimeClient = runtimeClient
            self.jobId = jobId
            self.mode = mode
            self.reply: object | None = None
            self.error: str | None = None
            self.resultHandled = False
            self.replyReceived = _Signal()
            self.failed = _Signal()
            self.finished = _Signal()
            self._thread: threading.Thread | None = None

        def start(self) -> None:
            self._thread = threading.Thread(target=self.run, daemon=True)
            self._thread.start()

        def run(self) -> None:
            try:
                _runStopWorker(self)
            finally:
                self.finished.emit()

        def wait(self, timeoutMs: int = -1) -> bool:
            if self._thread is not None:
                self._thread.join(None if timeoutMs < 0 else timeoutMs / 1000.0)
                return not self._thread.is_alive()
            return True

        def isRunning(self) -> bool:
            return self._thread is not None and self._thread.is_alive()


def _cancelSubscription(worker: RuntimeWorker) -> None:
    with worker._streamLock:
        stream = worker._stream
    if stream is not None:
        cancel = getattr(stream, "cancel", None)
        if callable(cancel):
            cancel()
    cancelClientStream = getattr(worker.runtimeClient, "cancelEventStream", None)
    if callable(cancelClientStream) and worker._jobId:
        cancelClientStream(worker._jobId)


def _runStopWorker(worker: RuntimeStopWorker) -> None:
    try:
        reply = worker.runtimeClient.stopJob(worker.jobId, mode=worker.mode)
    except Exception as err:
        worker.error = str(err)
        worker.failed.emit(str(err))
        return
    worker.reply = reply
    worker.replyReceived.emit(reply)


def _runWorker(worker: RuntimeWorker) -> None:
    try:
        if worker.stopRequested():
            return
        reply = _startJobCompat(worker)
        if not bool(getattr(reply, "ok", False)):
            worker.failed.emit(str(getattr(reply, "message", "runtime job failed")))
            worker.statusChanged.emit(reply)
            return
        worker.jobAccepted.emit(reply)
        jobId = str(getattr(reply, "job_id", ""))
        worker.setJobId(jobId)
        if jobId == "":
            worker.failed.emit("runtime returned an empty job id")
            return
        if worker.stopRequested():
            stopJob = getattr(worker.runtimeClient, "stopJob", None)
            if callable(stopJob):
                try:
                    stopJob(jobId)
                except Exception:
                    pass
            _emitStatusAfterStop(worker, jobId)
            return
        events = _streamEventsCompat(worker.runtimeClient, jobId)
        worker.setActiveStream(events)
        try:
            for event in events:
                worker.eventReceived.emit(event)
        except BaseException:
            if not worker.stopRequested():
                raise
        finally:
            close = getattr(events, "close", None)
            if callable(close):
                close()
            worker.clearActiveStream()
        if worker.stopRequested():
            _emitStatusAfterStop(worker, jobId)
        else:
            worker.statusChanged.emit(worker.runtimeClient.getJobStatus(jobId))
    except Exception as err:
        if worker.stopRequested():
            _emitStatusAfterStop(worker, worker._jobId)
        else:
            worker.failed.emit(str(err))


def _emitStatusAfterStop(worker: RuntimeWorker, jobId: str) -> None:
    if jobId == "":
        return
    deadline = time.monotonic() + 10.0
    while True:
        try:
            status = worker.runtimeClient.getJobStatus(jobId)
        except Exception:
            return
        worker.statusChanged.emit(status)
        if str(getattr(status, "status", "")) in {"COMPLETED", "FAILED", "ABORTED"}:
            return
        if time.monotonic() >= deadline:
            return
        threading.Event().wait(0.1)


def _startJobCompat(worker: RuntimeWorker):
    method = worker.runtimeClient.startJob
    parameters = _parameters(method)
    acceptsVarKeywords = any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in parameters.values()
    )
    keywordArguments: dict[str, object] = {}
    if acceptsVarKeywords or "workflowId" in parameters:
        keywordArguments["workflowId"] = worker.workflowId
    elif "workflow_id" in parameters:
        keywordArguments["workflow_id"] = worker.workflowId
    if acceptsVarKeywords or "inputs" in parameters:
        keywordArguments["inputs"] = worker.inputs
    elif "inputs_json" in parameters:
        keywordArguments["inputs_json"] = json.dumps(worker.inputs, ensure_ascii=True)
    if keywordArguments:
        return method(worker.projectId, **keywordArguments)
    return method(worker.projectId)


def _streamEventsCompat(runtimeClient, jobId: str):
    method = getattr(runtimeClient, "iterJobEvents", None)
    if not callable(method):
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
