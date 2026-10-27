"""Source-only checks for the operator graph; not a custom runtime operator."""
import base64
import math
from pathlib import Path
from time import monotonic

import cv2
import numpy as np
import pytest

from emo_master.apps.designer.state.flow_graph_model import FlowGraphModel
from emo_master.apps.designer.state.project_store import loadProjectDocument
from emo_master.apps.runtime.context.global_variables import ProjectGlobalVariables
from emo_master.apps.runtime.context.sqlite_store import SqliteStore
from emo_master.apps.runtime.workflow.cancellation import CancellationRequested, CancellationToken
from emo_master.apps.runtime.workflow.context import RunContext
from emo_master.apps.runtime.workflow.runner import WorkflowRunner
from emo_master.core.contracts.geometry2d import BBox2D, DetectionCollection
from emo_master.core.plugin.registry import PluginRegistry
from emo_master.core.project.runtime_directory import prepareRuntimeProject
from emo_master.core.workflow.compiler import WorkflowCompiler
from emo_master.core.workflow.errors import WorkflowCompileError
from emo_master.plugins.builtins.image_saver.operator import ImageSaverOperator
from emo_master.plugins.builtins.yolo_inference import operator as yolo_module
from emo_master.plugins.builtins.yolo_inference.onnx_backend import OnnxInferenceResult
from tests.plugins.test_video_loader_operator import makeVideo
from tests.plugins.test_yolo_onnx_backend import _TINY_YOLO_ONNX


ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = Path(__file__).with_name("video-yolo-crop.emoproj")
SAVE_EXAMPLE = Path(__file__).with_name("video-yolo-crop-save.emoproj")
SAMPLED_FRAMES = [0, 3, 5, 8, 10]
TARGET_COUNTS = {0: 2, 3: 0, 5: 3, 8: 1, 10: 2}


@pytest.fixture
def registry():
    scan = PluginRegistry("0.6.1").scan(ROOT / "src/emo_master/plugins/builtins")
    assert not scan.rejectedOperators, scan.rejectedOperators
    return scan.activeOperators


@pytest.fixture(autouse=True)
def clear_model_cache():
    yolo_module.YoloInferenceOperator.clearCache()
    yield
    yolo_module.YoloInferenceOperator.clearCache()


def node(document, workflow_id, node_id):
    return next(item for item in document.workflows[workflow_id].nodes if item.nodeId == node_id)


def prepare(tmp_path, registry, *, frames=11, classes=None, save_latest=False, example=EXAMPLE):
    makeVideo(tmp_path / "video.avi", count=frames)
    (tmp_path / "tiny.onnx").write_bytes(base64.b64decode(_TINY_YOLO_ONNX))
    document = loadProjectDocument(example)
    node(document, "video_frame", "video").params.update(videoPath="video.avi", intervalSec=.25)
    node(document, "video_frame", "yolo").params.update(modelPath="tiny.onnx", classes=classes or [])
    if save_latest:
        from emo_master.core.project.models import WorkflowEdge, WorkflowNode

        workflow = document.workflows["process_crop"]
        workflow.nodes.append(WorkflowNode(
            nodeId="save_latest", kind="operator", operatorId=ImageSaverOperator.meta.operatorId,
            params={"outputPath": "outputs/latest-crop.png", "overwrite": True},
        ))
        workflow.edges.append(WorkflowEdge(
            fromNode="input", fromPort="image", toNode="save_latest", toPort="image",
        ))
    prepared = prepareRuntimeProject(document, tmp_path, registry)
    assert node(document, "video_frame", "video").params["videoPath"] == "video.avi"
    compiled = WorkflowCompiler(registry).compile(prepared)
    store = SqliteStore(tmp_path / "isolated-state.db")
    store.initialize()
    return prepared, compiled, store


class Observer:
    def __init__(self, cancellation=None):
        self.samples = []
        self.crops = []
        self.received = []
        self.processed = 0
        self.cancellation = cancellation

    def activeTime(self):
        return monotonic()

    def loopPoint(self, phase, node, inputs, context):
        pass

    def before(self, node, inputs, context):
        if node.kind == "subflow" and node.targetWorkflowId == "process_crop":
            selected = DetectionCollection.fromPayload(inputs["detections"])
            assert len(selected.items) == 1
            frame = BBox2D.fromPayload(inputs["frame"])
            assert frame.coordinateSpace.sourceId.endswith(f"#frame={inputs['frameIndex']}")
            assert inputs["timestampSec"] == pytest.approx(inputs["frameIndex"] / 10)
            self.received.append((inputs["frameIndex"], selected.items[0].detectionId))
            np.testing.assert_array_equal(inputs["image"], self.crops[-1])

    def after(self, node, inputs, outputs, context):
        if node.operatorId == "vision.io.video_loader":
            self.samples.append(outputs["frameIndex"])
        elif node.operatorId == "vision.preprocess.crop":
            roi = BBox2D.fromPayload(inputs["roi"])
            image = inputs["image"]
            left, top = max(0, math.floor(roi.x)), max(0, math.floor(roi.y))
            right = min(image.shape[1], math.ceil(roi.x + roi.width))
            bottom = min(image.shape[0], math.ceil(roi.y + roi.height))
            np.testing.assert_array_equal(outputs["image"], image[top:bottom, left:right])
            assert not np.shares_memory(outputs["image"], image)
            frame = BBox2D.fromPayload(outputs["frame"])
            assert frame.coordinateSpace.sourceId == roi.coordinateSpace.sourceId
            assert frame.coordinateSpace.mapPointToSource(0, 0) == (float(left), float(top))
            self.crops.append(outputs["image"].copy())
        elif node.operatorId == "vision.color.rgb_statistics":
            np.testing.assert_array_equal(inputs["image"], self.crops[-1])
            self.processed += 1
            if self.cancellation is not None:
                self.cancellation.cancel()

    def failed(self, node, inputs, context, error):
        pass


