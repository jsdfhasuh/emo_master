import json
import time

import grpc
import pytest

from emo_master.apps.designer.state.workflow_store import WorkflowStore
from emo_master.apps.designer.services.runtime_client import RuntimeClient
from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb
from emo_master.apps.runtime.grpc_server.service import RuntimeService
from emo_master.apps.runtime.grpc_server.generated import runtime_pb2_grpc
from emo_master.apps.runtime.main import createRuntimeServer
from emo_master.core.project.migration import migrateProjectPayload
from tests.runtime.runtime_test_utils import waitForTerminal


def simulatedProject():
    payload = migrateProjectPayload(WorkflowStore().toPayload(), enableDevices=True)
    payload["devices"]["definitions"]["camera"] = dict(name="Simulated", deviceTypeId="device.simulated.camera",
        driverVersion="1.0.0", connection={"serial": "TEST-ONLY"}, defaults={"width": 7, "height": 5,
        "exposureUs": 8000, "gain": 0})
    workflow = payload["workflows"]["main"]
    workflow["outputs"] = {"image": "image"}
    workflow["nodes"] = [dict(nodeId="input", kind="workflow_input"),
        dict(nodeId="capture", operatorId="vision.io.simulated_camera",
             deviceBindings={"camera": {"deviceId": "camera"}}),
        dict(nodeId="output", kind="workflow_output")]
    workflow["edges"] = [dict(fromNode="capture", fromPort="image", toNode="output", toPort="image")]
    return payload


def testTwoSpawnedJobsShareRuntimeDeviceAndReleaseOriginals(tmp_path):
    payload = simulatedProject()
    path = tmp_path / "project.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    service = RuntimeService(dbPath=tmp_path / "runtime.db", workspaceRoot=tmp_path / "jobs")
    try:
        assert "vision.io.simulated_camera" in service.pluginScanResult.activeOperators, service.pluginScanResult.rejectedOperators
        reply = service.LoadProject(pb.LoadProjectRequest(project_path=str(path)), None)
        assert reply.ok, reply.message
        validate = service.ValidateProject(pb.ValidateProjectRequest(project_id=service.loadedProjectId), None)
        assert validate.ok, validate.errors
        started = [service.StartJob(pb.StartJobRequest(project_id=service.loadedProjectId, workflow_id="main"), None)
                   for _ in range(2)]
        assert all(reply.ok for reply in started), started
        for reply in started:
            result = waitForTerminal(service, reply.job_id, timeoutSeconds=30)
            assert result.status == "COMPLETED", result
        deadline = time.monotonic() + 5
        while service.deviceManager.workers and time.monotonic() < deadline:
            service.jobSupervisor.finishRetirements()
            time.sleep(.02)
        assert service.deviceManager.drivers["camera"].sequence == 2
        assert not service.deviceManager.frames.frames
        assert not service.deviceManager.frames.reservations
        assert not service.deviceManager.workers
        state = service.GetDeviceStates(pb.GetDeviceStatesRequest(project_id=service.loadedProjectId), None)
        assert state.ok
        assert json.loads(state.states_json)["camera"]["state"] == "IDLE"
    finally:
        service.close()


def testDeviceMetadataRemoteContractAndProjectFence(tmp_path):
    service = RuntimeService(dbPath=tmp_path / "runtime.db", workspaceRoot=tmp_path / "jobs")
    server, port, _ = createRuntimeServer(port=0, runtimeService=service)
    server.start()
    channel = grpc.insecure_channel(f"127.0.0.1:{port}")
    try:
        grpc.channel_ready_future(channel).result(timeout=5)
        client = RuntimeClient(runtime_pb2_grpc.RuntimeServiceStub(channel))
        types, requirements, _ = client.listDeviceTypes()
        assert "device.simulated.camera" in types
        assert "vision.io.simulated_camera" in requirements
        with pytest.raises(ValueError, match="Runtime"):
            client.getDeviceStates("unloaded")
        assert not service.deviceManager.drivers
    finally:
        channel.close()
        server.stop(0).wait()
        service.close()


def testDeviceMetadataThroughRuntimeClientHasNoHardwareSideEffects(tmp_path):
    service = RuntimeService(dbPath=tmp_path / "runtime.db", workspaceRoot=tmp_path / "jobs")
    try:
        client = RuntimeClient(runtimeService=service)
        types, requirements, rejected = client.listDeviceTypes()
        assert "device.simulated.camera" in types
        assert requirements["vision.io.simulated_camera"]["camera"].deviceTypeId == "device.simulated.camera"
        assert not rejected
        operators = client.listOperators()
        simulated = next(op for op in operators if op.operatorId == "vision.io.simulated_camera")
        assert simulated.deviceRequirements == {"camera": {"deviceTypeId": "device.simulated.camera"}}
        assert not service.deviceManager.drivers and not service.deviceManager.executors
    finally:
        service.close()
