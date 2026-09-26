import json
import time

import pytest

from emo_master.core.presentation.results import ClosedSource
from emo_master.apps.runtime.context.sqlite_store import SqliteStore


@pytest.mark.parametrize("value", ["1e309", "-1e999", '{"a":[1e309]}', "NaN", "[" * 14 + "0" + "]" * 14,
                                  json.dumps([0] * 4097), json.dumps("x" * (256 * 1024))], ids=["overflow", "negative", "nested", "nan", "depth", "elements", "bytes"])
def testInvalidValues(value):
    with pytest.raises(ValueError):
        ClosedSource(sourceId="s", state="AVAILABLE", valueJson=value)


@pytest.mark.parametrize("value", ["0", "false", "[]", "null", "1e308"])
def testValidValues(value):
    assert ClosedSource(sourceId="s", state="AVAILABLE", valueJson=value).valueJson == value


def waitResult(channel, job, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        snapshot = channel.store.snapshot(job)
        if snapshot["results"]:
            return snapshot["results"][0]
        time.sleep(.02)
    pytest.fail(f"no closed result; status={channel.runtime.jobRepository.get(job)}")


def testRealSpawnScalarUsesStableInputAndDebugState(channel, tmp_path, sample):
    project = sample(tmp_path, image=False)
    release = channel.prepare(project, tmp_path, mode="release", releaseRevision="approved-test")
    releaseDb = SqliteStore(__import__("pathlib").Path(release.snapshot.runtimeDbPath))
    releaseDb.setGlobalCounter(project.project.projectId, "production", 100)
    debug = channel.prepare(project, tmp_path)
    debugDb = SqliteStore(__import__("pathlib").Path(debug.snapshot.runtimeDbPath))
    debugDb.applyGlobalCounter(project.project.projectId, "production", increment=True)
    debugDb.resetGlobalCounter(project.project.projectId, "production")
    assert releaseDb.getGlobalCounter(project.project.projectId, "production").value == 100
    assert not channel.jobs  # preparation is not execution
    (tmp_path / "input.png").unlink()
    job = channel.start(debug.snapshot.snapshotId)
    result = waitResult(channel, job)
    assert result.status == "COMPLETE"
    assert result.sources[0].valueJson == "2"
    assert result.identity.jobId == job and result.identity.mode == "debug"
    assert len(channel.jobs) == 1


def testPreparationRejectsResourceMutationAndSemanticParameter(channel, tmp_path, sample):
    project = sample(tmp_path, image=False)
    (tmp_path / "input.png").write_bytes(b"changed")
    with pytest.raises(ValueError, match="mismatch"):
        channel.prepare(project, tmp_path)
    project = sample(tmp_path, image=False)
    project.workflows["main"].nodes[2].params["connectivity"] = 6
    with pytest.raises(ValueError):
        channel.prepare(project, tmp_path)


def testPreparedCopyTamperAndNextBinding(channel, tmp_path, sample):
    project = sample(tmp_path, image=False)
    first = channel.prepare(project, tmp_path)
    project.presentation.dataSources["count"].port = "missing"
    job = channel.start(first.snapshot.snapshotId)
    assert waitResult(channel, job).sources[0].valueJson == "2"
    with pytest.raises(ValueError):
        channel.prepare(project, tmp_path)
    first.projectPath.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="changed"):
        channel.start(first.snapshot.snapshotId)


def testRealRepeatedSubflowsAndLoopInvocations(channel, tmp_path, sample):
    from emo_master.core.project.models import ProjectDocument
    raw = sample(tmp_path, image=False).model_dump()
    raw["workflows"]["child"] = raw["workflows"].pop("main")
    raw["workflows"]["main"] = {"name": "root", "nodes": [
        {"nodeId": "input", "kind": "workflow_input"},
        {"nodeId": "a", "kind": "subflow", "targetWorkflowId": "child"},
        {"nodeId": "b", "kind": "subflow", "targetWorkflowId": "child"},
        {"nodeId": "loop", "kind": "loop", "loop": {"contractVersion": 1, "bodyWorkflowId": "child", "mode": "repeat", "repeatCount": 3, "maxIterations": 3}},
        {"nodeId": "output", "kind": "workflow_output"}]}
    raw["workflowOrder"] = ["main", "child"]
    raw["resources"]["parameterBindings"][0]["target"]["workflowId"] = "child"
    presentation = raw["presentation"]
    presentation["resultScopes"] = {}
    presentation["dataSources"] = {}
    presentation["pages"]["main"]["components"] = []
    for index, (scope, relation) in enumerate([("a", "subflow"), ("b", "subflow"), ("loop", "loop_body")]):
        path = [{"nodeId": scope, "relation": relation}]
        presentation["resultScopes"][scope] = {"entryWorkflowId": "main", "scopeWorkflowId": "child", "callPath": path}
        presentation["dataSources"][scope] = {"kind": "node_output", "resultScopeId": scope, "workflowId": "child", "nodeId": "count", "port": "count", "expectedType": "integer", "callPath": path}
        presentation["pages"]["main"]["components"].append({"componentId": scope, "type": "number", "bindings": {"value": scope}, "layout": {"row": index}})
    record = channel.prepare(ProjectDocument.model_validate(raw), tmp_path)
    job = channel.start(record.snapshot.snapshotId)
    until = time.monotonic() + 15
    while time.monotonic() < until and not channel.runtime.jobRepository.get(job).isTerminal:
        time.sleep(.02)
    assert channel.runtime.jobRepository.get(job).status == "COMPLETED"
    results = list(channel.store.history)
    assert len(results) == 5
    assert [r.identity.resultOrdinal for r in results if r.identity.resultScopeId == "loop"] == [1, 2, 3]
    assert len({r.identity.invocationId for r in results}) == 5
    assert all(r.sources[0].valueJson == "2" for r in results)
