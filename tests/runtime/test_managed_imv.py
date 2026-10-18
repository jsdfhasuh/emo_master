"""Real spawn/IPC with a fake IMV API only. Never enumerates physical devices."""
import json
import os
from pathlib import Path
import sys
import time

import pytest
import grpc

from emo_master.apps.runtime.devices.executor import DeviceError
from emo_master.apps.runtime.devices.frame_store import copyFrame
from emo_master.apps.runtime.devices.imv_capture import ImvCapture
from emo_master.apps.runtime.devices.imv_host import ManagedImv
from emo_master.apps.runtime.devices.manager import DeviceManager
from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb
from emo_master.apps.runtime.grpc_server.service import RuntimeService
from emo_master.apps.runtime.grpc_server.generated import runtime_pb2_grpc
from emo_master.apps.runtime.main import createRuntimeServer
from emo_master.apps.designer.services.runtime_client import RuntimeClient
from emo_master.core.plugin.registry import PluginRegistry
from emo_master.core.project.devices import DeviceDefinition
from emo_master.core.project.models import ProjectDocument
from emo_master.core.workflow.compiler import WorkflowCompiler
from emo_master.core.workflow.errors import WorkflowCompileError
from tests.plugins.test_huaray_camera_operator import _FakeImvApi
from tests.runtime.runtime_test_utils import waitForTerminal
from tests.runtime.test_device_runtime import simulatedProject


ROOT = Path(__file__).resolve().parents[2] / "src/emo_master/plugins/builtins"


class FakeImv(_FakeImvApi):
    def __init__(self):
        super().__init__()
        self.clearCount = 0

    def clear_buffer(self, handle):
        self.clearCount += 1
        return 0


class TimeoutImv(FakeImv):
    def __init__(self):
        super().__init__()
        self.alwaysTimeout = True


class CrashImv(FakeImv):
    def execute_command(self, handle, name):
        os._exit(19)


class StuckImv(FakeImv):
    def get_frame(self, handle, timeoutMs):
        time.sleep(60)


class BadCloseImv(FakeImv):
    def __init__(self):
        super().__init__()
        self.stopResult = -9


class OversizedFrameImv(FakeImv):
    def get_frame(self, handle, timeoutMs):
        code, frame = super().get_frame(handle, timeoutMs)
        frame.frameInfo.width = 100000
        frame.frameInfo.height = 100000
        return code, frame


def cameraProject(mode="software"):
    payload = simulatedProject()
    payload["devices"]["definitions"]["camera"] = dict(name="Fake IMV", deviceTypeId="device.camera.huaray_imv",
        driverVersion="1.0.0", connection=dict(cameraKey="FAKE-KEY", maxFrameBytes=1024),
        defaults=dict(triggerMode=mode, captureTimeoutMs=100), policies=dict(operationTimeoutMs=5000))
    payload["workflows"]["main"]["nodes"][1]["operatorId"] = "vision.io.huaray_camera"
    return payload


def definition(mode="software"):
    types, rejected = PluginRegistry("0.6.1").scanDeviceTypes([ROOT])
    assert not rejected
    return types["device.camera.huaray_imv"].validateDefinition(
        DeviceDefinition.model_validate(cameraProject(mode)["devices"]["definitions"]["camera"]))


