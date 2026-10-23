from pathlib import Path

import cv2
import numpy as np
import pytest

from examples.rtsp_face_detection.build_project import MODEL, buildProject, operatorRegistry
from emo_master.apps.designer.state.project_store import loadProjectDocument, saveProject
from emo_master.apps.runtime.workflow.cancellation import CancellationToken, CancellationRequested
from emo_master.apps.runtime.workflow.context import RunContext
from emo_master.apps.runtime.workflow.runner import WorkflowRunner
from emo_master.core.project.runtime_directory import prepareRuntimeProject
from emo_master.core.workflow.compiler import WorkflowCompiler
from emo_master.plugins.builtins._image_frame import defaultFrame
from emo_master.plugins.builtins.rtsp_camera.operator import RtspCameraOperator
from emo_master.plugins.builtins.yolo_inference import operator as yolo
from tests.plugins.test_yolo_inference_operator import _FakeModel, _result


def fixtureProject(root):
    (root / "models").mkdir()
    (root / "samples").mkdir()
    (root / MODEL).write_bytes(b"fixture-model-not-for-inference")
    image = np.arange(12 * 16 * 3, dtype=np.uint8).reshape(12, 16, 3)
    for name in ("astronaut", "empty"):
        assert cv2.imwrite(str(root / f"samples/{name}.png"), image)
    registry = operatorRegistry()
    document = buildProject(root, rtspUrl="rtsp://camera.invalid/live", password="fixture-only", registry=registry)
    path = root / "face.emoproj"
    saveProject(path, document.model_dump(mode="json"))
    return path, document, registry, image


def testCurrentFormatAndPortableResourceBindings(tmp_path):
    path, document, registry, _ = fixtureProject(tmp_path)
    assert document.schemaVersion == "2.7"
    assert document.devices.schemaVersion == "1.0" and not document.devices.definitions
    assert "communication" not in document.model_dump(mode="json")
    assert document.production.autoStart is False
    assert document.entryWorkflowId == "main"
    assert set(document.workflows) == {"main", "camera_tick", "camera_once", "detect", "sample", "empty"}
    loop = next(n for n in document.workflows["main"].nodes if n.nodeId == "camera_loop").loop
    assert loop["unlimited"] and loop["timeoutMs"] == 0 and loop["mode"] == "while"
    scope = document.presentation.resultScopes["main"]
    assert scope.scopeWorkflowId == "camera_once"
    assert [step.nodeId for step in scope.callPath] == ["camera_loop", "capture"]
    assert document.presentation.defaultPageId == "main"
    assert document.presentation.dataSources["main_image"].nodeId == "preview_image"
    assert document.presentation.dataSources["main_overlay"].nodeId == "preview_overlay"
    assert len(document.resources.parameterBindings) == 3
    assert loadProjectDocument(path) == document
    prepared = prepareRuntimeProject(document, tmp_path, registry)
    WorkflowCompiler(operatorRegistry=registry).compile(prepared)
    detector = next(n for n in prepared.workflows["detect"].nodes if n.nodeId == "yolo")
    assert Path(detector.params["modelPath"]) == tmp_path / MODEL
    assert not detector.params["outputCrop"]
    assert all(n.operatorId != "communication.camera.hikvision_audio"
               for w in document.workflows.values() for n in w.nodes)
    assert next(n for n in document.workflows["camera_once"].nodes if n.nodeId == "source").params["passwordEnv"] == ""


