from __future__ import annotations

from collections.abc import Mapping

import pytest

from emo_master.apps.runtime.workflow.cancellation import CancellationToken
from emo_master.apps.runtime.workflow.context import RunContext
from emo_master.apps.runtime.workflow.runner import WorkflowExecutionError, WorkflowRunner
from emo_master.core.contracts.geometry2d import (
    BBox2D,
    CoordinateSpace2D,
    Detection2D,
    DetectionCollection,
)
from emo_master.core.workflow.models import (
    CompiledEdge,
    CompiledNode,
    CompiledProject,
    CompiledWorkflow,
    freezeMapping,
)


class _StaticOperator:
    def __init__(self, outputs: object) -> None:
        self.outputs = outputs
        self.executed = False

    def executeNode(self, inputs, params, runtimeContext):
        _ = inputs
        _ = params
        _ = runtimeContext
        self.executed = True
        return {
            "status": "ok",
            "outputs": self.outputs,
            "metrics": {},
            "diagnostics": {},
        }


def _compiledProject(
    operatorOutputPorts: Mapping[str, object],
    *,
    workflowInputType: object = "object",
    operatorInputType: object = "object",
    workflowOutputType: object = "object",
    additionalOperatorInputPorts: Mapping[str, object] | None = None,
    connectOperatorInput: bool = True,
) -> CompiledProject:
    inputNode = CompiledNode(
        "input",
        "workflow_input",
        None,
        freezeMapping({}),
        freezeMapping({"value": workflowInputType}),
        freezeMapping({}),
    )
    operatorNode = CompiledNode(
        "operator",
        "operator",
        "test.contract",
        freezeMapping(
            {
                "value": operatorInputType,
                **dict(additionalOperatorInputPorts or {}),
            }
        ),
        freezeMapping(operatorOutputPorts),
        freezeMapping({}),
    )
    outputNode = CompiledNode(
        "output",
        "workflow_output",
        None,
        freezeMapping({"result": workflowOutputType}),
        freezeMapping({}),
        freezeMapping({}),
    )
    inputEdge = CompiledEdge("input", "value", "operator", "value")
    outputEdge = CompiledEdge("operator", "result", "output", "result")
    nodes = (inputNode, operatorNode, outputNode)
    edges = (
        (inputEdge, outputEdge)
        if connectOperatorInput
        else (outputEdge,)
    )
    incomingEdges = {"output": (outputEdge,)}
    outgoingEdges = {"operator": (outputEdge,)}
    if connectOperatorInput:
        incomingEdges["operator"] = (inputEdge,)
        outgoingEdges["input"] = (inputEdge,)
    workflow = CompiledWorkflow(
        workflowId="main",
        name="Main",
        inputs=freezeMapping({"value": workflowInputType}),
        outputs=freezeMapping({"result": workflowOutputType}),
        nodes=nodes,
        edges=edges,
        nodeById=freezeMapping({node.nodeId: node for node in nodes}),
        incomingEdges=freezeMapping(incomingEdges),
        outgoingEdges=freezeMapping(outgoingEdges),
        topologicalOrder=("input", "operator", "output"),
    )
    return CompiledProject(
        projectId="contract-project",
        revision=1,
        entryWorkflowId="main",
        workflows=freezeMapping({"main": workflow}),
        workflowCallGraph=freezeMapping({"main": ()}),
        runtime=freezeMapping({}),
    )


def _run(
    operator: _StaticOperator,
    project: CompiledProject,
    value: object = None,
):
    runner = WorkflowRunner(project, {"test.contract": operator})
    return runner.run(
        "main",
        {"value": value},
        RunContext.root("job", "main"),
        CancellationToken(),
    )


def _detectionsPayload() -> dict[str, object]:
    space = CoordinateSpace2D(imageWidth=640, imageHeight=480)
    return DetectionCollection(
        (
            Detection2D(
                "det-1",
                0,
                "part",
                0.9,
                BBox2D(10, 20, 30, 40, space),
            ),
        ),
        space,
    ).toPayload()


def testRunnerAcceptsTypedPayloadAndAllowsMissingDeclaredBranchOutput() -> None:
    operator = _StaticOperator({"result": _detectionsPayload()})
    project = _compiledProject(
        {"result": "detectionCollection", "optionalBranch": "detectionCollection"},
        workflowOutputType="detectionCollection",
    )

    result = _run(operator, project)

    assert result.outputs == {"result": _detectionsPayload()}


