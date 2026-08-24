from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Callable, Mapping

from emo_master.apps.runtime.workflow.cancellation import CancellationToken
from emo_master.apps.runtime.workflow.context import RunContext
from emo_master.core.workflow.models import CompiledProject


@dataclass(frozen=True)
class WorkflowResult:
    outputs: dict[str, object]
    metrics: dict[str, object] = field(default_factory=dict)
    diagnostics: dict[str, object] = field(default_factory=dict)


class WorkflowExecutionError(RuntimeError):
    def __init__(self, code: str, message: str, nodeId: str = "") -> None:
        self.code = code
        self.nodeId = nodeId
        super().__init__(message)


class WorkflowRunner:
    def __init__(
        self,
        compiledProject: CompiledProject,
        operatorRegistry: Mapping[str, object],
        eventPublisher: Callable[..., object] | None = None,
        maxCallDepth: int = 32,
        artifactStore: object | None = None,
    ) -> None:
        self.compiledProject = compiledProject
        self.operatorRegistry = operatorRegistry
        self.eventPublisher = eventPublisher
        self.maxCallDepth = maxCallDepth
        self.artifactStore = artifactStore
        from emo_master.apps.runtime.workflow.loop_runner import LoopRunner
        from emo_master.apps.runtime.workflow.subflow_runner import SubflowRunner

        self.loopRunner = LoopRunner(self)
        self.subflowRunner = SubflowRunner(self)

    def run(
        self,
        workflowId: str,
        inputs: dict[str, object] | None,
        context: RunContext,
        cancellation: CancellationToken,
    ) -> WorkflowResult:
        workflow = self.compiledProject.workflows.get(workflowId)
        if workflow is None:
            raise WorkflowExecutionError("E_WORKFLOW_INVALID", f"unknown workflow: {workflowId}")
        cancellation.raise_if_cancelled()
        self.publish("workflow.started", context, f"workflow started: {workflowId}")
        nodeInputs: dict[str, dict[str, object]] = {nodeId: {} for nodeId in workflow.nodeById}
        supplied = dict(inputs or {})
        missingInputs = _missingKeys(workflow.inputs, supplied)
        if missingInputs:
            raise WorkflowExecutionError(
                "E_INPUT_MISSING",
                f"workflow inputs are missing: {', '.join(missingInputs)}",
            )
        outputs: dict[str, object] = {}
        metrics: dict[str, object] = {}
        diagnostics: dict[str, object] = {}
        try:
            for nodeId in workflow.topologicalOrder:
                cancellation.raise_if_cancelled()
                node = workflow.nodeById[nodeId]
                nodeContext = context.forNode(node.nodeId)
                nodeInput = dict(nodeInputs.get(nodeId, {}))
                self.publish("node.started", nodeContext, f"node started: {node.nodeId}")
                try:
                    nodeOutputs, nodeMetrics, nodeDiagnostics = self._runNode(
                        node, nodeInput, supplied, nodeContext, cancellation
                    )
                    cancellation.raise_if_cancelled()
                except Exception as err:
                    code = getattr(err, "code", "E_EXEC_FAILED")
                    if isinstance(err, WorkflowExecutionError):
                        code = err.code
                    self.publish(
                        "node.failed",
                        nodeContext,
                        str(err),
                        level="ERROR",
                        code=str(code),
                    )
                    raise
                metrics.update(nodeMetrics)
                diagnostics.update(nodeDiagnostics)
                if node.kind == "workflow_output":
                    outputs.update(nodeInput)
                    outputs.update(nodeOutputs)
                self._route(nodeId, nodeOutputs, workflow.outgoingEdges, nodeInputs)
                cancellation.raise_if_cancelled()
                if node.kind == "operator" and node.inputPorts and not nodeInput:
                    continue
                payload = {"status": "COMPLETED", "outputs": _jsonSafe(nodeOutputs)}
                branch = _branchName(node.operatorId, nodeOutputs)
                if branch:
                    payload["branch"] = branch
                self.publish("node.completed", nodeContext, f"node completed: {node.nodeId}", payload=payload)
            if not outputs:
                outputNode = next((node for node in workflow.nodes if node.kind == "workflow_output"), None)
                if outputNode is not None:
                    outputs.update(nodeInputs.get(outputNode.nodeId, {}))
            missingOutputs = _missingKeys(workflow.outputs, outputs)
            if missingOutputs:
                raise WorkflowExecutionError(
                    "E_OUTPUT_MISSING",
                    f"workflow outputs are missing: {', '.join(missingOutputs)}",
                )
            result = WorkflowResult(outputs=outputs, metrics=metrics, diagnostics=diagnostics)
            self.publish("workflow.completed", context, f"workflow completed: {workflowId}", payload={"outputs": _jsonSafe(outputs)})
            return result
        except Exception as err:
            self.publish("workflow.failed", context, str(err), level="ERROR", code=str(getattr(err, "code", "E_EXEC_FAILED")))
            raise

    def _runNode(self, node, nodeInput, supplied, context, cancellation):
        if node.kind == "workflow_input":
            return {key: supplied[key] for key in node.outputPorts}, {}, {}
        if node.kind == "workflow_output":
            return dict(nodeInput), {}, {}
        if node.kind == "subflow":
            result = self.subflowRunner.run(node, nodeInput, context, cancellation)
            return result.outputs, result.metrics, result.diagnostics
        if node.kind == "loop":
            result = self.loopRunner.run(node, nodeInput, context, cancellation)
            return result.outputs, result.metrics, result.diagnostics
        if node.inputPorts and not nodeInput:
            self.publish("node.skipped", context, f"node skipped: {node.nodeId}", payload={"status": "SKIPPED"})
            return {}, {}, {}
        operator = _buildOperator(node.operatorId, self.operatorRegistry)
        if operator is None or not hasattr(operator, "executeNode"):
            raise WorkflowExecutionError("E_OPERATOR_UNAVAILABLE", f"operator not found: {node.operatorId}", node.nodeId)
        runtimeContext = {
            "jobId": context.jobId,
            "projectId": context.projectId,
            "workflowId": context.workflowId,
            "workflowRunId": context.workflowRunId,
            "parentWorkflowRunId": context.parentWorkflowRunId,
            "nodeId": node.nodeId,
            "nodeRunId": context.nodeRunId,
            "iterationPath": list(context.iterationPath),
            "workspacePath": context.workspacePath,
            "isCancellationRequested": cancellation.isCancellationRequested,
        }
        result = operator.executeNode(nodeInput, dict(node.params), runtimeContext)
        if not isinstance(result, dict) or result.get("status") != "ok":
            error = result.get("error", {}) if isinstance(result, dict) else {}
            code = error.get("code", "E_EXEC_FAILED") if isinstance(error, dict) else "E_EXEC_FAILED"
            message = error.get("message", f"node execute failed: {node.nodeId}") if isinstance(error, dict) else str(error)
            raise WorkflowExecutionError(str(code), str(message), node.nodeId)
        rawOutputs = result.get("outputs", {})
        outputs = rawOutputs if isinstance(rawOutputs, dict) else {}
        metrics = result.get("metrics", {})
        diagnostics = result.get("diagnostics", {})
        if isinstance(outputs, dict):
            for portName, value in outputs.items():
                if isinstance(value, dict) and isinstance(value.get("path"), str):
                    artifact = value
                    if self.artifactStore is not None:
                        register = getattr(self.artifactStore, "registerPath", None)
                        if callable(register):
                            artifactRef = register(
                                value["path"],
                                nodeId=context.callerNodeId,
                                workflowRunId=context.workflowRunId,
                            )
                            artifact = artifactRef.__dict__
                    self.publish(
                        "artifact.created",
                        context,
                        f"artifact created: {value['path']}",
                        payload={"artifact": _jsonSafe(artifact)},
                    )
        return outputs, metrics if isinstance(metrics, dict) else {}, diagnostics if isinstance(diagnostics, dict) else {}

    def _route(self, nodeId, outputs, edges, nodeInputs) -> None:
        for edge in edges.get(nodeId, ()):
            if edge.fromPort in outputs:
                nodeInputs.setdefault(edge.toNode, {})[edge.toPort] = outputs[edge.fromPort]

    def publish(self, eventType, context, message, level="INFO", code="", payload=None):
        if self.eventPublisher is None:
            return None
        return self.eventPublisher(
            eventType=eventType,
            context=context,
            message=message,
            level=level,
            code=code,
            payload={} if payload is None else payload,
        )

    def result(self, outputs: dict[str, object]) -> WorkflowResult:
        return WorkflowResult(outputs=outputs)


def _buildOperator(operatorId: str | None, registry: Mapping[str, object]) -> object | None:
    if not operatorId:
        return None
    value = registry.get(operatorId)
    if value is None:
        return None
    operatorClass = getattr(value, "operatorClass", None)
    if operatorClass is not None:
        value = operatorClass
    if isinstance(value, type):
        return value()
    return value


def _jsonSafe(value: object) -> object:
    try:
        json.dumps(value)
        return value
    except (TypeError, ValueError):
        if isinstance(value, dict):
            return {str(key): _jsonSafe(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [_jsonSafe(item) for item in value]
        return str(value)


def _missingKeys(interface: Mapping[str, object], values: Mapping[str, object]) -> list[str]:
    return sorted(key for key in interface if key not in values)


def _branchName(operatorId: str | None, outputs: Mapping[str, object]) -> str:
    if operatorId == "vision.flow.if":
        for name in ("true", "false"):
            if name in outputs:
                return name
    if operatorId == "vision.flow.switch":
        for name in ("case0", "case1", "case2", "case3", "default"):
            if name in outputs:
                return name
    return ""
