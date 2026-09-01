from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from emo_master.apps.runtime.workflow.cancellation import CancellationToken
from emo_master.apps.runtime.workflow.context import RunContext
from emo_master.apps.runtime.workflow.runner import WorkflowRunner
from emo_master.apps.runtime.artifacts.store import ArtifactStore
from emo_master.core.contracts.geometry2d import (
    BlobCollection,
    ColorStatistics,
    DetectionCollection,
    ShapeMeasurementCollection,
    TemplateMatchCollection,
)
from emo_master.core.plugin.registry import PluginRegistry
from emo_master.core.project.models import ProjectDocument
from emo_master.core.workflow.compiler import WorkflowCompiler
from emo_master.plugins.builtins.yolo_inference import operator as yolo_module
from emo_master.plugins.builtins.yolo_inference.operator import YoloInferenceOperator


def _registry() -> dict[str, object]:
    pluginRoot = Path(__file__).resolve().parents[2] / "src" / "emo_master" / "plugins"
    result = PluginRegistry(coreVersion="0.4.0").scan(pluginRoot)
    assert result.rejectedOperators == {}
    return dict(result.activeOperators)


def _project(
    operatorId: str,
    outputPort: str,
    outputType: str,
    params: dict[str, object] | None = None,
) -> ProjectDocument:
    return ProjectDocument.model_validate(
        {
            "schemaVersion": "2.1",
            "project": {
                "projectId": "builtin-workflow",
                "name": "Builtin Workflow",
                "revision": 1,
                "createdAt": "2026-01-01T00:00:00Z",
                "updatedAt": "2026-01-01T00:00:00Z",
            },
            "entryWorkflowId": "main",
            "workflowOrder": ["main"],
            "workflows": {
                "main": {
                    "name": "Main",
                    "inputs": {"image": "image"},
                    "outputs": {"result": outputType},
                    "nodes": [
                        {"nodeId": "input", "kind": "workflow_input"},
                        {
                            "nodeId": "operator",
                            "kind": "operator",
                            "operatorId": operatorId,
                            "params": dict(params or {}),
                        },
                        {"nodeId": "output", "kind": "workflow_output"},
                    ],
                    "edges": [
                        {
                            "fromNode": "input",
                            "fromPort": "image",
                            "toNode": "operator",
                            "toPort": "image",
                        },
                        {
                            "fromNode": "operator",
                            "fromPort": outputPort,
                            "toNode": "output",
                            "toPort": "result",
                        },
                    ],
                    "layout": {"nodePositions": {}},
                }
            },
            "runtime": {},
            "dependencies": {"operators": []},
            "devices": {"bindings": {}},
        }
    )


def _operatorNode(
    nodeId: str,
    operatorId: str,
    params: dict[str, object] | None = None,
) -> dict[str, object]:
    return {
        "nodeId": nodeId,
        "kind": "operator",
        "operatorId": operatorId,
        "params": dict(params or {}),
    }


def _edge(
    fromNode: str,
    fromPort: str,
    toNode: str,
    toPort: str,
) -> dict[str, object]:
    return {
        "fromNode": fromNode,
        "fromPort": fromPort,
        "toNode": toNode,
        "toPort": toPort,
    }


def _workflowProject(
    projectId: str,
    outputs: dict[str, str],
    operatorNodes: list[dict[str, object]],
    edges: list[dict[str, object]],
) -> ProjectDocument:
    return ProjectDocument.model_validate(
        {
            "schemaVersion": "2.1",
            "project": {
                "projectId": projectId,
                "name": projectId.replace("-", " ").title(),
                "revision": 1,
                "createdAt": "2026-01-01T00:00:00Z",
                "updatedAt": "2026-01-01T00:00:00Z",
            },
            "entryWorkflowId": "main",
            "workflowOrder": ["main"],
            "workflows": {
                "main": {
                    "name": "Main",
                    "inputs": {"image": "image"},
                    "outputs": outputs,
                    "nodes": [
                        {"nodeId": "input", "kind": "workflow_input"},
                        *operatorNodes,
                        {"nodeId": "output", "kind": "workflow_output"},
                    ],
                    "edges": edges,
                    "layout": {"nodePositions": {}},
                }
            },
            "runtime": {},
            "dependencies": {"operators": []},
            "devices": {"bindings": {}},
        }
    )


