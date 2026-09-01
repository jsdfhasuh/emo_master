from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

from emo_master.apps.runtime.events.operator_logger import (
    OperatorLogger,
    OperatorLogManager,
)
from emo_master.apps.runtime.workflow.cancellation import CancellationToken
from emo_master.apps.runtime.workflow.context import RunContext
from emo_master.core.contracts.port_types import (
    isPortRequired,
    matchesPortSpec,
)
from emo_master.core.workflow.models import CompiledProject


@dataclass(frozen=True)
class WorkflowResult:
    outputs: dict[str, object]
    metrics: dict[str, object] = field(default_factory=dict)
    diagnostics: dict[str, object] = field(default_factory=dict)


class WorkflowExecutionError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        nodeId: str = "",
        metrics: Mapping[str, object] | None = None,
        diagnostics: Mapping[str, object] | None = None,
    ) -> None:
        self.code = code
        self.nodeId = nodeId
        self.metrics = dict(metrics or {})
        self.diagnostics = dict(diagnostics or {})
        super().__init__(message)


class WorkflowRunner:
    def __init__(
        self,
        compiledProject: CompiledProject,
        operatorRegistry: Mapping[str, object],
        eventPublisher: Callable[..., object] | None = None,
        maxCallDepth: int = 32,
        artifactStore: object | None = None,
        previewSnapshotStore: object | None = None,
    ) -> None:
        self.compiledProject = compiledProject
        self.operatorRegistry = operatorRegistry
        self.eventPublisher = eventPublisher
        self.maxCallDepth = maxCallDepth
        self.artifactStore = artifactStore
        self.previewSnapshotStore = previewSnapshotStore
        self._operatorLogManager = OperatorLogManager(
            self.publish if eventPublisher is not None else None
        )
        self._runDepth = 0
        self._lifecycleOperators: dict[tuple[str, str], object] = {}
        self._lifecycleOrder: list[tuple[str, str]] = []
        self._lifecycleLoggers: dict[tuple[str, str], OperatorLogger] = {}
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
        isRootCall = self._runDepth == 0
        self._runDepth += 1
        try:
            return self._runWorkflow(
                workflowId,
                inputs,
                context,
                cancellation,
                disposeWhenComplete=isRootCall,
            )
        finally:
            self._runDepth -= 1

    def _runWorkflow(
        self,
        workflowId: str,
        inputs: dict[str, object] | None,
        context: RunContext,
        cancellation: CancellationToken,
        *,
        disposeWhenComplete: bool,
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
        invalidInputs = _invalidTypes(workflow.inputs, supplied)
        if invalidInputs:
            raise WorkflowExecutionError(
                "E_INPUT_TYPE",
                f"workflow inputs have invalid types: {', '.join(invalidInputs)}",
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
                if (
                    node.inputPorts
                    and not nodeInput
                    and _shouldSkipNodeWithoutInputs(
                        node.kind,
                        node.inputPorts,
                        hasIncomingEdges=bool(workflow.incomingEdges.get(nodeId)),
                    )
                ):
                    self.publish(
                        "node.skipped",
                        nodeContext,
                        f"node skipped: {node.nodeId}",
                        payload={"status": "SKIPPED"},
                    )
                    continue
                self.publish("node.started", nodeContext, f"node started: {node.nodeId}")
                try:
                    nodeOutputs, nodeMetrics, nodeDiagnostics = self._runNode(
                        node, nodeInput, supplied, nodeContext, cancellation
                    )
                    self._validateNodeOutputs(node, nodeOutputs)
                    self._capturePreviewSnapshot(
                        node, nodeOutputs, nodeContext
                    )
                    self._publishArtifacts(node, nodeOutputs, nodeContext)
                    cancellation.raise_if_cancelled()
                except Exception as err:
                    code = getattr(err, "code", "E_EXEC_FAILED")
                    if isinstance(err, WorkflowExecutionError):
                        code = err.code
                    failedPayload = {
                        "status": "FAILED",
                        "code": str(code),
                        "message": str(err),
                        "metrics": getattr(err, "metrics", {}),
                        "diagnostics": getattr(err, "diagnostics", {}),
                    }
                    self.publish(
                        "node.failed",
                        nodeContext,
                        str(err),
                        level="ERROR",
                        code=str(code),
                        payload=_jsonSafe(failedPayload),
                    )
                    raise
                metrics.update(nodeMetrics)
                diagnostics.update(nodeDiagnostics)
                if node.kind == "workflow_output":
                    outputs.update(nodeInput)
                    outputs.update(nodeOutputs)
                self._route(nodeId, nodeOutputs, workflow.outgoingEdges, nodeInputs)
                cancellation.raise_if_cancelled()
                payload = {
                    "status": "COMPLETED",
                    "outputs": _jsonSafe(nodeOutputs),
                    "metrics": _jsonSafe(nodeMetrics),
                    "diagnostics": _jsonSafe(nodeDiagnostics),
                }
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
            invalidOutputs = _invalidTypes(workflow.outputs, outputs)
            if invalidOutputs:
                raise WorkflowExecutionError(
                    "E_OUTPUT_TYPE",
                    f"workflow outputs have invalid types: {', '.join(invalidOutputs)}",
                )
            result = WorkflowResult(outputs=outputs, metrics=metrics, diagnostics=diagnostics)
            if disposeWhenComplete:
                cleanupError = self.disposeOperators()
                if cleanupError is not None:
                    self._publishCleanupFailure(context, cleanupError)
                    raise cleanupError
                self._operatorLogManager.finalize()
            self.publish("workflow.completed", context, f"workflow completed: {workflowId}", payload={"outputs": _jsonSafe(outputs)})
            return result
        except Exception as err:
            if disposeWhenComplete:
                cleanupError = self.disposeOperators()
                if cleanupError is not None:
                    self._attachCleanupDiagnostics(err, cleanupError)
                    self._publishCleanupFailure(context, cleanupError)
                self._operatorLogManager.finalize()
            code = str(getattr(err, "code", "E_EXEC_FAILED"))
            self.publish(
                "workflow.failed",
                context,
                str(err),
                level="ERROR",
                code=code,
                payload=_jsonSafe(
                    {
                        "status": "FAILED",
                        "code": code,
                        "message": str(err),
                        "metrics": getattr(err, "metrics", {}),
                        "diagnostics": getattr(err, "diagnostics", {}),
                    }
                ),
            )
            raise

    def _runNode(self, node, nodeInput, supplied, context, cancellation):
        if node.kind == "operator":
            missingInputs = _missingRequiredKeys(
                node.inputPorts,
                nodeInput,
                defaultRequired=False,
                descriptorDefaultRequired=True,
            )
            if missingInputs:
                raise WorkflowExecutionError(
                    "E_INPUT_MISSING",
                    f"node inputs are missing: {', '.join(missingInputs)}",
                    node.nodeId,
                )
            invalidInputs = _invalidTypes(node.inputPorts, nodeInput)
            if invalidInputs:
                raise WorkflowExecutionError(
                    "E_INPUT_TYPE",
                    f"node inputs have invalid types: {', '.join(invalidInputs)}",
                    node.nodeId,
                )
        if node.kind == "workflow_input":
            return {
                key: supplied[key]
                for key in node.outputPorts
                if key in supplied
            }, {}, {}
        if node.kind == "workflow_output":
            return dict(nodeInput), {}, {}
        if node.kind == "subflow":
            result = self.subflowRunner.run(node, nodeInput, context, cancellation)
            return result.outputs, result.metrics, result.diagnostics
        if node.kind == "loop":
            result = self.loopRunner.run(node, nodeInput, context, cancellation)
            return result.outputs, result.metrics, result.diagnostics
        operator = self._operatorForNode(node, context, cancellation)
        if operator is None or not hasattr(operator, "executeNode"):
            raise WorkflowExecutionError("E_OPERATOR_UNAVAILABLE", f"operator not found: {node.operatorId}", node.nodeId)
        logger = self._operatorLogManager.createLogger(
            context,
            str(node.operatorId or ""),
            "execute",
        )
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
            "raiseIfCancellationRequested": cancellation.raise_if_cancelled,
            "logger": logger,
        }
        try:
            result = operator.executeNode(nodeInput, dict(node.params), runtimeContext)
        except Exception as err:
            loggingDiagnostics = logger.close()
            if loggingDiagnostics:
                _attachOperatorLoggingDiagnostics(err, loggingDiagnostics)
            raise
        loggingDiagnostics = logger.close()
        if not isinstance(result, dict) or result.get("status") != "ok":
            error = result.get("error", {}) if isinstance(result, dict) else {}
            code = error.get("code", "E_EXEC_FAILED") if isinstance(error, dict) else "E_EXEC_FAILED"
            message = error.get("message", f"node execute failed: {node.nodeId}") if isinstance(error, dict) else str(error)
            failedMetrics = result.get("metrics", {}) if isinstance(result, dict) else {}
            failedDiagnostics = result.get("diagnostics", {}) if isinstance(result, dict) else {}
            normalizedDiagnostics = (
                dict(failedDiagnostics) if isinstance(failedDiagnostics, dict) else {}
            )
            if loggingDiagnostics:
                normalizedDiagnostics["operatorLogging"] = loggingDiagnostics
            raise WorkflowExecutionError(
                str(code),
                str(message),
                node.nodeId,
                failedMetrics if isinstance(failedMetrics, dict) else {},
                normalizedDiagnostics,
            )
        rawOutputs = result.get("outputs", {})
        if not isinstance(rawOutputs, dict):
            raise WorkflowExecutionError(
                "E_OUTPUT_TYPE",
                f"node outputs must be an object: {node.nodeId}",
                node.nodeId,
            )
        outputs = rawOutputs
        metrics = result.get("metrics", {})
        diagnostics = result.get("diagnostics", {})
        normalizedDiagnostics = diagnostics if isinstance(diagnostics, dict) else {}
        if loggingDiagnostics:
            normalizedDiagnostics = dict(normalizedDiagnostics)
            normalizedDiagnostics["operatorLogging"] = loggingDiagnostics
        return outputs, metrics if isinstance(metrics, dict) else {}, normalizedDiagnostics

    def _operatorForNode(self, node, context, cancellation) -> object | None:
        operatorClass = _operatorClass(node.operatorId, self.operatorRegistry)
        if operatorClass is None:
            return None
        if not isinstance(operatorClass, type) or not _hasLifecycle(operatorClass):
            return _buildOperator(node.operatorId, self.operatorRegistry)

        key = (context.workflowId, node.nodeId)
        cached = self._lifecycleOperators.get(key)
        if cached is not None:
            return cached

        operator = operatorClass()
        lifecycleLogger = self._operatorLogManager.createLogger(
            context,
            str(node.operatorId or ""),
            "lifecycle",
        )
        init = getattr(operator, "initOperator", None)
        if callable(init):
            initContext = {
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
                "raiseIfCancellationRequested": cancellation.raise_if_cancelled,
                "logger": lifecycleLogger,
            }
            try:
                init(initContext)
            except Exception as err:
                dispose = getattr(operator, "disposeOperator", None)
                if callable(dispose):
                    try:
                        dispose()
                    except Exception as cleanupErr:
                        cleanupError = WorkflowExecutionError(
                            "E_RESOURCE_CLEANUP_FAILED",
                            f"operator cleanup after initialization failed: {cleanupErr}",
                            node.nodeId,
                        )
                        self._attachCleanupDiagnostics(err, cleanupError)
                        self._publishCleanupFailure(context, cleanupError)
                lifecycleLogger.close()
                raise
        self._lifecycleOperators[key] = operator
        self._lifecycleOrder.append(key)
        self._lifecycleLoggers[key] = lifecycleLogger
        return operator

    def disposeOperators(self) -> WorkflowExecutionError | None:
        cleanupErrors: list[str] = []
        failedNodeIds: list[str] = []
        for key in reversed(self._lifecycleOrder):
            operator = self._lifecycleOperators.get(key)
            if operator is None:
                continue
            dispose = getattr(operator, "disposeOperator", None)
            try:
                if callable(dispose):
                    dispose()
            except Exception as err:
                failedNodeIds.append(key[1])
                cleanupErrors.append(f"{key[0]}/{key[1]}: {err}")
            finally:
                logger = self._lifecycleLoggers.get(key)
                if logger is not None:
                    logger.close()
        self._lifecycleOperators.clear()
        self._lifecycleOrder.clear()
        self._lifecycleLoggers.clear()
        if not cleanupErrors:
            return None
        return WorkflowExecutionError(
            "E_RESOURCE_CLEANUP_FAILED",
            "operator resource cleanup failed: " + "; ".join(cleanupErrors),
            failedNodeIds[0] if failedNodeIds else "",
            diagnostics={"cleanupErrors": cleanupErrors},
        )

    @staticmethod
    def _attachCleanupDiagnostics(
        primaryError: Exception,
        cleanupError: WorkflowExecutionError,
    ) -> None:
        diagnostics = getattr(primaryError, "diagnostics", None)
        if not isinstance(diagnostics, dict):
            diagnostics = {}
            try:
                setattr(primaryError, "diagnostics", diagnostics)
            except (AttributeError, TypeError):
                return
        diagnostics["resourceCleanup"] = {
            "code": cleanupError.code,
            "message": str(cleanupError),
            **cleanupError.diagnostics,
        }

    def _publishCleanupFailure(
        self,
        context: RunContext,
        cleanupError: WorkflowExecutionError,
    ) -> None:
        self.publish(
            "resource.cleanup.failed",
            context,
            str(cleanupError),
            level="ERROR",
            code=cleanupError.code,
            payload={
                "status": "FAILED",
                "code": cleanupError.code,
                "message": str(cleanupError),
            },
        )

    def _validateNodeOutputs(self, node, outputs: Mapping[object, object]) -> None:
        outputInterface = (
            node.inputPorts if node.kind == "workflow_output" else node.outputPorts
        )
        undeclared = _undeclaredKeys(outputInterface, outputs)
        if undeclared:
            raise WorkflowExecutionError(
                "E_OUTPUT_UNDECLARED",
                f"node returned undeclared outputs: {', '.join(undeclared)}",
                node.nodeId,
            )
        if node.kind == "operator":
            missingOutputs = _missingRequiredKeys(
                node.outputPorts, outputs, defaultRequired=False
            )
            if missingOutputs:
                raise WorkflowExecutionError(
                    "E_OUTPUT_MISSING",
                    f"node outputs are missing: {', '.join(missingOutputs)}",
                    node.nodeId,
                )
        invalidOutputs = _invalidTypes(outputInterface, outputs)
        if invalidOutputs:
            raise WorkflowExecutionError(
                "E_OUTPUT_TYPE",
                f"node outputs have invalid types: {', '.join(invalidOutputs)}",
                node.nodeId,
            )

    def _publishArtifacts(self, node, outputs, context) -> None:
        if node.kind != "operator":
            return
        for value in outputs.values():
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

    def _capturePreviewSnapshot(self, node, outputs, context) -> None:
        if node.kind != "operator" or self.previewSnapshotStore is None:
            return
        capture = getattr(self.previewSnapshotStore, "capture", None)
        if not callable(capture):
            return
        try:
            capture(node, outputs, context)
        except Exception as err:
            self.publish(
                "preview.snapshot.failed",
                context,
                str(err),
                level="WARN",
                code="E_PREVIEW_SNAPSHOT_FAILED",
                payload={"status": "FAILED", "message": str(err)},
            )

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

    def result(
        self,
        outputs: dict[str, object],
        metrics: Mapping[str, object] | None = None,
        diagnostics: Mapping[str, object] | None = None,
    ) -> WorkflowResult:
        return WorkflowResult(
            outputs=outputs,
            metrics=dict(metrics or {}),
            diagnostics=dict(diagnostics or {}),
        )


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


def _operatorClass(
    operatorId: str | None,
    registry: Mapping[str, object],
) -> object | None:
    if not operatorId:
        return None
    value = registry.get(operatorId)
    if value is None:
        return None
    return getattr(value, "operatorClass", value)


def _hasLifecycle(value: type) -> bool:
    return callable(getattr(value, "initOperator", None)) or callable(
        getattr(value, "disposeOperator", None)
    )


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


def _attachOperatorLoggingDiagnostics(
    error: Exception,
    loggingDiagnostics: Mapping[str, object],
) -> None:
    diagnostics = getattr(error, "diagnostics", None)
    if not isinstance(diagnostics, dict):
        diagnostics = {}
        try:
            setattr(error, "diagnostics", diagnostics)
        except (AttributeError, TypeError):
            return
    diagnostics["operatorLogging"] = dict(loggingDiagnostics)


def _missingKeys(interface: Mapping[str, object], values: Mapping[str, object]) -> list[str]:
    return _missingRequiredKeys(interface, values, defaultRequired=True)


def _missingRequiredKeys(
    interface: Mapping[str, object],
    values: Mapping[Any, object],
    *,
    defaultRequired: bool,
    descriptorDefaultRequired: bool | None = None,
) -> list[str]:
    return sorted(
        key
        for key, portSpec in interface.items()
        if key not in values
        and isPortRequired(
            portSpec,
            default=(
                descriptorDefaultRequired
                if descriptorDefaultRequired is not None
                and isinstance(portSpec, Mapping)
                else defaultRequired
            ),
        )
    )


def _invalidTypes(
    interface: Mapping[str, object], values: Mapping[Any, object]
) -> list[str]:
    return sorted(
        key
        for key, expectedType in interface.items()
        if key in values and not matchesPortSpec(values[key], expectedType)
    )


def _shouldSkipNodeWithoutInputs(
    nodeKind: str,
    inputPorts: Mapping[str, object],
    *,
    hasIncomingEdges: bool = False,
) -> bool:
    if nodeKind not in {"operator", "subflow"}:
        return True
    hasRequiredInput = any(
        not isinstance(portSpec, Mapping)
        or isPortRequired(portSpec, default=True)
        for portSpec in inputPorts.values()
    )
    if not hasRequiredInput:
        return False
    if hasIncomingEdges:
        return True
    return any(not isinstance(portSpec, Mapping) for portSpec in inputPorts.values())


def _undeclaredKeys(
    interface: Mapping[str, object], values: Mapping[object, object]
) -> list[str]:
    return sorted(
        key if isinstance(key, str) else repr(key)
        for key in values
        if not isinstance(key, str) or key not in interface
    )


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