@pytest.mark.skipif(sys.platform != "win32", reason="managed IMV declaration supports Windows Runtime only")
def testDiagnosticUsesFrozenCycleDefaultsAcrossConcurrentCommit(tmp_path, monkeypatch):
    from copy import deepcopy
    from tests.runtime.test_project_configuration import finish
    from emo_master.core.project.snapshots import revisionOf
    payload = cameraProject()
    payload["devices"]["definitions"]["camera"]["defaults"]["exposureUs"] = 8000
    path = tmp_path / "project.emoproj"
    path.write_text(json.dumps(payload), encoding="utf-8")
    service = RuntimeService(dbPath=tmp_path / "runtime.db", workspaceRoot=tmp_path / "jobs")
    try:
        assert service.LoadProject(pb.LoadProjectRequest(project_path=str(path)), None).ok
        manager = service.deviceManager
        manager.drivers["camera"].apiFactory = FakeImv
        # Opt the fake SDK into this capability for the race test. Production
        # IMV metadata still requires stopped execution for default changes.
        key = "device.camera.huaray_imv"
        monkeypatch.setitem(manager.types, key, manager.types[key].model_copy(update={"nextCyclePaths": ["exposureUs"]}))
        sessionId = service.cameraDiagnostics.open("camera", revisionOf(manager.definitions["camera"].model_dump()),
                                                  manager.executors["camera"].generation)
        session = service.cameraDiagnostics.sessions[sessionId]
        config = service.projectConfiguration
        begin = config.beginCycle
        def beginAndWrite(*args, **kwargs):
            cycle = begin(*args, **kwargs)
            snapshot = config.snapshot()
            local = deepcopy(snapshot["payload"])
            local["devices"]["definitions"]["camera"]["defaults"]["exposureUs"] = 10000
            config.start(dict(requestId="diagnostic-race", expectedRuntimeInstanceId=service.runtimeInstanceId,
                expectedRevision=snapshot["documentRevision"], localPayload=local,
                selectedPaths=["/devices/definitions/camera/defaults/exposureUs"]))
            status = finish(config, "diagnostic-race")
            assert status["state"] == "COMMITTED", status
            return cycle
        monkeypatch.setattr(config, "beginCycle", beginAndWrite)
        result = session.capture(1, True, lambda: False)
        assert result["ok"], result
        assert result["metadata"]["planRevision"] == 1
        assert result["metadata"]["acquisition"]["actualExposureUs"] == 8000
        session.close()
        assert manager.definitions["camera"].defaults["exposureUs"] == 10000
        monkeypatch.setattr(config, "beginCycle", begin)
        sessionId = service.cameraDiagnostics.open("camera", revisionOf(manager.definitions["camera"].model_dump()),
                                                  manager.executors["camera"].generation)
        result = service.cameraDiagnostics.sessions[sessionId].capture(1, True, lambda: False)
        assert result["ok"] and result["metadata"]["planRevision"] == 2
        assert result["metadata"]["acquisition"]["actualExposureUs"] == 10000
    finally:
        service.close()


def capture(camera, params, cancelled=lambda: False):
    return camera.capture(params, cancelled, time.monotonic() + 2, 1024)


@pytest.mark.parametrize("mode", ["software", "hardware", "freeRun"])
def testStreamSharedAcrossRequestsAndExposureEvidenceIsNotInvented(mode):
    api = FakeImv()
    camera = ImvCapture(definition().connection, lambda: api)
    try:
        params = definition(mode).defaults
        first, a = capture(camera, params)
        second, b = capture(camera, params)
        assert first.shape == second.shape == (3, 4, 3)
        assert (a["blockId"], b["blockId"]) == (41, 42)
        assert a["streamGeneration"] == b["streamGeneration"]
        assert a["frameExposureUs"] is None
        assert a["exposureEvidence"] == "configuration_readback_not_frame_metadata"
        assert api.openCount == len(api.startStrategies) == 1 and api.releaseCount == 2
        assert api.clearCount == 0
        assert len(api.commands) == (2 if mode == "software" else 0)
    finally:
        camera.close()


@pytest.mark.parametrize("mode", ["software", "freeRun"])
def testParameterChangeEstablishesNewStreamBoundary(mode):
    api = FakeImv()
    camera = ImvCapture(definition().connection, lambda: api)
    try:
        params = definition(mode).defaults
        _, first = capture(camera, params)
        _, second = capture(camera, dict(params, exposureUs=20000))
        assert first["streamGeneration"] != second["streamGeneration"]
        assert first["connectionGeneration"] == second["connectionGeneration"]
        assert second["actualExposureUs"] == 20000
        assert api.stopCount == api.clearCount == 1
        assert api.openCount == 1
    finally:
        camera.close()


