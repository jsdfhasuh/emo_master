from __future__ import annotations

from time import monotonic
from typing import TYPE_CHECKING

from emo_master.apps.runtime.workflow.cancellation import CancellationToken
from emo_master.apps.runtime.workflow.context import RunContext
from emo_master.core.contracts.port_types import matchesPortType
from emo_master.core.workflow.loop_contracts import CURRENT_LOOP_CONTRACT_VERSION

if TYPE_CHECKING:
    from emo_master.apps.runtime.workflow.runner import WorkflowResult, WorkflowRunner
    from emo_master.core.workflow.models import CompiledNode


class LoopExecutionError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


class LoopRunner:
    def __init__(self, workflowRunner: "WorkflowRunner") -> None:
        self.workflowRunner = workflowRunner

    def run(
        self,
        node: "CompiledNode",
        inputs: dict[str, object],
        context: RunContext,
        cancellation: CancellationToken,
    ) -> "WorkflowResult":
        config = node.loop
        mode = config.get("mode")
        if not isinstance(mode, str):
            raise LoopExecutionError("E_WORKFLOW_INVALID", "loop mode is required")
        maxIterations = _requiredInt(config, "maxIterations")
        timeoutMs = _optionalInt(config, "timeoutMs", 0)
        if maxIterations < 0 or timeoutMs < 0:
            raise LoopExecutionError("E_WORKFLOW_INVALID", "loop limits must be non-negative")
        startedAt = monotonic()
        self.workflowRunner.publish("loop.started", context, "loop started", payload={"mode": mode})
        try:
            if mode == "repeat":
                result = self._repeat(node, inputs, context, cancellation, maxIterations, timeoutMs, startedAt)
            elif mode == "foreach":
                result = self._foreach(node, inputs, context, cancellation, maxIterations, timeoutMs, startedAt)
            elif mode == "while":
                result = self._while(node, inputs, context, cancellation, maxIterations, timeoutMs, startedAt)
            else:
                raise LoopExecutionError("E_WORKFLOW_INVALID", f"unsupported loop mode: {mode}")
        except LoopExecutionError as err:
            if err.code == "E_LOOP_TIMEOUT":
                eventType = "loop.timeout"
            elif err.code == "E_LOOP_LIMIT_REACHED":
                eventType = "loop.limit_reached"
            else:
                eventType = "loop.failed"
            self.workflowRunner.publish(eventType, context, str(err), level="ERROR", code=err.code)
            raise
        except Exception as err:
            code = str(getattr(err, "code", "E_EXEC_FAILED"))
            self.workflowRunner.publish(
                "loop.failed",
                context,
                str(err),
                level="ERROR",
                code=code,
            )
            raise
        self.workflowRunner.publish("loop.completed", context, "loop completed", payload={"mode": mode})
        return result

    def _repeat(self, node, inputs, context, cancellation, maximum, timeoutMs, startedAt):
        count = _requiredInt(node.loop, "repeatCount")
        if count < 0 or count > maximum:
            raise LoopExecutionError("E_LOOP_LIMIT_REACHED", "repeatCount exceeds maxIterations")
        # A zero-count repeat is a no-op and preserves its input interface.
        outputs: dict[str, object] = {
            name: inputs[name] for name in node.outputPorts if name in inputs
        }
        metrics: dict[str, object] = {}
        diagnostics: dict[str, object] = {}
        for index in range(count):
            self._check(cancellation, timeoutMs, startedAt)
            iterationContext = context.forIteration(index)
            self._iterationEvent("loop.iteration.started", iterationContext, index)
            bodyInputs = dict(inputs)
            bodyInputs["__iteration__"] = index
            bodyContext = context.childWorkflow(
                str(node.loop["bodyWorkflowId"]), node.nodeId
            ).forIteration(index)
            bodyResult = self.workflowRunner.run(
                node.loop["bodyWorkflowId"], bodyInputs, bodyContext, cancellation
            )
            outputs = dict(bodyResult.outputs)
            metrics.update(bodyResult.metrics)
            diagnostics.update(bodyResult.diagnostics)
            self._iterationEvent("loop.iteration.completed", iterationContext, index)
            self._check(cancellation, timeoutMs, startedAt)
        return self.workflowRunner.result(outputs, metrics, diagnostics)

    def _foreach(self, node, inputs, context, cancellation, maximum, timeoutMs, startedAt):
        items = inputs.get("items")
        if not isinstance(items, list):
            raise LoopExecutionError("E_INPUT_TYPE", "ForEach requires items:list")
        if not matchesPortType(items, node.inputPorts.get("items", "list<any>")):
            raise LoopExecutionError(
                "E_INPUT_TYPE", "ForEach items do not match the body item type"
            )
        if len(items) > maximum:
            raise LoopExecutionError("E_LOOP_LIMIT_REACHED", "items exceed maxIterations")
        if node.loop.get("contractVersion") == CURRENT_LOOP_CONTRACT_VERSION:
            return self._foreachV2(
                node,
                inputs,
                items,
                context,
                cancellation,
                timeoutMs,
                startedAt,
            )
        results: list[object] = []
        metrics: dict[str, object] = {}
        diagnostics: dict[str, object] = {}
        for index, item in enumerate(items):
            self._check(cancellation, timeoutMs, startedAt)
            iterationContext = context.forIteration(index)
            self._iterationEvent("loop.iteration.started", iterationContext, index)
            bodyInputs = dict(inputs)
            bodyInputs.update({"item": item, "index": index, "__iteration__": index})
            bodyContext = context.childWorkflow(
                str(node.loop["bodyWorkflowId"]), node.nodeId
            ).forIteration(index)
            bodyResult = self.workflowRunner.run(
                node.loop["bodyWorkflowId"], bodyInputs, bodyContext, cancellation
            )
            results.append(dict(bodyResult.outputs))
            metrics.update(bodyResult.metrics)
            diagnostics.update(bodyResult.diagnostics)
            self._iterationEvent("loop.iteration.completed", iterationContext, index)
            self._check(cancellation, timeoutMs, startedAt)
        return self.workflowRunner.result({"results": results}, metrics, diagnostics)

    def _foreachV2(
        self,
        node,
        inputs,
        items,
        context,
        cancellation,
        timeoutMs,
        startedAt,
    ):
        itemInputPort = node.loop.get("itemInputPort")
        indexInputPort = node.loop.get("indexInputPort")
        aggregated: dict[str, list[object]] = {
            name: [] for name in node.outputPorts
        }
        metrics: dict[str, object] = {}
        diagnostics: dict[str, object] = {}
        for index, item in enumerate(items):
            self._check(cancellation, timeoutMs, startedAt)
            iterationContext = context.forIteration(index)
            self._iterationEvent("loop.iteration.started", iterationContext, index)
            bodyInputs = {
                name: value for name, value in inputs.items() if name != "items"
            }
            if isinstance(itemInputPort, str) and itemInputPort:
                bodyInputs[itemInputPort] = item
            if isinstance(indexInputPort, str) and indexInputPort:
                bodyInputs[indexInputPort] = index
            bodyInputs["__iteration__"] = index
            bodyContext = context.childWorkflow(
                str(node.loop["bodyWorkflowId"]), node.nodeId
            ).forIteration(index)
            bodyResult = self.workflowRunner.run(
                node.loop["bodyWorkflowId"], bodyInputs, bodyContext, cancellation
            )
            for outputName in aggregated:
                if outputName not in bodyResult.outputs:
                    raise LoopExecutionError(
                        "E_OUTPUT_MISSING",
                        f"ForEach body output is missing: {outputName}",
                    )
                aggregated[outputName].append(bodyResult.outputs[outputName])
            metrics.update(bodyResult.metrics)
            diagnostics.update(bodyResult.diagnostics)
            self._iterationEvent("loop.iteration.completed", iterationContext, index)
            self._check(cancellation, timeoutMs, startedAt)
        return self.workflowRunner.result(
            {name: values for name, values in aggregated.items()},
            metrics,
            diagnostics,
        )

    def _while(self, node, inputs, context, cancellation, maximum, timeoutMs, startedAt):
        if node.loop.get("contractVersion") == CURRENT_LOOP_CONTRACT_VERSION:
            return self._whileV2(
                node,
                inputs,
                context,
                cancellation,
                maximum,
                timeoutMs,
                startedAt,
            )
        state = inputs.get("state", {})
        if not isinstance(state, dict):
            raise LoopExecutionError("E_INPUT_TYPE", "While requires state:object")
        metrics: dict[str, object] = {}
        diagnostics: dict[str, object] = {}
        for index in range(maximum):
            self._check(cancellation, timeoutMs, startedAt)
            iterationContext = context.forIteration(index)
            self._iterationEvent("loop.iteration.started", iterationContext, index)
            conditionContext = context.childWorkflow(
                str(node.loop["conditionWorkflowId"]), node.nodeId
            ).forIteration(index)
            conditionResult = self.workflowRunner.run(
                node.loop["conditionWorkflowId"],
                {"state": state},
                conditionContext,
                cancellation,
            )
            metrics.update(conditionResult.metrics)
            diagnostics.update(conditionResult.diagnostics)
            condition = conditionResult.outputs.get("continue")
            if not isinstance(condition, bool):
                raise LoopExecutionError("E_INPUT_TYPE", "While condition must return bool")
            if not condition:
                self._iterationEvent("loop.iteration.completed", iterationContext, index)
                return self.workflowRunner.result({"state": state}, metrics, diagnostics)
            bodyContext = context.childWorkflow(
                str(node.loop["bodyWorkflowId"]), node.nodeId
            ).forIteration(index)
            bodyResult = self.workflowRunner.run(
                node.loop["bodyWorkflowId"],
                {"state": state, "__iteration__": index},
                bodyContext,
                cancellation,
            )
            metrics.update(bodyResult.metrics)
            diagnostics.update(bodyResult.diagnostics)
            nextState = bodyResult.outputs.get("state")
            if not isinstance(nextState, dict):
                raise LoopExecutionError("E_INPUT_TYPE", "While body must return state:object")
            state = nextState
            self._iterationEvent("loop.iteration.completed", iterationContext, index)
            self._check(cancellation, timeoutMs, startedAt)
        raise LoopExecutionError("E_LOOP_LIMIT_REACHED", "while loop reached maxIterations")

    def _whileV2(
        self,
        node,
        inputs,
        context,
        cancellation,
        maximum,
        timeoutMs,
        startedAt,
    ):
        missing = [name for name in node.inputPorts if name not in inputs]
        if missing:
            raise LoopExecutionError(
                "E_INPUT_MISSING",
                "While state inputs are missing: " + ", ".join(sorted(missing)),
            )
        state = {name: inputs[name] for name in node.inputPorts}
        metrics: dict[str, object] = {}
        diagnostics: dict[str, object] = {}
        conditionWorkflowId = str(node.loop["conditionWorkflowId"])
        bodyWorkflowId = str(node.loop["bodyWorkflowId"])
        conditionWorkflow = self.workflowRunner.compiledProject.workflows[
            conditionWorkflowId
        ]
        bodyWorkflow = self.workflowRunner.compiledProject.workflows[bodyWorkflowId]
        for index in range(maximum):
            self._check(cancellation, timeoutMs, startedAt)
            iterationContext = context.forIteration(index)
            self._iterationEvent("loop.iteration.started", iterationContext, index)
            conditionContext = context.childWorkflow(
                conditionWorkflowId, node.nodeId
            ).forIteration(index)
            conditionInputs = {
                name: state[name]
                for name in conditionWorkflow.inputs
                if name in state
            }
            conditionResult = self.workflowRunner.run(
                conditionWorkflowId,
                conditionInputs,
                conditionContext,
                cancellation,
            )
            metrics.update(conditionResult.metrics)
            diagnostics.update(conditionResult.diagnostics)
            condition = conditionResult.outputs.get("continue")
            if not isinstance(condition, bool):
                raise LoopExecutionError(
                    "E_INPUT_TYPE", "While condition must return continue:boolean"
                )
            if not condition:
                self._iterationEvent("loop.iteration.completed", iterationContext, index)
                return self.workflowRunner.result(state, metrics, diagnostics)
            bodyContext = context.childWorkflow(
                bodyWorkflowId, node.nodeId
            ).forIteration(index)
            bodyInputs = {
                name: state[name] for name in bodyWorkflow.inputs if name in state
            }
            bodyInputs["__iteration__"] = index
            bodyResult = self.workflowRunner.run(
                bodyWorkflowId,
                bodyInputs,
                bodyContext,
                cancellation,
            )
            nextState: dict[str, object] = {}
            for name in node.outputPorts:
                if name not in bodyResult.outputs:
                    raise LoopExecutionError(
                        "E_OUTPUT_MISSING", f"While body state output is missing: {name}"
                    )
                nextState[name] = bodyResult.outputs[name]
            state = nextState
            metrics.update(bodyResult.metrics)
            diagnostics.update(bodyResult.diagnostics)
            self._iterationEvent("loop.iteration.completed", iterationContext, index)
            self._check(cancellation, timeoutMs, startedAt)
        raise LoopExecutionError(
            "E_LOOP_LIMIT_REACHED", "while loop reached maxIterations"
        )

    def _check(self, cancellation, timeoutMs, startedAt) -> None:
        cancellation.raise_if_cancelled()
        if timeoutMs > 0 and (monotonic() - startedAt) * 1000.0 >= timeoutMs:
            raise LoopExecutionError("E_LOOP_TIMEOUT", "loop timeout exceeded")

    def _iterationEvent(self, eventType, context, index) -> None:
        self.workflowRunner.publish(eventType, context, f"loop iteration {index}", payload={"iteration": index})


def _requiredInt(config, key: str) -> int:
    value = config.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise LoopExecutionError("E_WORKFLOW_INVALID", f"{key} must be an integer")
    return value


def _optionalInt(config, key: str, default: int) -> int:
    value = config.get(key, default)
    if not isinstance(value, int) or isinstance(value, bool):
        raise LoopExecutionError("E_WORKFLOW_INVALID", f"{key} must be an integer")
    return value