def _businessClosureProject() -> ProjectDocument:
    return _workflowProject(
        "roi-business-closure",
        {
            "comparison": "boolean",
            "decision": "object",
            "count": "integer",
            "blobs": "blobCollection",
        },
        [
            _operatorNode(
                "roi",
                "vision.preprocess.roi",
                {
                    "roiType": "bbox",
                    "x": 2,
                    "y": 2,
                    "width": 6,
                    "height": 5,
                    "interpolation": "nearest",
                },
            ),
            _operatorNode(
                "threshold",
                "vision.preprocess.threshold",
                {"mode": "fixed", "threshold": 100},
            ),
            _operatorNode(
                "blob",
                "vision.analysis.blob",
                {"minArea": 1},
            ),
            _operatorNode(
                "filter",
                "vision.collection.filter",
                {"minArea": 1, "spatialMode": "centerInside"},
            ),
            _operatorNode("count", "vision.collection.count"),
            _operatorNode("limit", "vision.value.number", {"value": 1}),
            _operatorNode(
                "compare",
                "vision.compare.number",
                {"operator": "gte", "rightValue": 999},
            ),
            _operatorNode("if", "vision.flow.if", {"mode": "bool"}),
        ],
        [
            _edge("input", "image", "roi", "image"),
            _edge("roi", "maskedImage", "threshold", "image"),
            _edge("roi", "maskedFrame", "threshold", "frame"),
            _edge("roi", "maskedImage", "blob", "image"),
            _edge("threshold", "mask", "blob", "mask"),
            _edge("threshold", "frame", "blob", "frame"),
            _edge("blob", "blobs", "filter", "blobs"),
            _edge("roi", "roi", "filter", "roi"),
            _edge("filter", "keptBlobs", "count", "blobs"),
            _edge("count", "count", "compare", "left"),
            _edge("limit", "value", "compare", "right"),
            _edge("compare", "result", "if", "value"),
            _edge("compare", "result", "output", "comparison"),
            _edge("if", "true", "output", "decision"),
            _edge("count", "count", "output", "count"),
            _edge("filter", "keptBlobs", "output", "blobs"),
        ],
    )


def _detectionClosureProject(modelPath: str) -> ProjectDocument:
    return _workflowProject(
        "detection-business-closure",
        {
            "receipt": "json",
            "overlay": "image",
            "drawnCount": "integer",
            "selected": "detectionCollection",
            "selectedCount": "integer",
        },
        [
            _operatorNode(
                "resize",
                "vision.preprocess.resize",
                {
                    "width": 12,
                    "height": 12,
                    "mode": "letterbox",
                    "interpolation": "nearest",
                },
            ),
            _operatorNode(
                "roi",
                "vision.preprocess.roi",
                {
                    "roiType": "bbox",
                    "x": 0,
                    "y": 3,
                    "width": 8,
                    "height": 5,
                },
            ),
            _operatorNode(
                "yolo",
                "vision.inference.yolo",
                {"modelPath": modelPath},
            ),
            _operatorNode(
                "filter",
                "vision.collection.filter",
                {"spatialMode": "centerInside"},
            ),
            _operatorNode(
                "sort",
                "vision.collection.sort",
                {"key": "confidence", "direction": "descending"},
            ),
            _operatorNode(
                "select",
                "vision.collection.select",
                {"mode": "first"},
            ),
            _operatorNode(
                "annotate",
                "vision.render.annotate",
                {"drawLabels": False, "thickness": 1},
            ),
            _operatorNode(
                "writer",
                "vision.io.result_writer",
                {
                    "format": "json",
                    "relativePath": "results/detections",
                    "overwrite": False,
                },
            ),
        ],
        [
            _edge("input", "image", "resize", "image"),
            _edge("resize", "image", "roi", "image"),
            _edge("resize", "frame", "roi", "frame"),
            _edge("resize", "image", "yolo", "image"),
            _edge("resize", "frame", "yolo", "frame"),
            _edge("yolo", "detections", "filter", "detections"),
            _edge("roi", "roi", "filter", "roi"),
            _edge("filter", "keptDetections", "sort", "detections"),
            _edge("sort", "sortedDetections", "select", "detections"),
            _edge("resize", "image", "annotate", "image"),
            _edge("yolo", "frame", "annotate", "frame"),
            _edge("select", "selectedDetections", "annotate", "detections"),
            _edge("roi", "roi", "annotate", "roi"),
            _edge("select", "selectedDetections", "writer", "detections"),
            _edge("writer", "result", "output", "receipt"),
            _edge("annotate", "overlay", "output", "overlay"),
            _edge("annotate", "drawnCount", "output", "drawnCount"),
            _edge("select", "selectedDetections", "output", "selected"),
            _edge("select", "selectedCount", "output", "selectedCount"),
        ],
    )