def testHardwareOverrideRequiresSyncAndNeverClearsQueue():
    api = FakeImv()
    camera = ImvCapture(definition().connection, lambda: api)
    try:
        params = definition("hardware").defaults
        capture(camera, params)
        with pytest.raises(DeviceError, match="external triggers") as failure:
            capture(camera, dict(params, exposureUs=20000))
        assert failure.value.code == "E_CAMERA_SYNC_REQUIRED"
        assert not camera.quarantined and api.clearCount == api.stopCount == 0
        assert api.doubles["ExposureTime"] == params["exposureUs"]
        assert capture(camera, params)[1]["blockId"] == 42
    finally:
        camera.close()


def testSoftwareTimeoutDoesNotReplayAndRejectsLateFrameForNextRequest():
    api = TimeoutImv()
    camera = ImvCapture(definition().connection, lambda: api)
    try:
        params = definition().defaults
        with pytest.raises(DeviceError) as failure:
            capture(camera, params)
        assert failure.value.outcome == "UNKNOWN" and failure.value.unhealthy
        api.alwaysTimeout = False
        with pytest.raises(DeviceError, match="recovery"):
            capture(camera, params)
        assert api.commands == ["TriggerSoftware"] and api.releaseCount == 0
    finally:
        camera.close()


def testHardwareTimeoutRetainsContinuousQueueAndSequenceIsStreamGlobal():
    api = TimeoutImv()
    camera = ImvCapture(definition().connection, lambda: api)
    try:
        params = dict(definition("hardware").defaults, sequencePolicy="contiguous")
        with pytest.raises(DeviceError) as failure:
            capture(camera, params)
        assert not failure.value.unhealthy
        api.alwaysTimeout = False
        assert capture(camera, params)[1]["blockId"] == 41
        assert capture(camera, params)[1]["blockId"] == 42
        api.blockId += 1
        with pytest.raises(DeviceError, match="contiguous"):
            capture(camera, params)
        assert camera.quarantined and api.clearCount == 0
    finally:
        camera.close()


def testCapacityAndCancellationPreventTrigger():
    api = FakeImv()
    camera = ImvCapture(definition().connection, lambda: api)
    try:
        with pytest.raises(DeviceError):
            capture(camera, definition().defaults, lambda: True)
        assert not api.enumerateCalls
        with pytest.raises(DeviceError, match="capacity"):
            camera.capture(definition().defaults, lambda: False, time.monotonic() + 1, 1)
        assert not api.commands and not camera.quarantined
    finally:
        camera.close()


def testRealSpawnHostReusesConnectionAndClosesSharedMemory():
    d = definition()
    driver = ManagedImv(d, apiFactory=FakeImv)
    assert driver.process is None
    try:
        a, first = driver.execute("capture", d.defaults, lambda: False, time.monotonic() + 10)
        pid = driver.process.pid
        b, second = driver.execute("capture", d.defaults, lambda: False, time.monotonic() + 5)
        assert a.shape == b.shape == (3, 4, 3)
        assert driver.process.pid == pid != os.getpid()
        assert first["blockId"] + 1 == second["blockId"]
        assert first["connectionGeneration"] == second["connectionGeneration"]
        assert driver.memory is None
    finally:
        driver.close()
    assert driver.process is None


def testOversizedNativeFrameIsReleasedBeforeConversion():
    api = OversizedFrameImv()
    camera = ImvCapture(definition().connection, lambda: api)
    try:
        with pytest.raises(DeviceError, match="capacity"):
            capture(camera, definition().defaults)
        assert api.releaseCount == 1 and not api.convertCalls
        assert camera.quarantined
    finally:
        camera.close()


def testHostCleanupFailureIsNotReportedAsSuccessfulRelease():
    d = definition()
    driver = ManagedImv(d, apiFactory=BadCloseImv)
    driver.execute("capture", d.defaults, lambda: False, time.monotonic() + 10)
    with pytest.raises(DeviceError, match="cleanup failed"):
        driver.close()
    assert driver.process is None and driver.memory is None
    driver.close()


