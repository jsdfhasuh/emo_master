from __future__ import annotations

from typing import Callable


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

    def startJob(self) -> None:
        loadedProjectPath = self.getLoadedProjectPath()
        if loadedProjectPath is None:
            self.appendLog("WARN", "尚未加载项目")
            return
        _ = self.syncRuntimeProjectBeforeRun()
        reply = self.runtimeClient.startJob(loadedProjectPath)
        if not getattr(reply, "ok", False):
            self.appendLog(
                "ERROR", f"启动作业失败：{getattr(reply, 'message', '未知错误')}"
            )
            return
        currentJobId = str(getattr(reply, "job_id", ""))
        self.setCurrentJobId(currentJobId)
        self.setIsJobRunning(True)
        self.runtimePanelState.updateJob("RUNNING", "作业已启动")
        self.appendLog("INFO", f"作业已启动：{currentJobId}")
        statusReply = self.runtimeClient.getJobStatus(currentJobId)
        runtimeStatus = str(getattr(statusReply, "status", "UNKNOWN"))
        runtimeMessage = str(getattr(statusReply, "message", ""))
        self.runtimePanelState.updateJob(runtimeStatus, runtimeMessage)
        self.appendLog("INFO", f"作业状态：{runtimeStatus} | {runtimeMessage}")
        jobEvents = self.runtimeClient.streamJobEvents(currentJobId)
        for jobEvent in jobEvents:
            eventType = getattr(jobEvent, "event_type", "")
            eventMessage = getattr(jobEvent, "message", "")
            eventLevel = getattr(jobEvent, "level", "INFO")
            eventNodeId = getattr(jobEvent, "node_id", "")
            eventPayload = getattr(jobEvent, "payload", {})
            self.appendLog(str(eventLevel), f"事件 {eventType}：{eventMessage}")
            self.runtimePanelState.applyEvent(
                {
                    "eventType": str(eventType),
                    "message": str(eventMessage),
                    "nodeId": str(eventNodeId),
                }
            )
            self.applyRuntimeEventToNode(
                {
                    "nodeId": str(eventNodeId),
                    "payload": eventPayload if isinstance(eventPayload, dict) else {},
                }
            )
        self.refreshRuntimePanelView()
        if runtimeStatus in ("COMPLETED", "FAILED", "ABORTED"):
            self.setIsJobRunning(False)
        self.updateToolbarState()

    def stopJob(self) -> None:
        currentJobId = self.getCurrentJobId()
        if currentJobId is None:
            self.appendLog("WARN", "当前没有活动作业")
            return
        reply = self.runtimeClient.stopJob(currentJobId)
        stopStatus = str(getattr(reply, "status", "UNKNOWN"))
        stopMessage = str(getattr(reply, "message", ""))
        self.runtimePanelState.updateJob(stopStatus, stopMessage)
        self.appendLog("INFO", f"停止作业：{stopStatus} | {stopMessage}")
        self.refreshRuntimePanelView()
        if stopStatus in ("COMPLETED", "FAILED", "ABORTED"):
            self.setIsJobRunning(False)
        else:
            self.setIsJobRunning(not bool(getattr(reply, "ok", False)))
        self.updateToolbarState()
