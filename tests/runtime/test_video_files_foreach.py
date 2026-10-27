import numpy as np
import pytest

from emo_master.apps.designer.state.project_store import loadProjectDocument, saveProject
from emo_master.apps.runtime.workflow.cancellation import CancellationRequested, CancellationToken
from emo_master.apps.runtime.workflow.context import RunContext
from emo_master.apps.runtime.workflow.runner import WorkflowRunner
from emo_master.core.project.runtime_directory import prepareRuntimeProject
from emo_master.core.workflow.compiler import WorkflowCompiler
from emo_master.core.workflow.errors import WorkflowCompileError
from emo_master.plugins.builtins.blur.operator import BlurOperator
from emo_master.plugins.builtins.video_loader.operator import VideoLoaderOperator
from tests.plugins.test_video_loader_operator import makeVideo
from tests.runtime.test_image_batch_while import _registry
from tests.runtime.video_files_fixture import fileVideoProject


def prepare(tmp_path):
    paths = [makeVideo(tmp_path / "videos/01.avi", count=1), makeVideo(tmp_path / "videos/02.mp4", count=6)]
    registry = _registry()
    document = fileVideoProject(registry)
    path = tmp_path / "files.emoproj"
    saveProject(path, document.model_dump(mode="json"))
    assert loadProjectDocument(path) == document
    prepared = prepareRuntimeProject(document, tmp_path, registry)
    load = next(node for node in prepared.workflows["frame"].nodes if node.nodeId == "load")
    assert load.params["videoPath"] == "old-missing-draft.mp4"
    return WorkflowCompiler(registry).compile(prepared), registry, paths


def testFileListForEachWhileProcessesEveryFrameAndRestarts(tmp_path, monkeypatch):
    compiled, registry, paths = prepare(tmp_path)
    processed = []
    execute = BlurOperator.executeNode

    def record(self, inputs, params, context):
        processed.append(float(np.mean(inputs["image"])))
        return execute(self, inputs, params, context)

    monkeypatch.setattr(BlurOperator, "executeNode", record)
    runner = WorkflowRunner(compiled, registry)
    for job in ("first", "second"):
        result = runner.run("main", {}, RunContext.root(job, "main"), CancellationToken())
        assert result.outputs == {"videoPath": [str(path) for path in paths]}
        assert runner._lifecycleOperators == {}
    assert processed == pytest.approx([0, 0, 30, 50] * 2, abs=5)
    # Repeating the very same path in one Job resets only the first frame of each visit.
    processed.clear()
    repeated = [str(paths[1]), str(paths[1])]
    result = runner.run("batch", {"items": repeated}, RunContext.root("repeat", "batch"), CancellationToken())
    assert result.outputs == {"videoPath": repeated}
    assert processed == pytest.approx([0, 30, 50] * 2, abs=5)


def testEmptyFileListDoesNotEnterVideoReader(tmp_path, monkeypatch):
    (tmp_path / "videos").mkdir()
    registry = _registry()
    prepared = prepareRuntimeProject(fileVideoProject(registry), tmp_path, registry)
    def forbidden(*args):
        raise AssertionError("empty lists must not execute the video reader")
    monkeypatch.setattr(VideoLoaderOperator, "executeNode", forbidden)
    runner = WorkflowRunner(WorkflowCompiler(registry).compile(prepared), registry)
    assert runner.run("main", {}, RunContext.root("empty", "main"), CancellationToken()).outputs == {"videoPath": []}


def testStoppingNestedLoopsReleasesCurrentVideo(tmp_path, monkeypatch):
    compiled, registry, _ = prepare(tmp_path)
    captures = []
    token = CancellationToken()
    read = VideoLoaderOperator.executeNode
    execute = BlurOperator.executeNode

    def track(self, inputs, params, context):
        result = read(self, inputs, params, context)
        if self._capture is not None:
            captures.append(self._capture)
        return result

    def stop(self, inputs, params, context):
        if captures:
            token.cancel()
        return execute(self, inputs, params, context)

    monkeypatch.setattr(VideoLoaderOperator, "executeNode", track)
    monkeypatch.setattr(BlurOperator, "executeNode", stop)
    runner = WorkflowRunner(compiled, registry)
    with pytest.raises(CancellationRequested):
        runner.run("main", {}, RunContext.root("stop", "main"), token)
    assert captures and all(not capture.isOpened() for capture in captures)
    assert runner._lifecycleOperators == {}


def testConnectedPathCanOmitLiteralButUnconnectedPathCannot(tmp_path):
    registry = _registry()
    document = fileVideoProject(registry)
    node = next(node for node in document.workflows["frame"].nodes if node.nodeId == "load")
    node.params.pop("videoPath")
    WorkflowCompiler(registry).compile(document)
    document.workflows["frame"].edges = [edge for edge in document.workflows["frame"].edges
                                       if not (edge.toNode == "load" and edge.toPort == "videoPath")]
    with pytest.raises(WorkflowCompileError, match="videoPath"):
        WorkflowCompiler(registry).compile(document)


