"""Real CPU ORT with synthetic graphs; not business-model accuracy acceptance."""
import json
from threading import Event
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from emo_master.apps.designer.state.project_store import loadProjectDocument, saveProject
from emo_master.apps.designer.state.run_result_history import RunResultHistory
from emo_master.apps.runtime.jobs.worker_main import _runContinuous
from emo_master.apps.runtime.preview.store import PreviewSnapshotWriter
from emo_master.apps.runtime.workflow.cancellation import CancellationRequested, CancellationToken
from emo_master.apps.runtime.workflow.context import RunContext
from emo_master.apps.runtime.workflow.runner import WorkflowRunner
from emo_master.core.workflow.compiler import WorkflowCompiler
from emo_master.core.workflow.errors import WorkflowCompileError
from emo_master.plugins.builtins.patchcore_inference.onnx_backend import PatchCoreSession
from tests.plugins.patchcore_fixture import makeDeployment
from tests.runtime.patchcore_fixture import project
from tests.runtime.test_builtin_vision_operator_workflows import _registry


@pytest.mark.parametrize("score", [1.0, 2.5])
@pytest.mark.parametrize("draw", [True, False])
@pytest.mark.parametrize("overlayMode", ["heatmap", "highlight"])
def testProjectRoundTripBranchScoreSaveAndHistory(tmp_path, score, draw, overlayMode):
    raw = np.full((1, 1, 256, 256), 2., np.float32)
    raw[0, 0, 110:140, 140:180] = 6
    model = makeDeployment(tmp_path / "model", score=score, rawMap=raw)
    output = tmp_path / "overlay.png"
    document = project(model, outputPath=output, drawHeatmap=draw, drawOverlay=draw, overlayMode=overlayMode)
    path = tmp_path / "inspection.emoproj"
    saveProject(path, document.model_dump(mode="json"))
    assert loadProjectDocument(path) == document
    registry = _registry()
    compiled = WorkflowCompiler(operatorRegistry=registry).compile(document)
    history = RunResultHistory()
    history.accept("job", [{**node.model_dump(), "workflowId": "main"}
                           for node in document.workflows["main"].nodes], revision=document.project.revision)
    events, snapshots = [], []

    def publish(**event):
        events.append(event)
        context = event["context"]
        history.applyEvent({**event, "sequence": len(events), "jobId": context.jobId,
            "workflowId": context.workflowId, "nodeId": context.callerNodeId,
            "workflowRunId": context.workflowRunId, "nodeRunId": context.nodeRunId})

    writer = PreviewSnapshotWriter(tmp_path, jobId="job", projectRevision=document.project.revision,
                                   inspectionPublisher=lambda **event: snapshots.append(event))
    runner = WorkflowRunner(compiled, registry, eventPublisher=publish, previewSnapshotStore=writer)
    image = np.random.default_rng(6).integers(0, 256, (13, 29, 3), dtype=np.uint8)
    result = runner.run("main", {"image": image}, RunContext.root("job", "main", workspacePath=str(tmp_path)), CancellationToken())
    anomaly = score >= 2.5
    assert result.outputs == {"score": score, "isAnomaly": anomaly, "label": "NG" if anomaly else "OK"}
    assert not runner._lifecycleOperators
    assert json.loads((tmp_path / "results/score.json").read_text()) == score
    assert json.loads((tmp_path / "results" / ("NG.json" if anomaly else "OK.json")).read_text()) is anomaly
    assert not (tmp_path / "results" / ("OK.json" if anomaly else "NG.json")).exists()
    record = history.view("main", "patchcore")["record"]
    assert record["status"] == "COMPLETED"
    entries = [entry for snapshot in snapshots for entry in snapshot["payload"]["entries"]
               if entry["nodeId"] == "patchcore"]
    assert {entry["port"] for entry in entries} == ({"heatmap", "overlay"} if draw else set())
    assert set(record["imageOutputMessages"]) == (set() if draw else {"heatmap", "overlay"})
    if draw:
        entry = next(entry for entry in entries if entry["port"] == "overlay")
        assert entry["nodeRunId"] == record["nodeRunId"]
        displayed = cv2.imdecode(np.fromfile(writer.inspectionRoot / entry["relativePath"], np.uint8), cv2.IMREAD_COLOR)
        saved = cv2.imdecode(np.fromfile(output, np.uint8), cv2.IMREAD_COLOR)
        assert displayed.shape == saved.shape == image.shape
        np.testing.assert_array_equal(displayed, saved)
        if overlayMode == "highlight":
            np.testing.assert_array_equal(saved[:, :5], image[:, :5])
            if anomaly:
                assert np.all(saved == (0, 255, 255), axis=2).any()
            else:
                np.testing.assert_array_equal(saved, image)
    else:
        assert not output.exists()
        assert history.view("main", "save")["observation"]["status"] == "SKIPPED"


