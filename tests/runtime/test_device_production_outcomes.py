"""Synthetic continuous-production results remain distinct from device faults."""
import json
from threading import Event
import time

from emo_master.apps.runtime.devices.executor import DeviceError
from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb
from emo_master.apps.runtime.grpc_server.service import RuntimeService
from tests.runtime.test_device_runtime import simulatedProject


def waitFor(predicate, timeout=15):
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        time.sleep(.01)
    assert predicate()


def testRepeatedNgContinuesButDeviceFaultStopsWithoutReplay(tmp_path):
    payload = simulatedProject()
    payload["production"].update(mode="continuous", cycleIntervalMs=20)
    flow = payload["workflows"]["main"]
    flow["outputs"]["accepted"] = "boolean"
    flow["nodes"].extend([
        dict(nodeId="measurement", operatorId="vision.value.number", params={"value": 0}),
        dict(nodeId="decision", operatorId="vision.compare.number", params={"operator": "gte", "rightValue": 1}),
    ])
    flow["edges"].extend([
        dict(fromNode="measurement", fromPort="value", toNode="decision", toPort="left"),
        dict(fromNode="decision", fromPort="result", toNode="output", toPort="accepted"),
    ])
    path = tmp_path / "project.emoproj"
    path.write_text(json.dumps(payload), encoding="utf-8")
    service = RuntimeService(dbPath=tmp_path / "runtime.db", workspaceRoot=tmp_path / "jobs")
    fail = Event()
    try:
        loaded = service.LoadProject(pb.LoadProjectRequest(project_path=str(path)), None)
        assert loaded.ok, loaded.message
        service.productionMode = True
        driver = service.deviceManager.drivers["camera"]
        execute = driver.execute
        failedCalls = []
        def controlled(action, params, cancelled, deadline):
            if fail.is_set():
                failedCalls.append(action)
                raise DeviceError("E_TEST_DEVICE_FAULT", "injected synthetic device fault", outcome="UNKNOWN", unhealthy=True)
            return execute(action, params, cancelled, deadline)
        driver.execute = controlled
        started = service.StartJob(pb.StartJobRequest(project_id=service.loadedProjectId, workflow_id="main"), None)
        assert started.ok, started.message
        def decisions():
            return [json.loads(event.payloadJson) for event in service._eventsAfter(started.job_id, 0)
                    if event.eventType == "node.completed" and event.nodeId == "decision"]
        waitFor(lambda: len(decisions()) >= 3)
        assert all(result["outputs"]["result"] is False for result in decisions())
        assert service.jobRepository.get(started.job_id).status == "RUNNING"
        assert not service.deviceManager.executors["camera"].quarantined
        fail.set()
        waitFor(lambda: service.jobRepository.get(started.job_id).isTerminal)
        record = service.jobRepository.get(started.job_id)
        assert record.status == "FAILED" and record.errorCode == "E_TEST_DEVICE_FAULT"
        waitFor(lambda: not service.deviceManager.workers)
        assert failedCalls == ["capture"]
        assert service.deviceManager.executors["camera"].quarantined
        assert not service.deviceManager.frames.frames and not service.deviceManager.frames.reservations
        assert len(service.jobRepository.all()) == 1
    finally:
        service.close()