def run_job(prepared, compiled, store, registry, observer, *, job="first", token=None):
    variables = ProjectGlobalVariables(store, prepared.project.projectId, prepared.globalVariables, job)
    variables.synchronize()
    variables.initializeJob()
    runner = WorkflowRunner(compiled, registry, globalVariables=variables, debugController=observer)
    result = runner.run(
        "main", {}, RunContext.root(job, "main"), token or CancellationToken(),
    )
    assert result.outputs == {"hasNext": False}
    assert runner._lifecycleOperators == {}
    return variables


def install_multi_target_model(monkeypatch, calls):
    class Model:
        def predict(self, image, **params):
            frame_index = round(float(np.mean(image)) / 10)
            calls.append(frame_index)
            count = TARGET_COUNTS[frame_index]
            boxes = [[-1.0 + index * 8, 1.2 + index, 8.1 + index * 8, 12.7 + index]
                     for index in range(count)]
            return OnnxInferenceResult(
                boxesXyxy=np.asarray(boxes, dtype=np.float32).reshape(-1, 4),
                scores=np.asarray([.9 - index * .1 for index in range(count)], dtype=np.float32),
                classIds=np.zeros(count, dtype=np.int64), names={0: "person"},
                provider="fixture-only", inputShape=(1, 3, 24, 32),
            )

    monkeypatch.setattr(yolo_module, "_createModel", lambda path: Model())


@pytest.mark.parametrize("example", [EXAMPLE, SAVE_EXAMPLE], ids=["downstream", "save-crops"])
def test_project_contract_and_designer_pins(tmp_path, registry, example):
    document = loadProjectDocument(example)
    with pytest.raises(WorkflowCompileError, match="videoPath must be non-empty"):
        WorkflowCompiler(registry).compile(document)
    _, compiled, _ = prepare(tmp_path, registry, example=example)
    assert set(compiled.workflows) == {"main", "video_frame", "one_target", "process_crop"}
    assert document.globalVariables["targetIndex"].lifetime == "job"
    edge_count = 0
    for workflow in document.workflows.values():
        graph = FlowGraphModel()
        graph.loadProjectGraph(workflow.model_dump(mode="json"))
        for edge in graph.edges:
            assert edge.fromPort in graph.nodes[edge.fromNode].outputPorts
            assert edge.toPort in graph.nodes[edge.toNode].inputPorts
            edge_count += 1
        for item in workflow.nodes:
            if item.kind == "operator":
                assert item.paramSchema
    assert edge_count == (45 if example == SAVE_EXAMPLE else 41)
    binding = node(document, "one_target", "select").globalVariableBindings[0]
    assert binding.parameterPath == ["index"] and binding.variableId == "targetIndex"
    assert node(document, "video_frame", "yolo").params["outputCrop"] is False
    if example == EXAMPLE:
        assert all(item.operatorId != ImageSaverOperator.meta.operatorId
                   for workflow in document.workflows.values() for item in workflow.nodes)
    else:
        assert node(document, "process_crop", "save").inputPorts["fileName"] == "string"


@pytest.mark.parametrize("frames,expected", [(1, [0]), (11, SAMPLED_FRAMES)])
@pytest.mark.parametrize("classes", [[], [1]], ids=["one-target", "no-target"])
def test_real_onnx_video_to_crop_to_native_downstream(tmp_path, registry, frames, expected, classes):
    prepared, compiled, store = prepare(tmp_path, registry, frames=frames, classes=classes)
    observer = Observer()
    run_job(prepared, compiled, store, registry, observer)
    assert observer.samples == expected
    expected_received = [] if classes else [(frame, "det-1") for frame in expected]
    assert observer.received == expected_received
    assert observer.processed == len(expected_received)


