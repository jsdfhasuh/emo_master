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


def testDebugSaverCannotOverwriteReleaseOutputAndUnicodeResourceRoot(channel, tmp_path, sample):
    from pathlib import Path
    from emo_master.core.project.models import ProjectDocument
    raw = sample(tmp_path, image=False).model_dump()
    raw["workflows"]["main"]["nodes"].append({"nodeId": "save", "operatorId": "vision.io.image_saver"})
    raw["workflows"]["main"]["edges"].append({"fromNode": "load", "fromPort": "image", "toNode": "save", "toPort": "image"})
    raw["resources"]["siteBindings"] = [{"target": {"workflowId": "main", "nodeId": "save", "parameterPath": ["outputPath"]},
                                         "field": "output", "purpose": "output_file", "required": True}]
    sourceRoot = tmp_path / "中文 resource folder"
    sourceRoot.mkdir()
    (sourceRoot / "input.png").write_bytes((tmp_path / "input.png").read_bytes())
    project = ProjectDocument.model_validate(raw)
    release = channel.prepare(project, sourceRoot, mode="release", releaseRevision="release-test", siteValues={"output": "saved.png"})
    releaseFile = Path(release.snapshot.outputRoot) / "saved.png"
    releaseFile.write_bytes(b"production output sentinel")
    debug = channel.prepare(project, sourceRoot, siteValues={"output": "saved.png"})
    job = channel.start(debug.snapshot.snapshotId)
    assert waitResult(channel, job).sources[0].valueJson == "2"
    assert (Path(debug.snapshot.outputRoot) / "saved.png").is_file()
    assert releaseFile.read_bytes() == b"production output sentinel"


def testValidRebindOnlyChangesNextExplicitJob(channel, tmp_path, sample):
    from emo_master.core.project.models import WorkflowNode, WorkflowEdge
    project = sample(tmp_path, image=False)
    project.workflows["main"].nodes.append(WorkflowNode(nodeId="second", operatorId="vision.collection.count"))
    project.workflows["main"].edges.append(WorkflowEdge(fromNode="blob", fromPort="blobs", toNode="second", toPort="blobs"))
    original = channel.prepare(project, tmp_path)
    project.presentation.dataSources["count"].nodeId = "second"
    rebound = channel.prepare(project, tmp_path)
    assert original.snapshot.executionRevision == rebound.snapshot.executionRevision
    assert original.snapshot.capturePlanRevision != rebound.snapshot.capturePlanRevision
    first = waitResult(channel, channel.start(original.snapshot.snapshotId))
    second = waitResult(channel, channel.start(rebound.snapshot.snapshotId))
    assert first.sources[0].valueJson == second.sources[0].valueJson == "2"
    assert first.identity.capturePlanRevision == original.snapshot.capturePlanRevision
    assert second.identity.capturePlanRevision == rebound.snapshot.capturePlanRevision


def testOnePresentationOwnerAndFailedPrepareDoesNotLeakAdmission(channel, tmp_path, sample, monkeypatch):
    from emo_master.apps.runtime.presentation.service import PresentationService
    import emo_master.apps.runtime.presentation.service as module
    with pytest.raises(ValueError, match="already"):
        PresentationService(channel.runtime, tmp_path / "second")

    def fail(*args, **kwargs):
        raise RuntimeError("injected exporter startup failure")
    monkeypatch.setattr(module, "ExportPool", fail)
    with pytest.raises(RuntimeError, match="startup"):
        channel.prepare(sample(tmp_path), tmp_path)
    assert not channel.prepared and not channel.jobs
    assert not list((channel.root / "prepared").iterdir())
    assert not list(channel.root.rglob("runtime.sqlite3"))
