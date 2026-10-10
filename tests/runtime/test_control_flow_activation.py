from copy import deepcopy
from types import SimpleNamespace

import pytest

from emo_master.apps.runtime.workflow.cancellation import CancellationToken
from emo_master.apps.runtime.workflow.context import RunContext
from emo_master.apps.runtime.workflow.runner import WorkflowExecutionError, WorkflowRunner
from emo_master.core.workflow.compiler import WorkflowCompiler
from emo_master.core.contracts.port_types import normalizePortType
from emo_master.plugins.builtins.flow_if.operator import FlowIfOperator
from emo_master.plugins.builtins.flow_switch.operator import FlowSwitchOperator


class Probe:
    calls = []

    def executeNode(self, inputs, params, runtimeContext):
        self.calls.append((runtimeContext["workflowId"], deepcopy(inputs)))
        return {"status": "ok", "outputs": {"result": inputs["data"]}}


def program(branch="if", target="subflow", optional=False):
    gate = {"type": "boolean", "required": not optional}
    body = {"name": "Body", "inputs": {"gate": gate, "data": "string"},
            "outputs": {"result": "string"}, "nodes": [
                {"nodeId": "input", "kind": "workflow_input"},
                {"nodeId": "probe", "operatorId": "test.probe", "inputPorts": {"data": "string"},
                 "outputPorts": {"result": "string"}},
                {"nodeId": "output", "kind": "workflow_output"}], "edges": [
                edge("input", "data", "probe", "data"),
                edge("probe", "result", "output", "result")]}
    work = {"nodeId": "work", "kind": target}
    if target == "operator":
        work.update(operatorId="test.probe", inputPorts=deepcopy(body["inputs"]), outputPorts=body["outputs"])
    elif target == "subflow":
        work["targetWorkflowId"] = "body"
    else:
        work.update(inputPorts={"gate": "boolean", "data": "string"}, outputPorts=body["outputs"],
                    loop={"contractVersion": 2, "mode": "repeat", "bodyWorkflowId": "body",
                          "repeatCount": 1, "maxIterations": 10, "timeoutMs": 0})
    branchNode = {"nodeId": "branch", "operatorId": f"vision.flow.{branch}", "params": (
        {"mode": "bool"} if branch == "if" else {"case0Value": "True", "case1Value": "False"})}
    main = {"name": "Main", "inputs": {"condition": "boolean", "data": "string"},
            "outputs": {"result": {"type": "string", "required": False}},
            "nodes": [{"nodeId": "input", "kind": "workflow_input"}, branchNode, work,
                      {"nodeId": "output", "kind": "workflow_output"}],
            "edges": [edge("input", "condition", "branch", "value"),
                      edge("branch", "false" if branch == "if" else "case1", "work", "gate"),
                      edge("input", "data", "work", "data"), edge("work", "result", "output", "result")]}
    return {"schemaVersion": "2.1", "project": {"projectId": "branch-safety", "name": "Branch Safety",
            "createdAt": "2026-10-09T00:00:00Z", "updatedAt": "2026-10-09T00:00:00Z"},
            "entryWorkflowId": "main", "workflowOrder": ["main", "body"],
            "workflows": {"main": main, "body": body}}


def edge(source, sourcePort, target, targetPort):
    return {"fromNode": source, "fromPort": sourcePort, "toNode": target, "toPort": targetPort}


def runner(payload, extraRegistry=None):
    payload = deepcopy(payload)
    registry = {"vision.flow.if": FlowIfOperator, "vision.flow.switch": FlowSwitchOperator, "test.probe": Probe}
    registry.update(extraRegistry or {})
    # Saved node caches contain type strings; descriptors come from the registry.
    for workflowId, workflow in payload["workflows"].items():
        for node in workflow["nodes"]:
            if node.get("operatorId") != "test.probe":
                continue
            operatorId = f"test.probe.{workflowId}.{node['nodeId']}"
            inputs = deepcopy(node["inputPorts"])
            registry[operatorId] = type("RegisteredProbe", (Probe,), {"meta": SimpleNamespace(
                inputPorts=inputs, outputPorts=deepcopy(node["outputPorts"]))})
            node["operatorId"] = operatorId
            node["inputPorts"] = {name: normalizePortType(spec) for name, spec in inputs.items()}
    events = []
    return WorkflowRunner(WorkflowCompiler(registry).compile(payload), registry,
                          eventPublisher=lambda **event: events.append(event)), events


def execute(executor, condition=True):
    return executor.run("main", {"condition": condition, "data": "shared"},
                        RunContext.root("job", "main"), CancellationToken()).outputs


