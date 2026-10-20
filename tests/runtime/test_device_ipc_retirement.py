"""Native IPC shutdown must not wait on a worker-owned cancellation lock."""
import multiprocessing
import sys
from threading import Event
import traceback

import pytest

from emo_master.apps.runtime.devices.manager import DeviceManager
from emo_master.apps.runtime.jobs.models import JobProcessSpec
from tests.runtime.test_job_supervisor_contracts import _FakeBridge, _FakeProcessContext, _supervisor


def _holdCancellationRead(cancellation, ready, release):
    # Force the read boundary that a shutdown can meet. The old Event keeps
    # its shared lock here; a lock-free signal has no worker-owned lock.
    condition = getattr(cancellation, "_cond", None)
    if condition is not None:
        condition.acquire()
    try:
        if condition is None:
            assert cancellation.is_set()
        ready.send(True)
        release.recv()
    finally:
        if condition is not None:
            condition.release()


def _jobCancellation(monkeypatch):
    # Keep the production signal construction while avoiding unrelated workers.
    # The cancellation-lock holder and IPC bridge below are real native owners.
    import emo_master.apps.runtime.jobs.supervisor as module
    supervisor, _, _, process = _supervisor()
    supervisor._handles.clear()
    supervisor._bridges.clear()
    supervisor._context = _FakeProcessContext(process)

    class PassiveEventBridge(_FakeBridge):
        def __init__(self, *_args):
            super().__init__()

        def start(self):
            pass

    monkeypatch.setattr(module, "EventBridge", PassiveEventBridge)
    # Use native Event/Value even though this bootstrap's process is a test peer.
    supervisor._context.Event = multiprocessing.get_context("spawn").Event
    supervisor.startJob(JobProcessSpec("job-stop", "project.json", "main"))
    cancellation = supervisor._handles["job-stop"][1]
    supervisor.shutdown()
    assert cancellation.is_set()
    return cancellation


def testDeviceIpcRetiresWhileWorkerOwnsCancellationReadLock(monkeypatch):
    context = multiprocessing.get_context("spawn")
    cancellation = _jobCancellation(monkeypatch)
    ready, childReady = context.Pipe(duplex=False)
    childRelease, release = context.Pipe(duplex=False)
    holder = context.Process(target=_holdCancellationRead, args=(cancellation, childReady, childRelease))
    manager = DeviceManager({})
    readEntered = Event()
    originalIsSet = cancellation.is_set

    def readCancellation():
        readEntered.set()
        return originalIsSet()

    holder.start()
    # Observe only in the parent, after the native signal is pickled for spawn.
    cancellation.is_set = readCancellation
    childReady.close()
    childRelease.close()
    workerPipe = None
    try:
        assert ready.poll(5), "spawned worker did not reach cancellation read boundary"
        assert ready.recv()
        workerPipe = manager.openWorker("job", [], cancellation, context)
        assert readEntered.wait(2), "native IPC bridge did not reach cancellation check"
        bridge = manager.workers["job"]
        try:
            manager.retire("job")
        except BaseException:
            frame = sys._current_frames().get(bridge.thread.ident)
            if frame is not None:
                traceback.print_stack(frame)
            raise
        assert not bridge.thread.is_alive()
        assert not manager.workers
    finally:
        release.send(True)
        holder.join(5)
        assert not holder.is_alive(), "fixture worker did not exit after release"
        holder.close()
        ready.close()
        release.close()
        if workerPipe is not None:
            workerPipe.close()
        manager.retire("job")
        manager.close()


def _observeCancellation(cancellation, ready, result, timeout):
    ready.send(cancellation.is_set())
    result.send(cancellation.wait(timeout))


def testJobCancellationWaitHasFiniteTimeout():
    from emo_master.apps.runtime.jobs.cancellation import JobCancellation
    cancellation = JobCancellation(multiprocessing.get_context("spawn"))
    assert not cancellation.is_set()
    assert not cancellation.wait(0)
    assert not cancellation.wait(-1)
    assert not cancellation.wait(.02)
    cancellation.set()
    cancellation.set()
    assert cancellation.is_set()
    assert cancellation.wait(0)


@pytest.mark.parametrize("timeout", [None, 5.0])
def testSpawnedWorkerObservesCancellationWhileWaiting(timeout):
    from emo_master.apps.runtime.jobs.cancellation import JobCancellation
    context = multiprocessing.get_context("spawn")
    cancellation = JobCancellation(context)
    ready, childReady = context.Pipe(duplex=False)
    result, childResult = context.Pipe(duplex=False)
    worker = context.Process(target=_observeCancellation, args=(cancellation, childReady, childResult, timeout))
    worker.start()
    childReady.close()
    childResult.close()
    try:
        assert ready.poll(5)
        assert ready.recv() is False
        cancellation.set()
        assert result.poll(2), "spawned cancellation wait did not wake"
        assert result.recv() is True
        worker.join(2)
        assert worker.exitcode == 0
        # A retired reader cannot retain or reset the parent's cancellation.
        cancellation.set()
        assert cancellation.wait(0)
    finally:
        cancellation.set()
        worker.join(5)
        assert not worker.is_alive()
        worker.close()
        ready.close()
        result.close()