def _thresholdBlobProject() -> ProjectDocument:
    return ProjectDocument.model_validate(
        {
            "schemaVersion": "2.1",
            "project": {
                "projectId": "threshold-blob-workflow",
                "name": "Threshold Blob Workflow",
                "revision": 1,
                "createdAt": "2026-01-01T00:00:00Z",
                "updatedAt": "2026-01-01T00:00:00Z",
            },
            "entryWorkflowId": "main",
            "workflowOrder": ["main"],
            "workflows": {
                "main": {
                    "name": "Main",
                    "inputs": {"image": "image"},
                    "outputs": {"result": "blobCollection"},
                    "nodes": [
                        {"nodeId": "input", "kind": "workflow_input"},
                        {
                            "nodeId": "threshold",
                            "kind": "operator",
                            "operatorId": "vision.preprocess.threshold",
                            "params": {"mode": "fixed", "threshold": 100},
                        },
                        {
                            "nodeId": "blob",
                            "kind": "operator",
                            "operatorId": "vision.analysis.blob",
                            "params": {"threshold": 250, "minArea": 1},
                        },
                        {"nodeId": "output", "kind": "workflow_output"},
                    ],
                    "edges": [
                        {
                            "fromNode": "input",
                            "fromPort": "image",
                            "toNode": "threshold",
                            "toPort": "image",
                        },
                        {
                            "fromNode": "input",
                            "fromPort": "image",
                            "toNode": "blob",
                            "toPort": "image",
                        },
                        {
                            "fromNode": "threshold",
                            "fromPort": "mask",
                            "toNode": "blob",
                            "toPort": "mask",
                        },
                        {
                            "fromNode": "threshold",
                            "fromPort": "frame",
                            "toNode": "blob",
                            "toPort": "frame",
                        },
                        {
                            "fromNode": "blob",
                            "fromPort": "blobs",
                            "toNode": "output",
                            "toPort": "result",
                        },
                    ],
                    "layout": {"nodePositions": {}},
                }
            },
            "runtime": {},
            "dependencies": {"operators": []},
            "devices": {"bindings": {}},
        }
    )


def _preprocessBlobProject() -> ProjectDocument:
    return ProjectDocument.model_validate(
        {
            "schemaVersion": "2.1",
            "project": {
                "projectId": "preprocess-blob-workflow",
                "name": "Preprocess Blob Workflow",
                "revision": 1,
                "createdAt": "2026-01-01T00:00:00Z",
                "updatedAt": "2026-01-01T00:00:00Z",
            },
            "entryWorkflowId": "main",
            "workflowOrder": ["main"],
            "workflows": {
                "main": {
                    "name": "Main",
                    "inputs": {"image": "image"},
                    "outputs": {"result": "blobCollection"},
                    "nodes": [
                        {"nodeId": "input", "kind": "workflow_input"},
                        {
                            "nodeId": "resize",
                            "kind": "operator",
                            "operatorId": "vision.preprocess.resize",
                            "params": {
                                "width": 12,
                                "height": 8,
                                "mode": "stretch",
                                "interpolation": "nearest",
                            },
                        },
                        {
                            "nodeId": "color",
                            "kind": "operator",
                            "operatorId": "vision.preprocess.color_convert",
                            "params": {"sourceSpace": "BGR", "targetSpace": "HSV"},
                        },
                        {
                            "nodeId": "range",
                            "kind": "operator",
                            "operatorId": "vision.segment.in_range",
                            "params": {
                                "colorSpace": "HSV",
                                "lower": [50, 200, 200],
                                "upper": [70, 255, 255],
                            },
                        },
                        {
                            "nodeId": "morphology",
                            "kind": "operator",
                            "operatorId": "vision.preprocess.morphology",
                            "params": {"operation": "open", "kernelSize": 1},
                        },
                        {
                            "nodeId": "blob",
                            "kind": "operator",
                            "operatorId": "vision.analysis.blob",
                            "params": {"minArea": 1},
                        },
                        {"nodeId": "output", "kind": "workflow_output"},
                    ],
                    "edges": [
                        {
                            "fromNode": "input",
                            "fromPort": "image",
                            "toNode": "resize",
                            "toPort": "image",
                        },
                        {
                            "fromNode": "resize",
                            "fromPort": "image",
                            "toNode": "color",
                            "toPort": "image",
                        },
                        {
                            "fromNode": "resize",
                            "fromPort": "frame",
                            "toNode": "color",
                            "toPort": "frame",
                        },
                        {
                            "fromNode": "color",
                            "fromPort": "image",
                            "toNode": "range",
                            "toPort": "image",
                        },
                        {
                            "fromNode": "color",
                            "fromPort": "frame",
                            "toNode": "range",
                            "toPort": "frame",
                        },
                        {
                            "fromNode": "range",
                            "fromPort": "mask",
                            "toNode": "morphology",
                            "toPort": "mask",
                        },
                        {
                            "fromNode": "range",
                            "fromPort": "frame",
                            "toNode": "morphology",
                            "toPort": "frame",
                        },
                        {
                            "fromNode": "resize",
                            "fromPort": "image",
                            "toNode": "blob",
                            "toPort": "image",
                        },
                        {
                            "fromNode": "morphology",
                            "fromPort": "mask",
                            "toNode": "blob",
                            "toPort": "mask",
                        },
                        {
                            "fromNode": "morphology",
                            "fromPort": "frame",
                            "toNode": "blob",
                            "toPort": "frame",
                        },
                        {
                            "fromNode": "blob",
                            "fromPort": "blobs",
                            "toNode": "output",
                            "toPort": "result",
                        },
                    ],
                    "layout": {"nodePositions": {}},
                }
            },
            "runtime": {},
            "dependencies": {"operators": []},
            "devices": {"bindings": {}},
        }
    )


