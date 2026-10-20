import hashlib

import pytest

from emo_master.apps.runtime.workflow.cancellation import CancellationToken
from emo_master.apps.runtime.workflow.context import RunContext
from emo_master.apps.runtime.workflow.runner import WorkflowRunner, WorkflowExecutionError
from emo_master.core.workflow.compiler import WorkflowCompiler
from emo_master.plugins.builtins._coordinate_operators import CoordinateReaderOperator
from emo_master.plugins.builtins.flow_switch.operator import FlowSwitchOperator
from emo_master.plugins.builtins._image_frame import defaultFrame
from emo_master.core.contracts.geometry2d import Point2D


def project(path, mode="session"):
    workflows = {}
    for root in ("a", "b"):
        workflows[root] = {"name": root, "inputs": {"after": "integer", "frame": "bbox2d"},
            "outputs": {"points": "list<point2d>", "hash": "string"}, "nodes": [
                {"nodeId": "input", "kind": "workflow_input"},
                {"nodeId": "reader", "kind": "operator", "operatorId": CoordinateReaderOperator.meta.operatorId,
                 "params": {"filePath": str(path), "readMode": mode, "headerMode": "absent"}},
                {"nodeId": "output", "kind": "workflow_output"}],
            "edges": [dict(fromNode="input", fromPort="frame", toNode="reader", toPort="frame"),
                      dict(fromNode="reader", fromPort="points", toNode="output", toPort="points"),
                      dict(fromNode="reader", fromPort="contentHash", toNode="output", toPort="hash")]}
    workflows["main"] = {"name": "Main", "inputs": {"phase": "integer", "frame": "bbox2d"},
        "outputs": {key: {"type": typeName, "required": False} for key, typeName in
                    (("a", "list<point2d>"), ("b", "list<point2d>"), ("ha", "string"), ("hb", "string"))},
        "nodes": [{"nodeId": "input", "kind": "workflow_input"},
                  {"nodeId": "switch", "kind": "operator", "operatorId": FlowSwitchOperator.meta.operatorId,
                   "params": {"case1Value": "1", "case2Value": "2"}},
                  *[{"nodeId": key, "kind": "subflow", "targetWorkflowId": key} for key in ("a", "b")],
                  {"nodeId": "output", "kind": "workflow_output"}],
        "edges": [dict(fromNode="input", fromPort="phase", toNode="switch", toPort="value"),
                  *[edge for index, key in enumerate(("a", "b"), 1) for edge in (
                      dict(fromNode="switch", fromPort=f"case{index}", toNode=key, toPort="after"),
                      dict(fromNode="input", fromPort="frame", toNode=key, toPort="frame"),
                      dict(fromNode=key, fromPort="points", toNode="output", toPort=key),
                      dict(fromNode=key, fromPort="hash", toNode="output", toPort="h" + key))]]}
    return {"schemaVersion": "2.1", "project": {"projectId": "snapshot", "name": "Snapshot",
        "revision": 1, "createdAt": "2026-10-09T00:00:00Z", "updatedAt": "2026-10-09T00:00:00Z"},
        "entryWorkflowId": "main", "workflowOrder": ["main", "a", "b"], "workflows": workflows}


def runner(path, mode="session"):
    registry = {cls.meta.operatorId: cls for cls in (CoordinateReaderOperator, FlowSwitchOperator)}
    return WorkflowRunner(WorkflowCompiler(registry).compile(project(path, mode)), registry, retainOperators=True)


def run(runner, phase=1, job="job"):
    return runner.run("main", {"phase": phase, "frame": defaultFrame(10, 10).toPayload()},
                      RunContext.root(job, "main"), CancellationToken()).outputs


def testAdmissionFreezesEvenAnUnselectedReaderAndNewSessionReloads(tmp_path):
    path = tmp_path / "points.txt"
    original = b"1 2\n"
    path.write_bytes(original)
    r = runner(path)
    first = run(r)
    path.write_bytes(b"3 4\n")
    second = run(r, 2)
    assert first["a"] == second["b"]
    assert first["ha"] == second["hb"] == hashlib.sha256(original).hexdigest()
    path.unlink()
    assert run(r, 2)["b"] == first["a"]  # no live read after admission
    r.closeSession(RunContext.root("job", "main"))
    path.write_bytes(b"3 4\n")
    fresh = runner(path)
    loaded = run(fresh, 2)
    assert loaded["hb"] != first["ha"]
    assert Point2D.fromPayload(loaded["b"][0]).x == 3
    fresh.closeSession(RunContext.root("job", "main"))


@pytest.mark.parametrize("content", [None, b"1 invalid\n", b"\xff\xfe"])
def testAdmissionRejectsBeforeWorkflowDeviceSideEffects(tmp_path, content):
    path = tmp_path / "points.txt"
    if content is not None:
        path.write_bytes(content)
    r = runner(path)
    events = []
    r.eventPublisher = lambda **event: events.append(event)
    with pytest.raises(WorkflowExecutionError):
        run(r)
    assert not any(e["eventType"] in {"workflow.started", "node.started"} for e in events)
    assert not r.coordinateSnapshots._contents
    r.closeSession(RunContext.root("job", "main"))


def testOldReaderRefreshAndSnapshotJobIsolation(tmp_path):
    path = tmp_path / "points.txt"
    path.write_bytes(b"1 2\n")
    r = runner(path, "perInvocation")
    first = run(r)
    path.write_bytes(b"3 4\n")
    second = run(r, 2)
    assert first["ha"] != second["hb"]
    r.closeSession(RunContext.root("job", "main"))
    r = runner(path)
    run(r)
    with pytest.raises(WorkflowExecutionError) as raised:
        run(r, job="different")
    assert raised.value.code == "E_COORDINATE_SNAPSHOT_CONTEXT"
    r.closeSession(RunContext.root("job", "main"))
