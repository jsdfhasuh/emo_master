from __future__ import annotations

import threading
import inspect
import json
import time
from uuid import uuid4
from typing import Any, Callable

from emo_master.core.contracts.legacy_snapshots import normalizeLegacySnapshotPolicy


try:
    from PySide2.QtCore import QThread, Signal

    class RuntimeWorker(QThread):
        """Runs the long-lived runtime stream outside the Designer UI thread."""

        jobAccepted: Any = Signal(object)
        eventReceived: Any = Signal(object)
        statusChanged: Any = Signal(object)
        failed: Any = Signal(str)
        uncertain: Any = Signal(str)

        def __init__(
            self,
            runtimeClient,
            projectId: str,
            workflowId: str = "",
            inputs: dict[str, object] | None = None,
            *,
            capturePresentation: bool = False,
            legacySnapshotPolicy: str = "ALL",
            previousCaptureJob: tuple[str, str] | None = None,
        ) -> None:
            super().__init__()
            self.runtimeClient = runtimeClient
            self.projectId = projectId
            self.workflowId = workflowId
            self.inputs = dict(inputs or {})
            self.capturePresentation = capturePresentation
            self.legacySnapshotPolicy = normalizeLegacySnapshotPolicy(legacySnapshotPolicy)
            self.captureRequirements: dict = {}
            self.previousCaptureJob = previousCaptureJob
            self.startRequestId = uuid4().hex
            self.expectedRuntimeInstanceId = ""
            self.startUncertain = False
            self.cancelAfterStart = False
            self._stopEvent = threading.Event()
            self._streamLock = threading.RLock()
            self._stream = None
            self._jobId = ""

        def run(self) -> None:  # type: ignore[override]
            _runWorker(self)

        def requestStop(self) -> None:
            self._stopEvent.set()
            self.cancelSubscription()

        def requestStopAfterStart(self) -> None:
            self.cancelAfterStart = True

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
            *,
            capturePresentation: bool = False,
            legacySnapshotPolicy: str = "ALL",
            previousCaptureJob: tuple[str, str] | None = None,
        ) -> None:
            self.runtimeClient = runtimeClient
            self.projectId = projectId
            self.workflowId = workflowId
            self.inputs = dict(inputs or {})
            self.capturePresentation = capturePresentation
            self.legacySnapshotPolicy = normalizeLegacySnapshotPolicy(legacySnapshotPolicy)
            self.captureRequirements: dict = {}
            self.previousCaptureJob = previousCaptureJob
            self.startRequestId = uuid4().hex
            self.expectedRuntimeInstanceId = ""
            self.startUncertain = False
            self.cancelAfterStart = False
            self.uncertain = _Signal()
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

        def requestStopAfterStart(self) -> None:
            self.cancelAfterStart = True

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
        # All negotiation/cleanup happens off the GUI thread and before Start.
        prepare = getattr(worker.runtimeClient, "prepareStart", None)
        if callable(prepare):
            requirements = getattr(worker, "captureRequirements", {})
            parameters = _parameters(prepare)
            acceptsKeywords = any(parameter.kind is inspect.Parameter.VAR_KEYWORD
                                  for parameter in parameters.values())
            kwargs: dict[str, object] = {}
            if requirements:
                if not acceptsKeywords and "captureRequirements" not in parameters:
                    raise ValueError("当前 Runtime 客户端不支持页面采集额度协商")
                kwargs["captureRequirements"] = requirements
            if acceptsKeywords or "legacySnapshotPolicy" in parameters:
                kwargs["legacySnapshotPolicy"] = worker.legacySnapshotPolicy
            elif worker.legacySnapshotPolicy != "ALL":
                raise ValueError("当前 Runtime 客户端不支持 NONE 节点快照策略；未创建任务")
            worker.expectedRuntimeInstanceId = prepare(worker.capturePresentation, **kwargs)
        elif worker.capturePresentation or worker.legacySnapshotPolicy != "ALL":
            raise ValueError("当前 Runtime 客户端不支持所选运行采集策略；未创建任务")
        if worker.legacySnapshotPolicy != "ALL":
            parameters = _parameters(worker.runtimeClient.startJob)
            acceptsKeywords = any(parameter.kind is inspect.Parameter.VAR_KEYWORD
                                  for parameter in parameters.values())
            required = {"legacySnapshotPolicy", "startRequestId", "expectedRuntimeInstanceId"}
            if not worker.expectedRuntimeInstanceId or (not acceptsKeywords and not required.issubset(parameters)):
                raise ValueError("当前 Runtime 客户端不支持 NONE 启动请求身份；未创建任务")
        _releasePreviousCapture(worker)
        inspection = getattr(worker, 'inspection', None)
        if inspection is not None:
            worker.inspectionSessionId = inspection.ensureSession(worker.projectId)
        if worker.stopRequested():
            return
        try:
            reply = _startJobCompat(worker)
        except Exception as error:
            reply = _reconcileStart(worker, error)
            if reply is None:
                return
        if str(getattr(reply, "status", "")) in {"UNKNOWN", "RESET_REQUIRED"}:
            reply = _reconcileStart(worker, RuntimeError(str(getattr(reply, "message", "启动结果未知"))))
            if reply is None:
                return
        jobId = str(getattr(reply, "job_id", ""))
        if not bool(getattr(reply, "ok", False)):
            # A rejected start can still own a failed job's capture resources.
            if jobId:
                worker.setJobId(jobId)
                worker.jobAccepted.emit(reply)
            worker.statusChanged.emit(reply)
            worker.failed.emit(str(getattr(reply, "message", "runtime job failed")))
            return
        if not jobId:
            _markUncertain(worker, "Runtime 返回了空任务标识；须核实此次启动，不能重新启动")
            return
        worker.setJobId(jobId)
        worker.startUncertain = False
        worker.jobAccepted.emit(reply)
        if worker.stopRequested() or worker.cancelAfterStart:
            stopJob = getattr(worker.runtimeClient, "stopJob", None)
            if callable(stopJob):
                try:
                    stopJob(jobId)
                except Exception:
                    pass
            _emitStatusAfterStop(worker, jobId)
            return
        _followJobEvents(worker, jobId)
        if worker.stopRequested():
            _emitStatusAfterStop(worker, jobId)
    except Exception as err:
        if worker.stopRequested():
            _emitStatusAfterStop(worker, worker._jobId)
        else:
            worker.failed.emit(str(err))