def _classicContourClosureProject() -> ProjectDocument:
    return _workflowProject(
        "classic-contour-closure",
        {
            "measurements": "shapeMeasurementCollection",
            "overlay": "image",
            "drawnCount": "integer",
            "receipt": "json",
        },
        [
            _operatorNode(
                "threshold",
                "vision.preprocess.threshold",
                {"mode": "fixed", "threshold": 100},
            ),
            _operatorNode(
                "contour",
                "vision.analysis.contour",
                {"retrievalMode": "tree", "approximation": "simple"},
            ),
            _operatorNode("measure", "vision.analysis.shape_measurement"),
            _operatorNode(
                "filter",
                "vision.collection.filter",
                {"minArea": 10, "minPerimeter": 10},
            ),
            _operatorNode(
                "sort",
                "vision.collection.sort",
                {"key": "area", "direction": "descending"},
            ),
            _operatorNode(
                "select", "vision.collection.select", {"mode": "first"}
            ),
            _operatorNode(
                "annotate",
                "vision.render.annotate",
                {"drawLabels": False, "thickness": 1},
            ),
            _operatorNode(
                "writer",
                "vision.io.result_writer",
                {
                    "format": "json",
                    "relativePath": "results/measurements",
                },
            ),
        ],
        [
            _edge("input", "image", "threshold", "image"),
            _edge("threshold", "mask", "contour", "mask"),
            _edge("threshold", "frame", "contour", "frame"),
            _edge("contour", "contours", "measure", "contours"),
            _edge("measure", "measurements", "filter", "measurements"),
            _edge("filter", "keptMeasurements", "sort", "measurements"),
            _edge("sort", "sortedMeasurements", "select", "measurements"),
            _edge("input", "image", "annotate", "image"),
            _edge("threshold", "frame", "annotate", "frame"),
            _edge("select", "selectedMeasurements", "annotate", "measurements"),
            _edge("select", "selectedMeasurements", "writer", "measurements"),
            _edge("select", "selectedMeasurements", "output", "measurements"),
            _edge("annotate", "overlay", "output", "overlay"),
            _edge("annotate", "drawnCount", "output", "drawnCount"),
            _edge("writer", "result", "output", "receipt"),
        ],
    )


