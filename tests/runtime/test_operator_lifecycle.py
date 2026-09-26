from __future__ import annotations

import pytest

from emo_master.apps.runtime.workflow.cancellation import (
    CancellationRequested,
    CancellationToken,
)
from emo_master.apps.runtime.workflow.context import RunContext
from emo_master.apps.runtime.workflow.runner import WorkflowExecutionError, WorkflowRunner
from emo_master.core.project.models import ProjectDocument
from emo_master.core.workflow.compiler import WorkflowCompiler


class _LifecycleEcho:
    initCount = 0
    executeCount = 0
    disposeCount = 0

    def initOperator(self, initContext):
        assert callable(initContext["raiseIfCancellationRequested"])
        type(self).initCount += 1

    def executeNode(self, inputs, params, runtimeContext):
        _ = params
        assert callable(runtimeContext["raiseIfCancellationRequested"])
        type(self).executeCount += 1
        return {"status": "ok", "outputs": {"value": inputs["item"]}}

    def disposeOperator(self):
        type(self).disposeCount += 1


class _OrdinaryEcho:
    constructCount = 0
    executeCount = 0

    def __init__(self):
        type(self).constructCount += 1

    def executeNode(self, inputs, params, runtimeContext):
        _ = params
        _ = runtimeContext
        type(self).executeCount += 1
        return {"status": "ok", "outputs": {"value": inputs["item"]}}


class _SubflowLifecycle:
    initCount = 0
    executeCount = 0
    disposeCount = 0

    def initOperator(self, initContext):
        _ = initContext
        type(self).initCount += 1

    def executeNode(self, inputs, params, runtimeContext):
        _ = inputs
        _ = params
        _ = runtimeContext
        type(self).executeCount += 1
        return {"status": "ok", "outputs": {}}

    def disposeOperator(self):
        type(self).disposeCount += 1


class _TrackedLifecycle:
    createdNames: list[str] = []
    disposedNames: list[str] = []
    failExecute = False
    failDispose = False
    raiseCancellation = False

    def __init__(self):
        self.name = ""

    def initOperator(self, initContext):
        self.name = str(initContext["nodeId"])
        type(self).createdNames.append(self.name)

    def executeNode(self, inputs, params, runtimeContext):
        _ = inputs
        _ = params
        _ = runtimeContext
        if type(self).raiseCancellation:
            raise CancellationRequested("cancel during stateful operator")
        if type(self).failExecute:
            return {
                "status": "error",
                "error": {"code": "E_PRIMARY", "message": "primary failure"},
            }
        return {"status": "ok", "outputs": {}}

    def disposeOperator(self):
        type(self).disposedNames.append(self.name)
        if type(self).failDispose:
            raise RuntimeError(f"cannot dispose {self.name}")


class _InitFailure:
    disposeCount = 0
    failDispose = False

    def initOperator(self, initContext):
        _ = initContext
        raise RuntimeError("init failed")

    def executeNode(self, inputs, params, runtimeContext):
        raise AssertionError("must not execute")

    def disposeOperator(self):
        type(self).disposeCount += 1
        if type(self).failDispose:
            raise RuntimeError("dispose after init failure failed")


@pytest.fixture(autouse=True)
def _resetLifecycleState() -> None:
    _LifecycleEcho.initCount = 0
    _LifecycleEcho.executeCount = 0
    _LifecycleEcho.disposeCount = 0
    _OrdinaryEcho.constructCount = 0
    _OrdinaryEcho.executeCount = 0
    _SubflowLifecycle.initCount = 0
    _SubflowLifecycle.executeCount = 0
    _SubflowLifecycle.disposeCount = 0
    _TrackedLifecycle.createdNames = []
    _TrackedLifecycle.disposedNames = []
    _TrackedLifecycle.failExecute = False
    _TrackedLifecycle.failDispose = False
    _TrackedLifecycle.raiseCancellation = False
    _InitFailure.disposeCount = 0
    _InitFailure.failDispose = False


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


