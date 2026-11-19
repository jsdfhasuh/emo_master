import json
import sys
from threading import Event
from time import monotonic

import numpy as np
import pytest

from emo_master.apps.runtime.devices.mvs_host import MvsCapture
from emo_master.core.contracts.device_driver import DeviceError


class FakeMvsSession:
    def __init__(self, serialNumber):
        self.serialNumber = serialNumber
        self.starts = []
        self.stops = self.closes = self.connects = self.requests = 0
        self.error = None
        self.timeout = None

    def connect(self):
        self.connects += 1

    def start(self, params):
        self.starts.append(dict(params))

    def stop(self):
        self.stops += 1

    def capture(self, params, check, capacity, timeoutMs):
        self.requests += 1
        self.timeout = timeoutMs
        check()
        if self.error:
            raise self.error
        shape = (3, 4) if params["outputColor"] == "gray" else (3, 4, 3)
        return np.zeros(shape, np.uint8), dict(blockId=self.requests, deviceTimestamp=self.requests * 100,
            actualExposureUs=1000, offsetX=2, offsetY=4)

    def close(self):
        self.closes += 1


def params(mode):
    return dict(triggerMode=mode, triggerSource="Line0", triggerActivation="RisingEdge",
                exposureMode="keep", exposureUs=1000, gainMode="keep", gainDb=0,
                outputColor="bgr", captureTimeoutMs=5000)


def capture(camera, settings):
    return camera.capture(settings, lambda: False, float("inf"), 4096)


def testSoftwareSettingsStartANewStreamOnTheSameConnection():
    camera = MvsCapture(dict(serialNumber="TEST"), FakeMvsSession)
    try:
        settings = params("software")
        _, first = capture(camera, settings)
        gray, second = capture(camera, dict(settings, outputColor="gray", captureTimeoutMs=500,
                                            triggerSource="Line2", exposureUs=2000))
        session = camera.session
        assert len(session.starts) == 1 and session.timeout == 500 and gray.shape == (3, 4)
        assert first["streamGeneration"] == second["streamGeneration"]
        _, third = capture(camera, dict(settings, exposureMode="manual", exposureUs=800))
        assert third["connectionGeneration"] == first["connectionGeneration"]
        assert third["streamGeneration"] != first["streamGeneration"]
        assert first["sourceId"] == "hikrobot-mvs:TEST"
        assert third["blockId"] == 3 and session.connects == session.stops == 1
    finally:
        camera.close()
    assert session.closes == 1


def testHardwareRequestsAndRejectedSettingChangePreserveStreamOrder():
    camera = MvsCapture(dict(serialNumber="TEST"), FakeMvsSession)
    settings = dict(params("hardware"), captureTimeoutMs=0)
    try:
        _, first = capture(camera, settings)
        session = camera.session
        with pytest.raises(DeviceError, match="external triggers") as error:
            capture(camera, dict(settings, triggerSource="Line1"))
        assert error.value.code == "E_CAMERA_SYNC_REQUIRED"
        _, result = capture(camera, settings)
        assert first["blockId"] == 1 and result["blockId"] == 2 and first["streamGeneration"] == result["streamGeneration"]
        assert session.timeout is None and session.stops == 0 and len(session.starts) == session.connects == 1
    finally:
        camera.close()


@pytest.mark.parametrize(("mode", "marker"), [("software", "softwareTriggerIssued"), ("hardware", "frameReceived")])
def testInterruptedAdmittedTriggerOrConsumedFrameRequiresRecovery(mode, marker):
    camera = MvsCapture(dict(serialNumber="TEST"), FakeMvsSession)
    settings = params(mode)
    camera.connect(settings, lambda: False, monotonic() + 5)
    session = camera.session
    session.error = DeviceError("E_DEVICE_CANCELLED", "interrupted")
    setattr(session.error, marker, True)
    with pytest.raises(DeviceError) as error:
        capture(camera, settings)
    assert error.value.unhealthy and error.value.outcome == "UNKNOWN"
    with pytest.raises(DeviceError, match="recovery"):
        capture(camera, settings)
    assert session.closes == session.requests == 1


@pytest.mark.parametrize("code", ["E_CAMERA_TIMEOUT", "E_DEVICE_OPERATION_TIMEOUT", "E_DEVICE_CANCELLED"])
def testInterruptedArmedHardwareWaitRequiresSynchronizationEvenWithoutFrame(code):
    camera = MvsCapture(dict(serialNumber="TEST"), FakeMvsSession)
    settings = params("hardware")
    camera.connect(settings, lambda: False, monotonic() + 5)
    session = camera.session
    session.error = DeviceError(code, "no buffer received")
    with pytest.raises(DeviceError, match="外部触发") as error:
        capture(camera, settings)
    assert error.value.code == code and error.value.unhealthy
    assert error.value.outcome == ("CANCELLED" if code == "E_DEVICE_CANCELLED" else "UNKNOWN")
    assert len(session.starts) == session.closes == 1
    with pytest.raises(DeviceError, match="recovery"):
        capture(camera, settings)