def _perspectiveTemplateClosureProject() -> ProjectDocument:
    return ProjectDocument.model_validate(
        {
            "schemaVersion": "2.1",
            "project": {
                "projectId": "perspective-template-closure",
                "name": "Perspective Template Closure",
                "revision": 1,
                "createdAt": "2026-01-01T00:00:00Z",
                "updatedAt": "2026-01-01T00:00:00Z",
            },
            "entryWorkflowId": "main",
            "workflowOrder": ["main"],
            "workflows": {
                "main": {
                    "name": "Main",
                    "inputs": {"image": "image", "template": "image"},
                    "outputs": {
                        "matches": "templateMatchCollection",
                        "overlay": "image",
                        "receipt": "json",
                    },
                    "nodes": [
                        {"nodeId": "input", "kind": "workflow_input"},
                        _operatorNode(
                            "perspective",
                            "vision.preprocess.perspective",
                            {
                                "srcPoints": [
                                    {"x": 0, "y": 0},
                                    {"x": 19, "y": 0},
                                    {"x": 19, "y": 19},
                                    {"x": 0, "y": 19},
                                ],
                                "outputWidth": 20,
                                "outputHeight": 20,
                                "interpolation": "nearest",
                            },
                        ),
                        _operatorNode(
                            "match",
                            "vision.analysis.template_match",
                            {
                                "method": "sqdiffNormed",
                                "thresholdMode": "quality",
                                "threshold": 0.99,
                                "maxMatches": 5,
                            },
                        ),
                        _operatorNode(
                            "filter",
                            "vision.collection.filter",
                            {"minQuality": 0.99},
                        ),
                        _operatorNode(
                            "annotate",
                            "vision.render.annotate",
                            {"drawLabels": False, "thickness": 1},
                        ),
                        _operatorNode(
                            "writer",
                            "vision.io.result_writer",
                            {
                                "format": "jsonl",
                                "relativePath": "results/matches",
                            },
                        ),
                        {"nodeId": "output", "kind": "workflow_output"},
                    ],
                    "edges": [
                        _edge("input", "image", "perspective", "image"),
                        _edge("perspective", "image", "match", "image"),
                        _edge("perspective", "frame", "match", "frame"),
                        _edge("input", "template", "match", "template"),
                        _edge("match", "matches", "filter", "matches"),
                        _edge("perspective", "image", "annotate", "image"),
                        _edge("perspective", "frame", "annotate", "frame"),
                        _edge("filter", "keptMatches", "annotate", "matches"),
                        _edge("filter", "keptMatches", "writer", "matches"),
                        _edge("filter", "keptMatches", "output", "matches"),
                        _edge("annotate", "overlay", "output", "overlay"),
                        _edge("writer", "result", "output", "receipt"),
                    ],
                    "layout": {"nodePositions": {}},
                }
            },
            "runtime": {},
            "dependencies": {"operators": []},
            "devices": {"bindings": {}},
        }
    )


def _run(
    registry: dict[str, object],
    project: ProjectDocument,
    image: np.ndarray,
):
    compiled = WorkflowCompiler(operatorRegistry=registry).compile(project)
    return WorkflowRunner(compiled, registry).run(
        "main",
        {"image": image},
        RunContext.root("job", "main"),
        CancellationToken(),
    )


def testThresholdBuiltinRunsThroughCompilerAndRunner() -> None:
    registry = _registry()
    image = np.asarray([[0, 100, 101, 255]], dtype=np.uint8)

    result = _run(
        registry,
        _project(
            "vision.preprocess.threshold",
            "mask",
            "image",
            {"mode": "fixed", "threshold": 100},
        ),
        image,
    )

    np.testing.assert_array_equal(
        result.outputs["result"],
        np.asarray([[0, 0, 255, 255]], dtype=np.uint8),
    )


def testBlobBuiltinRunsThroughCompilerAndRunner() -> None:
    registry = _registry()
    image = np.zeros((6, 6, 3), dtype=np.uint8)
    image[1:4, 2:5] = 255

    result = _run(
        registry,
        _project("vision.analysis.blob", "blobs", "blobCollection"),
        image,
    )

    blobs = BlobCollection.fromPayload(result.outputs["result"])
    assert len(blobs.items) == 1
    assert blobs.items[0].area == 9.0


def testThresholdMaskFeedsBlobThroughMultipleInputs() -> None:
    registry = _registry()
    image = np.zeros((6, 6, 3), dtype=np.uint8)
    image[1:4, 2:5] = 200

    result = _run(registry, _thresholdBlobProject(), image)

    blobs = BlobCollection.fromPayload(result.outputs["result"])
    assert len(blobs.items) == 1
    assert blobs.items[0].area == 9.0


