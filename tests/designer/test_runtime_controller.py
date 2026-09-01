from emo_master.apps.designer.controllers.runtime_controller import RuntimeController
from emo_master.core.contracts.execution import RuntimeEventDTO
import emo_master.apps.designer.controllers.runtime_controller as runtimeControllerModule
import threading
import time


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


def testRuntimeControllerForwardsCompleteStructuredEventWithoutTextDuplicate() -> None:
    panel = _Panel()
    structured = []
    textLogs = []
    controller = RuntimeController(
        runtimeClient=None,
        runtimePanelState=panel,
        appendLog=lambda level, message: textLogs.append((level, message)),
        appendEvent=structured.append,
        refreshRuntimePanelView=lambda: None,
        updateToolbarState=lambda: None,
        syncRuntimeProjectBeforeRun=lambda: True,
        applyRuntimeEventToNode=lambda event: None,
        setCurrentJobId=lambda jobId: None,
        setIsJobRunning=lambda running: None,
        getLoadedProjectPath=lambda: None,
        getCurrentJobId=lambda: None,
    )

    controller._onRuntimeEvent(
        RuntimeEventDTO(
            jobId="job",
            eventType="node.log",
            message="connected",
            level="INFO",
            nodeId="camera",
            payload={"operatorId": "vision.io.huaray_camera", "phase": "execute"},
            sequence=9,
            timestampMs=456,
            workflowId="main",
            workflowRunId="run",
            nodeRunId="node-run",
            iterationPath=(3,),
        )
    )

    assert textLogs == []
    assert structured == panel.events
    assert structured[0]["eventType"] == "node.log"
    assert structured[0]["payload"]["phase"] == "execute"
    assert structured[0]["sequence"] == 9


def testRuntimeControllerToleratesMalformedEventNumbers() -> None:
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
    malformed = type(
        "Event",
        (),
        {
            "event_type": "node.log",
            "sequence": "not-a-number",
            "timestamp_ms": None,
        },
    )()

    before = int(time.time() * 1000)
    controller._onRuntimeEvent(malformed)
    after = int(time.time() * 1000)

    assert received == panel.events
    assert received[0]["sequence"] == 0
    assert before <= received[0]["timestampMs"] <= after


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
            self.forceCalled = threading.Event()

        def stopJob(self, jobId: str, mode: str):
            assert jobId == "job-active"
            self.modes.append(mode)
            if mode == "force":
                self.forceCalled.set()
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

    from PySide2.QtCore import QCoreApplication

    application = QCoreApplication.instance()
    assert application is not None
    deadline = time.monotonic() + 2.0
    while not client.forceCalled.is_set():
        application.processEvents()
        assert time.monotonic() < deadline
        client.forceCalled.wait(0.01)
    while panel.jobStatus != "ABORTED":
        application.processEvents()
        assert time.monotonic() < deadline + 2.0
        client.forceCalled.wait(0.01)

    assert client.modes == ["graceful", "force"]
    assert running[-1] is False
    assert panel.jobStatus == "ABORTED"


def testRuntimeControllerRunsStopRpcOutsideQtThreadAndEscalates() -> None:
    mainThreadId = threading.get_ident()

    class Client:
        def __init__(self) -> None:
            self.started = threading.Event()
            self.release = threading.Event()
            self.calls = []

        def stopJob(self, jobId: str, mode: str):
            self.calls.append((jobId, mode, threading.get_ident()))
            self.started.set()
            self.release.wait(timeout=2.0)
            status = "ABORTED" if mode == "force" else "STOPPING"
            return type("Reply", (), {"status": status, "message": status.lower()})()

    class Panel(_Panel):
        jobStatus = "RUNNING"

        def updateJob(self, status, message="") -> None:
            self.jobStatus = status
            self.message = message

    client = Client()
    panel = Panel()
    controller = RuntimeController(
        runtimeClient=client,
        runtimePanelState=panel,
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

    controller.stopJob()
    assert panel.jobStatus == "STOPPING"
    assert client.started.wait(timeout=2.0)
    assert client.calls[0][2] != mainThreadId

    controller.stopJob()
    assert len(client.calls) == 1
    client.release.set()

    from PySide2.QtCore import QCoreApplication

    application = QCoreApplication.instance()
    assert application is not None
    deadline = time.monotonic() + 2.0
    while panel.jobStatus != "ABORTED":
        application.processEvents()
        assert time.monotonic() < deadline
        client.release.wait(0.01)

    assert [call[1] for call in client.calls] == ["graceful", "force"]


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
            self.running = True

        def requestStop(self) -> None:
            self.stopRequests += 1

        def isRunning(self) -> bool:
            return self.running

        def wait(self, timeoutMs: int) -> None:
            self.waitTimeouts.append(timeoutMs)
            self.running = False

    class Panel(_Panel):
        jobStatus = "IDLE"

        def updateJob(self, status, message="") -> None:
            self.jobStatus = status
            self.message = message

    client = Client()
    worker = Worker()
    controller = RuntimeController(
        runtimeClient=client,
        runtimePanelState=Panel(),
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


def testRuntimeControllerCloseEscalatesAndReapsStopWorkers() -> None:
    class Client:
        def __init__(self) -> None:
            self.modes = []
            self.closed = 0

        def stopJob(self, jobId: str, mode: str):
            assert jobId == "job-active"
            self.modes.append(mode)
            status = "STOPPING" if mode == "graceful" else "ABORTED"
            return type("Reply", (), {"ok": True, "status": status, "message": status})()

        def close(self) -> None:
            self.closed += 1

    class Panel(_Panel):
        jobStatus = "RUNNING"

        def updateJob(self, status, message="") -> None:
            self.jobStatus = status
            self.message = message

    client = Client()
    controller = RuntimeController(
        runtimeClient=client,
        runtimePanelState=Panel(),
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
    controller._jobActive = True

    controller.close()

    assert client.modes == ["graceful", "force"]
    assert controller._stopWorker is None
    assert controller._closed is True
    assert client.closed == 1


def testRuntimeControllerHandlesRejectedStopReply() -> None:
    class Panel(_Panel):
        jobStatus = "RUNNING"

        def updateJob(self, status, message="") -> None:
            self.jobStatus = status
            self.message = message

    class Worker:
        def __init__(self) -> None:
            self.resultHandled = False

    panel = Panel()
    logs = []
    controller = RuntimeController(
        runtimeClient=None,
        runtimePanelState=panel,
        appendLog=lambda level, message: logs.append((level, message)),
        refreshRuntimePanelView=lambda: None,
        updateToolbarState=lambda: None,
        syncRuntimeProjectBeforeRun=lambda: True,
        applyRuntimeEventToNode=lambda event: None,
        setCurrentJobId=lambda jobId: None,
        setIsJobRunning=lambda running: None,
        getLoadedProjectPath=lambda: "project",
        getCurrentJobId=lambda: "job-active",
    )
    worker = Worker()
    controller._stopWorker = worker

    controller._onStopReply(
        worker,
        type("Reply", (), {"ok": False, "status": "FAILED", "message": "busy"})(),
    )

    assert worker.resultHandled is True
    assert panel.jobStatus == "RUNNING"
    assert logs == [("ERROR", "停止作业失败：busy")]
