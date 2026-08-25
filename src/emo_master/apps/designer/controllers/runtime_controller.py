from __future__ import annotations

from typing import Callable

from emo_master.apps.designer.services.runtime_worker import RuntimeStopWorker, RuntimeWorker


class RuntimeController:
    def __init__(
        self,
        runtimeClient,
        runtimePanelState,
        appendLog: Callable[[str, str], None],
        refreshRuntimePanelView: Callable[[], None],
        updateToolbarState: Callable[[], None],
        syncRuntimeProjectBeforeRun: Callable[[], bool],
        applyRuntimeEventToNode: Callable[[dict[str, object]], None],
        setCurrentJobId: Callable[[str | None], None],
        setIsJobRunning: Callable[[bool], None],
        getLoadedProjectPath: Callable[[], str | None],
        getCurrentJobId: Callable[[], str | None],
        getActiveWorkflowId: Callable[[], str | None] | None = None,
        getEntryWorkflowId: Callable[[], str | None] | None = None,
    ) -> None:
        self.runtimeClient = runtimeClient
        self.runtimePanelState = runtimePanelState
        self.appendLog = appendLog
        self.refreshRuntimePanelView = refreshRuntimePanelView
        self.updateToolbarState = updateToolbarState
        self.syncRuntimeProjectBeforeRun = syncRuntimeProjectBeforeRun
        self.applyRuntimeEventToNode = applyRuntimeEventToNode
        self.setCurrentJobId = setCurrentJobId
        self.setIsJobRunning = setIsJobRunning
        self.getLoadedProjectPath = getLoadedProjectPath
        self.getCurrentJobId = getCurrentJobId
        self.getActiveWorkflowId = getActiveWorkflowId or (lambda: None)
        self.getEntryWorkflowId = getEntryWorkflowId or self.getActiveWorkflowId
        self._worker: RuntimeWorker | None = None
        self._stopWorker: RuntimeStopWorker | None = None
        self._stopJobId = ""
        self._stopMode = ""
        self._stopEscalateToForce = False
        self._jobActive = False
        self._closing = False
        self._closed = False

    def startJob(self) -> None:
        if self._closing or self._closed:
            return
        if self._jobActive or (
            self._worker is not None and self._worker.isRunning()
        ) or self._stopWorkerIsRunning():
            self.appendLog("WARN", "已有作业正在运行")
            return
        loadedProjectPath = self.getLoadedProjectPath()
        if loadedProjectPath is None:
            self.appendLog("WARN", "尚未加载项目")
            return
        if not self.syncRuntimeProjectBeforeRun():
            return
        # A new start attempt must not keep presenting the previous terminal job
        # if the worker fails before it receives a new job id.
        self.setCurrentJobId(None)
        workflowId = self.getEntryWorkflowId() or ""
        worker = RuntimeWorker(
            runtimeClient=self.runtimeClient,
            projectId=loadedProjectPath,
            workflowId=workflowId,
        )
        worker.jobAccepted.connect(self._onJobAccepted)
        worker.eventReceived.connect(self._onRuntimeEvent)
        worker.statusChanged.connect(self._onJobStatus)
        worker.failed.connect(self._onWorkerFailed)
        finished = getattr(worker, "finished", None)
        if finished is not None and hasattr(finished, "connect"):
            finished.connect(lambda: self._onWorkerFinished(worker))
        self._worker = worker
        self._jobActive = True
        self.setIsJobRunning(True)
        self.runtimePanelState.updateJob("STARTING", "正在启动作业")
        self.refreshRuntimePanelView()
        self.updateToolbarState()
        worker.start()

    def _onJobAccepted(self, reply) -> None:
        self._jobActive = True
        currentJobId = str(getattr(reply, "job_id", ""))
        self.setCurrentJobId(currentJobId or None)
        self.runtimePanelState.updateJob(
            str(getattr(reply, "status", "ACCEPTED")),
            str(getattr(reply, "message", "作业已接受")),
        )
        self.appendLog("INFO", f"作业已接受：{currentJobId}")
        self.refreshRuntimePanelView()

    def _onWorkerFinished(self, worker: RuntimeWorker) -> None:
        if self._worker is worker:
            self._worker = None
        self.updateToolbarState()

    def _onRuntimeEvent(self, jobEvent) -> None:
        eventType = str(getattr(jobEvent, "event_type", getattr(jobEvent, "eventType", "")))
        eventMessage = str(getattr(jobEvent, "message", ""))
        eventLevel = str(getattr(jobEvent, "level", "INFO"))
        eventNodeId = str(getattr(jobEvent, "node_id", getattr(jobEvent, "nodeId", "")))
        eventPayload = getattr(jobEvent, "payload", {})
        payload = eventPayload if isinstance(eventPayload, dict) else {}
        iterationPath = getattr(
            jobEvent,
            "iterationPath",
            getattr(jobEvent, "iteration_path", ()),
        )
        if not isinstance(iterationPath, (list, tuple)):
            iterationPath = ()
        event = {
            "eventType": eventType,
            "message": eventMessage,
            "level": eventLevel,
            "code": str(getattr(jobEvent, "code", "")),
            "nodeId": eventNodeId,
            "payload": payload,
            "jobId": str(getattr(jobEvent, "job_id", getattr(jobEvent, "jobId", ""))),
            "projectId": str(getattr(jobEvent, "project_id", getattr(jobEvent, "projectId", ""))),
            "workflowId": str(getattr(jobEvent, "workflow_id", getattr(jobEvent, "workflowId", ""))),
            "workflowRunId": str(getattr(jobEvent, "workflow_run_id", getattr(jobEvent, "workflowRunId", ""))),
            "parentWorkflowRunId": str(
                getattr(
                    jobEvent,
                    "parent_workflow_run_id",
                    getattr(jobEvent, "parentWorkflowRunId", ""),
                )
            ),
            "nodeRunId": str(getattr(jobEvent, "node_run_id", getattr(jobEvent, "nodeRunId", ""))),
            "iterationPath": tuple(iterationPath),
            "sequence": int(getattr(jobEvent, "sequence", 0)),
            "timestampMs": int(
                getattr(jobEvent, "timestamp_ms", getattr(jobEvent, "timestampMs", 0))
            ),
        }
        self.appendLog(eventLevel, f"事件 {eventType}：{eventMessage}")
        self.runtimePanelState.applyEvent(event)
        self.applyRuntimeEventToNode(event)
        self.refreshRuntimePanelView()

    def _onJobStatus(self, statusReply) -> None:
        runtimeStatus = str(getattr(statusReply, "status", "UNKNOWN"))
        runtimeMessage = str(getattr(statusReply, "message", ""))
        self.runtimePanelState.updateJob(runtimeStatus, runtimeMessage)
        self.appendLog("INFO", f"作业状态：{runtimeStatus} | {runtimeMessage}")
        if runtimeStatus in ("COMPLETED", "FAILED", "ABORTED"):
            self._jobActive = False
            self.setIsJobRunning(False)
        elif runtimeStatus in ("ACCEPTED", "STARTING", "RUNNING", "STOPPING"):
            self._jobActive = True
            self.setIsJobRunning(True)
        self.refreshRuntimePanelView()
        self.updateToolbarState()

    def _onWorkerFailed(self, message: str) -> None:
        self._jobActive = False
        self.runtimePanelState.updateJob("FAILED", message)
        self.appendLog("ERROR", f"启动作业失败：{message}")
        self.setIsJobRunning(False)
        self.refreshRuntimePanelView()
        self.updateToolbarState()

    def stopJob(self, mode: str | None = None) -> None:
        if self._closed:
            return
        currentJobId = self.getCurrentJobId()
        if currentJobId is None:
            self.appendLog("WARN", "当前没有活动作业")
            return
        stopMode = mode or (
            "force"
            if str(getattr(self.runtimePanelState, "jobStatus", "")) == "STOPPING"
            else "graceful"
        )
        if self._stopWorkerIsRunning():
            if stopMode == "force" and self._stopMode == "graceful":
                self._stopEscalateToForce = True
                self.appendLog("INFO", "已请求将停止作业升级为强制停止")
            return

        if self._worker is not None:
            self._worker.requestStop()
        self._beginStop(currentJobId, stopMode)

    def _beginStop(self, jobId: str, mode: str, updateUi: bool = True) -> None:
        if self._stopWorkerIsRunning():
            return
        self._stopJobId = jobId
        self._stopMode = mode
        if updateUi:
            self._jobActive = True
            self.setIsJobRunning(True)
            self.runtimePanelState.updateJob(
                "STOPPING", f"正在停止作业（{mode}）"
            )
            self.refreshRuntimePanelView()
            self.updateToolbarState()
        worker = RuntimeStopWorker(self.runtimeClient, jobId, mode)
        worker.replyReceived.connect(
            lambda reply, currentWorker=worker: self._onStopReply(currentWorker, reply)
        )
        worker.failed.connect(
            lambda message, currentWorker=worker: self._onStopFailed(
                currentWorker, message
            )
        )
        finished = getattr(worker, "finished", None)
        if finished is not None and hasattr(finished, "connect"):
            finished.connect(
                lambda currentWorker=worker: self._onStopWorkerFinished(currentWorker)
            )
        self._stopWorker = worker
        worker.start()

    def _onStopReply(self, worker: RuntimeStopWorker, reply) -> None:
        if self._stopWorker is not worker:
            return
        if getattr(worker, "resultHandled", False):
            return
        ok = bool(getattr(reply, "ok", True))
        stopStatus = str(getattr(reply, "status", "UNKNOWN"))
        stopMessage = str(getattr(reply, "message", ""))
        if not ok or stopStatus == "FAILED":
            self._onStopFailed(worker, stopMessage or "runtime rejected stop request")
            return
        worker.resultHandled = True
        self.runtimePanelState.updateJob(stopStatus, stopMessage)
        self.appendLog("INFO", f"停止作业：{stopStatus} | {stopMessage}")
        self.refreshRuntimePanelView()
        if stopStatus in ("COMPLETED", "FAILED", "ABORTED"):
            self._jobActive = False
            self.setIsJobRunning(False)
        else:
            self._jobActive = True
            self.setIsJobRunning(True)
        self.updateToolbarState()

    def _onStopFailed(self, worker: RuntimeStopWorker, message: str) -> None:
        if self._stopWorker is not worker:
            return
        if getattr(worker, "resultHandled", False):
            return
        worker.resultHandled = True
        self.appendLog("ERROR", f"停止作业失败：{message}")
        self.runtimePanelState.updateJob("RUNNING", f"停止失败：{message}")
        self._jobActive = True
        self.setIsJobRunning(True)
        self.refreshRuntimePanelView()
        self.updateToolbarState()

    def _onStopWorkerFinished(self, worker: RuntimeStopWorker) -> None:
        if self._stopWorker is not worker:
            return
        shouldEscalate = (
            self._stopEscalateToForce
            and self._stopMode == "graceful"
            and self._jobActive
            and self._stopJobId != ""
        )
        jobId = self._stopJobId
        self._stopWorker = None
        self._stopJobId = ""
        self._stopMode = ""
        self._stopEscalateToForce = False
        if shouldEscalate and not self._closing and not self._closed:
            self._beginStop(jobId, "force")

    def _stopWorkerIsRunning(self) -> bool:
        worker = self._stopWorker
        return worker is not None

    def _consumeStopWorkerResult(self, worker: RuntimeStopWorker) -> None:
        if getattr(worker, "resultHandled", False):
            return
        error = getattr(worker, "error", None)
        if error is not None:
            self._onStopFailed(worker, str(error))
            return
        reply = getattr(worker, "reply", None)
        if reply is not None:
            self._onStopReply(worker, reply)

    def _waitForWorker(self, worker, timeoutMs: int) -> None:
        wait = getattr(worker, "wait", None)
        isRunning = getattr(worker, "isRunning", None)
        if not callable(wait) or not callable(isRunning) or not isRunning():
            return
        wait(timeoutMs)
        if isRunning():
            self.appendLog("WARN", "等待运行时线程结束")
            wait(-1)

    def close(self) -> None:
        """Stop the worker subscription before the Designer window closes."""
        if self._closed:
            return
        self._closing = True
        stopStarted = False
        currentJobId = self.getCurrentJobId()
        currentStatus = str(getattr(self.runtimePanelState, "jobStatus", ""))
        if (
            currentJobId is not None
            and (self._jobActive or currentStatus in {"ACCEPTED", "STARTING", "RUNNING", "STOPPING"})
            and not self._stopWorkerIsRunning()
        ):
            if self._worker is not None:
                self._worker.requestStop()
            self._beginStop(currentJobId, "graceful", updateUi=False)
            stopStarted = True

        stopWorker = self._stopWorker
        stopJobId = self._stopJobId or (currentJobId or "")
        stopMode = self._stopMode
        if stopWorker is not None:
            self._waitForWorker(stopWorker, 12000)
            self._consumeStopWorkerResult(stopWorker)
            self._onStopWorkerFinished(stopWorker)

        if self._jobActive and stopJobId and stopMode == "graceful":
            self._beginStop(stopJobId, "force", updateUi=False)
            forceWorker = self._stopWorker
            if forceWorker is not None:
                self._waitForWorker(forceWorker, 12000)
                self._consumeStopWorkerResult(forceWorker)
                self._onStopWorkerFinished(forceWorker)

        worker = self._worker
        if worker is not None:
            worker.requestStop()
            self._waitForWorker(worker, 12000 if stopStarted else 2000)
            self._worker = None
        self._jobActive = False
        self._closed = True
        closeClient = getattr(self.runtimeClient, "close", None)
        if callable(closeClient):
            closeClient()