def _markUncertain(worker: RuntimeWorker, message: str) -> None:
    worker.startUncertain = True
    worker.uncertain.emit(message)


def _reconcileStart(worker: RuntimeWorker, error: Exception):
    _markUncertain(worker, f"启动结果尚未确认，正在核实同一次请求；不会重复启动：{error}")
    query = getattr(worker.runtimeClient, "getStartRequest", None)
    if not worker.expectedRuntimeInstanceId or not callable(query):
        return None
    delay = .1
    while not worker.stopRequested():
        try:
            reply = query(worker.startRequestId, worker.expectedRuntimeInstanceId)
            generation = str(getattr(reply, "runtime_instance_id", ""))
            requestId = str(getattr(reply, "start_request_id", ""))
            if generation == worker.expectedRuntimeInstanceId and requestId == worker.startRequestId:
                if getattr(reply, "ok", False) and getattr(reply, "job_id", ""):
                    worker.startUncertain = False
                    return reply
                if str(getattr(reply, "status", "")) in {"REJECTED", "FAILED"}:
                    worker.startUncertain = False
                    return reply
            if str(getattr(reply, "status", "")) == "RESET_REQUIRED":
                _markUncertain(worker, "Runtime 实例已变化，旧启动尚未核实；不会在新实例自动重启")
                return None
        except Exception:
            pass  # Reconnect/query this request only; NEVER issue a second Start.
        if worker._stopEvent.wait(delay):
            break
        delay = min(2.0, delay * 2)
    return None


def _releasePreviousCapture(worker: RuntimeWorker) -> None:
    previous = worker.previousCaptureJob
    if not previous:
        return
    release = getattr(worker.runtimeClient, "releaseDisplayJob", None)
    if not callable(release):
        raise ValueError("无法核实并释放上次页面采集任务；未启动新任务")
    if worker.expectedRuntimeInstanceId != previous[1]:
        raise ValueError("Runtime 实例已变化，无法确认上次任务资源已结束；未启动新任务")
    deadline = time.monotonic() + 10
    while not worker.stopRequested():
        try:
            release(*previous)
            return
        except Exception as error:
            code = str(getattr(error, "code", ""))
            if "FAILED_PRECONDITION" not in code or "RESET_REQUIRED" in str(error):
                raise
            if time.monotonic() >= deadline:
                raise TimeoutError("上次任务仍在释放资源；请稍候再明确启动") from error
            worker._stopEvent.wait(.1)


def _followJobEvents(worker: RuntimeWorker, jobId: str) -> None:
    lastSequence = 0
    reconnectDelay = 0.1
    while not worker.stopRequested():
        events = None
        streamError: BaseException | None = None
        try:
            events = _streamEventsCompat(
                worker.runtimeClient,
                jobId,
                afterSequence=lastSequence,
                follow=True,
            )
            worker.setActiveStream(events)
            for event in events:
                sequence = _eventSequence(event)
                if sequence > 0 and sequence <= lastSequence:
                    continue
                worker.eventReceived.emit(event)
                if sequence > 0:
                    lastSequence = sequence
            reconnectDelay = 0.1
        except BaseException as err:
            if worker.stopRequested():
                break
            streamError = err
        finally:
            if events is not None:
                close = getattr(events, "close", None)
                if callable(close):
                    try:
                        close()
                    except BaseException:
                        pass
            worker.clearActiveStream()

        if worker.stopRequested():
            break
        status = _jobStatusOrNone(worker.runtimeClient, jobId)
        if status is not None and _isTerminalStatus(status):
            if lastSequence > 0:
                try:
                    lastSequence = _replayAvailableEvents(
                        worker,
                        jobId,
                        lastSequence,
                    )
                except BaseException:
                    if worker.stopRequested():
                        break
                    if worker._stopEvent.wait(reconnectDelay):
                        break
                    reconnectDelay = min(2.0, reconnectDelay * 2.0)
                    continue
            worker.statusChanged.emit(status)
            return
        if streamError is not None and not callable(
            getattr(worker.runtimeClient, "getJobStatus", None)
        ):
            raise streamError
        if worker._stopEvent.wait(reconnectDelay):
            break
        reconnectDelay = min(2.0, reconnectDelay * 2.0)


