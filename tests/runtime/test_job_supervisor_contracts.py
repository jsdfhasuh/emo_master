from __future__ import annotations

import queue
import multiprocessing
from threading import Event, Thread

import pytest

from emo_master.apps.runtime.events.event_store import EventStore
from emo_master.apps.runtime.jobs.event_bridge import EventBridge
from emo_master.apps.runtime.jobs.models import JobProcessSpec, JobRecord, JobStatus
from emo_master.apps.runtime.jobs.repository import JobRepository
from emo_master.apps.runtime.jobs.supervisor import JobSupervisor
import emo_master.apps.runtime.jobs.supervisor as supervisorModule


class _FakeProcess:
    def __init__(self) -> None:
        self.alive = True
        self.pid = 1234
        self.exitcode = None
        self.terminated = False
        self.joinTimeouts: list[float | None] = []
        self.closed = False

    def is_alive(self) -> bool:
        return self.alive

    def terminate(self) -> None:
        self.terminated = True
        self.alive = False

    def kill(self) -> None:
        self.alive = False

    def join(self, timeout: float | None = None) -> None:
        self.joinTimeouts.append(timeout)

    def close(self) -> None:
        self.closed = True

    def start(self) -> None:
        self.alive = True


class _FakeBridge:
    def __init__(self) -> None:
        self.stopped = False

    def requestStop(self) -> None:
        self.stopped = True

    def join(self, timeout: float | None = None) -> None:
        _ = timeout

    def is_alive(self) -> bool:
        return False


class _FailingEventStore(EventStore):
    def append(self, *args, **kwargs):
        _ = args
        _ = kwargs
        raise RuntimeError("sqlite is unavailable")


def _supervisor() -> tuple[JobSupervisor, JobRepository, EventStore, _FakeProcess]:
    repository = JobRepository()
    eventStore = EventStore()
    record = JobRecord(
        jobId="job-stop",
        projectId="project",
        projectRevision=1,
        workflowId="main",
        status=JobStatus.RUNNING.value,
    )
    repository.create(record)
    supervisor = JobSupervisor(
        repository,
        eventStore,
        gracefulStopTimeoutMs=10000,
    )
    process = _FakeProcess()
    supervisor._handles[record.jobId] = (process, Event(), object())
    supervisor._bridges[record.jobId] = _FakeBridge()
    return supervisor, repository, eventStore, process


def testTerminalStatusWaitsForAssetPromotion() -> None:
    supervisor, repository, _, _ = _supervisor()
    entered, release = Event(), Event()
    def promote(jobId, status):
        entered.set()
        assert release.wait(2)
    supervisor.terminalCallback = promote
    thread = Thread(target=supervisor._markTerminal, args=("job-stop", "COMPLETED", 123))
    thread.start()
    try:
        assert entered.wait(1)
        assert repository.get("job-stop").status == "RUNNING"
    finally:
        release.set()
        thread.join(2)
    assert not thread.is_alive()
    assert repository.get("job-stop").status == "COMPLETED"


def testTerminalCallbackFailureStillPublishesTerminalStatus() -> None:
    supervisor, repository, _, _ = _supervisor()
    def fail(jobId, status):
        raise RuntimeError("promotion failure")
    supervisor.terminalCallback = fail
    with pytest.raises(RuntimeError, match="promotion failure"):
        supervisor._markTerminal("job-stop", "COMPLETED", 123)
    assert repository.get("job-stop").status == "COMPLETED"


def testForceStopTerminatesImmediatelyAndIgnoresLateWorkerTerminal() -> None:
    supervisor, repository, eventStore, process = _supervisor()

    assert supervisor.stopJob("job-stop", mode="force") == JobStatus.ABORTED.value
    record = repository.get("job-stop")
    assert record is not None
    assert record.status == JobStatus.ABORTED.value
    assert process.terminated is True
    assert 10.0 not in process.joinTimeouts

    sequence = len(eventStore.read("job-stop"))
    supervisor.consumeWorkerEvent(
        "job-stop",
        {"eventType": "job.completed", "message": "late completion"},
    )
    assert len(eventStore.read("job-stop")) == sequence
    assert repository.get("job-stop").status == JobStatus.ABORTED.value


