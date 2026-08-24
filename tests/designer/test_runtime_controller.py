from emo_master.apps.designer.controllers.runtime_controller import RuntimeController
from emo_master.core.contracts.execution import RuntimeEventDTO


class _Panel:
    def __init__(self) -> None:
        self.events = []

    def applyEvent(self, event) -> None:
        self.events.append(event)


def testRuntimeControllerPreservesEventCorrelationMetadata() -> None:
    panel = _Panel()
    received = []
    controller = RuntimeController(
        runtimeClient=None,
        runtimePanelState=panel,
        appendLog=lambda level, message: None,
        refreshRuntimePanelView=lambda: None,
        updateToolbarState=lambda: None,
        syncRuntimeProjectBeforeRun=lambda: True,
        applyRuntimeEventToNode=received.append,
        setCurrentJobId=lambda jobId: None,
        setIsJobRunning=lambda running: None,
        getLoadedProjectPath=lambda: None,
        getCurrentJobId=lambda: None,
    )

    controller._onRuntimeEvent(
        RuntimeEventDTO(
            jobId="job",
            eventType="node.failed",
            message="failed",
            level="ERROR",
            nodeId="node",
            code="E_NODE_FAILED",
            sequence=7,
            timestampMs=123,
            workflowId="body",
            workflowRunId="run-body",
            parentWorkflowRunId="run-main",
            nodeRunId="node-run",
            iterationPath=(2, 4),
        )
    )

    assert received == panel.events
    assert received[0]["code"] == "E_NODE_FAILED"
    assert received[0]["sequence"] == 7
    assert received[0]["timestampMs"] == 123
    assert received[0]["parentWorkflowRunId"] == "run-main"
    assert received[0]["iterationPath"] == (2, 4)