def _replayAvailableEvents(
    worker: RuntimeWorker,
    jobId: str,
    lastSequence: int,
) -> int:
    events = None
    try:
        events = _streamEventsCompat(
            worker.runtimeClient,
            jobId,
            afterSequence=lastSequence,
            follow=True,
        )
        worker.setActiveStream(events)
        for event in events:
            sequence = _eventSequence(event)
            if sequence > 0 and sequence <= lastSequence:
                continue
            worker.eventReceived.emit(event)
            if sequence > 0:
                lastSequence = sequence
    finally:
        if events is not None:
            close = getattr(events, "close", None)
            if callable(close):
                try:
                    close()
                except BaseException:
                    pass
        worker.clearActiveStream()
    return lastSequence


def _jobStatusOrNone(runtimeClient, jobId: str):
    getStatus = getattr(runtimeClient, "getJobStatus", None)
    if not callable(getStatus):
        return None
    try:
        return getStatus(jobId)
    except BaseException:
        return None


def _isTerminalStatus(status: object) -> bool:
    return str(getattr(status, "status", "")) in {
        "COMPLETED",
        "FAILED",
        "ABORTED",
    }


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
    sessionId = getattr(worker, 'inspectionSessionId', '')
    if sessionId:
        if not acceptsVarKeywords and 'inspectionSessionId' not in parameters:
            raise ValueError('当前 Runtime 客户端不支持运行检查会话；未创建任务')
        keywordArguments['inspectionSessionId'] = sessionId
    if acceptsVarKeywords or "workflowId" in parameters:
        keywordArguments["workflowId"] = worker.workflowId
    elif "workflow_id" in parameters:
        keywordArguments["workflow_id"] = worker.workflowId
    if acceptsVarKeywords or "inputs" in parameters:
        keywordArguments["inputs"] = worker.inputs
    elif "inputs_json" in parameters:
        keywordArguments["inputs_json"] = json.dumps(worker.inputs, ensure_ascii=True)
    if acceptsVarKeywords or "legacySnapshotPolicy" in parameters:
        keywordArguments["legacySnapshotPolicy"] = worker.legacySnapshotPolicy
    elif worker.legacySnapshotPolicy != "ALL":
        raise ValueError("当前 Runtime 客户端不支持 NONE 节点快照策略；未创建任务")
    if worker.capturePresentation:
        if not acceptsVarKeywords and "capturePresentation" not in parameters:
            raise ValueError("当前 Runtime 客户端不支持正常运行时页面采集")
        keywordArguments["capturePresentation"] = True
    if worker.expectedRuntimeInstanceId:
        if not acceptsVarKeywords and not {"startRequestId", "expectedRuntimeInstanceId"}.issubset(parameters):
            raise ValueError("当前 Runtime 客户端不支持启动请求身份")
        keywordArguments["startRequestId"] = worker.startRequestId
        keywordArguments["expectedRuntimeInstanceId"] = worker.expectedRuntimeInstanceId
    if keywordArguments:
        return method(worker.projectId, **keywordArguments)
    return method(worker.projectId)


def _streamEventsCompat(
    runtimeClient,
    jobId: str,
    *,
    afterSequence: int = 0,
    follow: bool = True,
):
    method = getattr(runtimeClient, "iterJobEvents", None)
    if not callable(method):
        method = runtimeClient.streamJobEvents
    parameters = _parameters(method)
    acceptsVarKeywords = any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in parameters.values()
    )
    keywordArguments: dict[str, object] = {}
    if acceptsVarKeywords or "afterSequence" in parameters:
        keywordArguments["afterSequence"] = afterSequence
    elif "after_sequence" in parameters:
        keywordArguments["after_sequence"] = afterSequence
    if acceptsVarKeywords or "follow" in parameters:
        keywordArguments["follow"] = follow
    return method(jobId, **keywordArguments)


def _eventSequence(event: object) -> int:
    rawValue = getattr(event, "sequence", 0)
    if isinstance(rawValue, bool):
        return 0
    if isinstance(rawValue, int):
        return max(0, rawValue)
    if isinstance(rawValue, float):
        return max(0, int(rawValue))
    if isinstance(rawValue, str):
        try:
            return max(0, int(rawValue))
        except ValueError:
            return 0
    return 0


def _parameters(method) -> dict[str, inspect.Parameter]:
    try:
        return dict(inspect.signature(method).parameters)
    except (TypeError, ValueError):
        return {}