def testGracefulStopAcceptsWorkerAbortAndEmitsOneTerminalEvent() -> None:
    supervisor, repository, eventStore, process = _supervisor()
    process.alive = False

    assert supervisor.stopJob("job-stop", mode="graceful") == JobStatus.STOPPING.value
    supervisor.consumeWorkerEvent(
        "job-stop",
        {
            "eventType": "job.aborted",
            "message": "worker cancelled",
            "code": "E_CANCELLED",
        },
    )
    supervisor.consumeWorkerEvent(
        "job-stop",
        {"eventType": "job.completed", "message": "late completion"},
    )

    record = repository.get("job-stop")
    assert record is not None
    assert record.status == JobStatus.ABORTED.value
    assert record.errorCode == "E_CANCELLED"
    assert [event.eventType for event in eventStore.read("job-stop")].count("job.aborted") == 1


def testGracefulStopTimeoutForceTerminatesAndCleansHandle() -> None:
    repository = JobRepository()
    eventStore = EventStore()
    record = JobRecord(
        jobId="job-timeout",
        projectId="project",
        projectRevision=1,
        workflowId="main",
        status=JobStatus.RUNNING.value,
    )
    repository.create(record)
    supervisor = JobSupervisor(repository, eventStore, gracefulStopTimeoutMs=0)
    process = _FakeProcess()
    supervisor._handles[record.jobId] = (process, Event(), _SilentQueue())
    supervisor._bridges[record.jobId] = _FakeBridge()

    supervisor.stopJob(record.jobId, mode="graceful")
    supervisor._enforceGracefulStop(record.jobId, process)

    updated = repository.get(record.jobId)
    assert updated is not None
    assert updated.status == JobStatus.ABORTED.value
    assert updated.errorCode == "E_STOP_TIMEOUT"
    assert process.terminated is True
    assert supervisor.getProcess(record.jobId) is None


def testShutdownEmitsStoppingTerminationAndAbortedEvents() -> None:
    supervisor, repository, eventStore, _process = _supervisor()

    supervisor.shutdown()

    eventTypes = [event.eventType for event in eventStore.read("job-stop")]
    assert eventTypes == [
        "job.stopping",
        "process.terminated",
        "job.aborted",
    ]
    record = repository.get("job-stop")
    assert record is not None
    assert record.status == JobStatus.ABORTED.value
    assert record.errorCode == "E_RUNTIME_SHUTDOWN"


class _SilentQueue:
    def get(self, timeout: float):
        _ = timeout
        raise queue.Empty


class _BridgeObserver:
    def __init__(self) -> None:
        self.bridge: EventBridge | None = None
        self.heartbeatChecks = 0
        self.exits: list[tuple[str, int | None]] = []

    def checkHeartbeat(self, jobId: str) -> None:
        _ = jobId
        self.heartbeatChecks += 1
        assert self.bridge is not None
        self.bridge.requestStop()

    def consumeWorkerEvent(self, jobId: str, event: dict[str, object]) -> None:
        _ = jobId
        _ = event

    def bridgeError(self, jobId: str, error: BaseException) -> None:
        raise error

    def bridgeStopped(self, jobId: str) -> None:
        _ = jobId

    def processExited(self, jobId: str, exitCode: int | None) -> None:
        self.exits.append((jobId, exitCode))


def testEventBridgeChecksHeartbeatWhenQueueIsSilent() -> None:
    observer = _BridgeObserver()
    process = _FakeProcess()
    bridge = EventBridge(observer, "job-silent", process, _SilentQueue(), Event())
    observer.bridge = bridge

    bridge.run()

    assert observer.heartbeatChecks == 1


