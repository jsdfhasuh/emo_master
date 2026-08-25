from emo_master.apps.designer.controllers.runtime_controller import RuntimeController
from emo_master.core.contracts.execution import RuntimeEventDTO
import emo_master.apps.designer.controllers.runtime_controller as runtimeControllerModule


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


def testRuntimeControllerKeepsToolbarLockedWhileStopping() -> None:
    class Panel(_Panel):
        def updateJob(self, status, message="") -> None:
            self.status = status
            self.message = message

    panel = Panel()
    running = []
    controller = RuntimeController(
        runtimeClient=None,
        runtimePanelState=panel,
        appendLog=lambda level, message: None,
        refreshRuntimePanelView=lambda: None,
        updateToolbarState=lambda: None,
        syncRuntimeProjectBeforeRun=lambda: True,
        applyRuntimeEventToNode=lambda event: None,
        setCurrentJobId=lambda jobId: None,
        setIsJobRunning=running.append,
        getLoadedProjectPath=lambda: None,
        getCurrentJobId=lambda: None,
    )

    controller._onJobStatus(type("Status", (), {"status": "STOPPING", "message": "stopping"})())
    controller._onJobStatus(type("Status", (), {"status": "COMPLETED", "message": "done"})())

    assert running == [True, False]
    assert panel.status == "COMPLETED"


def testRuntimeControllerClearsPreviousJobBeforeStartingNewWorker(monkeypatch) -> None:
    class Signal:
        def connect(self, callback) -> None:
            _ = callback

    class Worker:
        def __init__(self, **kwargs) -> None:
            _ = kwargs
            self.jobAccepted = Signal()
            self.eventReceived = Signal()
            self.statusChanged = Signal()
            self.failed = Signal()

        def isRunning(self) -> bool:
            return False

        def start(self) -> None:
            return None

    class Panel(_Panel):
        def updateJob(self, status, message="") -> None:
            self.status = status
            self.message = message

    monkeypatch.setattr(runtimeControllerModule, "RuntimeWorker", Worker)
    currentJob = ["job-old"]
    panel = Panel()
    controller = RuntimeController(
        runtimeClient=None,
        runtimePanelState=panel,
        appendLog=lambda level, message: None,
        refreshRuntimePanelView=lambda: None,
        updateToolbarState=lambda: None,
        syncRuntimeProjectBeforeRun=lambda: True,
        applyRuntimeEventToNode=lambda event: None,
        setCurrentJobId=lambda jobId: currentJob.__setitem__(0, jobId),
        setIsJobRunning=lambda running: None,
        getLoadedProjectPath=lambda: "project",
        getCurrentJobId=lambda: currentJob[0],
    )

    controller.startJob()

    assert currentJob == [None]


def testRuntimeControllerDoesNotReplaceStoppingJob() -> None:
    class Worker:
        def isRunning(self) -> bool:
            return False

    class Panel(_Panel):
        def updateJob(self, status, message="") -> None:
            self.status = status
            self.message = message

    currentJob = ["job-stopping"]
    logs = []
    controller = RuntimeController(
        runtimeClient=None,
        runtimePanelState=Panel(),
        appendLog=lambda level, message: logs.append((level, message)),
        refreshRuntimePanelView=lambda: None,
        updateToolbarState=lambda: None,
        syncRuntimeProjectBeforeRun=lambda: True,
        applyRuntimeEventToNode=lambda event: None,
        setCurrentJobId=lambda jobId: currentJob.__setitem__(0, jobId),
        setIsJobRunning=lambda running: None,
        getLoadedProjectPath=lambda: "project",
        getCurrentJobId=lambda: currentJob[0],
    )
    controller._worker = Worker()
    controller._onJobStatus(
        type("Status", (), {"status": "STOPPING", "message": "stopping"})()
    )

    controller.startJob()

    assert currentJob == ["job-stopping"]
    assert logs[-1] == ("WARN", "已有作业正在运行")


def testRuntimeControllerCanForceStopAfterGracefulStop() -> None:
    class Client:
        def __init__(self) -> None:
            self.modes = []

        def stopJob(self, jobId: str, mode: str):
            assert jobId == "job-active"
            self.modes.append(mode)
            status = "STOPPING" if mode == "graceful" else "ABORTED"
            return type("Reply", (), {"status": status, "message": status.lower()})()

    class Panel(_Panel):
        jobStatus = "RUNNING"

        def updateJob(self, status, message="") -> None:
            self.jobStatus = status
            self.message = message

    client = Client()
    panel = Panel()
    running = []
    controller = RuntimeController(
        runtimeClient=client,
        runtimePanelState=panel,
        appendLog=lambda level, message: None,
        refreshRuntimePanelView=lambda: None,
        updateToolbarState=lambda: None,
        syncRuntimeProjectBeforeRun=lambda: True,
        applyRuntimeEventToNode=lambda event: None,
        setCurrentJobId=lambda jobId: None,
        setIsJobRunning=running.append,
        getLoadedProjectPath=lambda: "project",
        getCurrentJobId=lambda: "job-active",
    )

    controller.stopJob()
    controller.stopJob()

    assert client.modes == ["graceful", "force"]
    assert running == [True, False]
    assert panel.jobStatus == "ABORTED"


def testRuntimeControllerCloseStopsWorkerSubscriptionAndClient() -> None:
    class Client:
        def __init__(self) -> None:
            self.closed = 0

        def close(self) -> None:
            self.closed += 1

    class Worker:
        def __init__(self) -> None:
            self.stopRequests = 0
            self.waitTimeouts = []

        def requestStop(self) -> None:
            self.stopRequests += 1

        def isRunning(self) -> bool:
            return True

        def wait(self, timeoutMs: int) -> None:
            self.waitTimeouts.append(timeoutMs)

    client = Client()
    worker = Worker()
    controller = RuntimeController(
        runtimeClient=client,
        runtimePanelState=_Panel(),
        appendLog=lambda level, message: None,
        refreshRuntimePanelView=lambda: None,
        updateToolbarState=lambda: None,
        syncRuntimeProjectBeforeRun=lambda: True,
        applyRuntimeEventToNode=lambda event: None,
        setCurrentJobId=lambda jobId: None,
        setIsJobRunning=lambda running: None,
        getLoadedProjectPath=lambda: "project",
        getCurrentJobId=lambda: "job-active",
    )
    controller._worker = worker

    controller.close()

    assert worker.stopRequests == 1
    assert worker.waitTimeouts == [2000]
    assert controller._worker is None
    assert controller._jobActive is False
    assert client.closed == 1
