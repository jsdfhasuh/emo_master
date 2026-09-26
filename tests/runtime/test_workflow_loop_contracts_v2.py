from __future__ import annotations

import pytest

from emo_master.apps.runtime.workflow.cancellation import CancellationToken
from emo_master.apps.runtime.workflow.context import RunContext
from emo_master.apps.runtime.workflow.loop_runner import LoopExecutionError
from emo_master.apps.runtime.workflow.runner import WorkflowExecutionError, WorkflowRunner
from emo_master.core.project.models import ProjectDocument
from emo_master.core.workflow.compiler import WorkflowCompiler


class _MapItemOperator:
    def executeNode(self, inputs, params, runtimeContext):
        _ = params
        _ = runtimeContext
        return {
            "status": "ok",
            "outputs": {
                "edge": f"{inputs['prefix']}{inputs['image']}",
                "ordinal": inputs["index"],
            },
        }


class _IncrementOperator:
    def executeNode(self, inputs, params, runtimeContext):
        _ = params
        _ = runtimeContext
        return {"status": "ok", "outputs": {"count": inputs["count"] + 1}}


class _LessThanOperator:
    def executeNode(self, inputs, params, runtimeContext):
        _ = runtimeContext
        return {
            "status": "ok",
            "outputs": {"continue": inputs["count"] < params["limit"]},
        }


def _metadata(name: str) -> dict[str, object]:
    return {
        "schemaVersion": "2.1",
        "project": {
            "projectId": f"{name}-project",
            "name": name,
            "revision": 1,
            "createdAt": "2026-01-01T00:00:00Z",
            "updatedAt": "2026-01-01T00:00:00Z",
        },
        "entryWorkflowId": "main",
    }


def _run(payload: dict[str, object], registry: dict[str, object], inputs):
    document = ProjectDocument.model_validate(payload)
    compiled = WorkflowCompiler(operatorRegistry=registry).compile(document)
    runner = WorkflowRunner(compiled, registry)
    return runner.run(
        "main",
        inputs,
        RunContext.root("job", "main"),
        CancellationToken(),
    ).outputs


def _foreachPayload() -> dict[str, object]:
    payload = _metadata("foreach-v2")
    payload["workflowOrder"] = ["main", "body"]
    payload["workflows"] = {
        "main": {
            "name": "Main",
            "inputs": {"items": "list<string>", "prefix": "string"},
            "outputs": {
                "edge": "list<string>",
                "ordinal": "list<integer>",
            },
            "nodes": [
                {"nodeId": "input", "kind": "workflow_input"},
                {
                    "nodeId": "foreach",
                    "kind": "loop",
                    "inputPorts": {
                        "items": "list<string>",
                        "prefix": "string",
                    },
                    "outputPorts": {
                        "edge": "list<string>",
                        "ordinal": "list<integer>",
                    },
                    "loop": {
                        "contractVersion": 2,
                        "mode": "foreach",
                        "bodyWorkflowId": "body",
                        "itemInputPort": "image",
                        "indexInputPort": "index",
                        "maxIterations": 10,
                        "timeoutMs": 0,
                    },
                },
                {"nodeId": "output", "kind": "workflow_output"},
            ],
            "edges": [
                {"fromNode": "input", "fromPort": "items", "toNode": "foreach", "toPort": "items"},
                {"fromNode": "input", "fromPort": "prefix", "toNode": "foreach", "toPort": "prefix"},
                {"fromNode": "foreach", "fromPort": "edge", "toNode": "output", "toPort": "edge"},
                {"fromNode": "foreach", "fromPort": "ordinal", "toNode": "output", "toPort": "ordinal"},
            ],
        },
        "body": {
            "name": "Body",
            "inputs": {
                "image": "string",
                "index": "integer",
                "prefix": "string",
            },
            "outputs": {"edge": "string", "ordinal": "integer"},
            "nodes": [
                {"nodeId": "input", "kind": "workflow_input"},
                {
                    "nodeId": "map",
                    "kind": "operator",
                    "operatorId": "test.map",
                    "inputPorts": {
                        "image": "string",
                        "index": "integer",
                        "prefix": "string",
                    },
                    "outputPorts": {"edge": "string", "ordinal": "integer"},
                },
                {"nodeId": "output", "kind": "workflow_output"},
            ],
            "edges": [
                {"fromNode": "input", "fromPort": "image", "toNode": "map", "toPort": "image"},
                {"fromNode": "input", "fromPort": "index", "toNode": "map", "toPort": "index"},
                {"fromNode": "input", "fromPort": "prefix", "toNode": "map", "toPort": "prefix"},
                {"fromNode": "map", "fromPort": "edge", "toNode": "output", "toPort": "edge"},
                {"fromNode": "map", "fromPort": "ordinal", "toNode": "output", "toPort": "ordinal"},
            ],
        },
    }
    return payload


def testForEachV2AggregatesBodyOutputsByPort() -> None:
    result = _run(
        _foreachPayload(),
        {"test.map": _MapItemOperator},
        {"items": ["a", "b", "c"], "prefix": "edge-"},
    )

    assert result == {
        "edge": ["edge-a", "edge-b", "edge-c"],
        "ordinal": [0, 1, 2],
    }