def _loopProject(operatorId: str) -> dict[str, object]:
    payload = _metadata("lifecycle-loop")
    payload["workflowOrder"] = ["main", "body"]
    payload["workflows"] = {
        "main": {
            "name": "Main",
            "inputs": {"items": "list<integer>"},
            "outputs": {"value": "list<integer>"},
            "nodes": [
                {"nodeId": "input", "kind": "workflow_input"},
                {
                    "nodeId": "loop",
                    "kind": "loop",
                    "inputPorts": {"items": "list<integer>"},
                    "outputPorts": {"value": "list<integer>"},
                    "loop": {
                        "contractVersion": 2,
                        "mode": "foreach",
                        "bodyWorkflowId": "body",
                        "itemInputPort": "item",
                        "maxIterations": 10,
                        "timeoutMs": 0,
                    },
                },
                {"nodeId": "output", "kind": "workflow_output"},
            ],
            "edges": [
                {"fromNode": "input", "fromPort": "items", "toNode": "loop", "toPort": "items"},
                {"fromNode": "loop", "fromPort": "value", "toNode": "output", "toPort": "value"},
            ],
        },
        "body": {
            "name": "Body",
            "inputs": {"item": "integer"},
            "outputs": {"value": "integer"},
            "nodes": [
                {"nodeId": "input", "kind": "workflow_input"},
                {
                    "nodeId": "camera",
                    "kind": "operator",
                    "operatorId": operatorId,
                    "inputPorts": {"item": "integer"},
                    "outputPorts": {"value": "integer"},
                },
                {"nodeId": "output", "kind": "workflow_output"},
            ],
            "edges": [
                {"fromNode": "input", "fromPort": "item", "toNode": "camera", "toPort": "item"},
                {"fromNode": "camera", "fromPort": "value", "toNode": "output", "toPort": "value"},
            ],
        },
    }
    return payload


def _run(payload: dict[str, object], registry: dict[str, object], inputs=None):
    compiled = WorkflowCompiler(operatorRegistry=registry).compile(
        ProjectDocument.model_validate(payload)
    )
    runner = WorkflowRunner(compiled, registry)
    return runner.run(
        "main",
        dict(inputs or {}),
        RunContext.root("job", "main"),
        CancellationToken(),
    )


def testLifecycleOperatorIsInitializedOnceAcrossLoopAndDisposedOnce() -> None:
    result = _run(
        _loopProject("test.lifecycle"),
        {"test.lifecycle": _LifecycleEcho},
        {"items": [1, 2, 3]},
    )

    assert result.outputs == {"value": [1, 2, 3]}
    assert (_LifecycleEcho.initCount, _LifecycleEcho.executeCount, _LifecycleEcho.disposeCount) == (1, 3, 1)


def testOrdinaryOperatorKeepsPerExecutionInstantiationSemantics() -> None:
    result = _run(
        _loopProject("test.ordinary"),
        {"test.ordinary": _OrdinaryEcho},
        {"items": [1, 2, 3]},
    )

    assert result.outputs == {"value": [1, 2, 3]}
    assert (_OrdinaryEcho.constructCount, _OrdinaryEcho.executeCount) == (3, 3)


def testLifecycleOperatorIsReusedAcrossRepeatedSubflowCalls() -> None:
    payload = _metadata("lifecycle-subflow")
    payload["workflowOrder"] = ["main", "child"]
    payload["workflows"] = {
        "main": {
            "name": "Main",
            "inputs": {},
            "outputs": {},
            "nodes": [
                {"nodeId": "input", "kind": "workflow_input"},
                {"nodeId": "call-a", "kind": "subflow", "targetWorkflowId": "child"},
                {"nodeId": "call-b", "kind": "subflow", "targetWorkflowId": "child"},
                {"nodeId": "output", "kind": "workflow_output"},
            ],
            "edges": [],
        },
        "child": {
            "name": "Child",
            "inputs": {},
            "outputs": {},
            "nodes": [
                {"nodeId": "input", "kind": "workflow_input"},
                {
                    "nodeId": "camera",
                    "kind": "operator",
                    "operatorId": "test.subflow-lifecycle",
                    "inputPorts": {},
                    "outputPorts": {},
                },
                {"nodeId": "output", "kind": "workflow_output"},
            ],
            "edges": [],
        },
    }

    _run(payload, {"test.subflow-lifecycle": _SubflowLifecycle})

    assert (
        _SubflowLifecycle.initCount,
        _SubflowLifecycle.executeCount,
        _SubflowLifecycle.disposeCount,
    ) == (1, 2, 1)


def _sequentialProject(nodeNames: tuple[str, ...]) -> dict[str, object]:
    payload = _metadata("lifecycle-sequential")
    payload["workflowOrder"] = ["main"]
    payload["workflows"] = {
        "main": {
            "name": "Main",
            "inputs": {},
            "outputs": {},
            "nodes": [
                {"nodeId": "input", "kind": "workflow_input"},
                *[
                {
                    "nodeId": name,
                    "kind": "operator",
                    "operatorId": "test.tracked",
                    "inputPorts": {},
                    "outputPorts": {},
                }
                for name in nodeNames
                ],
                {"nodeId": "output", "kind": "workflow_output"},
            ],
            "edges": [],
        }
    }
    return payload


def testLifecycleOperatorsDisposeInReverseCreationOrder() -> None:
    _run(_sequentialProject(("first", "second")), {"test.tracked": _TrackedLifecycle})

    assert _TrackedLifecycle.createdNames == ["first", "second"]
    assert _TrackedLifecycle.disposedNames == ["second", "first"]


