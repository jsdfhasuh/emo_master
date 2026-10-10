from __future__ import annotations

import multiprocessing
import queue
from threading import Event, Lock, Thread
import time

import pytest

from emo_master.apps.runtime.jobs.event_bridge import EventBridge
from emo_master.apps.runtime.jobs.heartbeat import HeartbeatCell, monotonicMs
from emo_master.apps.runtime.jobs.models import JobProcessSpec
from emo_master.apps.runtime.jobs.worker_main import heartbeatLoop
import emo_master.apps.runtime.jobs.heartbeat as heartbeatModule
import emo_master.apps.runtime.jobs.supervisor as supervisorModule
from tests.runtime.test_job_supervisor_contracts import (
    _FakeProcessContext,
    _FailingBridge,
    _supervisor,
)


def _withCell(monkeypatch):
    clock = [0]
    monkeypatch.setattr(heartbeatModule, "monotonicMs", lambda: clock[0])
    monkeypatch.setattr(supervisorModule, "monotonicMs", lambda: clock[0])
    # Queue receipt remains an epoch timestamp, never compared to cell time.
    monkeypatch.setattr(supervisorModule, "nowMs", lambda: 1_700_000_000_000 + clock[0])
    supervisor, repository, store, process = _supervisor()
    cell = HeartbeatCell(multiprocessing.get_context("spawn"))
    supervisor._heartbeatCells["job-stop"] = cell
    supervisor._heartbeatMonotonic["job-stop"] = 0
    supervisor._heartbeat["job-stop"] = supervisorModule.nowMs()
    supervisor._heartbeatTimeouts["job-stop"] = 5000
    supervisor._heartbeatIntervals["job-stop"] = 500
    return supervisor, repository, store, process, cell, clock


def testDelayedPersistenceDoesNotKillWorkerWithUnconsumedHeartbeat(monkeypatch) -> None:
    supervisor, repository, store, process, cell, clock = _withCell(monkeypatch)
    events = queue.Queue()
    events.put({"eventType": "job.process.started"})
    events.put({"eventType": "process.heartbeat"})
    cancel = Event()
    supervisor._handles["job-stop"] = (process, cancel, events)
    originalAppend = store.append

    def delayedAppend(*args, **kwargs):
        stored = originalAppend(*args, **kwargs)
        if kwargs.get("eventType") == "job.process.started":
            clock[0] = 5001
            cell.publish()
        return stored

    monkeypatch.setattr(store, "append", delayedAppend)
    bridge = EventBridge(supervisor, "job-stop", process, events, cancel)
    supervisor._bridges["job-stop"] = bridge
    originalCheck = supervisor.checkHeartbeat

    def checkOnce(jobId):
        originalCheck(jobId)
        bridge.requestStop()

    monkeypatch.setattr(supervisor, "checkHeartbeat", checkOnce)
    bridge._run()
    assert repository.get("job-stop").status == "RUNNING"
    assert not process.terminated
    assert supervisor._heartbeatTimeouts["job-stop"] == 5000
    assert "job-stop" not in supervisor._heartbeatSeen
    # The formal heartbeat is still in order in the queue, and still persists.
    supervisor.consumeWorkerEvent("job-stop", events.get_nowait())
    assert [event.eventType for event in store.read("job-stop")] == [
        "job.process.started", "process.heartbeat",
    ]


def testFullDiagnosticQueueDoesNotBlockCellOrTerminalLock(monkeypatch) -> None:
    supervisor, repository, _, process, cell, clock = _withCell(monkeypatch)
    events = multiprocessing.get_context("spawn").Queue(maxsize=64)
    for _ in range(64):
        events.put({"eventType": "backlog"})
    stop, lock = Event(), Lock()
    worker = Thread(target=heartbeatLoop,
                    args=("job-stop", "project", "main", events, stop, 50, cell, Event(), lock))
    worker.start()
    try:
        for sample in (100, 6000, 12000):
            clock[0] = sample
            deadline = time.monotonic() + 1
            while cell.read() != sample and time.monotonic() < deadline:
                stop.wait(.005)
            assert cell.read() == sample
            assert lock.acquire(timeout=.1), "diagnostic enqueue holds the terminal lock"
            lock.release()
            supervisor.checkHeartbeat("job-stop")
            assert not process.terminated and repository.get("job-stop").status == "RUNNING"
        assert events.qsize() == 64
    finally:
        stop.set()
        # Draining also retires the producer when this test runs against the old code.
        while True:
            try:
                events.get(timeout=.1)
            except queue.Empty:
                break
        worker.join(2)
        events.close()
        events.join_thread()
    assert not worker.is_alive()