@pytest.mark.parametrize("workflow", ["camera_once", "sample", "empty"])
@pytest.mark.parametrize("count", [0, 1, 2])
def testWholeWorkflowUsesCorrectedPixelsAndHandlesZeroFaces(tmp_path, monkeypatch, workflow, count):
    _, document, registry, original = fixtureProject(tmp_path)
    def capture(self, inputs, params, context):
        return {"status": "ok", "outputs": {"image": original.copy(), "frame": defaultFrame(16, 12).toPayload(), "capturedAt": 1.0}}
    monkeypatch.setattr(RtspCameraOperator, "executeNode", capture)
    fake = _FakeModel(_result([[2, 2, 8, 10], [10, 3, 14, 9]][:count], [0.9, 0.8][:count], [0, 0][:count], {0: "face"}))
    monkeypatch.setattr(yolo, "_createModel", lambda path: fake)
    yolo.YoloInferenceOperator.clearCache()
    try:
        prepared = prepareRuntimeProject(document, tmp_path, registry)
        runner = WorkflowRunner(WorkflowCompiler(operatorRegistry=registry).compile(prepared), registry)
        for iteration in range(2):
            out = runner.run(workflow, {}, RunContext.root(str(iteration), workflow), CancellationToken()).outputs
            expected = cv2.flip(original, -1) if workflow == "camera_once" else original
            assert np.array_equal(out["image"], expected)
            assert np.array_equal(fake.calls[-1]["image"], expected)
            assert out["faceCount"] == count and out["hasFace"] is (count > 0)
            assert len(out["detections"]["items"]) == count
            if count == 0:
                assert np.array_equal(out["overlay"], expected)
            if workflow == "camera_once":
                assert out["frame"]["coordinateSpace"]["transformToSource"] == [-1.0, 0.0, 0.0, -1.0, 15.0, 11.0]
            receipt = out["archive"]
            assert receipt["saved"] is (count > 0)
            assert len(receipt["cropPaths"]) == count
            for crop, (x1, y1, x2, y2) in zip(receipt["cropPaths"], [[2, 2, 8, 10], [10, 3, 14, 9]][:count]):
                assert np.array_equal(cv2.imread(crop), expected[y1:y2, x1:x2])
            files = list((tmp_path / "outputs").rglob("*.*"))
            assert len(files) == ((iteration + 1) * (count + 1) if count else 0)
    finally:
        yolo.YoloInferenceOperator.clearCache()


def testContinuousWhileReusesCooldownAndStopsWithoutAnotherCapture(tmp_path, monkeypatch):
    from emo_master.plugins.builtins.detection_archive import operator as archive
    _, document, registry, original = fixtureProject(tmp_path)
    captures, receipts = [], []
    def capture(self, inputs, params, context):
        captures.append(1)
        return {"status": "ok", "outputs": {"image": original.copy(), "frame": defaultFrame(16, 12).toPayload(), "capturedAt": 1.0}}
    monkeypatch.setattr(RtspCameraOperator, "executeNode", capture)
    fake = _FakeModel(_result([[2, 2, 8, 10]], [0.9], [0], {0: "face"}))
    monkeypatch.setattr(yolo, "_createModel", lambda path: fake)
    monkeypatch.setattr(archive, "monotonic", lambda: 10.0)
    yolo.YoloInferenceOperator.clearCache()
    token = CancellationToken()
    def publish(**event):
        if event["eventType"] == "workflow.completed" and event["context"].workflowId == "camera_once":
            receipts.append(event["payload"]["outputs"]["archive"])
            if len(receipts) == 3:
                token.cancel()
    prepared = prepareRuntimeProject(document, tmp_path, registry)
    runner = WorkflowRunner(WorkflowCompiler(operatorRegistry=registry).compile(prepared), registry, eventPublisher=publish)
    try:
        with pytest.raises(CancellationRequested):
            runner.run("main", {}, RunContext.root("loop", "main"), token)
        assert len(captures) == 3
        assert [r["reason"] for r in receipts] == ["saved", "cooldown", "cooldown"]
        assert len(list((tmp_path / "outputs").rglob("*.*"))) == 2
        assert not runner._lifecycleOperators
    finally:
        runner.disposeOperators()
        yolo.YoloInferenceOperator.clearCache()


def testArchiveOutputDirectoryCannotOverlapModel(tmp_path):
    from emo_master.core.workflow.errors import WorkflowCompileError
    _, document, registry, _ = fixtureProject(tmp_path)
    archive = next(n for n in document.workflows["camera_once"].nodes if n.nodeId == "archive")
    archive.params["outputPath"] = "models/capture.jpg"
    with pytest.raises(WorkflowCompileError):
        prepareRuntimeProject(document, tmp_path, registry)
