import json

import cv2
import numpy as np
import pytest

from emo_master.core.contracts.geometry2d import CircleCollection, ContourCollection, Point2D
from emo_master.plugins.builtins._geometry_bridges import (
    GeometryPointsOperator,
    MinimumEnclosingCircleOperator,
    PointsReframeOperator,
)
from emo_master.plugins.builtins._image_frame import defaultFrame, transformedFrame
from tests.plugins.test_geometry_bridges import triangle


def _threeContours(space):
    return ContourCollection(tuple(triangle(space, offset, str(offset)) for offset in (0, 20, 70)), space)


def testMergedCircleUsesEveryVertexNotFirstLargestOrMeanCircleCenter():
    space = defaultFrame(200, 100, sourceId="camera").coordinateSpace
    collection = _threeContours(space)
    payload = collection.toPayload()
    before = json.dumps(payload, sort_keys=True)
    operator = MinimumEnclosingCircleOperator()
    result = operator.executeNode({"contours": payload}, {"scope": "allContours"}, {})
    assert result["status"] == "ok"
    assert result["outputs"]["circleCount"] == 1
    circles = CircleCollection.fromPayload(result["outputs"]["circles"])
    merged = circles.items[0].circle
    coordinates = np.array([(p.x, p.y) for c in collection.items for p in c.polygon.points], np.float32)
    center, radius = cv2.minEnclosingCircle(coordinates)
    assert (merged.center.x, merged.center.y) == pytest.approx(center)
    assert merged.radius == pytest.approx(radius)
    assert all(np.hypot(p.x - merged.center.x, p.y - merged.center.y) <= merged.radius
               for c in collection.items for p in c.polygon.points)
    individual = operator.executeNode({"contours": payload}, {}, {})
    assert individual["outputs"]["circleCount"] == 3
    separate = CircleCollection.fromPayload(individual["outputs"]["circles"])
    averageX = sum(c.circle.center.x for c in separate.items) / 3
    assert merged.center.x != pytest.approx(averageX)
    assert merged.radius > max(c.circle.radius for c in separate.items)
    assert circles.coordinateSpace == space
    assert result["diagnostics"]["sourceContourIds"] == ["0", "20", "70"]
    assert result["diagnostics"]["scope"] == "allContours"
    assert json.dumps(payload, sort_keys=True) == before


def testExplicitPerContourIsExactlyLegacyDefault():
    space = defaultFrame(200, 100, sourceId="camera").coordinateSpace
    inputs = {"contours": _threeContours(space).toPayload()}
    operator = MinimumEnclosingCircleOperator()
    assert operator.executeNode(inputs, {}, {}) == operator.executeNode(inputs, {"scope": "perContour"}, {})


@pytest.mark.parametrize("scope", ("perContour", "allContours"))
def testEmptyInputDoesNotInventCircleOrCenter(scope):
    space = defaultFrame(200, 100, sourceId="camera").coordinateSpace
    result = MinimumEnclosingCircleOperator().executeNode(
        {"contours": ContourCollection((), space).toPayload()}, {"scope": scope}, {})
    assert result["status"] == "ok"
    assert result["outputs"]["circleCount"] == 0
    assert CircleCollection.fromPayload(result["outputs"]["circles"]).items == ()


def testOneContourHasSameGeometryInBothModes():
    space = defaultFrame(200, 100, sourceId="camera").coordinateSpace
    inputs = {"contours": ContourCollection((triangle(space),), space).toPayload()}
    operator = MinimumEnclosingCircleOperator()
    separate = operator.executeNode(inputs, {}, {})
    merged = operator.executeNode(inputs, {"scope": "allContours"}, {})
    assert CircleCollection.fromPayload(separate["outputs"]["circles"]).items[0].circle == (
        CircleCollection.fromPayload(merged["outputs"]["circles"]).items[0].circle)


def testMergedCirclePreservesCropCoordinatesAndReframesExactlyOnce():
    source = defaultFrame(1000, 800, sourceId="camera")
    crop = transformedFrame(source, 200, 100, (1, 0, 0, 1, 100, 200),
                            (0, 0, 200, 100), reference="crop")
    result = MinimumEnclosingCircleOperator().executeNode(
        {"contours": _threeContours(crop.coordinateSpace).toPayload()}, {"scope": "allContours"}, {})
    extracted = GeometryPointsOperator().executeNode({"circles": result["outputs"]["circles"]}, {}, {})
    assert extracted["outputs"]["pointCount"] == 1
    local = Point2D.fromPayload(extracted["outputs"]["points"][0])
    assert local.coordinateSpace == crop.coordinateSpace
    reframe = PointsReframeOperator()
    reframed = reframe.executeNode({"points": extracted["outputs"]["points"], "frame": source.toPayload()}, {}, {})
    point = Point2D.fromPayload(reframed["outputs"]["points"][0])
    assert (point.x, point.y) == pytest.approx((local.x + 100, local.y + 200))
    assert reframe.executeNode({"points": reframed["outputs"]["points"], "frame": source.toPayload()}, {}, {}) == reframed


@pytest.mark.parametrize("params", ({"scope": "largest"}, {"scope": ""}, {"scope": None},
                                   {"scope": True}, {"scope": []}, {"unknown": 1}))
def testUnsupportedScopeIsRejectedWithoutExecution(params):
    operator = MinimumEnclosingCircleOperator()
    assert operator.validateParams(params)["code"] == "E_PARAM_INVALID"
    assert operator.executeNode({}, params, {})["error"]["code"] == "E_PARAM_INVALID"
