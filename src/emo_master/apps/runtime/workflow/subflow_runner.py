from __future__ import annotations

from typing import TYPE_CHECKING

from emo_master.apps.runtime.workflow.cancellation import CancellationToken
from emo_master.apps.runtime.workflow.context import RunContext
from emo_master.core.contracts.port_types import isPortRequired, matchesPortSpec

if TYPE_CHECKING:
    from emo_master.apps.runtime.workflow.runner import WorkflowResult, WorkflowRunner
    from emo_master.core.workflow.models import CompiledNode


class SubflowRunner:
    def __init__(self, workflowRunner: "WorkflowRunner") -> None:
        self.workflowRunner = workflowRunner

    def run(
        self,
        node: "CompiledNode",
        inputs: dict[str, object],
        context: RunContext,
        cancellation: CancellationToken,
    ) -> "WorkflowResult":
        from emo_master.apps.runtime.workflow.runner import WorkflowExecutionError

        target = node.targetWorkflowId
        if not target:
            raise WorkflowExecutionError(
                "E_WORKFLOW_INVALID", "subflow target workflow is required", node.nodeId
            )
        targetWorkflow = self.workflowRunner.compiledProject.workflows.get(target)
        if targetWorkflow is None:
            raise WorkflowExecutionError(
                "E_WORKFLOW_INVALID", f"unknown subflow target: {target}", node.nodeId
            )
        missing = sorted(
            key
            for key, portSpec in targetWorkflow.inputs.items()
            if key not in inputs and isPortRequired(portSpec, default=True)
        )
        if missing:
            raise WorkflowExecutionError(
                "E_INPUT_MISSING",
                f"subflow inputs are missing: {', '.join(missing)}",
                node.nodeId,
            )
        for name, expected in targetWorkflow.inputs.items():
            if name not in inputs:
                continue
            if not _matchesType(inputs[name], expected):
                raise WorkflowExecutionError(
                    "E_INPUT_TYPE", f"invalid subflow input type: {name}", node.nodeId
                )
        childContext = context.childWorkflow(target, node.nodeId)
        if childContext.callDepth > self.workflowRunner.maxCallDepth:
            raise WorkflowExecutionError(
                "E_CALL_DEPTH", "maximum workflow call depth exceeded", node.nodeId
            )
        self.workflowRunner.publish(
            "subflow.started", context, f"subflow started: {target}"
        )
        result = self.workflowRunner.run(
            workflowId=target,
            inputs=inputs,
            context=childContext,
            cancellation=cancellation,
        )
        self.workflowRunner.publish(
            "subflow.completed", context, f"subflow completed: {target}"
        )
        return result


def _matchesType(value: object, expected: object) -> bool:
    return matchesPortSpec(value, expected)
