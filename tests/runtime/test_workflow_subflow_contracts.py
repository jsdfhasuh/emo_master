from __future__ import annotations

import pytest

from emo_master.apps.runtime.workflow.cancellation import CancellationToken
from emo_master.apps.runtime.workflow.context import RunContext
from emo_master.apps.runtime.workflow.runner import (
    WorkflowExecutionError,
    WorkflowRunner,
)
from emo_master.core.project.models import ProjectDocument
from emo_master.core.workflow.compiler import WorkflowCompiler
from emo_master.core.workflow.errors import WorkflowCompileError
from emo_master.plugins.builtins.flow_if.operator import FlowIfOperator


def _workflow(targetWorkflowId: str | None = None) -> dict[str, object]:
    nodes: list[dict[str, object]] = [
        {"nodeId": "input", "kind": "workflow_input"},
    ]
    edges: list[dict[str, str]] = []
    sourceNode = "input"
    if targetWorkflowId is not None:
        nodes.append(
            {
                "nodeId": "call",
                "kind": "subflow",
                "targetWorkflowId": targetWorkflowId,
            }
        )
        edges.append(
            {
                "fromNode": "input",
                "fromPort": "value",
                "toNode": "call",
                "toPort": "value",
            }
        )
        sourceNode = "call"
    nodes.append({"nodeId": "output", "kind": "workflow_output"})
    edges.append(
        {
            "fromNode": sourceNode,
            "fromPort": "value",
            "toNode": "output",
            "toPort": "value",
        }
    )
    return {
        "name": targetWorkflowId or "Leaf",
        "inputs": {"value": "string"},
        "outputs": {"value": "string"},
        "nodes": nodes,
        "edges": edges,
        "layout": {"nodePositions": {}},
    }


def _project(workflowIds: tuple[str, ...]) -> dict[str, object]:
    workflows = {
        workflowId: _workflow(
            workflowIds[index + 1] if index + 1 < len(workflowIds) else None
        )
        for index, workflowId in enumerate(workflowIds)
    }
    return {
        "schemaVersion": "2.0",
        "project": {
            "projectId": "subflow-project",
            "name": "Subflows",
            "revision": 1,
            "createdAt": "2026-01-01T00:00:00Z",
            "updatedAt": "2026-01-01T00:00:00Z",
        },
        "entryWorkflowId": workflowIds[0],
        "workflowOrder": list(workflowIds),
        "workflows": workflows,
        "runtime": {"maxConcurrentJobs": 2},
        "dependencies": {"operators": []},
        "devices": {"bindings": {}},
    }


def _runner(
    payload: dict[str, object],
    events: list[dict[str, object]] | None = None,
    *,
    maxCallDepth: int = 32,
    registry: dict[str, object] | None = None,
) -> WorkflowRunner:
    operators = dict(registry or {})
    compiled = WorkflowCompiler(operatorRegistry=operators).compile(
        ProjectDocument.model_validate(payload)
    )
    publisher = None
    if events is not None:
        def publish(**event) -> None:
            events.append(event)

        publisher = publish
    return WorkflowRunner(
        compiled,
        operators,
        eventPublisher=publisher,
        maxCallDepth=maxCallDepth,
    )


def testSubflowMapsInputsOutputsAndRunContext() -> None:
    events: list[dict[str, object]] = []
    runner = _runner(_project(("main", "child")), events)
    root = RunContext.root("job-subflow", "main")

    result = runner.run("main", {"value": "mapped"}, root, CancellationToken())

    assert result.outputs == {"value": "mapped"}
    childStarted = next(
        event
        for event in events
        if event["eventType"] == "workflow.started"
        and event["context"].workflowId == "child"
    )
    childContext = childStarted["context"]
    assert childContext.workflowRunId != root.workflowRunId
    assert childContext.parentWorkflowRunId == root.workflowRunId
    assert childContext.callDepth == 1
    assert childContext.callerNodeId == "call"


def testNestedSubflowPreservesParentRunChain() -> None:
    events: list[dict[str, object]] = []
    runner = _runner(_project(("main", "child", "grandchild")), events)
    root = RunContext.root("job-nested", "main")

    result = runner.run("main", {"value": "nested"}, root, CancellationToken())

    assert result.outputs == {"value": "nested"}
    contexts = {
        event["context"].workflowId: event["context"]
        for event in events
        if event["eventType"] == "workflow.started"
    }
    assert contexts["child"].parentWorkflowRunId == root.workflowRunId
    assert (
        contexts["grandchild"].parentWorkflowRunId
        == contexts["child"].workflowRunId
    )
    assert contexts["grandchild"].callDepth == 2


def testWorkflowCompilerRejectsRecursiveSubflow() -> None:
    payload = _project(("main", "child"))
    payload["workflows"]["child"] = _workflow("main")

    with pytest.raises(WorkflowCompileError) as error:
        WorkflowCompiler().compile(ProjectDocument.model_validate(payload))

    assert any(issue.code == "E_WORKFLOW_RECURSIVE" for issue in error.value.issues)


def testSubflowRejectsInvalidInputType() -> None:
    runner = _runner(_project(("main", "child")))

    with pytest.raises(WorkflowExecutionError) as error:
        runner.run("main", {"value": 3}, RunContext.root("job", "main"), CancellationToken())

    assert error.value.code == "E_INPUT_TYPE"


def testSubflowEnforcesMaximumCallDepth() -> None:
    runner = _runner(
        _project(("main", "child", "grandchild")),
        maxCallDepth=1,
    )

    with pytest.raises(WorkflowExecutionError) as error:
        runner.run("main", {"value": "depth"}, RunContext.root("job", "main"), CancellationToken())

    assert error.value.code == "E_CALL_DEPTH"


def testUnselectedIfBranchSkipsSubflowAndDownstream() -> None:
    payload = _project(("main", "child"))
    payload["workflows"]["main"] = {
        "name": "Main",
        "inputs": {"value": "string"},
        "outputs": {"value": "string"},
        "nodes": [
            {"nodeId": "input", "kind": "workflow_input"},
            {
                "nodeId": "branch",
                "kind": "operator",
                "operatorId": "vision.flow.if",
                "params": {"mode": "bool"},
            },
            {
                "nodeId": "call",
                "kind": "subflow",
                "targetWorkflowId": "child",
            },
            {"nodeId": "output", "kind": "workflow_output"},
        ],
        "edges": [
            {
                "fromNode": "input",
                "fromPort": "value",
                "toNode": "branch",
                "toPort": "value",
            },
            {
                "fromNode": "branch",
                "fromPort": "false",
                "toNode": "call",
                "toPort": "value",
            },
            {
                "fromNode": "call",
                "fromPort": "value",
                "toNode": "output",
                "toPort": "value",
            },
        ],
        "layout": {"nodePositions": {}},
    }
    events: list[dict[str, object]] = []
    runner = _runner(
        payload,
        events,
        registry={"vision.flow.if": FlowIfOperator},
    )

    with pytest.raises(WorkflowExecutionError) as error:
        runner.run(
            "main",
            {"value": "selected-true"},
            RunContext.root("job", "main"),
            CancellationToken(),
        )

    assert error.value.code == "E_OUTPUT_MISSING"
    callEvents = [
        event["eventType"]
        for event in events
        if event["context"].workflowId == "main"
        and event["context"].callerNodeId == "call"
    ]
    assert callEvents == ["node.skipped"]
    assert not any(
        event["eventType"] == "workflow.started"
        and event["context"].workflowId == "child"
        for event in events
    )