def testFileListInputDirectoryIsStillProtected(tmp_path):
    (tmp_path / "videos").mkdir()
    registry = _registry()
    document = fileVideoProject(registry)
    from emo_master.core.project.models import WorkflowNode
    document.workflows["frame"].nodes.append(WorkflowNode(nodeId="save", operatorId="vision.io.image_saver",
        params={"outputPath": "videos/overwrite.png"}))
    with pytest.raises(WorkflowCompileError, match="output overlaps"):
        prepareRuntimeProject(document, tmp_path, registry)


def testDynamicVideoPathWithVariableBoundInterval(tmp_path):
    from emo_master.apps.runtime.context.global_variables import ProjectGlobalVariables
    from emo_master.apps.runtime.context.sqlite_store import SqliteStore
    from emo_master.core.project.models import ProjectDocument

    path = makeVideo(tmp_path / "videos/one.avi", count=6)
    registry = _registry()
    payload = fileVideoProject(registry).model_dump(mode="json")
    payload["globalVariables"] = {"interval": dict(name="interval", type="number", initialValue=.25, lifetime="job")}
    node = next(node for node in payload["workflows"]["frame"]["nodes"] if node["nodeId"] == "load")
    node["params"].pop("videoPath")
    node["globalVariableBindings"] = [dict(parameterPath=["intervalSec"], variableId="interval")]
    document = ProjectDocument.model_validate(payload)
    compiled = WorkflowCompiler(registry).compile(prepareRuntimeProject(document, tmp_path, registry))
    store = SqliteStore(tmp_path / "variables.db")
    store.initialize()
    variables = ProjectGlobalVariables(store, document.project.projectId, document.globalVariables, "bound")
    variables.synchronize()
    variables.initializeJob()
    samples = []

    def publish(eventType, context, message, level="INFO", code="", payload=None):
        if eventType == "node.completed" and context.callerNodeId == "load":
            samples.append(payload["outputs"]["frameIndex"])

    try:
        runner = WorkflowRunner(compiled, registry, globalVariables=variables, eventPublisher=publish)
        assert runner.run("main", {}, RunContext.root("bound", "main"), CancellationToken()).outputs == {"videoPath": [str(path)]}
        assert samples == [0, 3, 5]
    finally:
        store.releaseIdleConnection()


def testRepositoryFileListExampleCompiles():
    from pathlib import Path
    path = Path(__file__).resolve().parents[2] / "examples/video_files_foreach/video-files-foreach.emoproj"
    document = loadProjectDocument(path)
    assert document.schemaVersion == "2.7"
    assert document.devices.definitions == {}
    WorkflowCompiler(_registry()).compile(document)


def testFileListAndDynamicVideoPathInSpawnedRuntimeJob(tmp_path):
    import json
    import os
    from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb
    from emo_master.apps.runtime.grpc_server.service import RuntimeService
    from tests.runtime.runtime_test_utils import jobFailureDetails, waitForTerminal

    _, _, paths = prepare(tmp_path)
    path = tmp_path / "files.emoproj"
    document = loadProjectDocument(path)
    service = RuntimeService(dbPath=tmp_path / "runtime.db", workspaceRoot=tmp_path / "jobs")
    try:
        loaded = service.LoadProject(pb.LoadProjectRequest(project_path=str(path)), None)
        assert loaded.ok, loaded.message
        loadedVideo = next(node for node in service.loadedDocument.workflows["frame"].nodes if node.nodeId == "load")
        assert loadedVideo.params["videoPath"] == "old-missing-draft.mp4"
        started = service.StartJob(pb.StartJobRequest(project_id=document.project.projectId,
                                  workflow_id="main", inputs_json="{}"), None)
        assert started.ok, started.message
        status = waitForTerminal(service, started.job_id, timeoutSeconds=30)
        assert status.status == "COMPLETED", jobFailureDetails(service, started.job_id, status)
        assert status.pid > 0 and status.pid != os.getpid()
        events = service.eventStore.read(started.job_id)
        samples = [json.loads(event.payloadJson)["outputs"] for event in events
                   if event.eventType == "node.completed" and event.nodeId == "load"]
        assert [sample["videoPath"] for sample in samples] == [str(paths[0]), *[str(paths[1])] * 3]
        assert [sample["frameIndex"] for sample in samples] == [0, 0, 3, 5]
        assert [sample["hasNext"] for sample in samples] == [False, True, True, False]
    finally:
        service.close()
