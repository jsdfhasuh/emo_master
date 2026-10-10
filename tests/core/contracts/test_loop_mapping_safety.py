from copy import deepcopy

import pytest

from emo_master.core.workflow.compiler import WorkflowCompiler
from emo_master.core.workflow.errors import WorkflowCompileError
from emo_master.core.workflow.loop_contracts import deriveLoopContract
from emo_master.core.presentation.catalog import resolveCallPath
from emo_master.core.presentation.models import CallStep
from emo_master.core.project.models import ProjectDocument


def loopProgram(mode="foreach", **extra):
    inputs = {"items": "list<integer>"} if mode == "foreach" else {"value": "integer"}
    outputs = {"value": "list<integer>" if mode == "foreach" else "integer"}
    config = {"contractVersion": 2, "mode": mode, "bodyWorkflowId": "body", "maxIterations": 10, "timeoutMs": 0}
    config.update({"itemInputPort": "value"} if mode == "foreach" else {"repeatCount": 1})
    config.update(extra)
    return {"schemaVersion": "2.1", "project": {"projectId": "loop-safety", "name": "Loop Safety",
        "createdAt": "2026-10-09T00:00:00Z", "updatedAt": "2026-10-09T00:00:00Z"},
        "entryWorkflowId": "main", "workflowOrder": ["main", "body"], "workflows": {
        "main": {"name": "Main", "inputs": inputs, "outputs": outputs, "nodes": [
            {"nodeId": "input", "kind": "workflow_input"},
            {"nodeId": "loop", "kind": "loop", "inputPorts": inputs, "outputPorts": outputs, "loop": config},
            {"nodeId": "output", "kind": "workflow_output"}], "edges": [
            {"fromNode": "input", "fromPort": next(iter(inputs)), "toNode": "loop", "toPort": next(iter(inputs))},
            {"fromNode": "loop", "fromPort": "value", "toNode": "output", "toPort": "value"}]},
        "body": {"name": "Body", "inputs": {"value": "integer"}, "outputs": {"value": "integer"},
            "nodes": [{"nodeId": "input", "kind": "workflow_input"}, {"nodeId": "output", "kind": "workflow_output"}],
            "edges": [{"fromNode": "input", "fromPort": "value", "toNode": "output", "toPort": "value"}]}}}


@pytest.mark.parametrize("extra,code", [({"itemInputPort": "old"}, "E_LOOP_ITEM_PORT_UNKNOWN"),
                                      ({"indexInputPort": "value"}, "E_LOOP_INPUT_PORT_COLLISION")])
def testCompilerRejectsUnsafeExplicitForEachMappingsWithoutMutatingProject(extra, code):
    payload = loopProgram(**extra)
    before = deepcopy(payload)
    with pytest.raises(WorkflowCompileError) as error:
        WorkflowCompiler().compile(payload)
    assert code in {issue.code for issue in error.value.issues}
    assert payload == before


def testNewForEachCanInferAnUnspecifiedItemPort():
    contract = deriveLoopContract({"contractVersion": 2, "mode": "foreach"}, {"value": "integer"}, {})
    assert contract.issues == ()
    assert contract.normalizedConfig["itemInputPort"] == "value"


@pytest.mark.parametrize("mode", ["repeat", "foreach"])
@pytest.mark.parametrize("inactive", ["main", "missing"])
def testDormantConditionDoesNotAffectCallGraphOrCompilation(mode, inactive):
    payload = loopProgram(mode, conditionWorkflowId=inactive)
    before = deepcopy(payload)
    compiled = WorkflowCompiler().compile(payload)
    assert compiled.workflowCallGraph["main"] == ("body",)
    assert payload == before
    assert compiled.workflows["main"].nodeById["loop"].loop["conditionWorkflowId"] == inactive


@pytest.mark.parametrize("mode,conditionMode,active", [
    ("repeat", None, False), ("foreach", None, False),
    ("while", "boolean", False), ("while", None, True),
])
def testResultCallPathOnlyAcceptsExecutableLoopCondition(mode, conditionMode, active):
    payload = loopProgram(mode, conditionWorkflowId="body")
    if conditionMode is not None:
        payload["workflows"]["main"]["nodes"][1]["loop"]["conditionMode"] = conditionMode
    document = ProjectDocument.model_validate(payload)
    before = document.model_dump()
    assert resolveCallPath(document, "main", [CallStep(nodeId="loop", relation="loop_body")]) == "body"
    path = [CallStep(nodeId="loop", relation="loop_condition")]
    if active:
        assert resolveCallPath(document, "main", path) == "body"
    else:
        with pytest.raises(ValueError, match="not executable"):
            resolveCallPath(document, "main", path)
    assert document.model_dump() == before
