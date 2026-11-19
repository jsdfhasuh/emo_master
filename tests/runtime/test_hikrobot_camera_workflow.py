from functools import partial
import multiprocessing
from queue import Empty
import sys
from time import monotonic, sleep

import cv2
import numpy as np
import pytest

from emo_master.apps.designer.state.project_store import loadProjectDocument, saveProject
from emo_master.apps.designer.state.workflow_store import WorkflowStore
from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb
from emo_master.apps.runtime.grpc_server.service import RuntimeService
from emo_master.core.project.models import ProjectDocument
from emo_master.core.project.snapshots import revisionOf
from tests.runtime.runtime_test_utils import waitForTerminal
from tests.runtime.test_managed_imv import FakeImv


class FakeMvsSession:
    """Spawnable SDK substitute producing a different BGR image on each trigger."""

    def __init__(self, serialNumber):
        assert serialNumber == "MVS-FIXTURE"
        self.sequence = 0
        self.grabbing = False

    def connect(self):
        pass

    def start(self, params):
        assert params["triggerMode"] == "software"
        self.grabbing = True

    def capture(self, params, check, capacity, timeoutMs):
        check()
        assert self.grabbing and params["outputColor"] == "bgr"
        self.sequence += 1
        image = np.full((3, 5, 3), (self.sequence, 72, 154), dtype=np.uint8)
        assert image.nbytes <= capacity
        return image, dict(blockId=self.sequence, deviceTimestamp=200 + self.sequence,
                           actualExposureUs=6500.0, offsetX=4, offsetY=8)

    def stop(self):
        self.grabbing = False

    def close(self):
        self.stop()


def loadCaptureProject(service, tmp_path, definitions):
    path = tmp_path / "mvs.emoproj"
    scan = service.pluginScanResult
    camera = scan.activeOperators["vision.io.huaray_camera"].manifest
    saver = scan.activeOperators["vision.io.image_saver"].manifest
    payload = WorkflowStore().toPayload("MVS workflow test")
    payload["devices"]["definitions"] = definitions
    payload["dependencies"]["operators"] = [dict(operatorId=m.operatorId, version=m.version) for m in (camera, saver)]
    flow = payload["workflows"]["main"]
    for deviceId, definition in definitions.items():
        assert camera.deviceRequirements["camera"].acceptsDeclaration(scan.deviceTypes[definition["deviceTypeId"]])
        captureId, saveId = "capture-" + deviceId, "save-" + deviceId
        flow["nodes"].extend([
            dict(nodeId=captureId, operatorId=camera.operatorId, deviceBindings={"camera": {"deviceId": deviceId}}),
            dict(nodeId=saveId, operatorId=saver.operatorId, params={"outputPath": str(tmp_path / f"{deviceId}.png")}),
        ])
        flow["edges"].append(dict(fromNode=captureId, fromPort="image", toNode=saveId, toPort="image"))
    document = ProjectDocument.model_validate(payload)
    saveProject(path, document.model_dump(mode="json"))
    assert loadProjectDocument(path) == document
    loaded = service.LoadProject(pb.LoadProjectRequest(project_path=str(path)), None)
    assert loaded.ok, loaded.message
    validated = service.ValidateProject(pb.ValidateProjectRequest(project_id=service.loadedProjectId), None)
    assert validated.ok, validated.errors


def startJob(service):
    job = service.StartJob(pb.StartJobRequest(project_id=service.loadedProjectId, workflow_id="main"), None)
    assert job.ok, job.message
    return job.job_id


@pytest.mark.skipif(sys.platform != "win32", reason="MVS declaration requires a Windows Runtime")
def testSameOperatorCapturesBothBrandsAcrossSpawnedJobs(tmp_path):
    service = RuntimeService(dbPath=tmp_path / "runtime.db", workspaceRoot=tmp_path / "jobs")
    try:
        loadCaptureProject(service, tmp_path, {
            "camera": dict(name="Fixture MVS", deviceTypeId="device.camera.hikrobot_mvs", driverVersion="1.0.0",
                           connection=dict(serialNumber="MVS-FIXTURE", maxFrameBytes=1024)),
            "imv": dict(name="Fixture IMV", deviceTypeId="device.camera.huaray_imv", driverVersion="1.0.0",
                        connection=dict(cameraKey="IMV-FIXTURE", maxFrameBytes=1024),
                        defaults=dict(triggerMode="software", outputColor="gray")),
        })
        driver = service.deviceManager.drivers["camera"]
        assert driver.process is None
        driver.apiFactory = FakeMvsSession
        service.deviceManager.drivers["imv"].apiFactory = FakeImv
        for expected in (1, 2):
            result = waitForTerminal(service, startJob(service), timeoutSeconds=30)
            assert result.status == "COMPLETED", result
            saved = cv2.imdecode(np.fromfile(tmp_path / "camera.png", np.uint8), cv2.IMREAD_UNCHANGED)
            np.testing.assert_array_equal(saved, np.full((3, 5, 3), (expected, 72, 154), np.uint8))
            gray = cv2.imdecode(np.fromfile(tmp_path / "imv.png", np.uint8), cv2.IMREAD_UNCHANGED)
            np.testing.assert_array_equal(gray, np.full((3, 4), 17, np.uint8))
    finally:
        service.close()