@pytest.mark.parametrize("factory", [CrashImv, TimeoutImv, StuckImv])
def testHostFailureNeverReplaysAndConfirmsExitBeforeReleasingMemory(factory):
    d = definition()
    driver = ManagedImv(d, apiFactory=factory)
    try:
        with pytest.raises(DeviceError) as failure:
            driver.execute("capture", d.defaults, lambda: False, time.monotonic() + 2)
        assert failure.value.unhealthy and failure.value.outcome == "UNKNOWN"
        assert driver.process is None and driver.memory is None
        with pytest.raises(DeviceError, match="recovery"):
            driver.execute("capture", d.defaults, lambda: False, time.monotonic() + 2)
        assert driver.sequence == 1
    finally:
        driver.close()


@pytest.mark.skipif(sys.platform != "win32", reason="managed IMV declaration supports Windows Runtime only")
def testManagerSharesHostBetweenOwnersAndCapacityFailureDoesNotOpenSdk():
    scan = PluginRegistry("0.6.1").scan(ROOT)
    manager = DeviceManager(scan.deviceTypes)
    manager.load(ProjectDocument.model_validate(cameraProject()))
    manager.drivers["camera"].apiFactory = FakeImv
    try:
        manager.authorize("a", ["camera"])
        manager.authorize("b", ["camera"])
        params = manager.definitions["camera"].defaults
        manager.frames.maxBytes = 1
        with pytest.raises(DeviceError, match="capacity"):
            manager.execute("a", "camera", "capture", params, cycleId="a")
        assert manager.drivers["camera"].process is None
        manager.frames.maxBytes = 4096
        a = manager.execute("a", "camera", "capture", params, cycleId="a")
        b = manager.execute("b", "camera", "capture", params, cycleId="b")
        assert a["frameId"] != b["frameId"]
        assert a["acquisition"]["blockId"] + 1 == b["acquisition"]["blockId"]
        assert copyFrame(a).shape == (3, 4, 3)
        manager.retire("a")
        assert b["frameId"] in manager.frames.frames
        manager.retire("b")
        manager.recover("camera", manager.executors["camera"].generation)
        assert manager.drivers["camera"].process is None
    finally:
        manager.close()


def testCompilerRequiresManagedCameraButPreservesLegacyProjects():
    scan = PluginRegistry("0.6.1").scan(ROOT)
    compiler = WorkflowCompiler(scan.activeOperators, deviceTypes=scan.deviceTypes)
    payload = cameraProject()
    assert compiler.compile(payload).workflows["main"].nodes[1].devicePlans
    node = payload["workflows"]["main"]["nodes"][1]
    node["params"] = dict(retryCount=1)
    with pytest.raises(WorkflowCompileError, match="connection/retry"):
        compiler.compile(payload)
    node["params"] = {}
    node.pop("deviceBindings")
    with pytest.raises(WorkflowCompileError, match="device slots"):
        compiler.compile(payload)
    payload["schemaVersion"] = "2.5"
    payload["devices"] = {"bindings": {}}
    node["params"] = dict(ipAddress="127.0.0.1")
    assert not compiler.compile(payload).workflows["main"].nodes[1].devicePlans


def testOverrideRequiresApplicableModeAndKeepDoesNotInventExposure():
    scan = PluginRegistry("0.6.1").scan(ROOT)
    declaration = scan.deviceTypes["device.camera.huaray_imv"]
    d = definition().model_copy(update={"defaults": dict(definition().defaults, exposureMode="keep")})
    assert declaration.validateDefinition(d).defaults["exposureMode"] == "keep"
    with pytest.raises(ValueError, match="exposureMode"):
        declaration.validateNodeSources(d, {"exposureUs": 12000}, {})
    effective = declaration.validateNodeSources(d, {"exposureMode": "manual", "exposureUs": 12000}, {})
    assert effective["exposureUs"] == 12000


