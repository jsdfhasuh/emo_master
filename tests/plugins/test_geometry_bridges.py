import json
from pathlib import Path

import numpy as np
import pytest

from emo_master.core.contracts.geometry2d import (
    CircleCollection, ContourCollection, ContourItem, Detection2D, DetectionCollection, Point2D, Polygon2D,
)
from emo_master.plugins.builtins._geometry_bridges import (
    DetectionBBoxOperator, GeometryPointsOperator, MinimumEnclosingCircleOperator, PointsReframeOperator,
)
from emo_master.plugins.builtins._image_frame import defaultFrame, transformedFrame
from emo_master.plugins.builtins._shape_operators import ShapeMeasurementOperator


def triangle(space, offset=0, name="triangle"):
    return ContourItem(name, Polygon2D(tuple(Point2D(x + offset, y, space) for x, y in ((0, 0), (6, 0), (0, 8)))))


def testMinimumCircleIsNotCentroidOrHoughAndPreservesProvenance():
    space = defaultFrame(200, 100, sourceId="camera").coordinateSpace
    contours = ContourCollection((triangle(space), triangle(space, 20, "other")), space).toPayload()
    original = json.dumps(contours, sort_keys=True)
    result = MinimumEnclosingCircleOperator().executeNode({"contours": contours}, {}, {})
    assert result["status"] == "ok"
    circles = CircleCollection.fromPayload(result["outputs"]["circles"])
    assert [(i.circle.center.x, i.circle.center.y) for i in circles.items] == pytest.approx([(3, 4), (23, 4)])
    assert [i.circle.radius for i in circles.items] == pytest.approx([5, 5], abs=0.001)
    assert [i.circleId for i in circles.items] == ["enclosing:triangle", "enclosing:other"]
    for i, contour in zip(circles.items, ContourCollection.fromPayload(contours).items):
        assert all(np.hypot(p.x - i.circle.center.x, p.y - i.circle.center.y) <= i.circle.radius for p in contour.polygon.points)
    assert json.dumps(contours, sort_keys=True) == original
    points = GeometryPointsOperator().executeNode({"circles": circles.toPayload()}, {}, {})
    assert points["outputs"]["pointCount"] == 2
    assert Point2D.fromPayload(points["outputs"]["points"][0]).coordinateSpace == space
    measured = ShapeMeasurementOperator().executeNode({"contours": contours}, {}, {})
    centroid = GeometryPointsOperator().executeNode({"measurements": measured["outputs"]["measurements"]}, {"feature": "centroid"}, {})
    assert Point2D.fromPayload(centroid["outputs"]["points"][0]).x == pytest.approx(2)
    assert centroid["outputs"]["points"][0] != points["outputs"]["points"][0]


def testGeometryFeaturesAndStrictSingleDetection():
    frame = defaultFrame(100, 80, sourceId="a")
    item = Detection2D.fromGeometry("target", 0, "part", .9, frame)
    operator = DetectionBBoxOperator()
    for items in ((), (item, item.__class__.fromGeometry("other", 0, "part", .8, frame))):
        result = operator.executeNode({"detections": DetectionCollection(items, frame.coordinateSpace).toPayload()}, {}, {})
        assert result["status"] == "error"
    result = operator.executeNode({"detections": DetectionCollection((item,), frame.coordinateSpace).toPayload()}, {}, {})
    assert result["outputs"]["bbox"] == frame.toPayload()
    for feature, expected in (("bboxCenter", (50, 40)), ("bboxTopLeft", (0, 0))):
        points = GeometryPointsOperator().executeNode({"bbox": frame.toPayload()}, {"feature": feature}, {})
        point = Point2D.fromPayload(points["outputs"]["points"][0])
        assert (point.x, point.y) == expected
    assert GeometryPointsOperator().executeNode({"bbox": frame.toPayload()}, {}, {})["status"] == "error"


def testLocalPointsReframeExactlyOnceAndRejectDifferentCamera():
    frame = defaultFrame(1000, 800, sourceId="camera")
    local = transformedFrame(frame, 50, 50, (1, 0, 0, 1, 100, 200), (0, 0, 50, 50), reference="crop")
    inputs = {"points": [Point2D(10, 20, local.coordinateSpace).toPayload()], "frame": frame.toPayload()}
    operator = PointsReframeOperator()
    result = operator.executeNode(inputs, {}, {})
    assert result["status"] == "ok"
    point = Point2D.fromPayload(result["outputs"]["points"][0])
    assert (point.x, point.y) == (110, 220)
    again = operator.executeNode({**inputs, "points": result["outputs"]["points"]}, {}, {})
    assert again["outputs"] == result["outputs"]
    mismatch = operator.executeNode({**inputs, "frame": defaultFrame(1000, 800, sourceId="other").toPayload()}, {}, {})
    assert mismatch["error"]["code"] == "E_INPUT_SHAPE"


def testBridgeManifestsAndStrictContracts():
    root = Path(__file__).resolve().parents[2] / "src/emo_master/plugins/builtins"
    for directory, cls in (("minimum_enclosing_circle", MinimumEnclosingCircleOperator),
                            ("detection_bbox", DetectionBBoxOperator), ("geometry_points", GeometryPointsOperator),
                            ("points_reframe", PointsReframeOperator)):
        manifest = json.loads((root / directory / "manifest.json").read_text(encoding="utf-8"))
        for key in ("operatorId", "displayName", "version", "inputPorts", "outputPorts", "paramSchema"):
            assert manifest[key] == getattr(cls.meta, key)
        assert cls().executeNode({}, {"unsupported": True}, {})["status"] == "error"