@pytest.mark.parametrize("published", [False, True])
def testQueuedHeartbeatsCannotMaskSilentWorker(monkeypatch, published) -> None:
    supervisor, repository, store, process, cell, clock = _withCell(monkeypatch)
    if published:
        cell.publish()
    clock[0] = 5000
    supervisor.checkHeartbeat("job-stop")
    assert not process.terminated  # The configured boundary is unchanged.
    clock[0] = 5001
    events = queue.Queue()
    for _ in range(100):
        events.put({"eventType": "process.heartbeat"})
    cancel = Event()
    supervisor._handles["job-stop"] = (process, cancel, events)
    bridge = EventBridge(supervisor, "job-stop", process, events, cancel)
    supervisor._bridges["job-stop"] = bridge
    bridge._run()
    assert process.terminated
    assert repository.get("job-stop").errorCode == "E_HEARTBEAT_TIMEOUT"
    assert events.qsize() == 99  # A nonempty backlog does not postpone checks.
    assert store.read("job-stop")[0].eventType == "process.heartbeat"


@pytest.mark.parametrize("sample", [-1, -100, 1_000_000_000])
def testInvalidCellSampleCannotExtendDeadline(monkeypatch, sample) -> None:
    supervisor, repository, _, process, cell, clock = _withCell(monkeypatch)
    monkeypatch.setattr(cell, "read", lambda: sample)
    clock[0] = 5001
    supervisor.checkHeartbeat("job-stop")
    assert process.terminated
    assert repository.get("job-stop").errorCode == "E_HEARTBEAT_TIMEOUT"


def testUnreadableCellDoesNotFallBackToQueuedHeartbeats(monkeypatch) -> None:
    supervisor, repository, _, process, cell, clock = _withCell(monkeypatch)

    class UnavailableLock:
        def acquire(self, block):
            assert block is False
            raise OSError("shared lock unavailable")

    monkeypatch.setattr(cell._value, "get_lock", lambda: UnavailableLock())
    clock[0] = 5001
    supervisor.consumeWorkerEvent("job-stop", {"eventType": "process.heartbeat"})
    supervisor.checkHeartbeat("job-stop")
    assert process.terminated
    assert repository.get("job-stop").errorCode == "E_HEARTBEAT_TIMEOUT"


def testContendedCellKeepsLastGoodSampleAndFailsClosed(monkeypatch) -> None:
    supervisor, repository, _, process, cell, clock = _withCell(monkeypatch)
    clock[0] = 100
    cell.publish()
    supervisor.checkHeartbeat("job-stop")
    assert supervisor._heartbeatMonotonic["job-stop"] == 100
    acquired, release = Event(), Event()

    def holdLock():
        with cell._value.get_lock():
            acquired.set()
            release.wait(2)

    holder = Thread(target=holdLock)
    holder.start()
    try:
        assert acquired.wait(1)
        assert cell.read() is None
        cell.publish()  # Publishing also returns without waiting for the lock.
        clock[0] = 5100
        supervisor.checkHeartbeat("job-stop")
        assert not process.terminated
        clock[0] = 5101
        supervisor.checkHeartbeat("job-stop")
        assert process.terminated
        assert repository.get("job-stop").errorCode == "E_HEARTBEAT_TIMEOUT"
    finally:
        release.set()
        holder.join(2)
    assert not holder.is_alive()


def testHeartbeatEmissionPublishesCellBeforeUnchangedFormalEvent(monkeypatch) -> None:
    _, _, _, _, cell, clock = _withCell(monkeypatch)
    clock[0] = 42
    stop = Event()
    events = []

    class ObserverQueue:
        def put(self, event):
            assert cell.read() == 42
            events.append(event)
            stop.set()

        put_nowait = put

    heartbeatLoop("job", "project", "main", ObserverQueue(), stop, 500, cell)
    assert len(events) == 1
    assert events[0]["eventType"] == "process.heartbeat"
    assert set(events[0]) == {"eventType", "jobId", "projectId", "workflowId", "pid"}
    # Existing six-argument heartbeat callers and three-argument worker entry
    # keep the same signature; an absent optional cell needs no extra IPC.
    legacy = queue.Queue()
    heartbeatLoop("job", "project", "main", legacy, stop, 500)
    assert legacy.get_nowait()["eventType"] == "process.heartbeat"
    assert JobProcessSpec("job", "project.json", "main").heartbeatCell is None


@pytest.mark.parametrize("owner", ["process", "bridge"])
def testCellRetainedUntilActualOwnerRetirement(monkeypatch, owner) -> None:
    supervisor, repository, _, process, cell, _ = _withCell(monkeypatch)
    repository.update("job-stop", status="FAILED")
    bridge = supervisor._bridges["job-stop"]
    if owner == "process":
        process.terminate = lambda: None
        process.kill = lambda: None
    else:
        process.alive = False
        bridge.is_alive = lambda: True
    supervisor._reap("job-stop")
    assert supervisor.ownsJobResources("job-stop")
    assert supervisor._heartbeatCells["job-stop"] is cell
    assert "job-stop" in supervisor._heartbeatMonotonic
    process.alive = False
    bridge.is_alive = lambda: False
    supervisor.bridgeStopped("job-stop")
    assert not supervisor.ownsJobResources("job-stop")
    assert "job-stop" not in supervisor._heartbeatCells
    assert "job-stop" not in supervisor._heartbeatMonotonic