@pytest.mark.skipif(sys.platform != "win32", reason="managed IMV declaration supports Windows Runtime only")
def testSpawnedProductionJobsUseManagedCameraHost(tmp_path):
    path = tmp_path / "project.emoproj"
    path.write_text(json.dumps(cameraProject()), encoding="utf-8")
    service = RuntimeService(dbPath=tmp_path / "runtime.db", workspaceRoot=tmp_path / "jobs")
    try:
        loaded = service.LoadProject(pb.LoadProjectRequest(project_path=str(path)), None)
        assert loaded.ok, loaded.message
        service.deviceManager.drivers["camera"].apiFactory = FakeImv
        replies = [service.StartJob(pb.StartJobRequest(project_id=service.loadedProjectId, workflow_id="main"), None)
                   for _ in range(2)]
        assert all(reply.ok for reply in replies), replies
        for reply in replies:
            result = waitForTerminal(service, reply.job_id, timeoutSeconds=30)
            assert result.status == "COMPLETED", result
        until = time.monotonic() + 5
        while service.deviceManager.workers and time.monotonic() < until:
            service.jobSupervisor.finishRetirements()
            time.sleep(.02)
        driver = service.deviceManager.drivers["camera"]
        assert driver.sequence == 3 and driver.process.is_alive()  # Connect plus two captures.
        assert not service.deviceManager.frames.frames and not service.deviceManager.frames.reservations
    finally:
        service.close()


@pytest.mark.skipif(sys.platform != "win32", reason="managed IMV declaration supports Windows Runtime only")
def testRemoteDiagnosticsShareHostDeduplicateAndRejectPrivatePreview(tmp_path):
    path = tmp_path / "project.emoproj"
    path.write_text(json.dumps(cameraProject()), encoding="utf-8")
    service = RuntimeService(dbPath=tmp_path / "runtime.db", workspaceRoot=tmp_path / "jobs")
    server, port, _ = createRuntimeServer(port=0, runtimeService=service)
    server.start()
    channel = grpc.insecure_channel(f"127.0.0.1:{port}")
    try:
        assert service.LoadProject(pb.LoadProjectRequest(project_path=str(path)), None).ok
        driver = service.deviceManager.drivers["camera"]
        driver.apiFactory = FakeImv
        client = RuntimeClient(runtime_pb2_grpc.RuntimeServiceStub(channel))
        runtimeId, states = client.getDeviceStates(service.loadedProjectId)
        state = states["camera"]
        args = (service.loadedProjectId, runtimeId, "camera", state)
        opened = client.cameraDiagnostic(*args, "open")
        sessionId = opened["sessionId"]
        assert driver.process is None
        with pytest.raises(ValueError, match="confirm"):
            client.cameraDiagnostic(*args, "capture", sessionId=sessionId, sequence=1)
        first = client.cameraDiagnostic(*args, "capture", sessionId=sessionId, sequence=1, confirmed=True)
        repeated = client.cameraDiagnostic(*args, "capture", sessionId=sessionId, sequence=1, confirmed=True)
        assert first == repeated and driver.sequence == 2 and first["preview"].startswith(b"\xff\xd8")
        assert not service.deviceManager.frames.frames
        with pytest.raises(ValueError, match="sequence"):
            client.cameraDiagnostic(*args, "capture", sessionId=sessionId, sequence=3, confirmed=True)
        request = pb.OpenOperatorPreviewSessionRequest(project_id=service.loadedProjectId, workflow_id="main",
            node_id="capture", operator_id="vision.io.huaray_camera", params_json='{"ipAddress":"127.0.0.1"}')
        assert service.OpenOperatorPreviewSession(request, None).code == "E_DEVICE_DEBUG_REQUIRED"
        request.project_json = json.dumps(cameraProject())
        assert service.OpenDraftOperatorPreviewSession(request, None).code == "E_DEVICE_DEBUG_REQUIRED"
        assert service.OpenCameraConfigurationSession(request, None).code == "E_DEVICE_DEBUG_REQUIRED"
        client.cameraDiagnostic(*args, "close", sessionId=sessionId)
        assert not service.deviceManager.maintenanceOwners
        assert driver.process.is_alive()
        with pytest.raises(ValueError, match="synchronized"):
            client.recoverDevice(service.loadedProjectId, runtimeId, "camera", state)
        client.recoverDevice(service.loadedProjectId, runtimeId, "camera", state, hardwareSynchronized=True)
        assert driver.process is None
        with pytest.raises(ValueError, match="changed"):
            client.cameraDiagnostic(*args, "open")
    finally:
        channel.close()
        server.stop(0).wait()
        service.close()