def testPreprocessChainPreservesFrameIntoBlobCoordinates() -> None:
    registry = _registry()
    image = np.zeros((4, 6, 3), dtype=np.uint8)
    image[1:3, 2:5] = (0, 255, 0)

    result = _run(registry, _preprocessBlobProject(), image)

    blobs = BlobCollection.fromPayload(result.outputs["result"])
    assert len(blobs.items) == 1
    assert blobs.items[0].area == 24.0
    assert blobs.items[0].bbox.x == 4.0
    assert blobs.items[0].bbox.y == 2.0
    assert blobs.coordinateSpace.mapPointToSource(4.0, 2.0) == (2.0, 1.0)


def testRgbBuiltinRunsThroughCompilerAndRunner() -> None:
    registry = _registry()
    image = np.asarray([[[1, 2, 3], [4, 5, 6]]], dtype=np.uint8)

    result = _run(
        registry,
        _project(
            "vision.color.rgb_statistics",
            "statistics",
            "colorStatistics",
        ),
        image,
    )

    statistics = ColorStatistics.fromPayload(result.outputs["result"])
    assert statistics.pixelCount == 2
    assert statistics.channels["r"].mean == 4.5


def testYoloBuiltinRunsThroughCompilerAndRunner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Boxes:
        xyxy = np.asarray([[1, 1, 4, 4]], dtype=np.float32)
        conf = np.asarray([0.8], dtype=np.float32)
        cls = np.asarray([0], dtype=np.float32)

    class Result:
        boxes = Boxes()
        names = {0: "part"}

    class Model:
        names = {0: "part"}

        def predict(self, **kwargs: object):
            _ = kwargs
            return [Result()]

    modelPath = tmp_path / "model.pt"
    modelPath.write_bytes(b"test")
    monkeypatch.setattr(yolo_module, "_createModel", lambda path: Model())
    YoloInferenceOperator.clearCache()
    registry = _registry()

    result = _run(
        registry,
        _project(
            "vision.inference.yolo",
            "detections",
            "detectionCollection",
            {"modelPath": str(modelPath)},
        ),
        np.zeros((6, 6, 3), dtype=np.uint8),
    )

    detections = DetectionCollection.fromPayload(result.outputs["result"])
    assert len(detections.items) == 1
    assert detections.items[0].label == "part"
    YoloInferenceOperator.clearCache()


def testRoiBlobFilterCountCompareAndIfRunAsBusinessClosure() -> None:
    registry = _registry()
    image = np.zeros((10, 12, 3), dtype=np.uint8)
    image[3:6, 4:7] = 255

    result = _run(registry, _businessClosureProject(), image)

    assert result.outputs["comparison"] is True
    assert result.outputs["decision"] is True
    assert result.outputs["count"] == 1
    blobs = BlobCollection.fromPayload(result.outputs["blobs"])
    assert len(blobs.items) == 1
    assert blobs.items[0].area == 9.0