@pytest.mark.parametrize("branch", ["if", "switch"])
@pytest.mark.parametrize("target", ["operator", "subflow", "loop"])
@pytest.mark.parametrize("optional", [False, True])
@pytest.mark.parametrize("selected", [False, True])
def testUnselectedBranchCannotBeActivatedBySharedData(branch, target, optional, selected):
    Probe.calls.clear()
    executor, events = runner(program(branch, target, optional))
    assert execute(executor, condition=not selected) == ({"result": "shared"} if selected else {})
    assert len(Probe.calls) == int(selected)
    workEvents = [e["eventType"] for e in events if e["context"].workflowId == "main"
                  and e["context"].callerNodeId == "work"]
    if not selected:
        assert workEvents == ["node.skipped"]
        skipped = next(e for e in events if e["eventType"] == "node.skipped")
        assert skipped["payload"]["code"] == "E_BRANCH_NOT_SELECTED"
        assert not any(e["eventType"] == "workflow.started" and e["context"].workflowId == "body" for e in events)


def testSkipPropagatesDespiteSharedInputsAndIsInvocationLocal():
    payload = program(target="operator", optional=True)
    main = payload["workflows"]["main"]
    main["nodes"].insert(-1, {"nodeId": "after", "operatorId": "test.probe",
        "inputPorts": {"gate": {"type": "string", "required": False}, "data": "string"},
        "outputPorts": {"result": "string"}})
    main["edges"][-1] = edge("after", "result", "output", "result")
    main["edges"].extend([edge("work", "result", "after", "gate"), edge("input", "data", "after", "data")])
    executor, events = runner(payload)
    for condition, count in [(True, 0), (False, 2), (True, 0)]:
        Probe.calls.clear()
        assert execute(executor, condition) == ({"result": "shared"} if count else {})
        assert len(Probe.calls) == count
    assert sum(e["eventType"] == "node.skipped" and e["context"].callerNodeId == "after" for e in events) == 2


class NoOutput:
    def executeNode(self, inputs, params, runtimeContext):
        return {"status": "ok", "outputs": {}}


def testOrdinaryMissingOptionalInputDoesNotSuppressExecution():
    payload = program(target="operator", optional=True)
    branch = payload["workflows"]["main"]["nodes"][1]
    branch.update(operatorId="test.no-output", inputPorts={"value": "object"}, outputPorts={"false": "boolean"})
    executor, _events = runner(payload, {"test.no-output": NoOutput})
    Probe.calls.clear()
    assert execute(executor) == {"result": "shared"}
    assert len(Probe.calls) == 1


def testOrdinaryMissingRequiredInputStillFails():
    payload = program(target="subflow")
    payload["workflows"]["main"]["edges"] = [
        e for e in payload["workflows"]["main"]["edges"] if e["toPort"] != "gate"]
    executor, _events = runner(payload)
    with pytest.raises(WorkflowExecutionError) as error:
        execute(executor)
    assert error.value.code == "E_INPUT_MISSING"


@pytest.mark.parametrize("condition", [True, False])
def testWorkflowOutputCollectsSelectedAlternativeWithoutDiscardingIt(condition):
    payload = program(target="operator")
    main = payload["workflows"]["main"]
    main["nodes"] = [n for n in main["nodes"] if n["nodeId"] != "work"]
    main["outputs"] = {p: {"type": "boolean", "required": False} for p in ("true", "false")}
    main["edges"] = [edge("input", "condition", "branch", "value")]
    main["edges"].extend(edge("branch", p, "output", p) for p in ("true", "false"))
    executor, _events = runner(payload)
    assert execute(executor, condition) == {"true" if condition else "false": condition}


@pytest.mark.parametrize("condition", [True, False])
def testOptionalAlternativeInputsCanRejoinSameBranchDecision(condition):
    payload = program(target="operator", optional=True)
    main = payload["workflows"]["main"]
    work = main["nodes"][2]
    work["inputPorts"]["alternative"] = {"type": "boolean", "required": False}
    main["edges"].append(edge("branch", "true", "work", "alternative"))
    Probe.calls.clear()
    executor, _events = runner(payload)
    assert execute(executor, condition) == {"result": "shared"}
    assert len(Probe.calls) == 1


def testUnrelatedSelectedBranchDoesNotOverrideAnInactiveGate():
    payload = program(target="operator", optional=True)
    main = payload["workflows"]["main"]
    main["nodes"].insert(2, {"nodeId": "other", "operatorId": "vision.flow.if", "params": {"mode": "bool"}})
    main["nodes"][3]["inputPorts"]["alternative"] = {"type": "boolean", "required": False}
    main["edges"].extend([edge("input", "condition", "other", "value"), edge("other", "true", "work", "alternative")])
    Probe.calls.clear()
    executor, _events = runner(payload)
    assert execute(executor) == {}
    assert Probe.calls == []
