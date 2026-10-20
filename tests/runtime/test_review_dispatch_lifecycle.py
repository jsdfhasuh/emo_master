"""Real spawned consumer regressions for long runs and scoped draining."""
from copy import deepcopy
import json
from threading import Event

import pytest

from emo_master.apps.runtime.frame_dispatch.scheduler import RunGroup
from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb
from emo_master.apps.runtime.grpc_server.service import RuntimeService
from tests.runtime.runtime_test_utils import waitForTerminal
from tests.runtime.test_device_production_outcomes import waitFor
from tests.runtime.test_device_runtime import simulatedProject
from tests.runtime.test_loop_cycle_configuration import loopProject
from tests.runtime.test_project_configuration import finish


def passthrough():
    return dict(name="Scalar", inputs={"value": "number"}, outputs={"value": "number"},
        nodes=[dict(nodeId="input", kind="workflow_input"), dict(nodeId="output", kind="workflow_output")],
        edges=[dict(fromNode="input", fromPort="value", toNode="output", toPort="value")])


def addDispatch(body, *, join):
    body["nodes"].append(dict(nodeId="dispatch", kind="dispatch", inputPorts={"value": "number"}, params=dict(targets=[
        dict(targetSlotId=slot, workflowId="consumer", inputs={"value": "value"}, queueCapacity=2)
        for slot in ("left", "right")])))
    body["edges"].append(dict(fromNode="value", fromPort="value", toNode="dispatch", toPort="value"))
    if join:
        body["nodes"].append(dict(nodeId="join", kind="join", params=dict(dispatchNodeId="dispatch", timeoutMs=30000)))
        body["edges"].append(dict(fromNode="dispatch", fromPort="token", toNode="join", toPort="token"))


def repeated(body, count):
    workflow = passthrough()
    workflow["nodes"].insert(1, dict(nodeId="repeat", kind="loop", inputPorts={"value": "number"}, outputPorts={"value": "number"},
        loop=dict(contractVersion=2, mode="repeat", repeatCount=count, maxIterations=count,
                  bodyWorkflowId=body)))
    workflow["edges"] = [dict(fromNode="input", fromPort="value", toNode="repeat", toPort="value"),
        dict(fromNode="repeat", fromPort="value", toNode="output", toPort="value")]
    return workflow


def testThreeHundredSequentialJoinsWithTwoRealPersistentConsumers(tmp_path):
    payload = simulatedProject()
    payload["devices"]["definitions"] = {}
    payload["runtime"]["maxConcurrentJobs"] = 3
    body = passthrough()
    body["nodes"].append(dict(nodeId="value", operatorId="vision.value.number", params=dict(value=42)))
    addDispatch(body, join=True)
    payload["workflows"] = dict(main=repeated("body", 300), body=body, consumer=passthrough())
    payload["workflowOrder"] = ["main", "body", "consumer"]
    path = tmp_path / "project.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    service = RuntimeService(dbPath=tmp_path / "runtime.db", workspaceRoot=tmp_path / "jobs")
    try:
        loaded = service.LoadProject(pb.LoadProjectRequest(project_path=str(path)), None)
        assert loaded.ok, loaded.message
        started = service.StartJob(pb.StartJobRequest(workflow_id="main", inputs_json='{"value":42}'), None)
        assert started.ok, started.message
        assert waitForTerminal(service, started.job_id, timeoutSeconds=90).status == "COMPLETED"
        waitFor(lambda: not service.deviceManager.dispatchGroups, timeout=15)
        records = service.jobRepository.all()
        assert len(records) == 3 and len({r.pid for r in records}) == 3
        assert all(r.status == "COMPLETED" for r in records)
        assert not service.projectConfiguration.cycles and not service.projectConfiguration.childCycles
        assert not service.deviceManager.frames.frames
    finally:
        service.close()


