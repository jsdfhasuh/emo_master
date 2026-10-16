from copy import deepcopy

import pytest

from emo_master.apps.runtime.workflow.cancellation import CancellationRequested, CancellationToken
from emo_master.apps.runtime.workflow.context import RunContext
from emo_master.apps.runtime.workflow.loop_runner import LoopExecutionError
from emo_master.apps.runtime.workflow.runner import WorkflowRunner
from emo_master.core.project.models import ProjectDocument
from emo_master.core.workflow.compiler import WorkflowCompiler
from emo_master.core.workflow.errors import WorkflowCompileError
from tests.runtime.test_workflow_loop_contracts_v2 import _whilePayload


class _Advance:
    calls = []

    def executeNode(self, inputs, params, runtimeContext):
        self.calls.append(inputs["count"])
        count = inputs["count"] + 1
        return {"status": "ok", "outputs": {"count": count, "hasNext": count < params.get("limit", 3)}}


def booleanPayload():
    payload = _whilePayload()
    payload["workflowOrder"] = ["main", "body"]
    del payload["workflows"]["condition"]
    state = {"count": "integer", "hasNext": "boolean"}
    for key in ("main", "body"):
        workflow = payload["workflows"][key]
        workflow["inputs"] = dict(state)
        workflow["outputs"] = dict(state)
    loop = payload["workflows"]["main"]["nodes"][1]
    loop["inputPorts"] = loop["outputPorts"] = dict(state)
    loop["loop"].pop("conditionWorkflowId")
    loop["loop"].update(conditionMode="boolean", conditionPort="hasNext")
    payload["workflows"]["main"]["edges"].extend([
        {"fromNode": "input", "fromPort": "hasNext", "toNode": "while", "toPort": "hasNext"},
        {"fromNode": "while", "fromPort": "hasNext", "toNode": "output", "toPort": "hasNext"},
    ])
    advance = payload["workflows"]["body"]["nodes"][1]
    advance["outputPorts"] = dict(state)
    advance["params"] = {"limit": 3}
    payload["workflows"]["body"]["edges"].append(
        {"fromNode": "increment", "fromPort": "hasNext", "toNode": "output", "toPort": "hasNext"})
    return payload


def runnerFor(payload=None):
    registry = {"test.increment": _Advance}
    compiled = WorkflowCompiler(operatorRegistry=registry).compile(
        ProjectDocument.model_validate(payload or booleanPayload()))
    _Advance.calls = []
    return WorkflowRunner(compiled, registry)


@pytest.mark.parametrize("initial, expected, calls", [
    ({"count": 0, "hasNext": True}, {"count": 3, "hasNext": False}, [0, 1, 2]),
    ({"count": 7, "hasNext": False}, {"count": 7, "hasNext": False}, []),
])
def testBooleanWhileEvaluatesCurrentStateBeforeEveryBody(initial, expected, calls):
    runner = runnerFor()
    result = runner.run("main", initial, RunContext.root("job", "main"), CancellationToken())
    assert result.outputs == expected
    assert _Advance.calls == calls
    assert runner.compiledProject.workflowCallGraph["main"] == ("body",)


def testBooleanWhileDoesNotCoerceNumberToBoolean():
    runner = runnerFor()
    node = runner.compiledProject.workflows["main"].nodes[1]
    with pytest.raises(LoopExecutionError, match="boolean value"):
        runner.loopRunner.run(node, {"count": 0, "hasNext": 1},
            RunContext.root("job", "main"), CancellationToken())
    assert _Advance.calls == []


def testBooleanWhileStillEnforcesIterationLimit():
    payload = booleanPayload()
    payload["workflows"]["main"]["nodes"][1]["loop"]["maxIterations"] = 2
    runner = runnerFor(payload)
    with pytest.raises(LoopExecutionError) as raised:
        runner.run("main", {"count": 0, "hasNext": True},
            RunContext.root("job", "main"), CancellationToken())
    assert raised.value.code == "E_LOOP_LIMIT_REACHED"
    assert _Advance.calls == [0, 1]


@pytest.mark.parametrize("port", ["count", "missing"])
def testBooleanWhileRejectsInvalidConditionDuringCompile(port):
    payload = booleanPayload()
    payload["workflows"]["main"]["nodes"][1]["loop"]["conditionPort"] = port
    with pytest.raises(WorkflowCompileError) as raised:
        runnerFor(payload)
    assert any(issue.code.startswith("E_LOOP_CONDITION_PORT_") for issue in raised.value.issues)


def testBooleanWhileRoundTripPreservesContractAndStateKeys():
    payload = booleanPayload()
    before = deepcopy(payload)
    saved = ProjectDocument.model_validate(payload).toPayload()
    runner = runnerFor(saved)
    assert runner.compiledProject.workflows["main"].nodes[1].loop["conditionPort"] == "hasNext"
    assert payload == before


def testBooleanWhileDropsInactiveWorkflowReferenceBeforeCallGraph():
    payload = booleanPayload()
    payload["workflows"]["main"]["nodes"][1]["loop"]["conditionWorkflowId"] = "main"
    runner = runnerFor(payload)
    assert runner.compiledProject.workflowCallGraph["main"] == ("body",)
    assert "conditionWorkflowId" not in runner.compiledProject.workflows["main"].nodes[1].loop


def testBooleanWhileStillHonorsCancellation():
    runner = runnerFor()
    cancellation = CancellationToken()
    cancellation.cancel()
    with pytest.raises(CancellationRequested):
        runner.loopRunner.run(runner.compiledProject.workflows["main"].nodes[1],
            {"count": 0, "hasNext": True}, RunContext.root("job", "main"), cancellation)
    assert _Advance.calls == []


def testBooleanWhileStillHonorsTimeout(monkeypatch):
    payload = booleanPayload()
    payload["workflows"]["main"]["nodes"][1]["loop"]["timeoutMs"] = 1
    runner = runnerFor(payload)
    times = iter([0, 1])
    monkeypatch.setattr("emo_master.apps.runtime.workflow.loop_runner.monotonic", lambda: next(times))
    with pytest.raises(LoopExecutionError) as raised:
        runner.loopRunner.run(runner.compiledProject.workflows["main"].nodes[1],
            {"count": 0, "hasNext": True}, RunContext.root("job", "main"), CancellationToken())
    assert raised.value.code == "E_LOOP_TIMEOUT"
    assert _Advance.calls == []