def testRunnerRejectsInvalidSemanticOperatorOutput() -> None:
    operator = _StaticOperator(
        {
            "result": {
                "type": "detectionCollection",
                "schemaVersion": "1.0",
                "items": [],
            }
        }
    )
    project = _compiledProject(
        {"result": "detectionCollection"},
        workflowOutputType="detectionCollection",
    )

    with pytest.raises(WorkflowExecutionError) as error:
        _run(operator, project)

    assert error.value.code == "E_OUTPUT_TYPE"
    assert error.value.nodeId == "operator"


def testRunnerRejectsUndeclaredOperatorOutput() -> None:
    operator = _StaticOperator({"result": {}, "debug": {}})
    project = _compiledProject({"result": "object"})

    with pytest.raises(WorkflowExecutionError) as error:
        _run(operator, project)

    assert error.value.code == "E_OUTPUT_UNDECLARED"
    assert "debug" in str(error.value)


def testRunnerRejectsNonObjectOutputsEnvelope() -> None:
    operator = _StaticOperator([])
    project = _compiledProject({"result": "object"})

    with pytest.raises(WorkflowExecutionError) as error:
        _run(operator, project)

    assert error.value.code == "E_OUTPUT_TYPE"


def testRunnerValidatesActualNodeInputsBeforeCallingOperator() -> None:
    operator = _StaticOperator({"result": {}})
    project = _compiledProject(
        {"result": "object"},
        workflowInputType="object",
        operatorInputType="point2d",
    )

    with pytest.raises(WorkflowExecutionError) as error:
        _run(operator, project, {"x": 1, "y": 2})

    assert error.value.code == "E_INPUT_TYPE"
    assert error.value.nodeId == "operator"
    assert operator.executed is False


def testOptionalOnlyOperatorExecutesWithoutInput() -> None:
    operator = _StaticOperator({"result": {"ok": True}})
    project = _compiledProject(
        {"result": {"type": "object", "required": True}},
        workflowInputType={"type": "object", "required": False},
        operatorInputType={"type": "object", "required": False},
    )
    runner = WorkflowRunner(project, {"test.contract": operator})

    result = runner.run(
        "main",
        {},
        RunContext.root("job", "main"),
        CancellationToken(),
    )

    assert operator.executed is True
    assert result.outputs == {"result": {"ok": True}}


def testNullableDescriptorControlsExplicitNull() -> None:
    nullableOperator = _StaticOperator({"result": {}})
    nullableProject = _compiledProject(
        {"result": "object"},
        operatorInputType={"type": "point2d", "nullable": True},
    )
    assert _run(nullableOperator, nullableProject, None).outputs == {"result": {}}

    strictOperator = _StaticOperator({"result": {}})
    strictProject = _compiledProject(
        {"result": "object"},
        operatorInputType={"type": "point2d", "nullable": False},
    )
    with pytest.raises(WorkflowExecutionError) as error:
        _run(strictOperator, strictProject, None)

    assert error.value.code == "E_INPUT_TYPE"
    assert strictOperator.executed is False


def testPartiallySuppliedRequiredOperatorInputsFailBeforeExecution() -> None:
    operator = _StaticOperator({"result": {}})
    project = _compiledProject(
        {"result": "object"},
        additionalOperatorInputPorts={
            "mask": {"type": "image", "required": True}
        },
    )

    with pytest.raises(WorkflowExecutionError) as error:
        _run(operator, project, {})

    assert error.value.code == "E_INPUT_MISSING"
    assert "mask" in str(error.value)
    assert operator.executed is False


def testUnconnectedRequiredDescriptorInputFailsInsteadOfSkipping() -> None:
    operator = _StaticOperator({"result": {}})
    project = _compiledProject(
        {"result": "object"},
        workflowInputType={"type": "object", "required": False},
        operatorInputType={"type": "image", "required": True},
        connectOperatorInput=False,
    )
    runner = WorkflowRunner(project, {"test.contract": operator})

    with pytest.raises(WorkflowExecutionError) as error:
        runner.run(
            "main",
            {},
            RunContext.root("job", "main"),
            CancellationToken(),
        )

    assert error.value.code == "E_INPUT_MISSING"
    assert "value" in str(error.value)
    assert operator.executed is False


def testLegacyStringPortsKeepPartialInputBehavior() -> None:
    operator = _StaticOperator({"result": {}})
    project = _compiledProject(
        {"result": "object"},
        additionalOperatorInputPorts={"legacyOptional": "object"},
    )

    result = _run(operator, project, {})

    assert result.outputs == {"result": {}}
    assert operator.executed is True


def testExplicitlyRequiredOperatorOutputMustBeReturned() -> None:
    operator = _StaticOperator({})
    project = _compiledProject(
        {"result": {"type": "object", "required": True}}
    )

    with pytest.raises(WorkflowExecutionError) as error:
        _run(operator, project, {})

    assert error.value.code == "E_OUTPUT_MISSING"
    assert "result" in str(error.value)
