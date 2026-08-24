from dataclasses import dataclass, field


@dataclass
class RuntimePanelState:
    nodeStatus: dict[str, str] = field(default_factory=dict)
    nodeStatusByRun: dict[tuple[str, str], str] = field(default_factory=dict)
    iterationPathByNode: dict[tuple[str, str], tuple[int, ...]] = field(default_factory=dict)
    latestImagePath: str | None = None
    latestArtifact: dict[str, object] = field(default_factory=dict)
    jobStatus: str = "IDLE"
    lastMessage: str = ""

    def updateJob(self, status: str, message: str = "") -> None:
        self.jobStatus = status
        self.lastMessage = message

    def applyEvent(self, event: dict[str, object]) -> None:
        eventType = event.get("eventType")
        nodeId = event.get("nodeId")
        message = event.get("message")
        workflowRunId = event.get("workflowRunId")
        runId = workflowRunId if isinstance(workflowRunId, str) else ""
        iterationRaw = event.get("iterationPath", ())
        iterationPath = (
            tuple(item for item in iterationRaw if isinstance(item, int) and not isinstance(item, bool))
            if isinstance(iterationRaw, (list, tuple))
            else ()
        )

        if eventType in {"node.started", "node.completed", "node.failed", "node.skipped"} and isinstance(nodeId, str):
            status = {
                "node.started": "RUNNING",
                "node.completed": "COMPLETED",
                "node.failed": "FAILED",
                "node.skipped": "SKIPPED",
            }[str(eventType)]
            self.nodeStatus[nodeId] = status
            if runId:
                self.nodeStatus[f"{runId}:{nodeId}"] = status
                self.nodeStatusByRun[(runId, nodeId)] = status
                self.iterationPathByNode[(runId, nodeId)] = iterationPath
        if eventType == "artifact.created":
            payload = event.get("payload")
            artifact = payload.get("artifact") if isinstance(payload, dict) else None
            if isinstance(artifact, dict):
                self.latestArtifact = dict(artifact)
                path = artifact.get("path") or artifact.get("uri") or artifact.get("localPath")
                if isinstance(path, str) and path != "":
                    self.latestImagePath = path
        if eventType == "job.completed" and isinstance(message, str):
            if "edge image saved:" in message:
                self.latestImagePath = message.split("edge image saved:", 1)[1].strip()
            if "output image saved:" in message:
                self.latestImagePath = message.split("output image saved:", 1)[
                    1
                ].strip()
            self.jobStatus = "COMPLETED"
            self.lastMessage = message
        if eventType == "job.failed" and isinstance(message, str):
            self.jobStatus = "FAILED"
            self.lastMessage = message
        if eventType == "job.aborted" and isinstance(message, str):
            self.jobStatus = "ABORTED"
            self.lastMessage = message