def testCancelledDeviceProxyDoesNotSubmitOrReplay():
    from emo_master.apps.runtime.devices.executor import DeviceError
    from emo_master.apps.runtime.devices.ipc import DeviceProxy
    from emo_master.apps.runtime.jobs.cancellation import JobCancellation
    context = multiprocessing.get_context("spawn")
    cancellation = JobCancellation(context)
    parent, child = context.Pipe()
    proxy = DeviceProxy(child, cancellation)
    try:
        cancellation.set()
        for _ in range(2):
            with pytest.raises(DeviceError) as error:
                proxy.call("capture", deviceId="camera", cycleId="cycle", params={})
            assert error.value.code == "E_DEVICE_CANCELLED"
        assert not parent.poll(0)
        assert proxy.sequence == 0
    finally:
        proxy.close()
        parent.close()


def _cancelFromWorker(cancellation, result):
    from emo_master.apps.runtime.workflow.cancellation import CancellationToken
    token = CancellationToken(cancellation)
    token.cancel()
    result.send(token.is_cancelled())


def testWorkerCancellationIsVisibleToRuntime():
    from emo_master.apps.runtime.jobs.cancellation import JobCancellation
    context = multiprocessing.get_context("spawn")
    cancellation = JobCancellation(context)
    result, childResult = context.Pipe(duplex=False)
    worker = context.Process(target=_cancelFromWorker, args=(cancellation, childResult))
    worker.start()
    childResult.close()
    try:
        assert result.poll(5)
        assert result.recv() is True
        assert cancellation.is_set()
        worker.join(2)
        assert worker.exitcode == 0
    finally:
        worker.join(5)
        assert not worker.is_alive()
        worker.close()
        result.close()


def _holdShutdownCancellationRead(spec, cancellation, _events, _communication, _devices):
    ready, control = spec.presentation["ready"], spec.presentation["control"]
    ready.send("started")
    assert control.recv() == "read"
    condition = getattr(cancellation, "_cond", None)
    if condition is not None:
        condition.acquire()
    try:
        if condition is None:
            assert cancellation.is_set()
        ready.send("reading")
        # Production shutdown, not the fixture, terminates this exact worker.
        control.recv()
    finally:
        if condition is not None:
            condition.release()


def testRuntimeShutdownRetiresIpcAfterTerminatingCancellationReader(monkeypatch):
    import time
    from emo_master.apps.runtime.devices.ipc import DeviceBridge
    from emo_master.apps.runtime.events.event_store import EventStore
    from emo_master.apps.runtime.jobs.models import JobRecord
    from emo_master.apps.runtime.jobs.repository import JobRepository
    from emo_master.apps.runtime.jobs.supervisor import JobSupervisor
    import emo_master.apps.runtime.jobs.supervisor as module

    context = multiprocessing.get_context("spawn")
    ready, childReady = context.Pipe(duplex=False)
    childControl, control = context.Pipe(duplex=False)
    startRead, readEntered = Event(), Event()
    runBridge = DeviceBridge.run

    def gatedRun(bridge):
        assert startRead.wait(5)
        runBridge(bridge)

    monkeypatch.setattr(DeviceBridge, "run", gatedRun)
    monkeypatch.setattr(module, "runJobProcess", _holdShutdownCancellationRead)
    manager = DeviceManager({})
    repository = JobRepository()
    repository.create(JobRecord("shutdown-job", "project", 1, "main"))
    supervisor = JobSupervisor(repository, EventStore(), devices=manager, retiredCallback=manager.retire)
    supervisor.startJob(JobProcessSpec("shutdown-job", "project.json", "main", managedConfiguration=True,
        presentation=dict(ready=childReady, control=childControl)))
    childReady.close()
    childControl.close()
    process, cancellation, _queue = supervisor._handles["shutdown-job"]
    bridge = manager.workers["shutdown-job"]
    requested, requestedRead = cancellation.set, cancellation.is_set
    killed = []
    terminate = supervisor._terminateProcess
    boundaryArranged = False

    def observeRead():
        readEntered.set()
        return requestedRead()

    def stopAtReadBoundary():
        nonlocal boundaryArranged
        requested()
        if boundaryArranged:
            return
        boundaryArranged = True
        control.send("read")
        assert ready.poll(5)
        assert ready.recv() == "reading"
        startRead.set()
        assert readEntered.wait(2)

    def terminateFixtureWorker(target):
        assert target is process
        result = terminate(target)
        killed.append(target.exitcode)
        return result

    cancellation.is_set = observeRead
    cancellation.set = stopAtReadBoundary
    monkeypatch.setattr(supervisor, "_terminateProcess", terminateFixtureWorker)
    try:
        assert ready.poll(5)
        assert ready.recv() == "started"
        deadline = time.monotonic() + 2.0
        supervisor.shutdown(deadline=deadline)
        supervisor.waitForRetirement(deadline=deadline)
        errors = supervisor.finishRetirements()
        try:
            manager.close()
        except BaseException:
            frame = sys._current_frames().get(bridge.thread.ident)
            if frame is not None:
                traceback.print_stack(frame)
            print("shutdown exitcodes:", killed, "retirement:", errors, file=sys.stderr)
            raise
        assert killed and killed[0] != 0
        assert not errors and not supervisor._ownedJobs()
        assert not bridge.thread.is_alive() and not manager.workers
    finally:
        startRead.set()
        if not killed:
            # Failed synchronization still releases this fixture worker cleanly.
            if not boundaryArranged:
                control.send("read")
            control.send("release")
            process.join(5)
            assert not process.is_alive()
        # The legacy-signal diagnostic can poison this fixture-only Lock.
        # Release it only after the real shutdown killed its owner, so the
        # failing baseline leaves no abandoned test thread or native handle.
        condition = getattr(cancellation, "_cond", None)
        if condition is not None and killed:
            condition.release()
        cancellation.set = requested
        bridge.thread.join(3)
        manager.retire("shutdown-job")
        manager.close()
        ready.close()
        control.close()
