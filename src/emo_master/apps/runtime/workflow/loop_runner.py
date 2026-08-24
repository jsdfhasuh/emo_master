from __future__ import annotations

from time import monotonic
from typing import TYPE_CHECKING

from emo_master.apps.runtime.workflow.cancellation import CancellationToken
from emo_master.apps.runtime.workflow.context import RunContext

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
        self.workflowRunner.publish("loop.started", context.forNode(node.nodeId), "loop started", payload={"mode": mode})
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
            eventType = "loop.timeout" if err.code == "E_LOOP_TIMEOUT" else "loop.limit_reached"
            self.workflowRunner.publish(eventType, context.forNode(node.nodeId), str(err), level="ERROR", code=err.code)
            raise
        self.workflowRunner.publish("loop.completed", context.forNode(node.nodeId), "loop completed", payload={"mode": mode})
        return result

    def _repeat(self, node, inputs, context, cancellation, maximum, timeoutMs, startedAt):
        count = _requiredInt(node.loop, "repeatCount")
        if count < 0 or count > maximum:
            raise LoopExecutionError("E_LOOP_LIMIT_REACHED", "repeatCount exceeds maxIterations")
        outputs: dict[str, object] = {}
        for index in range(count):
            self._check(cancellation, timeoutMs, startedAt)
            iterationContext = context.forIteration(index)
            self._iterationEvent("loop.iteration.started", iterationContext, index)
            bodyInputs = dict(inputs)
            bodyInputs["__iteration__"] = index
            bodyResult = self.workflowRunner.run(
                node.loop["bodyWorkflowId"], bodyInputs, iterationContext, cancellation
            )
            outputs = dict(bodyResult.outputs)
            self._iterationEvent("loop.iteration.completed", iterationContext, index)
            self._check(cancellation, timeoutMs, startedAt)
        return self.workflowRunner.result(outputs)

    def _foreach(self, node, inputs, context, cancellation, maximum, timeoutMs, startedAt):
        items = inputs.get("items")
        if not isinstance(items, list):
            raise LoopExecutionError("E_INPUT_TYPE", "ForEach requires items:list")
        if len(items) > maximum:
            raise LoopExecutionError("E_LOOP_LIMIT_REACHED", "items exceed maxIterations")
        results: list[object] = []
        for index, item in enumerate(items):
            self._check(cancellation, timeoutMs, startedAt)
            iterationContext = context.forIteration(index)
            self._iterationEvent("loop.iteration.started", iterationContext, index)
            bodyInputs = dict(inputs)
            bodyInputs.update({"item": item, "index": index, "__iteration__": index})
            bodyResult = self.workflowRunner.run(
                node.loop["bodyWorkflowId"], bodyInputs, iterationContext, cancellation
            )
            results.append(dict(bodyResult.outputs))
            self._iterationEvent("loop.iteration.completed", iterationContext, index)
            self._check(cancellation, timeoutMs, startedAt)
        return self.workflowRunner.result({"results": results})

    def _while(self, node, inputs, context, cancellation, maximum, timeoutMs, startedAt):
        state = inputs.get("state", {})
        if not isinstance(state, dict):
            raise LoopExecutionError("E_INPUT_TYPE", "While requires state:object")
        for index in range(maximum):
            self._check(cancellation, timeoutMs, startedAt)
            iterationContext = context.forIteration(index)
            self._iterationEvent("loop.iteration.started", iterationContext, index)
            conditionResult = self.workflowRunner.run(
                node.loop["conditionWorkflowId"],
                {"state": state},
                iterationContext,
                cancellation,
            )
            condition = conditionResult.outputs.get("continue")
            if not isinstance(condition, bool):
                raise LoopExecutionError("E_INPUT_TYPE", "While condition must return bool")
            if not condition:
                self._iterationEvent("loop.iteration.completed", iterationContext, index)
                return self.workflowRunner.result({"state": state})
            bodyResult = self.workflowRunner.run(
                node.loop["bodyWorkflowId"],
                {"state": state, "__iteration__": index},
                iterationContext,
                cancellation,
            )
            nextState = bodyResult.outputs.get("state")
            if not isinstance(nextState, dict):
                raise LoopExecutionError("E_INPUT_TYPE", "While body must return state:object")
            state = nextState
            self._iterationEvent("loop.iteration.completed", iterationContext, index)
            self._check(cancellation, timeoutMs, startedAt)
        raise LoopExecutionError("E_LOOP_LIMIT_REACHED", "while loop reached maxIterations")

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
