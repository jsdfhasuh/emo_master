from copy import deepcopy
import math

import pytest

from emo_master.apps.runtime.workflow.runner import WorkflowRunner
from emo_master.apps.runtime.workflow.cancellation import CancellationToken
from emo_master.apps.runtime.workflow.context import RunContext
from emo_master.core.workflow.compiler import WorkflowCompiler
from emo_master.core.workflow.errors import WorkflowCompileError
from emo_master.core.contracts.sqlite_writer import OPERATOR_ID, rebindParams, toStorage, SqliteWriterError
from emo_master.apps.designer.state.workflow_store import WorkflowStore
from emo_master.apps.designer.state.workflow_package import buildWorkflowPackage, importWorkflowPackage
from emo_master.plugins.builtins.sqlite_writer.operator import SqliteWriterOperator


def mapping(column="value", node="input", port="value", storage="JSON", missing="error"):
    return {"column": column, "storageType": storage, "missing": missing,
            "source": {"kind": "node_output", "nodeId": node, "port": port}}


def config(path="business.sqlite3", rows=None, failure="stop"):
    return {"configVersion": 1, "databasePath": str(path), "table": "records",
            "mappings": [mapping()] if rows is None else rows, "failurePolicy": failure}


def project(rows=None, nodes=None, edges=None):
    nodes = deepcopy(nodes) if nodes is not None else [
        {"nodeId": "writer", "operatorId": OPERATOR_ID, "params": config(rows=rows)}]
    for kind, key in (("workflow_input", "input"), ("workflow_output", "output")):
        if not any(n.get("kind") == kind for n in nodes):
            nodes.append({"nodeId": key, "kind": kind})
    return {"schemaVersion": "2.1", "project": {"projectId": "sqlite-test", "name": "SQLite",
        "revision": 1, "createdAt": "2026-10-05T00:00:00Z", "updatedAt": "2026-10-05T00:00:00Z"},
        "entryWorkflowId": "main", "workflowOrder": ["main"], "workflows": {"main": {
        "name": "Main", "inputs": {"value": "json"}, "outputs": {},
        "nodes": nodes, "edges": edges or []}},
        "runtime": {}, "dependencies": {"operators": []}, "devices": {"bindings": {}}}


class Probe(SqliteWriterOperator):
    calls = []

    def executeNode(self, inputs, params, context):
        self.calls.append((deepcopy(inputs), dict(context["mappedOutputs"]), context["workflowRunId"], context["iterationPath"]))
        return {"status": "ok", "outputs": {"receipt": {"status": "SKIPPED"}}}


def run(payload, value, job="job"):
    registry = {OPERATOR_ID: Probe}
    compiled = WorkflowCompiler(registry).compile(payload)
    runner = WorkflowRunner(compiled, registry)
    runner.run("main", {"value": value}, RunContext.root(job, "main"), CancellationToken())
    return compiled


@pytest.mark.parametrize("value", [0, False, "", None, [1, 2], {"x": [3]}])
def testDeclaredDependencyWithoutCanvasEdgeUsesActualInvocation(value):
    Probe.calls.clear()
    compiled = run(project(), value)
    flow = compiled.workflows["main"]
    assert flow.topologicalOrder == ("input", "output", "writer")
    assert not flow.edges and set(flow.nodeById["writer"].inputPorts) == {"enabled"}
    inputs, bound, workflowRun, _ = Probe.calls[-1]
    assert not inputs and bound[0].workflowRunId == workflowRun
    assert bound[0].nodeRunId and bound[0].value == toStorage(value, "JSON")


@pytest.mark.parametrize("row,code", [
    (mapping(node="removed"), "E_BINDING_NODE"), (mapping(port="gone"), "E_BINDING_PORT"),
    (mapping(storage="REAL"), "E_BINDING_TYPE"), (mapping(node="writer", port="receipt"), "E_WORKFLOW_CYCLE")])
def testMissingTypeAndCycleAreCompileErrors(row, code):
    with pytest.raises(WorkflowCompileError) as error:
        WorkflowCompiler({OPERATOR_ID: Probe}).compile(project([row]))
    assert code in {i.code for i in error.value.issues}


def testMixedCanvasAndMappingCycleRejected():
    row = mapping(node="producer", port="value")
    nodes = [{"nodeId": "writer", "operatorId": OPERATOR_ID, "params": config(rows=[row])},
             {"nodeId": "producer", "kind": "operator", "operatorId": "test.producer",
              "inputPorts": {"receipt": "json"}, "outputPorts": {"value": "json"}}]
    with pytest.raises(WorkflowCompileError) as error:
        WorkflowCompiler({OPERATOR_ID: Probe, "test.producer": object()}).compile(project(nodes=nodes, rows=[row],
            edges=[{"fromNode": "writer", "fromPort": "receipt", "toNode": "producer", "toPort": "receipt"}]))
    assert "E_WORKFLOW_CYCLE" in {i.code for i in error.value.issues}


