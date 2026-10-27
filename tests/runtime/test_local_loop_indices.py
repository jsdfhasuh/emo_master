"""ForEach + While carries per-invocation indices, never touching shared state."""
from concurrent.futures import ThreadPoolExecutor

import pytest

from emo_master.apps.designer.state.project_store import loadProjectDocument, saveProject
from emo_master.apps.designer.state.workflow_store import WorkflowStore
from emo_master.apps.runtime.workflow.cancellation import CancellationRequested, CancellationToken
from emo_master.apps.runtime.workflow.context import RunContext
from emo_master.apps.runtime.workflow.runner import WorkflowRunner
from emo_master.core.contracts.geometry2d import DetectionCollection
from emo_master.core.project.models import ProjectDocument
from emo_master.core.workflow.compiler import WorkflowCompiler
from emo_master.plugins.builtins.collection_count.operator import CollectionCountOperator
from emo_master.plugins.builtins.collection_select.operator import CollectionSelectOperator
from emo_master.plugins.builtins.integer_add.operator import IntegerAddOperator
from emo_master.plugins.builtins.number_compare.operator import NumberCompareOperator
from tests.plugins.test_collection_operators import _detections


def localIndicesProject():
    def edge(source, port, target, targetPort):
        return dict(fromNode=source, fromPort=port, toNode=target, toPort=targetPort)

    def operator(nodeId, cls, **params):
        return dict(nodeId=nodeId, kind="operator", operatorId=cls.meta.operatorId, params=params)

    def workflow(inputs, outputs, nodes, edges):
        return dict(name="Local indices", inputs=inputs, outputs=outputs,
            nodes=[dict(nodeId="input", kind="workflow_input"), *nodes, dict(nodeId="output", kind="workflow_output")], edges=edges)

    state = dict(index="integer", count="integer", detections="detectionCollection", hasNext="boolean")
    payload = WorkflowStore().toPayload("Local loop state")
    payload["workflows"] = {
        "main": workflow({"collections": "list<detectionCollection>"}, {"index": "list<integer>"}, [
            dict(nodeId="foreach", kind="loop", inputPorts={"items": "list<detectionCollection>"},
                outputPorts={"index": "list<integer>"}, loop=dict(contractVersion=2, mode="foreach",
                bodyWorkflowId="group", itemInputPort="detections", maxIterations=20, timeoutMs=0)),
        ], [edge("input", "collections", "foreach", "items"), edge("foreach", "index", "output", "index")]),
        "group": workflow({"detections": "detectionCollection"}, {"index": "integer"}, [
            operator("zero", IntegerAddOperator), operator("count", CollectionCountOperator),
            operator("has", NumberCompareOperator, operator="lt"),
            dict(nodeId="while", kind="loop", inputPorts=state, outputPorts=state,
                loop=dict(contractVersion=2, mode="while", bodyWorkflowId="body",
                conditionMode="boolean", conditionPort="hasNext", maxIterations=20, timeoutMs=0)),
        ], [edge("input", "detections", "count", "detections"), edge("input", "detections", "while", "detections"),
            edge("zero", "value", "while", "index"), edge("zero", "value", "has", "left"),
            edge("count", "count", "has", "right"), edge("count", "count", "while", "count"),
            edge("has", "result", "while", "hasNext"), edge("while", "index", "output", "index")]),
        "body": workflow(state, state, [
            operator("select", CollectionSelectOperator, mode="index", index=99, onOutOfRange="error"),
            operator("next", IntegerAddOperator, rightValue=1), operator("has", NumberCompareOperator, operator="lt"),
        ], [edge("input", "detections", "select", "detections"), edge("input", "index", "select", "index"),
            edge("input", "index", "next", "left"), edge("next", "value", "has", "left"),
            edge("input", "count", "has", "right"), edge("has", "result", "output", "hasNext"),
            edge("next", "value", "output", "index"), edge("input", "count", "output", "count"),
            edge("input", "detections", "output", "detections")]),
    }
    payload["workflowOrder"] = list(payload["workflows"])
    registry = {cls.meta.operatorId: cls for cls in (IntegerAddOperator, NumberCompareOperator,
                                                   CollectionCountOperator, CollectionSelectOperator)}
    payload["dependencies"] = dict(operators=[dict(operatorId=key, version=cls.meta.version) for key, cls in registry.items()])
    return ProjectDocument.model_validate(payload), registry


class NoSharedVariables:
    def __getattr__(self, name):
        raise AssertionError(f"local loop indices must not access global variables: {name}")


def groups():
    source = _detections()
    return [value.toPayload() for value in (source, DetectionCollection((), source.coordinateSpace),
            DetectionCollection(source.items[:1], source.coordinateSpace), source)]


def testNestedLocalIndicesSaveReloadResetAndDoNotAccessDatabase(tmp_path, monkeypatch):
    document, registry = localIndicesProject()
    path = tmp_path / "local.emoproj"
    saveProject(path, document.model_dump(mode="json"))
    assert loadProjectDocument(path) == document and document.schemaVersion == "2.7"
    compiled = WorkflowCompiler(registry).compile(loadProjectDocument(path))
    visited = []
    execute = CollectionSelectOperator.executeNode

    def select(self, inputs, params, context):
        result = execute(self, inputs, params, context)
        selected = DetectionCollection.fromPayload(result["outputs"]["selectedDetections"])
        visited.append((context["jobId"], inputs["index"], selected.items[0].detectionId))
        return result

    monkeypatch.setattr(CollectionSelectOperator, "executeNode", select)
    runner = WorkflowRunner(compiled, registry, globalVariables=NoSharedVariables())
    for job in ("first", "second"):
        result = runner.run("main", dict(collections=groups()), RunContext.root(job, "main"), CancellationToken())
        assert result.outputs == {"index": [2, 0, 1, 2]}
        assert [(index, name) for jid, index, name in visited if jid == job] == [(0, "d1"), (1, "d2"), (0, "d1"), (0, "d1"), (1, "d2")]


def testCancelledIterationDoesNotLeakStateIntoTheNextRun(monkeypatch):
    document, registry = localIndicesProject()
    runner = WorkflowRunner(WorkflowCompiler(registry).compile(document), registry, globalVariables=NoSharedVariables())
    token = CancellationToken()
    execute = CollectionSelectOperator.executeNode

    def stop(self, inputs, params, context):
        result = execute(self, inputs, params, context)
        token.cancel()
        return result

    monkeypatch.setattr(CollectionSelectOperator, "executeNode", stop)
    with pytest.raises(CancellationRequested):
        runner.run("main", dict(collections=groups()), RunContext.root("stop", "main"), token)
    monkeypatch.setattr(CollectionSelectOperator, "executeNode", execute)
    result = runner.run("main", dict(collections=groups()), RunContext.root("fresh", "main"), CancellationToken())
    assert result.outputs == {"index": [2, 0, 1, 2]}


def testConcurrentRunsKeepIndependentLocalIndices():
    document, registry = localIndicesProject()
    compiled = WorkflowCompiler(registry).compile(document)

    def run(job):
        runner = WorkflowRunner(compiled, registry, globalVariables=NoSharedVariables())
        return runner.run("main", dict(collections=groups()), RunContext.root(job, "main"), CancellationToken()).outputs

    with ThreadPoolExecutor(2) as pool:
        assert list(pool.map(run, ("first", "second"))) == [{"index": [2, 0, 1, 2]}] * 2
