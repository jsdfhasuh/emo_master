from copy import deepcopy
import json
from threading import Event
import time

import pytest

from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb
from emo_master.apps.runtime.grpc_server.service import RuntimeService
from emo_master.apps.runtime.project_config.store import ConfigurationError
from emo_master.apps.runtime.project_config.cycle_plan import CyclePlanSession
from emo_master.apps.runtime.workflow.context import RunContext
from emo_master.apps.runtime.workflow.runner import WorkflowRunner
from emo_master.core.plugin.registry import PluginRegistry
from pathlib import Path
from emo_master.core.project.models import ProjectDocument
from emo_master.core.workflow.compiler import WorkflowCompiler
from emo_master.core.workflow.errors import WorkflowCompileError
from tests.runtime.runtime_test_utils import waitForTerminal
from tests.runtime.test_device_runtime import simulatedProject
from tests.runtime.test_project_configuration import authority, request, finish
from tests.runtime.test_workflow_while_boolean import booleanPayload, _Advance


def testNestedLeasesRetainOuterPlanAndRejectForeignOrStaleParent(tmp_path):
    c = authority(tmp_path)
    try:
        root = c.beginCycle("job", 1)
        c.start(request(c))
        assert finish(c)["state"] == "COMMITTED"
        child = c.beginCycle("job", 1, parentCycleId=root["cycleId"])
        assert c.requireCycle("job", root["cycleId"]) == 1
        assert c.requireCycle("job", child["cycleId"]) == 2
        with pytest.raises(ConfigurationError, match="matching"):
            c.beginCycle("other", 1, parentCycleId=root["cycleId"])
        with pytest.raises(ConfigurationError, match="child cycles"):
            c.endCycle("job", root["cycleId"])
        c.endCycle("job", child["cycleId"])
        with pytest.raises(ConfigurationError, match="matching"):
            c.requireCycle("job", child["cycleId"])
        c.endCycle("job", root["cycleId"])
        assert not c.cycles and not c.childCycles
    finally:
        c.close()


def testCancelledCycleAdmissionDoesNotAllocateLease(tmp_path):
    c = authority(tmp_path)
    try:
        with pytest.raises(ConfigurationError):
            c.beginCycle("cancelled", 1, lambda: True)
        assert not c.cycles and not c.childCycles
    finally:
        c.close()


def testDrainFinishesNestedCyclesThenParksAtOuterIterationAndCanCancel(tmp_path):
    from threading import Thread
    c = authority(tmp_path)
    cancelled = Event()
    errors = []
    try:
        root = c.beginCycle("job", 1)
        outer = c.beginCycle("job", 1, parentCycleId=root["cycleId"])
        c.drainingOwners.add("job")
        inner = c.beginCycle("job", 1, parentCycleId=outer["cycleId"])
        assert not c.parkedOwners
        assert inner["cycleId"] == outer["cycleId"] and not inner["ownsCycle"]
        c.endCycle("job", outer["cycleId"])
        def begin():
            try:
                c.beginCycle("job", 1, cancelled.is_set, parentCycleId=root["cycleId"])
            except ConfigurationError as error:
                errors.append(error.code)
        thread = Thread(target=begin)
        thread.start()
        deadline = time.monotonic() + 3
        while "job" not in c.parkedOwners and time.monotonic() < deadline:
            time.sleep(.01)
        assert "job" in c.parkedOwners and not c.childCycles
        cancelled.set()
        thread.join(3)
        assert not thread.is_alive() and errors == ["E_CONFIG_NOT_READY"]
        c.retireOwner("job")
        assert not c.parkedOwners
    finally:
        cancelled.set()
        c.close()


def testIterationRestoresOuterCompiledPlanAndReleasesOnException(tmp_path):
    c = authority(tmp_path)
    document = ProjectDocument.model_validate(c.snapshot()["payload"])
    scan = PluginRegistry("0.6.1").scanRoots([Path(__file__).resolve().parents[2] / "src/emo_master/plugins/builtins"])
    compiler = WorkflowCompiler(scan.activeOperators, deviceTypes=scan.deviceTypes)
    compiled = compiler.compile(document)
    runner = WorkflowRunner(compiled, scan.activeOperators)
    class Proxy:
        def framework(self, action, **fields):
            return getattr(c, action)("job", **fields)
    plans = CyclePlanSession(runner, Proxy(), document, compiler, scan.activeOperators, (), 1, lambda: False)
    try:
        with plans.scope(RunContext.root("job", "main")) as root:
            c.start(request(c))
            assert finish(c)["state"] == "COMMITTED"
            with pytest.raises(RuntimeError, match="body failed"):
                with plans.iteration(root) as child:
                    assert child.planRevision == 2 and child.cycleId != root.cycleId
                    assert plans.document.devices.definitions["camera"].defaults["exposureUs"] == 12000
                    raise RuntimeError("body failed")
            assert runner.compiledProject is compiled
            assert plans.document is document and plans.revision == 1
            assert c.requireCycle("job", root.cycleId) == 1 and not c.childCycles
        assert not c.cycles
    finally:
        c.close()


@pytest.mark.parametrize("boundary", [[], {}, 1, "node", "iteration"])
def testBoundaryIsExplicitAndRequiresManagedSchema(boundary):
    payload = booleanPayload()
    payload["workflows"]["main"]["nodes"][1]["loop"]["cycleBoundary"] = boundary
    with pytest.raises(WorkflowCompileError):
        WorkflowCompiler({"test.increment": _Advance}).compile(ProjectDocument.model_validate(payload))