def testBuiltinSqlitePreservesActualCoreCapabilityGate():
    from pathlib import Path
    from emo_master import __version__
    from emo_master.core.plugin.registry import PluginRegistry
    root = Path('src/emo_master/plugins')
    legacy = PluginRegistry(coreVersion='0.4.0').scan(root)
    # The seven migration bridges intentionally require the current core too;
    # do not lower their gates just to retain the old rejection-count fixture.
    migrationOperators = {
        "vision.analysis.minimum_enclosing_circle", "vision.geometry.detection_bbox",
        "vision.geometry.extract_points", "vision.geometry.reframe_points", "vision.flow.error",
        "communication.gateway.coordinate_format", "communication.gateway.ack_validate",
    }
    assert set(legacy.rejectedOperators) == {
        OPERATOR_ID, "vision.state.variable_read", "vision.state.variable_write", *migrationOperators}
    assert legacy.rejectedOperators[OPERATOR_ID][0].code == 'E_CORE_VERSION_INCOMPATIBLE'
    assert OPERATOR_ID not in legacy.activeOperators and 'vision.value.number' in legacy.activeOperators
    current = PluginRegistry(coreVersion=__version__).scan(root)
    assert current.rejectedOperators == {} and OPERATOR_ID in current.activeOperators


def testIndependentJobsNeverReuseDelivery():
    Probe.calls.clear()
    run(project(), "A", "A")
    run(project(), "B", "B")
    assert [call[1][0].value for call in Probe.calls] == ['"A"', '"B"']
    assert len({call[2] for call in Probe.calls}) == 2


def testRepeatedSubflowAndLoopInvocationsAreIsolated():
    payload = project()
    child = deepcopy(payload["workflows"]["main"])
    main = {"name": "Main", "inputs": {"value": "json"}, "outputs": {}, "nodes": [
        {"nodeId": "input", "kind": "workflow_input"},
        {"nodeId": "output", "kind": "workflow_output"},
        {"nodeId": "A", "kind": "subflow", "targetWorkflowId": "body"},
        {"nodeId": "B", "kind": "subflow", "targetWorkflowId": "body"},
        {"nodeId": "repeat", "kind": "loop", "inputPorts": {"value": "json"}, "loop": {"mode": "repeat", "contractVersion": 2,
            "bodyWorkflowId": "body", "repeatCount": 2, "maxIterations": 2, "timeoutMs": 0}}],
        "edges": [{"fromNode": "input", "fromPort": "value", "toNode": key, "toPort": "value"} for key in ("A", "B", "repeat")]}
    payload["workflows"] = {"main": main, "body": child}
    payload["workflowOrder"] = ["main", "body"]
    Probe.calls.clear()
    run(payload, {"data": "actual"})
    assert len(Probe.calls) == 4 and len({call[2] for call in Probe.calls}) == 4
    assert [call[3] for call in Probe.calls][-2:] == [[0], [1]]
    assert all(call[1][0].workflowRunId == call[2] for call in Probe.calls)
    payload["workflows"]["main"]["nodes"].append({"nodeId": "cross", "operatorId": OPERATOR_ID,
        "params": config(rows=[mapping(node="writer", port="receipt")])})
    with pytest.raises(WorkflowCompileError, match="来源节点不存在"):
        WorkflowCompiler({OPERATOR_ID: Probe}).compile(payload)


def testCopyRebindAndWorkflowImportDetachParams():
    original = config()
    copied = rebindParams(original, {"input": "input-copy"})
    assert original["mappings"][0]["source"]["nodeId"] == "input"
    assert copied["mappings"][0]["source"]["nodeId"] == "input-copy"
    copied["mappings"][0]["column"] = "other"
    assert original["mappings"][0]["column"] == "value"
    source = WorkflowStore(project())
    package = buildWorkflowPackage(source, "main")
    target = WorkflowStore()
    result = importWorkflowPackage(target, package)
    imported = target.get(result.rootWorkflowId)
    writer = next(n for n in imported.nodes if n.get("operatorId") == OPERATOR_ID)
    boundary = next(n for n in imported.nodes if n.get("kind") == "workflow_input")
    assert writer["params"]["mappings"][0]["source"]["nodeId"] == boundary["nodeId"]
    assert source.get().nodes[0]["params"] == original


@pytest.mark.parametrize("value,storage", [(math.inf, "REAL"), (math.nan, "JSON"), (2**63, "INTEGER"),
                                           (2**53 + 1, "REAL"), (1.1, "INTEGER"), (True, "INTEGER")])
def testLossyAndNonfiniteValuesRejected(value, storage):
    with pytest.raises(SqliteWriterError):
        toStorage(value, storage)