def testForEachV2EmptyInputReturnsOneEmptyListPerBodyOutput() -> None:
    result = _run(
        _foreachPayload(),
        {"test.map": _MapItemOperator},
        {"items": [], "prefix": "edge-"},
    )

    assert result == {"edge": [], "ordinal": []}


def testForEachV2TypeFailurePublishesLoopFailedEvent() -> None:
    payload = _foreachPayload()
    payload["workflows"]["main"]["inputs"]["items"] = "list<any>"
    events: list[str] = []
    document = ProjectDocument.model_validate(payload)
    registry = {"test.map": _MapItemOperator}
    compiled = WorkflowCompiler(operatorRegistry=registry).compile(document)
    runner = WorkflowRunner(
        compiled,
        registry,
        eventPublisher=lambda **event: events.append(str(event["eventType"])),
    )

    with pytest.raises(LoopExecutionError, match="body item type"):
        runner.run(
            "main",
            {"items": [1], "prefix": "edge-"},
            RunContext.root("job", "main"),
            CancellationToken(),
        )

    assert "loop.failed" in events
    assert "loop.limit_reached" not in events


def testForEachV2BodyFailurePublishesLoopFailedWithOriginalCode() -> None:
    payload = _foreachPayload()
    document = ProjectDocument.model_validate(payload)
    compiled = WorkflowCompiler(operatorRegistry={"test.map": _MapItemOperator}).compile(
        document
    )
    events: list[dict[str, object]] = []
    runner = WorkflowRunner(
        compiled,
        {},
        eventPublisher=lambda **event: events.append(event),
    )

    with pytest.raises(WorkflowExecutionError) as raised:
        runner.run(
            "main",
            {"items": ["a"], "prefix": "edge-"},
            RunContext.root("job", "main"),
            CancellationToken(),
        )

    assert raised.value.code == "E_OPERATOR_UNAVAILABLE"
    loopFailures = [event for event in events if event["eventType"] == "loop.failed"]
    assert len(loopFailures) == 1
    assert loopFailures[0]["code"] == "E_OPERATOR_UNAVAILABLE"
    assert not any(event["eventType"] == "loop.completed" for event in events)


def _whilePayload() -> dict[str, object]:
    payload = _metadata("while-v2")
    payload["workflowOrder"] = ["main", "condition", "body"]
    payload["workflows"] = {
        "main": {
            "name": "Main",
            "inputs": {"count": "integer"},
            "outputs": {"count": "integer"},
            "nodes": [
                {"nodeId": "input", "kind": "workflow_input"},
                {
                    "nodeId": "while",
                    "kind": "loop",
                    "inputPorts": {"count": "integer"},
                    "outputPorts": {"count": "integer"},
                    "loop": {
                        "contractVersion": 2,
                        "mode": "while",
                        "conditionWorkflowId": "condition",
                        "bodyWorkflowId": "body",
                        "maxIterations": 5,
                        "timeoutMs": 0,
                    },
                },
                {"nodeId": "output", "kind": "workflow_output"},
            ],
            "edges": [
                {"fromNode": "input", "fromPort": "count", "toNode": "while", "toPort": "count"},
                {"fromNode": "while", "fromPort": "count", "toNode": "output", "toPort": "count"},
            ],
        },
        "condition": {
            "name": "Condition",
            "inputs": {"count": "integer"},
            "outputs": {"continue": "boolean"},
            "nodes": [
                {"nodeId": "input", "kind": "workflow_input"},
                {
                    "nodeId": "condition",
                    "kind": "operator",
                    "operatorId": "test.condition",
                    "inputPorts": {"count": "integer"},
                    "outputPorts": {"continue": "boolean"},
                    "params": {"limit": 3},
                },
                {"nodeId": "output", "kind": "workflow_output"},
            ],
            "edges": [
                {"fromNode": "input", "fromPort": "count", "toNode": "condition", "toPort": "count"},
                {"fromNode": "condition", "fromPort": "continue", "toNode": "output", "toPort": "continue"},
            ],
        },
        "body": {
            "name": "Body",
            "inputs": {"count": "integer"},
            "outputs": {"count": "integer"},
            "nodes": [
                {"nodeId": "input", "kind": "workflow_input"},
                {
                    "nodeId": "increment",
                    "kind": "operator",
                    "operatorId": "test.increment",
                    "inputPorts": {"count": "integer"},
                    "outputPorts": {"count": "integer"},
                },
                {"nodeId": "output", "kind": "workflow_output"},
            ],
            "edges": [
                {"fromNode": "input", "fromPort": "count", "toNode": "increment", "toPort": "count"},
                {"fromNode": "increment", "fromPort": "count", "toNode": "output", "toPort": "count"},
            ],
        },
    }
    return payload


def testWhileV2UsesBodyPortsAsTypedState() -> None:
    result = _run(
        _whilePayload(),
        {
            "test.condition": _LessThanOperator,
            "test.increment": _IncrementOperator,
        },
        {"count": 0},
    )

    assert result == {"count": 3}