class HardwareLineSession(FakeMvsSession):
    """External pulses are received only while the simulated SDK is armed."""
    def __init__(self, pulses, armed, serialNumber):
        super().__init__(serialNumber)
        self.pulses, self.armed = pulses, armed

    def start(self, params):
        assert params["triggerMode"] == "hardware"
        self.armed.set()

    def capture(self, params, check, capacity, timeoutMs):
        assert timeoutMs is None
        while True:
            check()
            try:
                tag = self.pulses.get(timeout=.01)
            except Empty:
                continue
            return np.full((3, 5, 3), tag, np.uint8), dict(blockId=tag, deviceTimestamp=tag, actualExposureUs=1000,
                                                           offsetX=0, offsetY=0)

    def stop(self):
        self.armed.clear()


@pytest.mark.skipif(sys.platform != "win32", reason="MVS declaration requires a Windows Runtime")
def testCancelledHardwareJobCannotPassALateTriggerToAnotherJob(tmp_path):
    ctx = multiprocessing.get_context("spawn")
    pulses, armed = ctx.Queue(), ctx.Event()
    substitute = partial(HardwareLineSession, pulses, armed)
    output = tmp_path / "camera.png"
    service = RuntimeService(dbPath=tmp_path / "runtime.db", workspaceRoot=tmp_path / "jobs")
    try:
        loadCaptureProject(service, tmp_path, {
            "camera": dict(name="Hardware MVS", deviceTypeId="device.camera.hikrobot_mvs", driverVersion="1.0.0",
                           connection=dict(serialNumber="MVS-FIXTURE", maxFrameBytes=1024),
                           defaults=dict(triggerMode="hardware", captureTimeoutMs=0)),
        })
        manager = service.deviceManager
        driver = manager.drivers["camera"]
        driver.apiFactory = substitute
        first = startJob(service)
        assert armed.wait(20)
        stopped = service.StopJob(pb.StopJobRequest(job_id=first, mode="graceful"), None)
        assert stopped.ok, stopped.message
        assert waitForTerminal(service, first, timeoutSeconds=20).status == "ABORTED"
        executor = manager.executors["camera"]
        with executor.condition:
            assert executor.condition.wait_for(lambda: executor.active is None, timeout=10)
        if armed.is_set():
            pulses.put(100)  # Old workpiece pulse while the previous SDK stream is still armed.
        blocked = waitForTerminal(service, startJob(service), timeoutSeconds=20)
        assert blocked.status == "FAILED", blocked
        assert not output.exists() and executor.quarantined and driver.process is None

        # Stop the external source and acknowledge its synchronization before
        # rearming. Replacing the Host alone cannot prevent a future late pulse.
        identity = dict(project_id=service.loadedProjectId, runtime_instance_id=service.runtimeInstanceId,
                        device_id="camera", expected_generation=executor.generation,
                        expected_definition_revision=revisionOf(manager.definitions["camera"].model_dump()))
        refused = service.RecoverDevice(pb.RecoverDeviceRequest(**identity), None)
        assert not refused.ok and "synchronized" in refused.message
        until = monotonic() + 10
        while manager.permissions and monotonic() < until:
            sleep(.01)
        recovered = service.RecoverDevice(pb.RecoverDeviceRequest(**identity, hardware_synchronized=True), None)
        assert recovered.ok, recovered.message
        manager.drivers["camera"].apiFactory = substitute
        new = startJob(service)
        assert armed.wait(20)
        pulses.put(101)
        assert waitForTerminal(service, new, timeoutSeconds=20).status == "COMPLETED"
        saved = cv2.imdecode(np.fromfile(output, np.uint8), cv2.IMREAD_UNCHANGED)
        np.testing.assert_array_equal(saved, np.full((3, 5, 3), 101, np.uint8))
    finally:
        service.close()
        pulses.close()
