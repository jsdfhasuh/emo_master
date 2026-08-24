from __future__ import annotations

from threading import Event

import pytest

from emo_master.apps.runtime.workflow.cancellation import CancellationRequested, CancellationToken
from emo_master.apps.runtime.workflow.context import RunContext
from emo_master.apps.runtime.workflow.loop_runner import LoopExecutionError
from emo_master.apps.runtime.workflow.runner import WorkflowRunner
from emo_master.core.project.models import ProjectDocument
from emo_master.core.workflow.compiler import WorkflowCompiler


class _ConditionOperator:
    def executeNode(self, inputs, params, runtimeContext):
        _ = runtimeContext
        limit = int(params.get("limit", 0))
        state = inputs.get("state", {})
        return {"status": "ok", "outputs": {"continue": state.get("count", 0) < limit}}


class _BodyOperator:
    def executeNode(self, inputs, params, runtimeContext):
        _ = params
        _ = runtimeContext
        state = dict(inputs.get("state", {}))
        state["count"] = int(state.get("count", 0)) + 1
        return {"status": "ok", "outputs": {"state": state}}


class _CancelBodyOperator:
    cancelEvent = Event()

    def executeNode(self, inputs, params, runtimeContext):
        _ = params
        _ = runtimeContext
        self.cancelEvent.set()
        return {"status": "ok", "outputs": {"state": dict(inputs.get("state", {}))}}


class _SlowBodyOperator:
    def executeNode(self, inputs, params, runtimeContext):
        _ = params
        _ = runtimeContext
        Event().wait(0.02)
        return {"status": "ok", "outputs": {"state": dict(inputs.get("state", {}))}}


def _payload(mode: str, bodyOperator: str, loopConfig: dict[str, object]) -> dict[str, object]:
    return {
        "schemaVersion": "2.0",
        "project": {
            "projectId": "loop-project",
            "name": "loops",
            "revision": 1,
            "createdAt": "2026-01-01T00:00:00Z",
            "updatedAt": "2026-01-01T00:00:00Z",
        },
        "entryWorkflowId": "main",
        "workflowOrder": ["main", "condition", "body"],
        "workflows": {
            "main": {
                "name": "Main",
                "inputs": {"state": "object"},
                "outputs": {"state": "object"},
                "nodes": [
                    {"nodeId": "input", "kind": "workflow_input"},
                    {
                        "nodeId": "loop",
                        "kind": "loop",
                        "inputPorts": {"state": "object"},
                        "outputPorts": {"state": "object"},
                        "loop": loopConfig,
                    },
                    {"nodeId": "output", "kind": "workflow_output"},
                ],
                "edges": [
                    {"fromNode": "input", "fromPort": "state", "toNode": "loop", "toPort": "state"},
                    {"fromNode": "loop", "fromPort": "state", "toNode": "output", "toPort": "state"},
                ],
            },
            "condition": {
                "name": "Condition",
                "inputs": {"state": "object"},
                "outputs": {"continue": "boolean"},
                "nodes": [
                    {"nodeId": "input", "kind": "workflow_input"},
                    {
                        "nodeId": "condition",
                        "kind": "operator",
                        "operatorId": "test.condition",
                        "inputPorts": {"state": "object"},
                        "outputPorts": {"continue": "boolean"},
                        "params": {"limit": 2},
                    },
                    {"nodeId": "output", "kind": "workflow_output"},
                ],
                "edges": [
                    {"fromNode": "input", "fromPort": "state", "toNode": "condition", "toPort": "state"},
                    {"fromNode": "condition", "fromPort": "continue", "toNode": "output", "toPort": "continue"},
                ],
            },
            "body": {
                "name": "Body",
                "inputs": {"state": "object"},
                "outputs": {"state": "object"},
                "nodes": [
                    {"nodeId": "input", "kind": "workflow_input"},
                    {
                        "nodeId": "body",
                        "kind": "operator",
                        "operatorId": bodyOperator,
                        "inputPorts": {"state": "object"},
                        "outputPorts": {"state": "object"},
                    },
                    {"nodeId": "output", "kind": "workflow_output"},
                ],
                "edges": [
                    {"fromNode": "input", "fromPort": "state", "toNode": "body", "toPort": "state"},
                    {"fromNode": "body", "fromPort": "state", "toNode": "output", "toPort": "state"},
                ],
            },
        },
    }


def _runner(payload: dict[str, object], registry: dict[str, object]) -> WorkflowRunner:
    document = ProjectDocument.model_validate(payload)
    compiled = WorkflowCompiler(operatorRegistry=registry).compile(document)
    return WorkflowRunner(compiled, registry)


def testWhileUsesConditionAndState() -> None:
    payload = _payload(
        "while",
        "test.body",
        {
            "mode": "while",
            "conditionWorkflowId": "condition",
            "bodyWorkflowId": "body",
            "maxIterations": 3,
            "timeoutMs": 1000,
        },
    )
    runner = _runner(
        payload,
        {"test.condition": _ConditionOperator, "test.body": _BodyOperator},
    )
    result = runner.run("main", {"state": {"count": 0}}, RunContext.root("job", "main"), CancellationToken())
    assert result.outputs == {"state": {"count": 2}}


def testLoopReachesLimit() -> None:
    payload = _payload(
        "while",
        "test.body",
        {
            "mode": "while",
            "conditionWorkflowId": "condition",
            "bodyWorkflowId": "body",
            "maxIterations": 1,
            "timeoutMs": 1000,
        },
    )
    runner = _runner(
        payload,
        {"test.condition": _ConditionOperator, "test.body": _BodyOperator},
    )
    with pytest.raises(LoopExecutionError, match="maxIterations") as error:
        runner.run("main", {"state": {"count": 0}}, RunContext.root("job", "main"), CancellationToken())
    assert error.value.code == "E_LOOP_LIMIT_REACHED"


def testLoopTimesOut() -> None:
    payload = _payload(
        "while",
        "test.slow",
        {
            "mode": "while",
            "conditionWorkflowId": "condition",
            "bodyWorkflowId": "body",
            "maxIterations": 2,
            "timeoutMs": 1,
        },
    )
    runner = _runner(
        payload,
        {"test.condition": _ConditionOperator, "test.slow": _SlowBodyOperator},
    )
    with pytest.raises(LoopExecutionError) as error:
        runner.run("main", {"state": {"count": 0}}, RunContext.root("job", "main"), CancellationToken())
    assert error.value.code == "E_LOOP_TIMEOUT"


def testCancellationTokenAbortsBetweenIterations() -> None:
    _CancelBodyOperator.cancelEvent.clear()
    payload = _payload(
        "while",
        "test.cancel",
        {
            "mode": "repeat",
            "bodyWorkflowId": "body",
            "repeatCount": 2,
            "maxIterations": 2,
            "timeoutMs": 1000,
        },
    )
    runner = _runner(
        payload,
        {"test.condition": _ConditionOperator, "test.cancel": _CancelBodyOperator},
    )
    token = CancellationToken(_CancelBodyOperator.cancelEvent)
    with pytest.raises(CancellationRequested):
        runner.run("main", {"state": {"count": 0}}, RunContext.root("job", "main"), token)
