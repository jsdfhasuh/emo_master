"""Synthetic CPU model plumbing, not accuracy validation of a field model."""
import base64
import json

import cv2
import numpy as np
import pytest

from emo_master.apps.runtime.workflow.cancellation import CancellationToken
from emo_master.apps.runtime.workflow.context import RunContext
from emo_master.apps.runtime.workflow.runner import WorkflowRunner
from emo_master.core.contracts.geometry2d import DetectionCollection
from emo_master.core.workflow.compiler import WorkflowCompiler
from emo_master.plugins.builtins.yolo_inference.operator import YoloInferenceOperator
from tests.plugins.test_yolo_onnx_backend import _TINY_YOLO_ONNX
from tests.runtime.test_builtin_vision_operator_workflows import _edge, _registry, _workflowProject


@pytest.mark.parametrize("confidence, expectedCount", [(0.25, 1), (0.95, 0)])
def testRealOnnxCountsAnnotatesAndSavesResults(tmp_path, confidence, expectedCount):
    model = tmp_path / "tiny.onnx"
    model.write_bytes(base64.b64decode(_TINY_YOLO_ONNX))
    imagePath = tmp_path / "overlay.png"
    project = _workflowProject("real-onnx-pipeline",
        {"detections": "detectionCollection", "count": "integer", "overlay": "image",
         "receipt": "json", "imageReceipt": "json"},
        [
            {"nodeId": "yolo", "operatorId": "vision.inference.yolo",
             "params": {"modelPath": str(model), "confidence": confidence}},
            {"nodeId": "count", "operatorId": "vision.collection.count"},
            {"nodeId": "annotate", "operatorId": "vision.render.annotate",
             "params": {"drawLabels": False, "thickness": 1}},
            {"nodeId": "writer", "operatorId": "vision.io.result_writer",
             "params": {"relativePath": "results/detections.json", "format": "json"}},
            {"nodeId": "save", "operatorId": "vision.io.image_saver",
             "params": {"outputPath": str(imagePath)}},
        ],
        [
            _edge("input", "image", "yolo", "image"),
            _edge("input", "image", "annotate", "image"),
            _edge("yolo", "frame", "annotate", "frame"),
            _edge("yolo", "detections", "annotate", "detections"),
            _edge("yolo", "detections", "count", "detections"),
            _edge("yolo", "detections", "writer", "detections"),
            _edge("yolo", "detections", "output", "detections"),
            _edge("count", "count", "output", "count"),
            _edge("annotate", "overlay", "output", "overlay"),
            _edge("annotate", "overlay", "save", "image"),
            _edge("writer", "result", "output", "receipt"),
            _edge("save", "result", "output", "imageReceipt"),
        ])
    image = np.zeros((4, 8, 3), dtype=np.uint8)
    original = image.copy()
    registry = _registry()
    YoloInferenceOperator.clearCache()
    try:
        result = WorkflowRunner(WorkflowCompiler(operatorRegistry=registry).compile(project), registry).run(
            "main", {"image": image}, RunContext.root("job", "main", workspacePath=str(tmp_path)),
            CancellationToken())
    finally:
        YoloInferenceOperator.clearCache()
    detections = DetectionCollection.fromPayload(result.outputs["detections"])
    assert result.outputs["count"] == len(detections.items) == expectedCount
    if expectedCount:
        detection = detections.items[0]
        assert detection.label == "screw"
        assert detection.confidence == pytest.approx(0.9)
        assert (detection.bbox.x, detection.bbox.y, detection.bbox.width, detection.bbox.height) == pytest.approx((2, 0, 4, 4))
    receipt = result.outputs["receipt"]
    assert receipt["saved"] is True and receipt["recordCount"] == expectedCount
    saved = DetectionCollection.fromPayload(json.loads((tmp_path / "results/detections.json").read_text(encoding="utf-8")))
    assert saved == detections
    overlay = result.outputs["overlay"]
    assert overlay.shape == image.shape
    assert bool(np.any(overlay != original)) == bool(expectedCount)
    np.testing.assert_array_equal(cv2.imdecode(np.fromfile(imagePath, dtype=np.uint8), cv2.IMREAD_COLOR), overlay)
    np.testing.assert_array_equal(image, original)
