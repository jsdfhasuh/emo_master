import cv2
import numpy as np
import pytest

from emo_master.core.contracts.geometry2d import (
    ContourCollection,
    ContourItem,
    CoordinateSpace2D,
    Point2D,
    Polygon2D,
)
from emo_master.plugins.builtins._shape_operators import ContourExtractionOperator
from emo_master.plugins.builtins.collection_filter.configuration import prepareFilterParams
from emo_master.plugins.builtins.collection_filter.operator import CollectionFilterOperator
from emo_master.plugins.builtins.threshold.operator import ThresholdOperator


def _rectangle(identifier, width, height, count=10):
    space = CoordinateSpace2D(sourceId="source", imageWidth=200, imageHeight=200)
    points = [(0, 0), (width / 4, 0), (width / 2, 0), (width, 0),
              (width, height / 2), (width, height), (width / 2, height),
              (width / 4, height), (0, height), (0, height / 2)]
    if count == 9:
        points.pop(1)
    elif count == 11:
        points.insert(1, (width / 8, 0))
    return ContourItem(identifier, Polygon2D(tuple(Point2D(x, y, space) for x, y in points)))


def _filter(items, **params):
    source = ContourCollection(tuple(items), items[0].polygon.coordinateSpace)
    return CollectionFilterOperator().executeNode({"contours": source.toPayload()}, params, {})


def testMinimumContourPointsIncludesTenAndCombinesWithArea():
    items = [_rectangle(str(n), 20, 20, n) for n in (9, 10, 11)]
    result = _filter(items, collectionType="contours", contourPointsEnabled=True,
                     minContourPoints=10, areaEnabled=True, minArea=50, maxArea=10000)
    assert result["status"] == "ok"
    assert result["outputs"]["keptCount"] == 2
    assert result["outputs"]["rejectedCount"] == 1
    kept = ContourCollection.fromPayload(result["outputs"]["keptContours"])
    assert [item.contourId for item in kept.items] == ["10", "11"]
    assert [len(item.polygon.points) for item in kept.items] == [10, 11]


def testAreaBoundsRemainInclusiveWhenPointCountIsEnabled():
    items = [_rectangle("below", 7, 7), _rectangle("minimum", 5, 10),
             _rectangle("maximum", 100, 100), _rectangle("above", 73, 137)]
    result = _filter(items, minContourPoints=10, minArea=50, maxArea=10000)
    kept = ContourCollection.fromPayload(result["outputs"]["keptContours"])
    assert [item.contourId for item in kept.items] == ["minimum", "maximum"]


@pytest.mark.parametrize("params", ({}, {"minContourPoints": 0},
                                   {"contourPointsEnabled": False, "minContourPoints": 99}))
def testPointCountDefaultAndDisabledGroupDoNotChangeLegacyResults(params):
    result = _filter([_rectangle("nine", 20, 20, 9)], **params)
    assert result["status"] == "ok"
    assert result["outputs"]["keptCount"] == 1


@pytest.mark.parametrize("invalid", (-1, 1.5, True, "10", None))
def testMinimumPointsRequiresNonNegativeInteger(invalid):
    operator = CollectionFilterOperator()
    assert operator.validateParams({"minContourPoints": invalid})["code"] == "E_PARAM_INVALID"
    assert operator.validateParams({"contourPointsEnabled": False, "minContourPoints": invalid}) is None


def testEditorInfersPointCountGroupFromStoredValue():
    assert prepareFilterParams({"minContourPoints": 10}, "contours")["contourPointsEnabled"]
    assert not prepareFilterParams({}, "contours")["contourPointsEnabled"]


def testScreenshotThresholdAndContourFilteringMatchOpenCv():
    # Legacy app's "automatic" branch actually calls fixed THRESH_BINARY_INV.
    gray = np.full((240, 320), 180, np.uint8)
    cv2.circle(gray, (80, 110), 20, 20, -1)
    cv2.circle(gray, (220, 110), 15, 80, -1)
    cv2.rectangle(gray, (5, 5), (10, 10), 20, -1)
    cv2.circle(gray, (150, 200), 2, 20, -1)
    threshold = ThresholdOperator().executeNode(
        {"image": gray}, {"mode": "fixed", "threshold": 80, "invert": True,
                          "blockSize": 11, "constant": 2}, {})
    assert threshold["status"] == "ok"
    _, expectedMask = cv2.threshold(gray, 80, 255, cv2.THRESH_BINARY_INV)
    np.testing.assert_array_equal(threshold["outputs"]["mask"], expectedMask)
    assert threshold["outputs"]["threshold"] == 80
    extracted = ContourExtractionOperator().executeNode(
        {"mask": threshold["outputs"]["mask"], "frame": threshold["outputs"]["frame"]},
        {"retrievalMode": "list", "approximation": "simple"}, {})
    assert extracted["status"] == "ok"
    result = CollectionFilterOperator().executeNode(
        {"contours": extracted["outputs"]["contours"]},
        {"collectionType": "contours", "contourPointsEnabled": True,
         "minContourPoints": 10, "areaEnabled": True, "minArea": 50, "maxArea": 10000}, {})
    assert result["status"] == "ok"
    expected, _ = cv2.findContours(expectedMask, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    expected = [c for c in expected if len(c) >= 10 and 50 <= cv2.contourArea(c) <= 10000]
    kept = ContourCollection.fromPayload(result["outputs"]["keptContours"])
    assert len(kept.items) == len(expected) == 2
    for item, contour in zip(kept.items, expected):
        np.testing.assert_array_equal([(p.x, p.y) for p in item.polygon.points], contour.reshape(-1, 2))