def testEventBridgeTreatsClosedProcessAsExited() -> None:
    class ClosedProcess:
        @property
        def exitcode(self):
            raise ValueError("process object is closed")

        def is_alive(self) -> bool:
            raise ValueError("process object is closed")

    observer = _BridgeObserver()
    bridge = EventBridge(
        observer,
        "job-closed",
        ClosedProcess(),
        queue.Queue(),
        Event(),
    )
    observer.bridge = bridge

    bridge.run()

    assert observer.exits == [("job-closed", None)]


class _FakeProcessContext:
    def __init__(self, process: _FakeProcess) -> None:
        self.process = process

    def Event(self):
        return Event()

    def Queue(self, maxsize=0):
        assert maxsize == 64
        return queue.Queue(maxsize=maxsize)

    def Value(self, *args, **kwargs):
        return multiprocessing.get_context("spawn").Value(*args, **kwargs)

    def Process(self, **kwargs):
        _ = kwargs
        return self.process


class _FailingBridge:
    def __init__(self, *args) -> None:
        _ = args

    def start(self) -> None:
        raise RuntimeError("bridge startup failed")

    def requestStop(self) -> None:
        return None


def testSupervisorCleansUpProcessWhenBridgeStartupFails(monkeypatch) -> None:
    repository = JobRepository()
    eventStore = EventStore()
    record = JobRecord(
        jobId="job-start-failure",
        projectId="project",
        projectRevision=1,
        workflowId="main",
    )
    repository.create(record)
    process = _FakeProcess()
    supervisor = JobSupervisor(repository, eventStore)
    supervisor._context = _FakeProcessContext(process)
    monkeypatch.setattr(supervisorModule, "EventBridge", _FailingBridge)

    with pytest.raises(RuntimeError, match="bridge startup failed"):
        supervisor.startJob(
            JobProcessSpec(
                jobId=record.jobId,
                projectSnapshotPath="snapshot.json",
                workflowId="main",
            )
        )

    assert process.terminated is True
    assert process.closed is True
    assert supervisor.getProcess(record.jobId) is None


def testEventBridgePersistenceFailureStillMarksJobTerminal() -> None:
    repository = JobRepository()
    eventStore = _FailingEventStore()
    record = JobRecord(
        jobId="job-persistence-failure",
        projectId="project",
        projectRevision=1,
        workflowId="main",
        status=JobStatus.RUNNING.value,
    )
    repository.create(record)
    supervisor = JobSupervisor(repository, eventStore)
    process = _FakeProcess()
    supervisor._handles[record.jobId] = (process, Event(), _SilentQueue())

    supervisor.bridgeError(record.jobId, RuntimeError("event conversion failed"))

    updated = repository.get(record.jobId)
    assert updated is not None
    assert updated.status == JobStatus.FAILED.value
    assert updated.errorCode == "E_EVENT_PERSISTENCE"
    assert updated.endedAtMs > 0


def testUnkillableTerminalWorkerRetainsOwnershipAndAdmission() -> None:
    supervisor, repository, _, process = _supervisor()
    process.terminate = lambda: None
    process.kill = lambda: None
    supervisor.maxConcurrentJobs = 1
    repository.update("job-stop", status=JobStatus.FAILED.value)
    supervisor._reap("job-stop")
    assert supervisor.getProcess("job-stop") is process
    assert not process.closed
    with pytest.raises(RuntimeError, match="MAX_CONCURRENT"):
        supervisor.startJob(JobProcessSpec("another-job", "project.json", "main"))
    process.alive = False
    supervisor._reap("job-stop")
    assert supervisor.getProcess("job-stop") is None


