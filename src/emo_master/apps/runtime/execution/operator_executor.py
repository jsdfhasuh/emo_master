"""Shared operator invocation and lifecycle; no workflow traversal or RPC ownership."""
from __future__ import annotations

from copy import deepcopy
from typing import TYPE_CHECKING, Any, Callable, Mapping

from emo_master.apps.runtime.context.coordinate_snapshots import CoordinateSnapshots
from emo_master.apps.runtime.context.global_variables import ReadOnlyVariables
from emo_master.apps.runtime.events.operator_logger import OperatorLogger, OperatorLogManager
from emo_master.apps.runtime.execution.errors import WorkflowExecutionError
from emo_master.core.contracts.port_types import isPortRequired, matchesPortSpec

if TYPE_CHECKING:
    from emo_master.apps.runtime.workflow.cancellation import CancellationToken
    from emo_master.apps.runtime.workflow.context import RunContext
    from emo_master.core.workflow.models import CompiledNode


class OperatorExecutor:
    """One execution owner's operator instances, services, and bounded logs."""

    def __init__(
        self,
        operatorRegistry: Mapping[str, object],
        *,
        eventPublisher: Callable[..., object] | None = None,
        globalVariables: object | None = None,
        globalCounters: object | None = None,
        coordinateSnapshots: CoordinateSnapshots | None = None,
        logManager: OperatorLogManager | None = None,
    ) -> None:
        self.operatorRegistry = operatorRegistry
        self.eventPublisher = eventPublisher
        self.globalVariables = globalVariables if globalVariables is not None else ReadOnlyVariables({})
        self.globalCounters = globalCounters
        self.coordinateSnapshots = coordinateSnapshots if coordinateSnapshots is not None else CoordinateSnapshots()
        self._operatorLogManager = logManager if logManager is not None else OperatorLogManager(
            self.publish if eventPublisher is not None else None
        )
        self._lifecycleOperators: dict[tuple[str, str], object] = {}
        self._lifecycleOrder: list[tuple[str, str]] = []
        self._lifecycleLoggers: dict[tuple[str, str], OperatorLogger] = {}

    def execute(
        self,
        node: CompiledNode,
        nodeInput: dict[str, object],
        context: RunContext,
        cancellation: CancellationToken,
        boundValues: Mapping[int, object] | None = None,
    ) -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
        """Execute an already-validated operator without scheduling a graph."""
        if node.kind != "operator":
            raise WorkflowExecutionError("E_NODE_KIND", "single-node execution requires an operator", node.nodeId)
        cancellation.raise_if_cancelled()
        result = self.invoke(node, nodeInput, context, cancellation, boundValues)
        try:
            self.validateOutputs(node, result[0])
            cancellation.raise_if_cancelled()
        except Exception as error:
            setattr(error, "diagnostics", self.failureDiagnostics(node, context, result[2], error))
            raise
        return result

    @staticmethod
    def failureDiagnostics(node, context, nodeDiagnostics, error):
        diagnostics = getattr(error, "diagnostics", {})
        if node.operatorId == "vision.io.sqlite_writer" and "sqliteReceipt" in nodeDiagnostics:
            # A later validation/cancellation failure cannot undo an external commit.
            from emo_master.core.contracts.sqlite_writer import receiptSummary
            identity = {"jobId": context.jobId, "workflowId": context.workflowId,
                        "workflowRunId": context.workflowRunId, "nodeId": node.nodeId,
                        "nodeRunId": context.nodeRunId}
            if receiptSummary(nodeDiagnostics["sqliteReceipt"], identity) is not None:
                diagnostics = {**diagnostics, "sqliteReceipt": nodeDiagnostics["sqliteReceipt"]}
        return diagnostics

    def publish(self, eventType, context, message, level="INFO", code="", payload=None):
        if self.eventPublisher is None:
            return None
        return self.eventPublisher(
            eventType=eventType, context=context, message=message,
            level=level, code=code, payload={} if payload is None else payload,
        )

    def invoke(self, node, nodeInput, context, cancellation, boundValues=None):
        """Invoke once; the owner validates outputs before publishing or routing."""
        if node.kind == "operator":
            missingInputs = missingRequiredKeys(
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
            invalidInputs = invalidTypes(node.inputPorts, nodeInput)
            if invalidInputs:
                raise WorkflowExecutionError(
                    "E_INPUT_TYPE",
                    f"node inputs have invalid types: {', '.join(invalidInputs)}",
                    node.nodeId,
                )
        operator = self._operatorForNode(node, context, cancellation)
        if operator is None or not hasattr(operator, "executeNode"):
            raise WorkflowExecutionError("E_OPERATOR_UNAVAILABLE", f"operator not found: {node.operatorId}", node.nodeId)
        params = dict(node.params)
        if node.globalVariableBindings:
            from emo_master.core.project.global_variables import resolveParams, validateEffectiveParams
            values = self.globalVariables.readMany([binding["variableId"] for binding in node.globalVariableBindings])
            params = resolveParams(params, node.globalVariableBindings, values)
            params = validateEffectiveParams(params, node.globalVariableBindings, node.paramSchema)
            validator = getattr(operator, "validateParams", None)
            error = validator(params) if callable(validator) else None
            if isinstance(error, dict):
                raise WorkflowExecutionError(str(error.get("code", "E_PARAM_INVALID")),
                                             str(error.get("message", "invalid bound parameters")), node.nodeId)
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
            "globalCounters": self.globalCounters,
            "globalVariables": self.globalVariables,
            "mappedOutputs": dict(boundValues or {}),
            "coordinateSnapshots": self.coordinateSnapshots,
        }
        if node.operatorId == "vision.io.sqlite_writer":
            from emo_master.apps.runtime.business_sqlite.backend import insert
            runtimeContext["sqliteInsert"] = insert
            runtimeContext["sqliteCancelled"] = lambda: cancellation.isCancellationRequested
            runtimeContext["publishSqliteWrite"] = lambda event, receipt: self.publish(
                event, context, str(receipt.get("status", "")), payload={"receipt": deepcopy(receipt)})
        try:
            result = operator.executeNode(nodeInput, params, runtimeContext)
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
                "globalCounters": self.globalCounters,
                "globalVariables": self.globalVariables,
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
        self.coordinateSnapshots.clear()
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

    def closeSession(self, context: RunContext, primaryError: Exception | None = None) -> None:
        """The retained-session owner calls this once before its Job terminal."""
        cleanupError = self.disposeOperators()
        self._operatorLogManager.finalize()
        if cleanupError is not None:
            self._publishCleanupFailure(context, cleanupError)
            if primaryError is None or getattr(primaryError, "code", "") == "E_CANCELLED":
                # A requested stop is not a successful release if disposal fails.
                raise cleanupError from primaryError
            self._attachCleanupDiagnostics(primaryError, cleanupError)

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

    def validateOutputs(self, node, outputs: Mapping[object, object]) -> None:
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
            missingOutputs = missingRequiredKeys(
                node.outputPorts, outputs, defaultRequired=False
            )
            if missingOutputs:
                raise WorkflowExecutionError(
                    "E_OUTPUT_MISSING",
                    f"node outputs are missing: {', '.join(missingOutputs)}",
                    node.nodeId,
                )
        invalidOutputs = invalidTypes(outputInterface, outputs)
        if invalidOutputs:
            raise WorkflowExecutionError(
                "E_OUTPUT_TYPE",
                f"node outputs have invalid types: {', '.join(invalidOutputs)}",
                node.nodeId,
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


def missingRequiredKeys(
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


def invalidTypes(
    interface: Mapping[str, object], values: Mapping[Any, object]
) -> list[str]:
    return sorted(
        key
        for key, expectedType in interface.items()
        if key in values and not matchesPortSpec(values[key], expectedType)
    )


def _undeclaredKeys(
    interface: Mapping[str, object], values: Mapping[object, object]
) -> list[str]:
    return sorted(
        key if isinstance(key, str) else repr(key)
        for key in values
        if not isinstance(key, str) or key not in interface
    )