def testLifecycleOperatorDisposesAfterExecutionFailure() -> None:
    _TrackedLifecycle.failExecute = True

    with pytest.raises(WorkflowExecutionError) as error:
        _run(_sequentialProject(("camera",)), {"test.tracked": _TrackedLifecycle})

    assert error.value.code == "E_PRIMARY"
    assert _TrackedLifecycle.disposedNames == ["camera"]


def testLifecycleOperatorDisposesAfterCancellation() -> None:
    _TrackedLifecycle.raiseCancellation = True

    with pytest.raises(CancellationRequested):
        _run(_sequentialProject(("camera",)), {"test.tracked": _TrackedLifecycle})

    assert _TrackedLifecycle.disposedNames == ["camera"]


def testInitializationFailureImmediatelyDisposesPartialInstance() -> None:
    payload = _metadata("lifecycle-init-failure")
    payload["workflowOrder"] = ["main"]
    payload["workflows"] = {
        "main": {
            "name": "Main",
            "inputs": {},
            "outputs": {},
            "nodes": [
                {"nodeId": "input", "kind": "workflow_input"},
                {
                    "nodeId": "camera",
                    "kind": "operator",
                    "operatorId": "test.init-failure",
                    "inputPorts": {},
                    "outputPorts": {},
                },
                {"nodeId": "output", "kind": "workflow_output"},
            ],
            "edges": [],
        }
    }

    with pytest.raises(RuntimeError, match="init failed"):
        _run(payload, {"test.init-failure": _InitFailure})

    assert _InitFailure.disposeCount == 1


def testCleanupFailureFailsOtherwiseSuccessfulWorkflow() -> None:
    _TrackedLifecycle.failDispose = True
    events: list[dict[str, object]] = []
    payload = _sequentialProject(("camera",))
    registry = {"test.tracked": _TrackedLifecycle}
    compiled = WorkflowCompiler(operatorRegistry=registry).compile(
        ProjectDocument.model_validate(payload)
    )
    runner = WorkflowRunner(
        compiled,
        registry,
        eventPublisher=lambda **event: events.append(event),
    )

    with pytest.raises(WorkflowExecutionError) as error:
        runner.run("main", {}, RunContext.root("job", "main"), CancellationToken())

    assert error.value.code == "E_RESOURCE_CLEANUP_FAILED"
    assert not any(event["eventType"] == "workflow.completed" for event in events)
    cleanupEvents = [
        event for event in events if event["eventType"] == "resource.cleanup.failed"
    ]
    assert len(cleanupEvents) == 1
    assert cleanupEvents[0]["code"] == "E_RESOURCE_CLEANUP_FAILED"


def testInitializationCleanupFailurePublishesEventWithoutOverridingInitError() -> None:
    _InitFailure.failDispose = True
    events: list[dict[str, object]] = []
    payload = _metadata("lifecycle-init-cleanup-failure")
    payload["workflowOrder"] = ["main"]
    payload["workflows"] = {
        "main": {
            "name": "Main",
            "inputs": {},
            "outputs": {},
            "nodes": [
                {"nodeId": "input", "kind": "workflow_input"},
                {
                    "nodeId": "camera",
                    "kind": "operator",
                    "operatorId": "test.init-failure",
                    "inputPorts": {},
                    "outputPorts": {},
                },
                {"nodeId": "output", "kind": "workflow_output"},
            ],
            "edges": [],
        }
    }
    registry = {"test.init-failure": _InitFailure}
    compiled = WorkflowCompiler(operatorRegistry=registry).compile(
        ProjectDocument.model_validate(payload)
    )
    runner = WorkflowRunner(
        compiled,
        registry,
        eventPublisher=lambda **event: events.append(event),
    )

    with pytest.raises(RuntimeError, match="init failed") as error:
        runner.run("main", {}, RunContext.root("job", "main"), CancellationToken())

    assert error.value.diagnostics["resourceCleanup"]["code"] == "E_RESOURCE_CLEANUP_FAILED"
    cleanupEvents = [
        event for event in events if event["eventType"] == "resource.cleanup.failed"
    ]
    assert len(cleanupEvents) == 1
    assert cleanupEvents[0]["code"] == "E_RESOURCE_CLEANUP_FAILED"


def testCleanupFailureDoesNotOverridePrimaryError() -> None:
    _TrackedLifecycle.failExecute = True
    _TrackedLifecycle.failDispose = True

    with pytest.raises(WorkflowExecutionError) as error:
        _run(_sequentialProject(("camera",)), {"test.tracked": _TrackedLifecycle})

    assert error.value.code == "E_PRIMARY"
    assert error.value.diagnostics["resourceCleanup"]["code"] == "E_RESOURCE_CLEANUP_FAILED"