@pytest.mark.parametrize("cancel", [False, True])
def testDrainWaitsForQueuedNoJoinConsumersAndAllowsTheirNestedIterations(tmp_path, monkeypatch, cancel):
    payload = loopProject()
    payload["runtime"]["maxConcurrentJobs"] = 3
    addDispatch(payload["workflows"]["body"], join=False)
    consumer = repeated("leaf", 2)
    consumer["nodes"][1]["loop"] = dict(contractVersion=2, mode="while", maxIterations=2,
        bodyWorkflowId="leaf", conditionWorkflowId="consumerCondition", cycleBoundary="iteration")
    leaf = passthrough()
    leaf["nodes"].append(dict(nodeId="done", operatorId="vision.value.number", params=dict(value=100)))
    leaf["edges"] = [dict(fromNode="done", fromPort="value", toNode="output", toPort="value")]
    condition = dict(name="Condition", inputs={"value": "number"}, outputs={"continue": "boolean"}, nodes=[
        dict(nodeId="input", kind="workflow_input"), dict(nodeId="output", kind="workflow_output"),
        dict(nodeId="compare", operatorId="vision.compare.number", params=dict(operator="lt", rightValue=50))], edges=[
        dict(fromNode="input", fromPort="value", toNode="compare", toPort="left"),
        dict(fromNode="compare", fromPort="result", toNode="output", toPort="continue")])
    payload["workflows"].update(consumer=consumer, leaf=leaf, consumerCondition=condition)
    payload["workflowOrder"].extend(["consumer", "leaf", "consumerCondition"])
    path = tmp_path / "project.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    service = RuntimeService(dbPath=tmp_path / "runtime.db", workspaceRoot=tmp_path / "jobs")
    submitted, releaseSource, releaseConsumers = Event(), Event(), Event()
    submit, take = RunGroup.submit, RunGroup.next
    def gatedSubmit(group, owner, request):
        result = submit(group, owner, request)
        if not submitted.is_set():
            submitted.set()
            assert releaseSource.wait(15)
        return result
    def gatedNext(group, owner):
        if not releaseConsumers.is_set():
            for slot in group.slots.values():
                if slot.jobId == owner:
                    slot.ready = True
            return dict(status="IDLE")
        return take(group, owner)
    monkeypatch.setattr(RunGroup, "submit", gatedSubmit)
    monkeypatch.setattr(RunGroup, "next", gatedNext)
    try:
        loaded = service.LoadProject(pb.LoadProjectRequest(project_path=str(path)), None)
        assert loaded.ok, loaded.message
        started = service.StartJob(pb.StartJobRequest(workflow_id="main", inputs_json='{"count":0}'), None)
        assert started.ok, started.message
        assert submitted.wait(15)
        candidate = deepcopy(service.projectConfiguration.current["payload"])
        candidate["devices"]["definitions"]["camera"]["connection"]["serial"] = "NEXT"
        service.projectConfiguration.start(dict(requestId="drain", expectedRuntimeInstanceId=service.runtimeInstanceId,
            expectedRevision=1, localPayload=candidate, selectedPaths=["/devices/definitions/camera/connection/serial"],
            replacementPolicy="drainAffected"))
        waitFor(lambda: started.job_id in service.projectConfiguration.drainingOwners)
        releaseSource.set()
        waitFor(lambda: started.job_id in service.projectConfiguration.parkedOwners)
        group = service.deviceManager.dispatchGroups[started.job_id]
        assert all(slot.queue and not slot.active for slot in group.slots.values())
        assert service.projectConfiguration.status("drain")["state"] == "WAITING"
        assert not service.jobRepository.get(started.job_id).isTerminal
        if cancel:
            service.projectConfiguration.cancel("drain")
            assert finish(service.projectConfiguration, "drain")["state"] == "CANCELLED"
            assert service.projectConfiguration.current["documentRevision"] == 1
        releaseConsumers.set()
        if not cancel:
            status = finish(service.projectConfiguration, "drain")
            assert status["state"] == "COMMITTED", status
            events = [e for r in service.jobRepository.all() for e in service._eventsAfter(r.jobId, 0)
                      if e.eventType == "dispatch.invocation.completed"]
            assert len(events) == 2
            assert not service.projectConfiguration.cycles
        assert not service.projectConfiguration.drainingOwners and not service.projectConfiguration.parkedOwners
    finally:
        releaseSource.set()
        releaseConsumers.set()
        service.close()