@pytest.mark.parametrize("params,valid", [
    ({"overlayMode": "heatmap", "highlightThreshold": "unfinished"}, True),
    ({"drawOverlay": False, "overlayMode": "unfinished", "highlightThreshold": "unfinished"}, True),
    ({"overlayMode": "highlight", "highlightThreshold": "unfinished"}, False),
    ({"overlayMode": "highlight", "highlightThreshold": 0}, False),
])
def testWorkflowValidatesOnlyActiveDisplayParameters(tmp_path, params, valid):
    document = project(makeDeployment(tmp_path / "model"), **params)
    registry = _registry()
    if not valid:
        with pytest.raises(WorkflowCompileError) as failure:
            WorkflowCompiler(operatorRegistry=registry).compile(document)
        assert {issue.code for issue in failure.value.issues} == {"E_PARAM_INVALID"}
        return
    compiled = WorkflowCompiler(operatorRegistry=registry).compile(document)
    result = WorkflowRunner(compiled, registry).run("main", {"image": np.zeros((13, 29, 3), np.uint8)},
        RunContext.root("job", "main", workspacePath=str(tmp_path)), CancellationToken())
    assert result.outputs == {"score": 2.5, "isAnomaly": True, "label": "NG"}


def testContinuousSessionReuseStopAndRestartWithDifferentModel(tmp_path, monkeypatch):
    first = makeDeployment(tmp_path / "first")
    second = makeDeployment(tmp_path / "second", score=1.)
    document = project(first, drawHeatmap=False, drawOverlay=False)
    registry = _registry()
    compiled = WorkflowCompiler(operatorRegistry=registry).compile(document)
    created = []
    initialize = PatchCoreSession.__init__

    def track(session, *args, **kwargs):
        initialize(session, *args, **kwargs)
        created.append(session)

    monkeypatch.setattr(PatchCoreSession, "__init__", track)
    stop, completed, scores = Event(), [], []

    def publish(**event):
        if event["eventType"] == "node.completed" and event["context"].callerNodeId == "patchcore":
            scores.append(event["payload"]["outputs"]["score"])
        if event["eventType"] == "workflow.completed":
            completed.append(event["context"].workflowRunId)
            if len(completed) == 2:
                assert len(created) == 1 and created[0].session is not None
            elif len(completed) == 3:
                stop.set()

    runner = WorkflowRunner(compiled, registry, eventPublisher=publish, retainOperators=True)
    spec = SimpleNamespace(jobId="job", workflowId="main", projectId=document.project.projectId,
                           jobWorkspacePath=str(tmp_path), cycleIntervalMs=1)
    inputs = {"image": np.zeros((7, 9), np.uint8)}
    with pytest.raises(CancellationRequested):
        _runContinuous(runner, spec, inputs, CancellationToken(stop), stop)
    assert scores == [2.5, 2.5, 2.5] and len(set(completed)) == 3
    assert len(created) == 1 and created[0].session is None
    assert not runner._lifecycleOperators
    compiled = WorkflowCompiler(operatorRegistry=registry).compile(project(second))
    restarted = WorkflowRunner(compiled, registry)
    assert restarted.run("main", inputs, RunContext.root("restart", "main"), CancellationToken()).outputs["score"] == 1.
    assert len(created) == 2 and all(session.session is None for session in created)


def testCancellationDuringPredictionDisposesSession(tmp_path, monkeypatch):
    document = project(makeDeployment(tmp_path / "model"))
    registry = _registry()
    compiled = WorkflowCompiler(operatorRegistry=registry).compile(document)
    stop, sessions = Event(), []
    predict = PatchCoreSession.predict

    def cancelAfterPrediction(session, rgb):
        sessions.append(session)
        result = predict(session, rgb)
        stop.set()
        return result

    monkeypatch.setattr(PatchCoreSession, "predict", cancelAfterPrediction)
    runner = WorkflowRunner(compiled, registry)
    with pytest.raises(CancellationRequested):
        runner.run("main", {"image": np.zeros((7, 9), np.uint8)}, RunContext.root("job", "main"), CancellationToken(stop))
    assert len(sessions) == 1 and sessions[0].session is None
    assert not runner._lifecycleOperators