def test_all_targets_empty_middle_frame_last_frame_and_new_job(tmp_path, registry, monkeypatch):
    prepared, compiled, store = prepare(tmp_path, registry)
    calls = []
    install_multi_target_model(monkeypatch, calls)
    observer = Observer()
    expected = [(frame, f"det-{index + 1}") for frame in SAMPLED_FRAMES
                for index in range(TARGET_COUNTS[frame])]
    for job in ("first", "second"):
        variables = run_job(prepared, compiled, store, registry, observer, job=job)
        assert variables.get("targetIndex") == TARGET_COUNTS[10]
    assert calls == SAMPLED_FRAMES * 2
    assert observer.samples == SAMPLED_FRAMES * 2
    assert observer.received == expected * 2
    assert observer.processed == len(expected) * 2 == 16


def test_optional_fixed_path_saves_only_latest_crop(tmp_path, registry, monkeypatch):
    prepared, compiled, store = prepare(tmp_path, registry, save_latest=True)
    install_multi_target_model(monkeypatch, [])
    observer = Observer()
    run_job(prepared, compiled, store, registry, observer)
    files = list((tmp_path / "outputs").glob("*.png"))
    assert [file.name for file in files] == ["latest-crop.png"]
    np.testing.assert_array_equal(cv2.imread(str(files[0])), observer.crops[-1])
    assert observer.processed == 8


def test_dynamic_names_export_every_crop(tmp_path, registry, monkeypatch):
    prepared, compiled, store = prepare(tmp_path, registry, example=SAVE_EXAMPLE)
    install_multi_target_model(monkeypatch, [])
    observer = Observer()
    run_job(prepared, compiled, store, registry, observer)
    destination = tmp_path / "outputs/crops"
    expected = [f"{frame},{index}.png" for frame in SAMPLED_FRAMES
                for index in range(TARGET_COUNTS[frame])]
    assert {file.name for file in destination.glob("*.png")} == set(expected)
    assert observer.processed == len(expected) == 8
    for name, image in zip(expected, observer.crops):
        np.testing.assert_array_equal(cv2.imread(str(destination / name)), image)


@pytest.mark.parametrize("example", [EXAMPLE, SAVE_EXAMPLE], ids=["downstream", "save-crops"])
def test_native_runtime_job_with_real_onnx(tmp_path, registry, example):
    from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb
    from emo_master.apps.runtime.grpc_server.service import RuntimeService
    from tests.runtime.runtime_test_utils import waitForTerminal

    prepared, _, _ = prepare(tmp_path, registry, example=example)
    project_file = tmp_path / "configured.emoproj"
    project_file.write_text(prepared.model_dump_json(indent=2), encoding="utf-8")
    service = RuntimeService(
        dbPath=tmp_path / "isolated-runtime.db", workspaceRoot=tmp_path / "jobs",
        pluginRootPaths=(str(ROOT / "src/emo_master/plugins"),),
    )
    try:
        loaded = service.LoadProject(pb.LoadProjectRequest(project_path=str(project_file)), None)
        assert loaded.ok, loaded.message
        started = service.StartJob(pb.StartJobRequest(
            project_id=prepared.project.projectId, workflow_id="main",
        ), None)
        assert started.status == "ACCEPTED", started.message
        status = waitForTerminal(service, started.job_id)
        assert status.status == "COMPLETED", status.message
        events = list(service.StreamJobEvents(pb.StreamJobEventsRequest(
            job_id=started.job_id, follow=False,
        ), None))
        processed = [event for event in events
                     if event.event_type == "node.completed" and event.node_id == "statistics"]
        assert len(processed) == 5
        if example == SAVE_EXAMPLE:
            assert {file.name for file in (tmp_path / "outputs/crops").glob("*.png")} == {
                f"{frame},0.png" for frame in SAMPLED_FRAMES
            }
    finally:
        service.close()


def test_cancellation_stops_downstream_and_releases_video(tmp_path, registry, monkeypatch):
    from emo_master.plugins.builtins.video_loader.operator import VideoLoaderOperator

    prepared, compiled, store = prepare(tmp_path, registry)
    install_multi_target_model(monkeypatch, [])
    token = CancellationToken()
    observer = Observer(token)
    variables = ProjectGlobalVariables(store, prepared.project.projectId, prepared.globalVariables, "cancel")
    variables.synchronize()
    variables.initializeJob()
    runner = WorkflowRunner(compiled, registry, globalVariables=variables, debugController=observer)
    captures = []
    execute = VideoLoaderOperator.executeNode

    def record_capture(self, inputs, params, context):
        result = execute(self, inputs, params, context)
        captures.append(self._capture)
        return result

    monkeypatch.setattr(VideoLoaderOperator, "executeNode", record_capture)
    with pytest.raises(CancellationRequested):
        runner.run("main", {}, RunContext.root("cancel", "main"), token)
    assert observer.processed == 1
    assert observer.received == [(0, "det-1")]
    assert variables.get("targetIndex") == 0
    assert captures and all(not capture.isOpened() for capture in captures)
    assert runner._lifecycleOperators == {}