def loopProject(boundary="iteration"):
    payload = simulatedProject()
    body = payload["workflows"].pop("main")
    body.update(name="Body", inputs={"count": "number"}, outputs={"count": "number"})
    body["edges"] = [dict(fromNode="input", fromPort="count", toNode="output", toPort="count")]
    body["nodes"].append(dict(nodeId="value", operatorId="vision.value.number", params=dict(value=12)))
    payload["workflowOrder"] = ["main", "condition", "body"]
    payload["workflows"] = {
        "main": dict(name="Main", inputs={"count": "number"}, outputs={"count": "number"}, nodes=[
            dict(nodeId="input", kind="workflow_input"),
            dict(nodeId="loop", kind="loop", inputPorts={"count": "number"}, outputPorts={"count": "number"},
                loop=dict(contractVersion=2, mode="while", unlimited=True,
                maxIterations=10, timeoutMs=0, bodyWorkflowId="body", conditionWorkflowId="condition", cycleBoundary=boundary)),
            dict(nodeId="output", kind="workflow_output")], edges=[
                dict(fromNode="input", fromPort="count", toNode="loop", toPort="count"),
                dict(fromNode="loop", fromPort="count", toNode="output", toPort="count")]),
        "condition": dict(name="Condition", inputs={"count": "number"}, outputs={"continue": "boolean"}, nodes=[
            dict(nodeId="input", kind="workflow_input"),
            dict(nodeId="compare", operatorId="vision.compare.number", params=dict(operator="lt", rightValue=1)),
            dict(nodeId="output", kind="workflow_output")], edges=[
                dict(fromNode="input", fromPort="count", toNode="compare", toPort="left"),
                dict(fromNode="compare", fromPort="result", toNode="output", toPort="continue")]),
        "body": body,
    }
    return payload


def testRealSpawnWhileSwitchesOnlyBeforeConditionAndRetiresFalseIteration(tmp_path):
    path = tmp_path / "project.emoproj"
    path.write_text(json.dumps(loopProject()), encoding="utf-8")
    service = RuntimeService(dbPath=tmp_path / "runtime.db", workspaceRoot=tmp_path / "jobs")
    entered, release = Event(), Event()
    try:
        loaded = service.LoadProject(pb.LoadProjectRequest(project_path=str(path)), None)
        assert loaded.ok, loaded.message
        driver = service.deviceManager.drivers["camera"]
        execute = driver.execute
        def blockFirst(*args, **kwargs):
            if driver.sequence == 0:
                entered.set()
                assert release.wait(10)
            return execute(*args, **kwargs)
        driver.execute = blockFirst
        started = service.StartJob(pb.StartJobRequest(project_id=service.loadedProjectId, workflow_id="main",
            inputs_json='{"count":0}'), None)
        assert started.ok, started.message
        assert entered.wait(10)
        coordinator = service.projectConfiguration

        def write(requestId, workflow, nodeId, field, value):
            snapshot = coordinator.snapshot()
            local = deepcopy(snapshot["payload"])
            node = next(n for n in local["workflows"][workflow]["nodes"] if n["nodeId"] == nodeId)
            node["params"][field] = value
            coordinator.start(dict(requestId=requestId, expectedRuntimeInstanceId=service.runtimeInstanceId,
                expectedRevision=snapshot["documentRevision"], localPayload=local,
                selectedPaths=[f"/workflows/{workflow}/nodes/{nodeId}/params/{field}"]))
            assert finish(coordinator, requestId)["state"] == "COMMITTED"

        write("body-change", "body", "value", "value", 34)
        release.set()
        def events():
            return [(event, json.loads(event.payloadJson)) for event in service._eventsAfter(started.job_id, 0)
                    if event.eventType == "node.completed" and event.nodeId in {"compare", "value"}]
        deadline = time.monotonic() + 10
        while not any(data.get("outputs", {}).get("value") == 34 for _, data in events()) and time.monotonic() < deadline:
            time.sleep(.02)
        values = [(event, data) for event, data in events() if event.nodeId == "value"]
        assert {12, 34} <= {data["outputs"]["value"] for _, data in values}
        assert all(data["outputs"]["value"] == (12 if data["planRevision"] == 1 else 34) for _, data in values)
        write("end-loop", "condition", "compare", "rightValue", 0)
        assert waitForTerminal(service, started.job_id, timeoutSeconds=10).status == "COMPLETED"
        allEvents = events()
        conditions = {data["cycleId"]: data for event, data in allEvents if event.nodeId == "compare"}
        for event, data in allEvents:
            if event.nodeId == "value":
                assert conditions[data["cycleId"]]["planRevision"] == data["planRevision"]
                assert conditions[data["cycleId"]]["outputs"]["result"] is True
        assert any(data["planRevision"] == 3 and data["outputs"]["result"] is False for data in conditions.values())
        deadline = time.monotonic() + 5
        while coordinator.cycles and time.monotonic() < deadline:
            service.jobSupervisor.finishRetirements()
            time.sleep(.01)
        assert not coordinator.cycles and not coordinator.childCycles
    finally:
        release.set()
        service.close()
