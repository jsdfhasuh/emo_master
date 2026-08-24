from __future__ import annotations

import queue
from threading import Event

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
        _ = jobId
        _ = exitCode


def testEventBridgeChecksHeartbeatWhenQueueIsSilent() -> None:
    observer = _BridgeObserver()
    process = _FakeProcess()
    bridge = EventBridge(observer, "job-silent", process, _SilentQueue(), Event())
    observer.bridge = bridge

    bridge.run()

    assert observer.heartbeatChecks == 1


class _FakeProcessContext:
    def __init__(self, process: _FakeProcess) -> None:
        self.process = process

    def Event(self):
        return Event()

    def Queue(self):
        return queue.Queue()

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
