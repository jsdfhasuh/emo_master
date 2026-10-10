from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

from emo_master.apps.runtime.events.operator_logger import (
    OperatorLogManager,
)
from emo_master.apps.runtime.execution.operator_executor import (
    OperatorExecutor,
    invalidTypes as _invalidTypes,
    missingRequiredKeys as _missingRequiredKeys,
)
from emo_master.apps.runtime.execution.errors import WorkflowExecutionError as WorkflowExecutionError
from emo_master.apps.runtime.workflow.cancellation import CancellationToken
from emo_master.apps.runtime.workflow.branch_activity import BranchActivity
from emo_master.apps.runtime.workflow.context import RunContext
from emo_master.core.contracts.port_types import (
    isPortRequired,
)
from emo_master.core.workflow.models import CompiledProject
from emo_master.core.contracts.run_inspection import finishInspection, startInspection
from emo_master.core.workflow.parameter_bindings import captureBoundValue


@dataclass(frozen=True)
class WorkflowResult:
    outputs: dict[str, object]
    metrics: dict[str, object] = field(default_factory=dict)
    diagnostics: dict[str, object] = field(default_factory=dict)


class WorkflowRunner:
    def __init__(
        self,
        compiledProject: CompiledProject,
        operatorRegistry: Mapping[str, object],
        eventPublisher: Callable[..., object] | None = None,
        maxCallDepth: int = 32,
        artifactStore: object | None = None,
        previewSnapshotStore: object | None = None,
        globalCounters: object | None = None,
        resultCollector: Any = None,
        retainOperators: bool = False,
        globalVariables: object | None = None,
        debugController: Any = None,
    ) -> None:
        self.compiledProject = compiledProject
        self.operatorRegistry = operatorRegistry
        self.eventPublisher = eventPublisher
        self.maxCallDepth = maxCallDepth
        self.artifactStore = artifactStore
        self.previewSnapshotStore = previewSnapshotStore
        self.globalCounters = globalCounters
        from emo_master.apps.runtime.context.global_variables import ReadOnlyVariables
        from emo_master.core.project.global_variables import definitions
        self.globalVariables = globalVariables if globalVariables is not None else ReadOnlyVariables({
            key: value.initialValue for key, value in definitions(getattr(compiledProject, "globalVariables", {})).items()
            if value.kind == "constant"
        })
        self.resultCollector = resultCollector
        if resultCollector is not None:
            resultCollector.globalVariables = self.globalVariables
            resultCollector.globalCounters = self.globalCounters
        self.retainOperators = retainOperators
        self.captureErrors = 0
        self._operatorLogManager = OperatorLogManager(
            self.publish if eventPublisher is not None else None
        )
        self._runDepth = 0
        self.debugController = debugController
        from emo_master.apps.runtime.context.coordinate_snapshots import CoordinateSnapshots
        self.coordinateSnapshots = CoordinateSnapshots()
        self.operatorExecutor = OperatorExecutor(
            operatorRegistry,
            eventPublisher=self.publish,
            globalVariables=self.globalVariables,
            globalCounters=self.globalCounters,
            coordinateSnapshots=self.coordinateSnapshots,
            logManager=self._operatorLogManager,
        )
        # Preserve the existing lifecycle inspection hooks while ownership moves.
        self._lifecycleOperators = self.operatorExecutor._lifecycleOperators
        self._lifecycleOrder = self.operatorExecutor._lifecycleOrder
        self._lifecycleLoggers = self.operatorExecutor._lifecycleLoggers
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
        if self.resultCollector is not None:
            self._capture("begin", context)
        terminal = "FAILED"
        try:
            if isRootCall:
                self.prepareResources(workflowId, context, cancellation)
            result = self._runWorkflow(
                workflowId,
                inputs,
                context,
                cancellation,
                disposeWhenComplete=isRootCall and not self.retainOperators,
            )
            if self.resultCollector is not None:
                self._capture("output", context, result.outputs, workflow=True)
            terminal = "COMPLETED"
            return result
        except Exception as error:
            if getattr(error, "code", "") == "E_CANCELLED":
                terminal = "CANCELLED"
            raise
        finally:
            if self.resultCollector is not None:
                self._capture("end", context, terminal)
            self._runDepth -= 1

    def prepareResources(self, workflowId, context, cancellation):
        """Freeze explicitly requested resources before the first device node."""
        from emo_master.plugins.builtins._coordinate_operators import CoordinateFileError
        if workflowId not in self.compiledProject.workflows:
            raise WorkflowExecutionError("E_WORKFLOW_INVALID", f"unknown workflow: {workflowId}")
        try:
            self.coordinateSnapshots.prepare(self.compiledProject, workflowId, context, cancellation)
        except CoordinateFileError as exc:
            raise WorkflowExecutionError(exc.code, str(exc)) from exc

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
        # Invocation-local deliveries. A child/iteration never inherits this map.
        boundValues: dict[str, dict[int, object]] = {}
        branchActivity = BranchActivity(workflow)
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
                blockedBranches = branchActivity.blockingBranches(node)
                if blockedBranches or (
                    node.inputPorts
                    and not nodeInput
                    and _shouldSkipNodeWithoutInputs(
                        node.kind,
                        node.inputPorts,
                        hasIncomingEdges=bool(workflow.incomingEdges.get(nodeId)),
                    )
                ):
                    branchActivity.skipped(node, blockedBranches)
                    self.publish(
                        "node.skipped",
                        nodeContext,
                        f"node skipped: {node.nodeId}",
                        payload={"status": "SKIPPED", "ioSummary": startInspection(nodeInput),
                                 "code": "E_BRANCH_NOT_SELECTED" if blockedBranches else "E_INPUT_NOT_PRODUCED",
                                 "message": "upstream branch was not selected" if blockedBranches else "upstream did not produce input",
                                 "blockedByBranches": sorted(blockedBranches)},
                    )
                    continue
                inspection = startInspection(nodeInput)
                self.publish("node.started", nodeContext, f"node started: {node.nodeId}",
                             payload={"ioSummary": inspection})
                nodeDiagnostics = {}
                try:
                    if self.debugController is not None:
                        self.debugController.before(node, nodeInput, nodeContext)
                    nodeOutputs, nodeMetrics, nodeDiagnostics = self._runNode(
                        node, nodeInput, supplied, nodeContext, cancellation,
                        boundValues.get(nodeId, {})
                    )
                    self._validateNodeOutputs(node, nodeOutputs)
                    if self.debugController is not None:
                        self.debugController.after(node, nodeInput, nodeOutputs, nodeContext)
                    if self.resultCollector is not None:
                        self._capture("observe", node, nodeInput, nodeOutputs)
                        self._capture("output", nodeContext, nodeOutputs)
                    self._capturePreviewSnapshot(
                        node, nodeOutputs, nodeContext
                    )
                    self._publishArtifacts(node, nodeOutputs, nodeContext)
                    for binding in workflow.outgoingBindings.get(nodeId, ()):
                        if binding.fromPort in nodeOutputs:
                            boundValues.setdefault(binding.toNode, {})[binding.mappingIndex] = captureBoundValue(
                                nodeOutputs[binding.fromPort], binding, nodeContext)
                    cancellation.raise_if_cancelled()
                except Exception as err:
                    if self.debugController is not None:
                        self.debugController.failed(node, nodeInput, nodeContext, err)
                    code = getattr(err, "code", "E_EXEC_FAILED")
                    if isinstance(err, WorkflowExecutionError):
                        code = err.code
                    failedDiagnostics = self.operatorExecutor.failureDiagnostics(
                        node, nodeContext, nodeDiagnostics, err
                    )
                    failedPayload = {
                        "status": "FAILED",
                        "code": str(code),
                        "message": str(err),
                        "metrics": getattr(err, "metrics", {}),
                        "diagnostics": failedDiagnostics,
                        "ioSummary": inspection,
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
                branchActivity.completed(node, nodeOutputs)
                self._route(nodeId, nodeOutputs, workflow.outgoingEdges, nodeInputs)
                cancellation.raise_if_cancelled()
                payload = {
                    "status": "COMPLETED",
                    "outputs": _jsonSafe(nodeOutputs),
                    "metrics": _jsonSafe(nodeMetrics),
                    "diagnostics": _jsonSafe(nodeDiagnostics),
                    "ioSummary": finishInspection(inspection, nodeOutputs),
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

    def _runNode(self, node, nodeInput, supplied, context, cancellation, boundValues=None):
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
        return self.operatorExecutor.invoke(node, nodeInput, context, cancellation, boundValues)

    def disposeOperators(self) -> WorkflowExecutionError | None:
        return self.operatorExecutor.disposeOperators()

    def closeSession(self, context: RunContext, primaryError: Exception | None = None) -> None:
        self.operatorExecutor.closeSession(context, primaryError)

    @staticmethod
    def _attachCleanupDiagnostics(primaryError: Exception, cleanupError: WorkflowExecutionError) -> None:
        OperatorExecutor._attachCleanupDiagnostics(primaryError, cleanupError)

    def _publishCleanupFailure(self, context: RunContext, cleanupError: WorkflowExecutionError) -> None:
        self.operatorExecutor._publishCleanupFailure(context, cleanupError)

    def _validateNodeOutputs(self, node, outputs: Mapping[object, object]) -> None:
        self.operatorExecutor.validateOutputs(node, outputs)

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
        if self.resultCollector is not None:
            self._capture("event", eventType, context)
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

    def _capture(self, method, *args, **kwargs):
        try:
            return getattr(self.resultCollector, method)(*args, **kwargs)
        except Exception:
            self.captureErrors += 1
            # Bounded diagnostics in shared state; no unbounded error event queue.
            config = getattr(self.resultCollector, "config", {})
            rejected = config.get("rejected")
            if rejected is not None:
                with rejected.get_lock():
                    rejected.value += 1
            return None

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
    return _missingRequiredKeys(interface, values, defaultRequired=True)


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