def testCancellationOnlyFencesAnArmedHardwareStream():
    armed = Event()

    class ArmSession(FakeMvsSession):
        def start(self, settings):
            super().start(settings)
            armed.set()

    camera = MvsCapture(dict(serialNumber="TEST"), ArmSession)
    settings = params("hardware")
    with pytest.raises(DeviceError) as early:
        camera.capture(settings, lambda: True, float("inf"), 4096)
    assert not early.value.unhealthy and camera.session is None
    with pytest.raises(DeviceError) as late:
        camera.capture(settings, armed.is_set, float("inf"), 4096)
    assert late.value.code == "E_DEVICE_CANCELLED" and late.value.unhealthy
    assert camera.session is None and camera.quarantined


def testPreAdmissionCancelOnExistingStreamPreservesQueuedHardwareFrame():
    camera = MvsCapture(dict(serialNumber="TEST"), FakeMvsSession)
    settings = params("hardware")
    try:
        _, first = capture(camera, settings)
        with pytest.raises(DeviceError) as error:
            camera.capture(settings, lambda: True, float("inf"), 4096)
        assert error.value.outcome == "CANCELLED" and not error.value.unhealthy
        _, second = capture(camera, settings)
        assert second["blockId"] == first["blockId"] + 1 and second["streamGeneration"] == first["streamGeneration"]
    finally:
        camera.close()


def testFailedReleaseDuringHardwareCancellationRetainsUnknownOutcome():
    class FaultyClose(FakeMvsSession):
        def close(self):
            raise RuntimeError("close failed")

    camera = MvsCapture(dict(serialNumber="TEST"), FaultyClose)
    settings = params("hardware")
    camera.connect(settings, lambda: False, monotonic() + 5)
    camera.session.error = DeviceError("E_DEVICE_CANCELLED", "waiting")
    with pytest.raises(DeviceError, match="close failed") as error:
        capture(camera, settings)
    assert error.value.outcome == "UNKNOWN" and error.value.unhealthy


@pytest.mark.skipif(sys.platform != "win32", reason="MVS device declaration supports Windows Runtime")
def testMvsDiagnosticCapabilitiesPreviewAndRecovery(tmp_path):
    from emo_master.apps.designer.state.project_store import saveProject
    from emo_master.apps.designer.state.workflow_store import WorkflowStore
    from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb
    from emo_master.apps.runtime.grpc_server.service import RuntimeService
    from emo_master.core.project.snapshots import revisionOf

    payload = WorkflowStore().toPayload()
    payload["devices"]["definitions"]["camera"] = dict(name="Test MVS", deviceTypeId="device.camera.hikrobot_mvs",
        driverVersion="1.0.0", connection=dict(serialNumber="TEST", maxFrameBytes=4096))
    path = tmp_path / "diagnostic.emoproj"
    saveProject(path, payload)
    service = RuntimeService(dbPath=tmp_path / "runtime.db", workspaceRoot=tmp_path / "jobs")
    try:
        loaded = service.LoadProject(pb.LoadProjectRequest(project_path=str(path)), None)
        assert loaded.ok, loaded.message
        manager = service.deviceManager
        manager.drivers["camera"].apiFactory = FakeMvsSession
        identity = dict(project_id=service.loadedProjectId, runtime_instance_id=service.runtimeInstanceId,
                        device_id="camera", expected_generation=manager.executors["camera"].generation,
                        expected_definition_revision=revisionOf(manager.definitions["camera"].model_dump()))

        def diagnostic(action, **kwargs):
            return service.CameraDiagnostic(pb.CameraDiagnosticRequest(action=action, **identity, **kwargs), None)

        opened = diagnostic("open")
        assert opened.ok and json.loads(opened.metadata_json) == {"cameraTuningVersion": 0, "cameraPreviewVersion": 1}
        session = opened.session_id
        assert diagnostic("watch", session_id=session).ok and manager.drivers["camera"].process is None
        for action in ("read_parameters", "trial_capture"):
            assert not diagnostic(action, session_id=session, sequence=1, confirmed=True).ok
        preview = diagnostic("preview_capture", session_id=session, sequence=1, confirmed=True)
        assert preview.ok, preview.message
        assert preview.preview_jpeg.startswith(b"\xff\xd8")
        assert json.loads(preview.metadata_json)["acquisition"]["blockId"] == 1
        assert diagnostic("close", session_id=session).ok
        rejected = service.RecoverDevice(pb.RecoverDeviceRequest(**identity), None)
        assert not rejected.ok and "synchronized" in rejected.message
        recovered = service.RecoverDevice(pb.RecoverDeviceRequest(hardware_synchronized=True, **identity), None)
        assert recovered.ok, recovered.message
    finally:
        service.close()