def testYoloPostprocessAnnotateWriterAndArtifactRunAsBusinessClosure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Boxes:
        xyxy = np.asarray(
            [[1, 4, 3, 6], [4, 4, 6, 6], [9, 9, 11, 11]],
            dtype=np.float32,
        )
        conf = np.asarray([0.7, 0.9, 0.99], dtype=np.float32)
        cls = np.asarray([0, 1, 2], dtype=np.float32)

    class Result:
        boxes = Boxes()
        names = {0: "low", 1: "high", 2: "outside"}

    class Model:
        names = Result.names

        def predict(self, **kwargs: object):
            _ = kwargs
            return [Result()]

    modelPath = tmp_path / "model.pt"
    modelPath.write_bytes(b"test")
    monkeypatch.setattr(yolo_module, "_createModel", lambda path: Model())
    YoloInferenceOperator.clearCache()
    registry = _registry()
    events: list[dict[str, object]] = []

    def publish(**event: object) -> None:
        events.append(event)

    try:
        compiled = WorkflowCompiler(operatorRegistry=registry).compile(
            _detectionClosureProject(str(modelPath))
        )
        result = WorkflowRunner(
            compiled,
            registry,
            eventPublisher=publish,
            artifactStore=ArtifactStore(tmp_path),
        ).run(
            "main",
            {"image": np.zeros((4, 8, 3), dtype=np.uint8)},
            RunContext.root("job", "main", workspacePath=str(tmp_path)),
            CancellationToken(),
        )
    finally:
        YoloInferenceOperator.clearCache()

    selected = DetectionCollection.fromPayload(result.outputs["selected"])
    assert result.outputs["selectedCount"] == 1
    assert len(selected.items) == 1
    assert selected.items[0].label == "high"
    assert selected.coordinateSpace.mapPointToSource(4.0, 4.0) == pytest.approx(
        (8.0 / 3.0, 2.0 / 3.0)
    )
    assert result.outputs["drawnCount"] == 2
    assert result.outputs["overlay"].shape == (12, 12, 3)
    assert np.any(result.outputs["overlay"] != 0)

    receipt = result.outputs["receipt"]
    assert isinstance(receipt, dict)
    assert receipt["saved"] is True
    assert receipt["recordCount"] == 1
    target = Path(str(receipt["path"]))
    assert target == tmp_path / "results" / "detections.json"
    persisted = DetectionCollection.fromPayload(
        json.loads(target.read_text(encoding="utf-8"))
    )
    assert persisted.items[0].detectionId == selected.items[0].detectionId

    artifactEvents = [
        event for event in events if event.get("eventType") == "artifact.created"
    ]
    assert len(artifactEvents) == 1
    artifactPayload = artifactEvents[0]["payload"]
    assert isinstance(artifactPayload, dict)
    artifact = artifactPayload["artifact"]
    assert isinstance(artifact, dict)
    assert Path(str(artifact["path"])).is_file()
    assert artifact["nodeId"] == "writer"


def testThresholdContourMeasurementFilterSortSelectAnnotateWriterClosure(
    tmp_path: Path,
) -> None:
    registry = _registry()
    image = np.zeros((20, 20, 3), dtype=np.uint8)
    image[3:11, 4:12] = 220
    image[15:18, 15:18] = 220
    compiled = WorkflowCompiler(operatorRegistry=registry).compile(
        _classicContourClosureProject()
    )

    result = WorkflowRunner(
        compiled,
        registry,
        artifactStore=ArtifactStore(tmp_path),
    ).run(
        "main",
        {"image": image},
        RunContext.root("job", "main", workspacePath=str(tmp_path)),
        CancellationToken(),
    )

    measurements = ShapeMeasurementCollection.fromPayload(
        result.outputs["measurements"]
    )
    assert len(measurements.items) == 1
    assert measurements.items[0].area == 49
    assert result.outputs["drawnCount"] == 1
    assert np.any(result.outputs["overlay"] != image)
    receipt = result.outputs["receipt"]
    assert receipt["recordCount"] == 1
    persisted = ShapeMeasurementCollection.fromPayload(
        json.loads(Path(receipt["path"]).read_text(encoding="utf-8"))
    )
    assert persisted.items[0].sourceId == measurements.items[0].sourceId


def testPerspectiveTemplateFilterAnnotateWriterPreservesHomography(
    tmp_path: Path,
) -> None:
    registry = _registry()
    random = np.random.default_rng(8)
    template = random.integers(20, 240, (4, 5), dtype=np.uint8)
    image = random.integers(0, 15, (20, 20), dtype=np.uint8)
    image[7:11, 9:14] = template
    compiled = WorkflowCompiler(operatorRegistry=registry).compile(
        _perspectiveTemplateClosureProject()
    )

    result = WorkflowRunner(
        compiled,
        registry,
        artifactStore=ArtifactStore(tmp_path),
    ).run(
        "main",
        {"image": image, "template": template},
        RunContext.root("job", "main", workspacePath=str(tmp_path)),
        CancellationToken(),
    )

    matches = TemplateMatchCollection.fromPayload(result.outputs["matches"])
    assert len(matches.items) == 1
    match = matches.items[0]
    assert (match.bbox.x, match.bbox.y) == (9, 7)
    assert matches.coordinateSpace.homographyToSource is not None
    assert matches.coordinateSpace.mapPointToSource(
        match.bbox.x, match.bbox.y
    ) == pytest.approx((9, 7))
    assert np.any(result.outputs["overlay"] != image)
    receipt = result.outputs["receipt"]
    assert receipt["format"] == "jsonl"
    assert receipt["recordCount"] == 1
    record = json.loads(Path(receipt["path"]).read_text(encoding="utf-8"))
    assert record["collectionMetadata"]["method"] == "sqdiffNormed"
    assert record["item"]["id"] == "match-1"