def testFailedCellAllocationClosesExistingQueue(monkeypatch) -> None:
    supervisor, _, _, process = _supervisor()
    supervisor._handles.clear()
    supervisor._bridges.clear()
    closed = []

    class FailingContext(_FakeProcessContext):
        def Queue(self, maxsize=0):
            assert maxsize == 64
            class ClosableQueue:
                def close(self):
                    closed.append(True)
            return ClosableQueue()

        def Value(self, *args, **kwargs):
            raise OSError("shared allocation failed")

    supervisor._context = FailingContext(process)
    with pytest.raises(OSError, match="shared allocation failed"):
        supervisor.startJob(JobProcessSpec("job-stop", "project.json", "main"))
    assert closed == [True]
    assert not supervisor._heartbeatCells and not supervisor._heartbeatMonotonic
    assert not supervisor.ownsJobResources("job-stop")


def testSlowProcessStartPreservesExistingStartupDeadline(monkeypatch) -> None:
    supervisor, repository, _, process, _, clock = _withCell(monkeypatch)
    bridge = supervisor._bridges["job-stop"]
    bridge.start = lambda: None
    supervisor._handles.clear()
    supervisor._bridges.clear()
    supervisor._context = _FakeProcessContext(process)
    monkeypatch.setattr(supervisorModule, "EventBridge", lambda *args: bridge)

    def slowStart():
        clock[0] = 7000

    monkeypatch.setattr(process, "start", slowStart)
    spec = JobProcessSpec("job-stop", "project.json", "main")
    supervisor.startJob(spec)
    assert supervisor._heartbeatMonotonic["job-stop"] == 7000
    assert supervisor._heartbeat["job-stop"] == 1_700_000_007_000
    assert spec.heartbeatCell is None
    clock[0] = 12000
    supervisor.checkHeartbeat("job-stop")
    assert not process.terminated
    clock[0] = 12001
    supervisor.checkHeartbeat("job-stop")
    assert process.terminated
    assert repository.get("job-stop").errorCode == "E_HEARTBEAT_TIMEOUT"


@pytest.mark.parametrize("unkillable", [False, True])
def testFailedStartRetainsCellOnlyWhileProcessStillAlive(monkeypatch, unkillable) -> None:
    supervisor, _, _, process = _supervisor()
    supervisor._handles.clear()
    supervisor._bridges.clear()
    supervisor._context = _FakeProcessContext(process)
    monkeypatch.setattr(supervisorModule, "EventBridge", _FailingBridge)
    if unkillable:
        process.terminate = lambda: None
        process.kill = lambda: None
    with pytest.raises(RuntimeError, match="bridge startup failed"):
        supervisor.startJob(JobProcessSpec("job-stop", "project.json", "main"))
    assert ("job-stop" in supervisor._heartbeatCells) is unkillable
    assert ("job-stop" in supervisor._heartbeatMonotonic) is unkillable
    if unkillable:
        process.alive = False
        supervisor.bridgeStopped("job-stop")
        assert not supervisor._heartbeatCells and not supervisor._heartbeatMonotonic


def _emitOneHeartbeat(spec, stop, events):
    heartbeatLoop(spec.jobId, "project", "main", events, stop, 500, spec.heartbeatCell)


def _holdSharedLock(cell, acquired):
    cell._value.get_lock().acquire()
    acquired.set()
    Event().wait(60)


def testKilledCellLockOwnerCannotBlockWatchdog(monkeypatch) -> None:
    supervisor, repository, _, process, cell, clock = _withCell(monkeypatch)
    context = multiprocessing.get_context("spawn")
    acquired = context.Event()
    holder = context.Process(target=_holdSharedLock, args=(cell, acquired))
    holder.start()
    try:
        assert acquired.wait(5)
        holder.terminate()
        holder.join(2)
        assert not holder.is_alive()
        clock[0] = 5001
        finished = Event()

        def check():
            supervisor.checkHeartbeat("job-stop")
            finished.set()

        checker = Thread(target=check, daemon=True)
        checker.start()
        assert finished.wait(1), "dead lock holder blocked the watchdog"
        checker.join(1)
        assert process.terminated
        assert repository.get("job-stop").errorCode == "E_HEARTBEAT_TIMEOUT"
        assert not supervisor.ownsJobResources("job-stop")
    finally:
        if holder.is_alive():
            holder.terminate()
            holder.join(2)
        holder.close()


def testSpawnedCellVisibleBeforeFormalQueueConsumption() -> None:
    context = multiprocessing.get_context("spawn")
    cell = HeartbeatCell(context)
    spec = JobProcessSpec("job", "project.json", "main", heartbeatCell=cell)
    stop, events = context.Event(), context.Queue()
    stop.set()
    process = context.Process(target=_emitOneHeartbeat, args=(spec, stop, events))
    before = monotonicMs()
    try:
        process.start()
        process.join(5)
        assert process.exitcode == 0
        sample = cell.read()
        assert sample is not None and before <= sample <= monotonicMs()
        assert events.get(timeout=1)["eventType"] == "process.heartbeat"
    finally:
        if process.is_alive():
            process.terminate()
            process.join(2)
        process.close()
        events.close()
        events.join_thread()