def testAliveBridgeRetainsTerminalWorkerOwnershipUntilFinalCallback() -> None:
    supervisor, repository, _, process = _supervisor()
    process.alive = False
    repository.update("job-stop", status=JobStatus.ABORTED.value)
    bridge = supervisor._bridges["job-stop"]
    bridge.is_alive = lambda: True
    retired = []
    supervisor.retiredCallback = retired.append
    supervisor.maxConcurrentJobs = 1
    supervisor._reap("job-stop")
    assert supervisor.ownsJobResources("job-stop")
    assert supervisor.getProcess("job-stop") is process
    assert not process.closed and not retired
    with pytest.raises(RuntimeError, match="MAX_CONCURRENT"):
        supervisor.startJob(JobProcessSpec("another-job", "project.json", "main"))
    bridge.is_alive = lambda: False
    supervisor.bridgeStopped("job-stop")
    assert not supervisor.ownsJobResources("job-stop")
    assert process.closed and retired == ["job-stop"]


class _ClosableQueue(queue.Queue):
    def __init__(self) -> None:
        super().__init__()
        self.closeCount = 0

    def close(self) -> None:
        self.closeCount += 1


@pytest.mark.parametrize("action", ["force-stop", "shutdown", "process-exited"])
def testExternalReapDoesNotJoinBridgeWaitingForSupervisorLock(monkeypatch, action) -> None:
    supervisor, repository, _, process = _supervisor()
    eventQueue = _ClosableQueue()
    supervisor._handles["job-stop"] = (process, Event(), eventQueue)
    bridge = EventBridge(supervisor, "job-stop", process, eventQueue, Event())
    supervisor._bridges["job-stop"] = bridge
    supervisor.maxConcurrentJobs = 1
    retired = []
    supervisor.retiredCallback = retired.append
    finalizerEntered = Event()
    bridgeStopped = supervisor.bridgeStopped

    def observeFinalizer(jobId):
        finalizerEntered.set()
        bridgeStopped(jobId)

    monkeypatch.setattr(supervisor, "bridgeStopped", observeFinalizer)
    join = bridge.join
    joins = []

    def observeJoin(timeout=None):
        joins.append(timeout)
        join(timeout)

    monkeypatch.setattr(bridge, "join", observeJoin)
    # The real bridge reaches its final hook while the caller owns the lock.
    # Join observations, rather than elapsed-time limits, detect the old wait.
    bridge.requestStop()
    try:
        with supervisor._lock:
            bridge.start()
            assert finalizerEntered.wait(2)
            if action == "force-stop":
                assert supervisor.stopJob("job-stop", "force") == "ABORTED"
            elif action == "shutdown":
                supervisor.shutdown()
            else:
                process.alive = False
                repository.update("job-stop", status="COMPLETED")
                supervisor.processExited("job-stop", 0)
            assert repository.get("job-stop").isTerminal
            assert bridge.is_alive()
            assert supervisor.ownsJobResources("job-stop")
            assert supervisor.getProcess("job-stop") is process
            assert not process.closed and eventQueue.closeCount == 0 and retired == []
            with pytest.raises(RuntimeError, match="MAX_CONCURRENT"):
                supervisor.startJob(JobProcessSpec("another-job", "project.json", "main"))
            assert joins == [], "external reap joined a finalizer blocked on its own lock"
    finally:
        join(2)
    assert not bridge.is_alive()
    assert not supervisor.ownsJobResources("job-stop")
    assert process.closed and eventQueue.closeCount == 1 and retired == ["job-stop"]
    supervisor.bridgeStopped("job-stop")
    assert eventQueue.closeCount == 1 and retired == ["job-stop"]


