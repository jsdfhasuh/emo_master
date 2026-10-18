from copy import deepcopy
import json
from threading import Event, Thread
import time

import pytest

from emo_master.apps.runtime.devices.executor import DeviceError, DeviceExecutor
from emo_master.apps.runtime.devices.manager import DeviceManager
from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb
from emo_master.apps.runtime.grpc_server.service import RuntimeService
from emo_master.core.plugin.registry import PluginRegistry
from emo_master.core.project.models import ProjectDocument
from tests.runtime.test_device_production_outcomes import waitFor
from tests.runtime.test_managed_imv import ROOT, TimeoutImv, cameraProject


def testExecutorUnlimitedWaitRequiresExplicitCancellationContract():
    executor = DeviceExecutor("test")
    entered, cancelled = Event(), Event()
    def wait(stop, deadline, generation):
        assert deadline == float("inf")
        entered.set()
        while not stop():
            time.sleep(.005)
        cancelled.set()
    try:
        with pytest.raises(DeviceError, match="budgets"):
            executor.submit("a", wait, operationTimeout=float("inf"))
        operation = executor.submit("a", wait, operationTimeout=float("inf"), cancellableWait=True)
        assert entered.wait(2)
        queued = executor.submit("b", lambda *_: pytest.fail("expired queue must not run"), waitTimeout=.03)
        with pytest.raises(DeviceError) as expired:
            executor.wait(queued)
        assert expired.value.code == "E_DEVICE_WAIT_TIMEOUT"
        assert not operation.future.done()
        executor.cancel(operation)
        assert cancelled.wait(2)
        operation.future.result(2)
    finally:
        executor.close()


def testDeclarationRejectsUnlimitedSoftwareAndFreeRunBeforeAdmission():
    scan = PluginRegistry("0.6.1").scan(ROOT)
    declaration = scan.deviceTypes["device.camera.huaray_imv"]
    document = ProjectDocument.model_validate(cameraProject("hardware"))
    definition = declaration.validateDefinition(document.devices.definitions["camera"])
    for mode in ("software", "freeRun"):
        with pytest.raises(ValueError, match="unlimited wait requires"):
            declaration.validateNodeSources(definition, dict(triggerMode=mode, captureTimeoutMs=0), {})
    params = declaration.validateNodeSources(definition, dict(captureTimeoutMs=0), {})
    assert declaration.isUnlimitedWait("capture", params)
    assert not declaration.isUnlimitedWait("connect", params)


def testRealHostUnlimitedWaitKeepsQueueBoundedAndCancelsWithoutReplay():
    scan = PluginRegistry("0.6.1").scan(ROOT)
    payload = cameraProject("hardware")
    payload["devices"]["definitions"]["camera"]["defaults"]["captureTimeoutMs"] = 0
    manager = DeviceManager(scan.deviceTypes)
    manager.load(ProjectDocument.model_validate(payload))
    driver = manager.drivers["camera"]
    driver.apiFactory = TimeoutImv
    cancelled = Event()
    errors = []
    thread = None
    try:
        manager.authorize("a", ["camera"])
        manager.authorize("b", ["camera"])
        executor = manager.executors["camera"]
        lifecycle = manager.lifecycles["camera"]
        connect = executor.submit("test-connect", lambda cancel, deadline, _: lifecycle.connect(cancel, deadline))
        executor.wait(connect)
        manager.definitions["camera"].policies.update(operationTimeoutMs=50, deviceWaitTimeoutMs=50)
        params = deepcopy(manager.definitions["camera"].defaults)
        def capture():
            try:
                manager.execute("a", "camera", "capture", params, cycleId="a", cancelled=cancelled.is_set)
            except DeviceError as error:
                errors.append(error)
        thread = Thread(target=capture)
        thread.start()
        waitFor(lambda: driver.sequence == 2)
        time.sleep(.15)
        assert thread.is_alive() and not errors
        with pytest.raises(DeviceError) as queued:
            manager.execute("b", "camera", "capture", params, cycleId="b")
        assert queued.value.code == "E_DEVICE_WAIT_TIMEOUT"
        assert driver.sequence == 2
        cancelled.set()
        thread.join(5)
        assert not thread.is_alive()
        assert errors and errors[0].code == "E_DEVICE_CANCELLED"
        waitFor(lambda: executor.active is None)
        assert not manager.frames.reservations and not manager.frames.frames
        assert driver.sequence == 2 and driver.process is None
    finally:
        cancelled.set()
        if thread is not None:
            thread.join(5)
        manager.close()


def testSpawnedJobStopCancelsUnlimitedHardwareWaitAndRetiresHost(tmp_path):
    payload = cameraProject("hardware")
    payload["devices"]["definitions"]["camera"]["defaults"]["captureTimeoutMs"] = 0
    path = tmp_path / "project.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    service = RuntimeService(dbPath=tmp_path / "runtime.db", workspaceRoot=tmp_path / "jobs")
    try:
        loaded = service.LoadProject(pb.LoadProjectRequest(project_path=str(path)), None)
        assert loaded.ok, loaded.message
        driver = service.deviceManager.drivers["camera"]
        driver.apiFactory = TimeoutImv
        started = service.StartJob(pb.StartJobRequest(workflow_id="main"), None)
        assert started.ok, started.message
        waitFor(lambda: driver.sequence == 2, timeout=30)
        assert service.jobRepository.get(started.job_id).status == "RUNNING"
        assert service.StopJob(pb.StopJobRequest(job_id=started.job_id, mode="graceful"), None).ok
        waitFor(lambda: not service.jobSupervisor.ownsJobResources(started.job_id), timeout=30)
        record = service.jobRepository.get(started.job_id)
        # Stopping the IPC can lose the native receipt. Keep that uncertainty;
        # successful retirement does not prove that no external trigger occurred.
        assert (record.status, record.errorCode) in {
            ("ABORTED", "E_CANCELLED"), ("FAILED", "E_DEVICE_CANCELLED"),
            ("FAILED", "E_DEVICE_OUTCOME_UNKNOWN")}, record
        if record.status == "FAILED":
            events = [json.loads(event.payloadJson) for event in service._eventsAfter(started.job_id, 0)
                      if event.eventType == "node.failed"]
            assert events[-1]["diagnostics"]["deviceOutcome"]["outcome"] == "UNKNOWN"
        assert driver.sequence == 2 and driver.process is None
        assert not service.deviceManager.frames.reservations and not service.projectConfiguration.cycles
    finally:
        service.close()
