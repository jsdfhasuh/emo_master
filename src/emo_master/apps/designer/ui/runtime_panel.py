from dataclasses import dataclass, field


@dataclass
class RuntimePanelState:
    nodeStatus: dict[str, str] = field(default_factory=dict)
    latestImagePath: str | None = None
    jobStatus: str = "IDLE"
    lastMessage: str = ""

    def updateJob(self, status: str, message: str = "") -> None:
        self.jobStatus = status
        self.lastMessage = message

    def applyEvent(self, event: dict[str, object]) -> None:
        eventType = event.get("eventType")
        nodeId = event.get("nodeId")
        message = event.get("message")

        if eventType == "node.completed" and isinstance(nodeId, str):
            self.nodeStatus[nodeId] = "COMPLETED"
        if eventType == "node.failed" and isinstance(nodeId, str):
            self.nodeStatus[nodeId] = "FAILED"
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