def testExternalReapRetainsOwnersWhileRealBridgeStillReadsQueue(monkeypatch) -> None:
    supervisor, repository, _, process = _supervisor()
    reading, release = Event(), Event()

    class GatedQueue(_ClosableQueue):
        def get(self, timeout=None):
            reading.set()
            assert release.wait(5)
            assert self.closeCount == 0 and not process.closed
            raise queue.Empty

    eventQueue = GatedQueue()
    supervisor._handles["job-stop"] = (process, Event(), eventQueue)
    bridge = EventBridge(supervisor, "job-stop", process, eventQueue, Event())
    supervisor._bridges["job-stop"] = bridge
    retired = []
    supervisor.retiredCallback = retired.append
    join = bridge.join
    joins = []

    def observeJoin(timeout=None):
        joins.append(timeout)
        join(timeout)

    monkeypatch.setattr(bridge, "join", observeJoin)
    try:
        bridge.start()
        assert reading.wait(2)
        assert supervisor.stopJob("job-stop", "force") == "ABORTED"
        assert repository.get("job-stop").isTerminal
        assert bridge.stopEvent.is_set() and bridge.is_alive()
        assert supervisor.ownsJobResources("job-stop")
        assert not process.closed and eventQueue.closeCount == 0 and retired == []
        assert joins == [], "external reap joined a bridge that still owns queue I/O"
        # The explicit outside-lock wait remains bounded without retiring live I/O.
        supervisor.waitForRetirement(timeoutSeconds=0)
        assert joins == [0]
        assert supervisor.ownsJobResources("job-stop")
        assert not process.closed and eventQueue.closeCount == 0 and retired == []
    finally:
        release.set()
        supervisor.waitForRetirement()
        join(2)
    assert not bridge.is_alive()
    assert not supervisor.ownsJobResources("job-stop")
    assert process.closed and eventQueue.closeCount == 1 and retired == ["job-stop"]


def testFailedStartDoesNotJoinLiveBridgeOrReleaseItsOwners(monkeypatch) -> None:
    supervisor, _, _, process = _supervisor()
    supervisor._handles.clear()
    supervisor._bridges.clear()
    supervisor.maxConcurrentJobs = 1
    eventQueue = _ClosableQueue()
    context = _FakeProcessContext(process)
    monkeypatch.setattr(context, "Queue", lambda maxsize=0: eventQueue)
    supervisor._context = context
    finalizerEntered = Event()
    bridgeStopped = supervisor.bridgeStopped
    retired = []
    supervisor.retiredCallback = retired.append

    def observeFinalizer(jobId):
        finalizerEntered.set()
        bridgeStopped(jobId)

    monkeypatch.setattr(supervisor, "bridgeStopped", observeFinalizer)
    bridges = []
    joins = []

    class StartedFailingBridge(EventBridge):
        def start(self):
            bridges.append(self)
            self.requestStop()
            super().start()
            assert finalizerEntered.wait(2)
            raise RuntimeError("bridge failed after thread started")

        def join(self, timeout=None):
            joins.append(timeout)
            super().join(timeout)

    monkeypatch.setattr(supervisorModule, "EventBridge", StartedFailingBridge)
    try:
        with supervisor._lock:
            with pytest.raises(RuntimeError, match="failed after thread started"):
                supervisor.startJob(JobProcessSpec("job-stop", "project.json", "main"))
            assert bridges[0].is_alive()
            assert supervisor._bridges["job-stop"] is bridges[0]
            assert supervisor.getProcess("job-stop") is process
            assert process.terminated and not process.closed
            assert eventQueue.closeCount == 0 and retired == []
            assert "job-stop" in supervisor._heartbeatCells
            assert "job-stop" in supervisor._heartbeatMonotonic
            with pytest.raises(RuntimeError, match="MAX_CONCURRENT"):
                supervisor.startJob(JobProcessSpec("another-job", "project.json", "main"))
            assert joins == [], "failed-start cleanup joined a finalizer blocked on its own lock"
    finally:
        for bridge in bridges:
            Thread.join(bridge, 2)
    assert not bridges[0].is_alive()
    assert not supervisor.ownsJobResources("job-stop")
    assert not supervisor._heartbeatCells and not supervisor._heartbeatMonotonic
    assert process.closed and eventQueue.closeCount == 1 and retired == ["job-stop"]
    supervisor.bridgeStopped("job-stop")
    assert eventQueue.closeCount == 1 and retired == ["job-stop"]
