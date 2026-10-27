"""Lifecycle tests use synthetic devices, loopback RPC, and a fake SDK only."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
import multiprocessing
from pathlib import Path
from threading import Event, get_ident
import time

import grpc
import pytest

from emo_master.apps.designer.services.runtime_client import RuntimeClient, RuntimeClientError
from emo_master.apps.runtime.devices.executor import DeviceError
from emo_master.apps.runtime.devices.imv_capture import ImvCapture
from emo_master.apps.runtime.devices.imv_host import ManagedImv
from emo_master.apps.runtime.devices.manager import DeviceManager
from emo_master.apps.runtime.devices.frame_store import copyFrame
from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb, runtime_pb2_grpc
from emo_master.apps.runtime.grpc_server.service import RuntimeService
from emo_master.apps.runtime.main import createRuntimeServer
from emo_master.core.plugin.registry import PluginRegistry
from emo_master.core.project.models import ProjectDocument
from tests.runtime.test_device_runtime import simulatedProject
from tests.runtime.test_managed_imv import FakeImv, definition
from tests.runtime.test_project_configuration import authority, request, finish


def until(predicate):
    deadline = time.monotonic() + 5
    while not predicate() and time.monotonic() < deadline:
        time.sleep(.005)
    assert predicate()


@pytest.fixture
def makeManager():
    managers = []
    types, _ = PluginRegistry("0.6.1").scanDeviceTypes([Path(__file__).resolve().parents[2] / "src/emo_master/plugins/builtins"])
    def make(**policies):
        payload = simulatedProject()
        payload["devices"]["definitions"]["camera"]["policies"] = policies
        other = deepcopy(payload["devices"]["definitions"]["camera"])
        other["connection"]["serial"] = "SECOND"
        payload["devices"]["definitions"]["other"] = other
        manager = DeviceManager(types)
        managers.append(manager)
        manager.load(ProjectDocument.model_validate(payload))
        manager.authorize("job", ["camera", "other"])
        return manager
    yield make
    for manager in managers:
        for owner in list(manager.permissions):
            manager.retire(owner)
        manager.close()


def capture(manager, key="camera", **kwargs):
    return manager.execute("job", key, "capture", manager.definitions[key].defaults, cycleId="cycle", **kwargs)


def control(manager, action, key="camera"):
    executor, operation = manager.beginControl(key, manager.executors[key].generation, action)
    return executor.wait(operation)


@pytest.mark.parametrize("release", ["idle_timeout", "when_unused"])
def testStaticJobDoesNotKeepIdleDeviceConnected(makeManager, release):
    manager = makeManager(release=release, idleTimeoutMs=30)
    driver = manager.drivers["camera"]
    capture(manager)
    firstGeneration = driver.generation
    until(lambda: not driver.connected)
    assert "job" in manager.permissions
    assert manager.frames.frames  # Downstream originals do not hold the device connection.
    capture(manager)
    assert driver.generation != firstGeneration and driver.sequence == 2


def testExplicitLeaseAndQueuedOperationsPreventIdleRelease(makeManager):
    manager = makeManager(release="when_unused")
    driver = manager.drivers["camera"]
    manager.setKeepAlive("job", "camera", True)
    capture(manager)
    generation = driver.generation
    barrier = Event()
    entered = Event()
    executor = manager.executors["camera"]
    def held(cancel, deadline, _generation):
        entered.set()
        assert barrier.wait(2)
    running = executor.submit("job", held)
    assert entered.wait(2)
    manager.setKeepAlive("job", "camera", False)
    queued = executor.submit("job", lambda *_: (driver.connected, driver.generation))
    barrier.set()
    executor.wait(running)
    assert executor.wait(queued) == (True, generation)
    until(lambda: not driver.connected)


def testManualDisconnectPausesAutomaticAdmissionUntilExplicitConnect(makeManager):
    manager = makeManager()
    capture(manager)
    driver = manager.drivers["camera"]
    control(manager, "disconnect")
    assert not driver.connected
    with pytest.raises(DeviceError, match="paused"):
        capture(manager)
    assert driver.sequence == 1
    capture(manager, "other")
    control(manager, "connect")
    assert driver.connected and driver.sequence == 1
    capture(manager)
    assert driver.sequence == 2


def testManualPolicyDoesNotConnectFromJobOrActivation(makeManager):
    manager = makeManager(connect="manual")
    manager.activate()
    with pytest.raises(DeviceError) as error:
        capture(manager)
    assert error.value.code == "E_DEVICE_MANUAL_CONNECT"
    assert not manager.drivers["camera"].connected
    control(manager, "connect")
    capture(manager)


def testAlreadyCancelledOperationDoesNotOpenConnection(makeManager):
    manager = makeManager()
    with pytest.raises(DeviceError) as error:
        capture(manager, cancelled=lambda: True)
    assert error.value.code == "E_DEVICE_CANCELLED"
    assert not manager.drivers["camera"].connected
    assert not manager.executors["camera"].quarantined and not manager.frames.reservations


def testCancellationDuringConnectDoesNotStartCapture(makeManager):
    manager = makeManager()
    entered, release, cancelled = Event(), Event(), Event()
    driver = manager.drivers["camera"]
    connect = driver.connect
    def held(params, _cancel, deadline):
        entered.set()
        assert release.wait(5)
        connect(params, lambda: False, deadline)
    driver.connect = held
    with ThreadPoolExecutor(1) as pool:
        pending = pool.submit(capture, manager, cancelled=cancelled.is_set)
        assert entered.wait(2)
        cancelled.set()
        release.set()
        with pytest.raises(DeviceError):
            pending.result(2)
    until(lambda: manager.executors["camera"].active is None)
    assert driver.sequence == 0
    assert not manager.frames.frames and not manager.frames.reservations


def testAbandonedConnectCannotUnpauseAdmissionAfterLateNativeSuccess(makeManager):
    manager = makeManager()
    entered, release = Event(), Event()
    driver = manager.drivers["camera"]
    connect = driver.connect
    def held(params, _cancel, deadline):
        entered.set()
        assert release.wait(5)
        connect(params, lambda: False, deadline)
    driver.connect = held
    executor, operation = manager.beginControl("camera", manager.executors["camera"].generation, "connect")
    try:
        assert entered.wait(2)
        executor.cancel(operation)
    finally:
        release.set()
    with pytest.raises(DeviceError, match="abandoned"):
        executor.wait(operation)
    assert manager.lifecycles["camera"].paused
    assert not driver.connected and not executor.quarantined
    with pytest.raises(DeviceError, match="paused"):
        capture(manager)


def testDriverWithoutConnectOnlyContractCannotReplaceExistingAuthority(makeManager, monkeypatch):
    from emo_master.apps.runtime.devices import drivers
    manager = makeManager()
    previous = manager.drivers["camera"]
    class MissingConnectionContract:
        def __init__(self, _definition):
            pytest.fail("invalid adapter must not be constructed")
    monkeypatch.setattr(drivers, "resolveDriver", lambda _entry: MissingConnectionContract)
    with pytest.raises(DeviceError) as error:
        manager.load(ProjectDocument.model_validate(simulatedProject()))
    assert error.value.code == "E_DEVICE_DRIVER_CONTRACT"
    assert manager.drivers["camera"] is previous
    assert not previous.connected


def testQueuedAcquisitionKeepsOldCycleParametersAfterCommit(makeManager, tmp_path):
    manager = makeManager()
    manager.authorize("next", ["camera"])
    configuration = authority(tmp_path)
    manager.configuration = configuration
    entered, release = Event(), Event()
    executor = manager.executors["camera"]
    def hold(*_):
        entered.set()
        assert release.wait(5)
    holder = executor.submit("holder", hold)
    assert entered.wait(2)
    old = configuration.beginCycle("job", 1)
    try:
        with ThreadPoolExecutor(2) as pool:
            first = pool.submit(manager.execute, "job", "camera", "capture", manager.definitions["camera"].defaults,
                                cycleId=old["cycleId"], source=dict(workflowId='main', nodeId='capture', slot='camera'))
            until(lambda: len(executor.queue) == 1)
            configuration.start(request(configuration))
            assert finish(configuration)["state"] == "COMMITTED"
            new = configuration.beginCycle("next", 1)
            params = dict(manager.definitions["camera"].defaults, exposureUs=12000)
            second = pool.submit(manager.execute, "next", "camera", "capture", params, cycleId=new["cycleId"],
                                 source=dict(workflowId='main', nodeId='capture', slot='camera'))
            until(lambda: len(executor.queue) == 2)
            release.set()
            executor.wait(holder)
            before, after = first.result(3), second.result(3)
            assert before["planRevision"] == 1 and after["planRevision"] == 2
            assert (copyFrame(before)[:, :, 2] == 8).all()
            assert (copyFrame(after)[:, :, 2] == 12).all()
    finally:
        release.set()
        configuration.close()


def testActivationAndRunEventsDoNotAcquireFrames(makeManager):
    manager = makeManager(connect="on_project_open", release="idle_timeout", idleTimeoutMs=100)
    assert not manager.drivers["camera"].connected
    manager.activate()
    until(lambda: manager.drivers["camera"].connected)
    first = manager.drivers["camera"].generation
    assert manager.drivers["camera"].sequence == 0
    until(lambda: not manager.drivers["camera"].connected)
    time.sleep(.12)
    assert manager.drivers["camera"].generation == first
    capture(manager)
    assert manager.drivers["camera"].generation != first
    manager = makeManager(connect="on_project_run")
    manager.activate()
    assert not manager.drivers["camera"].connected
    ctx = multiprocessing.get_context("spawn")
    child = manager.openWorker("run", ["camera"], ctx.Event(), ctx)
    try:
        until(lambda: manager.drivers["camera"].connected)
        assert manager.drivers["camera"].sequence == 0
        assert not manager.drivers["other"].connected
    finally:
        child.close()
        manager.retire("run")


def testMaintenanceWaitsForRealExitButDoesNotBlockOtherDevices(makeManager):
    manager = makeManager()
    entered, exitAllowed = Event(), Event()
    driver = manager.drivers["camera"]
    closed = []
    originalClose = driver.close
    def close(deadline=None):
        closed.append(get_ident())
        originalClose(deadline)
    driver.close = close
    def held(*_args, **_kwargs):
        entered.set()
        assert exitAllowed.wait(3)
        raise DeviceError("E_DEVICE_CANCELLED", "native exit confirmed")
    driver.execute = held
    with ThreadPoolExecutor(2) as pool:
        pending = pool.submit(capture, manager)
        assert entered.wait(2)
        assert 'disconnect' in manager.availableControls('camera')
        executor, operation = manager.beginControl("camera", manager.executors["camera"].generation, "maintenance")
        try:
            assert not closed and not operation.future.done()
            with pytest.raises(DeviceError, match="paused"):
                capture(manager)
            capture(manager, "other")
        finally:
            exitAllowed.set()
        with pytest.raises(DeviceError):
            pending.result(2)
        executor.wait(operation)
        assert closed == [executor.thread.ident]
        assert manager.lifecycles["camera"].paused
        assert not manager.drivers["camera"].connected


def testIdleCloseFailureIsVisibleAndQuarantinesInsteadOfPretendingReleased(makeManager):
    manager = makeManager(release="when_unused")
    driver = manager.drivers["camera"]
    original = driver.close
    def fail(deadline=None):
        raise RuntimeError("cleanup rejected")
    driver.close = fail
    try:
        capture(manager)
        until(lambda: manager.executors["camera"].quarantined)
        assert manager.lifecycles["camera"].connected
        assert manager.lifecycles["camera"].snapshot()["lifecycleError"] == "cleanup rejected"
        with pytest.raises(DeviceError):
            capture(manager)
    finally:
        driver.close = original


@pytest.mark.parametrize("connect", ["manual", "on_project_run", "on_project_open"])
def testRejectImmediateConnectReleaseCombinations(makeManager, connect):
    with pytest.raises(ValueError, match="when_unused"):
        makeManager(connect=connect, release="when_unused")


def testCameraConnectDoesNotConfigureStartStreamOrTrigger():
    api = FakeImv()
    before = deepcopy((api.enums, api.doubles, api.ints, api.bools))
    camera = ImvCapture(definition().connection, lambda: api)
    try:
        camera.connect(definition().defaults, lambda: False, time.monotonic() + 2)
        assert api.openCount == 1
        assert not api.commands and not api.startStrategies and api.releaseCount == 0
        assert before == (api.enums, api.doubles, api.ints, api.bools)
        camera.capture(definition().defaults, lambda: False, time.monotonic() + 2, 1024)
        assert api.openCount == 1 and api.commands == ["TriggerSoftware"]
    finally:
        camera.close()


def testSpawnHostExplicitConnectionAndHardwareStreamHold():
    driver = ManagedImv(definition("hardware"), apiFactory=FakeImv)
    try:
        driver.connect(driver.definition.defaults, lambda: False, time.monotonic() + 5)
        generation = driver.generation
        assert not driver.holdsConnection() and driver.memory is None
        _, metadata = driver.execute("capture", driver.definition.defaults, lambda: False, time.monotonic() + 5)
        assert metadata["blockId"] == 41 and driver.holdsConnection()
        driver.close()
        assert driver.process is None and not driver.holdsConnection()
        assert driver.snapshot()["connectionState"] == "DISCONNECTED"
        driver.connect(driver.definition.defaults, lambda: False, time.monotonic() + 5)
        assert driver.generation != generation and driver.sequence == 1
    finally:
        driver.close()


def testRemoteControlsAreConfirmedFencedAndDoNotModifyConfiguration(tmp_path):
    payload = simulatedProject()
    path = tmp_path / "project.emoproj"
    path.write_text(json.dumps(payload), encoding="utf-8")
    service = RuntimeService(dbPath=tmp_path / "runtime.db", workspaceRoot=tmp_path / "jobs")
    server, port, _ = createRuntimeServer(port=0, runtimeService=service)
    server.start()
    channel = grpc.insecure_channel(f"127.0.0.1:{port}")
    try:
        assert service.LoadProject(pb.LoadProjectRequest(project_path=str(path)), None).ok
        before = deepcopy(service.loadedPayload)
        client = RuntimeClient(runtime_pb2_grpc.RuntimeServiceStub(channel))
        runtimeId, states = client.getDeviceStates(service.loadedProjectId)
        args = (service.loadedProjectId, runtimeId, "camera", states["camera"])
        with pytest.raises(RuntimeClientError, match="unconfirmed"):
            client.controlDevice(*args, "connect")
        assert not service.deviceManager.drivers["camera"].connected
        _, connected = client.controlDevice(*args, "connect", confirmed=True)
        assert connected["camera"]["driverState"]["connectionState"] == "CONNECTED"
        with pytest.raises(RuntimeClientError, match="control changed"):
            client.controlDevice(*args, "disconnect", confirmed=True)
        _, stopped = client.controlDevice(service.loadedProjectId, runtimeId, "camera", connected["camera"],
                                           "disconnect", confirmed=True)
        assert stopped["camera"]["admissionState"] == "PAUSED"
        with pytest.raises(RuntimeClientError, match="generation"):
            client.controlDevice(service.loadedProjectId, runtimeId, "camera", dict(states["camera"], generation="old"),
                                 "connect", confirmed=True)
        assert service.deviceManager.drivers["camera"].sequence == 0 and service.loadedPayload == before
        assert json.loads(path.read_text()) == payload
    finally:
        channel.close()
        server.stop(0).wait()
        service.close()
