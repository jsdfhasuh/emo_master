import math

import cv2
import numpy as np
import pytest

from emo_master.core.contracts.geometry2d import (
    BBox2D,
    Blob2D,
    BlobCollection,
    CircleCollection,
    ContourCollection,
    CoordinateSpace2D,
    LineCollection,
    Point2D,
    ShapeMeasurementCollection,
    TemplateMatchCollection,
)
from emo_master.plugins.builtins._detection_operators import (
    HoughCircleOperator,
    HoughLineOperator,
    TemplateMatchingOperator,
)
from emo_master.plugins.builtins._shape_operators import (
    ContourExtractionOperator,
    ShapeMeasurementOperator,
)
from emo_master.plugins.builtins.annotate.operator import AnnotateOperator
from emo_master.plugins.builtins.collection_filter.operator import CollectionFilterOperator


def _space(width: int, height: int) -> CoordinateSpace2D:
    return CoordinateSpace2D(
        sourceId="camera:0",
        imageWidth=width,
        imageHeight=height,
    )


def testContourExtractionPreservesTreeHierarchyAndSkipsFrameOutsidePixels() -> None:
    mask = np.zeros((14, 16), dtype=np.uint8)
    mask[1:11, 1:11] = 255
    mask[4:8, 4:8] = 0
    mask[12:14, 14:16] = 255
    frame = BBox2D(0, 0, 12, 12, _space(16, 14))
    original = mask.copy()

    result = ContourExtractionOperator().executeNode(
        {"mask": mask, "frame": frame.toPayload()},
        {"retrievalMode": "tree", "approximation": "simple"},
        {},
    )

    assert result["status"] == "ok"
    contours = ContourCollection.fromPayload(result["outputs"]["contours"])
    assert len(contours.items) == 2
    outer, hole = contours.items
    assert outer.firstChildId == hole.contourId
    assert hole.parentId == outer.contourId
    assert (outer.depth, outer.isHole) == (0, False)
    assert (hole.depth, hole.isHole) == (1, True)
    assert len(result["outputs"]["polygons"]) == len(contours.items)
    np.testing.assert_array_equal(mask, original)

    external = ContourExtractionOperator().executeNode(
        {"mask": mask, "frame": frame.toPayload()},
        {"retrievalMode": "external"},
        {},
    )
    assert len(ContourCollection.fromPayload(external["outputs"]["contours"]).items) == 1


def testShapeMeasurementMatchesOpenCvAndRejectsBlobWithoutContour() -> None:
    mask = np.zeros((12, 12), dtype=np.uint8)
    mask[2:9, 3:10] = 255
    contoursResult = ContourExtractionOperator().executeNode(
        {"mask": mask}, {"retrievalMode": "external"}, {}
    )
    contours = ContourCollection.fromPayload(contoursResult["outputs"]["contours"])

    result = ShapeMeasurementOperator().executeNode(
        {"contours": contours.toPayload()}, {}, {}
    )
    measurements = ShapeMeasurementCollection.fromPayload(
        result["outputs"]["measurements"]
    )
    assert len(measurements.items) == 1
    measured = measurements.items[0]
    raw = np.asarray(
        [(point.x, point.y) for point in contours.items[0].polygon.points],
        dtype=np.float32,
    ).reshape(-1, 1, 2)
    expectedArea = abs(float(cv2.contourArea(raw)))
    expectedPerimeter = float(cv2.arcLength(raw, True))
    assert measured.area == pytest.approx(expectedArea)
    assert measured.perimeter == pytest.approx(expectedPerimeter)
    assert measured.circularity == pytest.approx(
        4 * math.pi * expectedArea / expectedPerimeter**2
    )
    assert measured.minAreaRect.width >= measured.minAreaRect.height
    assert -90 <= measured.minAreaRect.angleDegrees < 90

    space = _space(12, 12)
    blobs = BlobCollection(
        (Blob2D("blob", 4, Point2D(2, 2, space), BBox2D(1, 1, 2, 2, space)),),
        space,
    )
    missingContour = ShapeMeasurementOperator().executeNode(
        {"blobs": blobs.toPayload()}, {}, {}
    )
    assert missingContour["error"]["code"] == "E_INPUT_SHAPE"


@pytest.mark.parametrize(
    "method",
    ["sqdiff", "sqdiffNormed", "ccorr", "ccorrNormed", "ccoeff", "ccoeffNormed"],
)
def testTemplateMatchingAllMethodsFindStableExactPeak(method: str) -> None:
    random = np.random.default_rng(3)
    template = random.integers(0, 256, (4, 5), dtype=np.uint8)
    image = random.integers(0, 30, (14, 16), dtype=np.uint8)
    image[6:10, 7:12] = template

    result = TemplateMatchingOperator().executeNode(
        {"image": image, "template": template},
        {
            "method": method,
            "thresholdMode": "quality",
            "threshold": 0.99,
            "peakKernelSize": 3,
            "maxMatches": 5,
        },
        {},
    )

    assert result["status"] == "ok"
    matches = TemplateMatchCollection.fromPayload(result["outputs"]["matches"])
    assert [item.matchId for item in matches.items] == ["match-1"]
    assert (matches.items[0].bbox.x, matches.items[0].bbox.y) == (7, 6)
    assert matches.items[0].quality == pytest.approx(1.0)


def testTemplateMatchingRoiAndInputShapePolicies() -> None:
    image = np.zeros((8, 8), dtype=np.uint8)
    template = np.zeros((4, 4), dtype=np.uint8)
    space = _space(8, 8)
    smallRoi = BBox2D(0, 0, 2, 2, space)
    empty = TemplateMatchingOperator().executeNode(
        {
            "image": image,
            "template": template,
            "frame": BBox2D(0, 0, 8, 8, space).toPayload(),
            "roi": smallRoi,
        },
        {},
        {},
    )
    tooLarge = TemplateMatchingOperator().executeNode(
        {"image": image, "template": np.zeros((9, 9), dtype=np.uint8)}, {}, {}
    )
    nativeMismatch = TemplateMatchingOperator().executeNode(
        {"image": cv2.cvtColor(image, cv2.COLOR_GRAY2BGR), "template": template},
        {"colorMode": "native"},
        {},
    )

    assert empty["status"] == "ok"
    assert TemplateMatchCollection.fromPayload(empty["outputs"]["matches"]).items == ()
    assert tooLarge["error"]["code"] == "E_INPUT_SHAPE"
    assert nativeMismatch["error"]["code"] == "E_INPUT_SHAPE"


def testHoughLineConsumesEdgeOnlyAndReturnsDeterministicLines() -> None:
    edge = np.zeros((30, 30), dtype=np.uint8)
    cv2.line(edge, (3, 10), (25, 10), 255, 1)

    result = HoughLineOperator().executeNode(
        {"edge": edge},
        {"threshold": 5, "minLineLength": 10, "maxLineGap": 1},
        {},
    )

    assert result["status"] == "ok"
    lines = LineCollection.fromPayload(result["outputs"]["lines"])
    assert len(lines.items) >= 1
    lengths = [
        math.hypot(
            item.line.end.x - item.line.start.x,
            item.line.end.y - item.line.start.y,
        )
        for item in lines.items
    ]
    assert lengths == sorted(lengths, reverse=True)
    assert all(
        (item.line.start.y, item.line.start.x)
        <= (item.line.end.y, item.line.end.x)
        for item in lines.items
    )

    wrongChannels = HoughLineOperator().executeNode(
        {"edge": cv2.cvtColor(edge, cv2.COLOR_GRAY2BGR)}, {}, {}
    )
    assert wrongChannels["error"]["code"] == "E_INPUT_SHAPE"


def testHoughCircleAppliesFrameMaskAndSortsResults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, np.ndarray] = {}

    def fakeHoughCircles(image: np.ndarray, *args: object, **kwargs: object):
        _ = args, kwargs
        captured["image"] = image.copy()
        return np.asarray([[[5, 4, 3], [2, 2, 6], [9, 1, 4]]], dtype=np.float32)

    monkeypatch.setattr(cv2, "HoughCircles", fakeHoughCircles)
    image = np.full((10, 12, 3), 100, dtype=np.uint8)
    frame = BBox2D(2, 3, 5, 4, _space(12, 10))

    result = HoughCircleOperator().executeNode(
        {"image": image, "frame": frame.toPayload()}, {}, {}
    )

    assert result["status"] == "ok"
    assert np.all(captured["image"][:3] == 0)
    assert np.all(captured["image"][:, :2] == 0)
    assert np.all(captured["image"][3:7, 2:7] == 100)
    circles = CircleCollection.fromPayload(result["outputs"]["circles"])
    assert [item.circle.radius for item in circles.items] == [6, 4, 3]
    assert [item.circleId for item in circles.items] == [
        "circle-1",
        "circle-2",
        "circle-3",
    ]

    invalid = HoughCircleOperator().executeNode(
        {"image": image}, {"minRadius": 5, "maxRadius": 2}, {}
    )
    assert invalid["error"]["code"] == "E_PARAM_INVALID"


@pytest.mark.parametrize("color", [False, True], ids=["gray", "bgr"])
def testRealHoughCircleFindsSyntheticCirclesAndRespectsFrame(color: bool) -> None:
    gray = np.zeros((160, 224), dtype=np.uint8)
    centers = [(56, 80), (168, 80)]
    for center in centers:
        cv2.circle(gray, center, 24, 255, 2, lineType=cv2.LINE_8)
    image = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR) if color else gray
    original = image.copy()
    space = CoordinateSpace2D(
        sourceId="synthetic-circles", imageWidth=224, imageHeight=160
    )
    fullFrame = BBox2D(0, 0, 224, 160, space)
    leftFrame = BBox2D(8, 32, 96, 96, space)
    params = {
        "dp": 1.0,
        "minDist": 40.0,
        "param1": 80.0,
        "param2": 18.0,
        "minRadius": 20,
        "maxRadius": 28,
    }
    operator = HoughCircleOperator()

    for frame, expectedCenters in [(fullFrame, centers), (leftFrame, centers[:1])]:
        framePayload = frame.toPayload()
        result = operator.executeNode(
            {"image": image, "frame": framePayload}, params, {}
        )

        assert result["status"] == "ok", result
        circles = CircleCollection.fromPayload(result["outputs"]["circles"])
        assert circles.coordinateSpace == space
        for centerX, centerY in expectedCenters:
            # Hough localization varies slightly across supported OpenCV builds.
            assert any(
                math.hypot(item.circle.center.x - centerX, item.circle.center.y - centerY)
                <= 3.0
                and abs(item.circle.radius - 24) <= 4.0
                for item in circles.items
            ), circles
        if frame == leftFrame:
            assert all(item.circle.center.x < 104 for item in circles.items)
        assert result["metrics"]["count"] == len(circles.items)
        assert BBox2D.fromPayload(result["outputs"]["frame"]) == frame
        assert framePayload == frame.toPayload()
        np.testing.assert_array_equal(image, original)


def testRealHoughCircleReturnsEmptyTypedCollectionForBlankImage() -> None:
    image = np.zeros((96, 96), dtype=np.uint8)
    original = image.copy()
    frame = BBox2D(0, 0, 96, 96, _space(96, 96))

    result = HoughCircleOperator().executeNode(
        {"image": image, "frame": frame.toPayload()},
        {"minRadius": 10, "maxRadius": 30, "param2": 18.0},
        {},
    )

    assert result["status"] == "ok", result
    circles = CircleCollection.fromPayload(result["outputs"]["circles"])
    assert circles.items == ()
    assert circles.coordinateSpace == frame.coordinateSpace
    assert result["metrics"]["count"] == 0
    assert BBox2D.fromPayload(result["outputs"]["frame"]) == frame
    np.testing.assert_array_equal(image, original)


@pytest.mark.parametrize(
    "inputs,code",
    [
        ({}, "E_INPUT_MISSING"),
        ({"image": [[0, 255]]}, "E_INPUT_TYPE"),
        ({"image": np.zeros((8, 8), dtype=np.float32)}, "E_INPUT_TYPE"),
        ({"image": np.zeros((0, 8), dtype=np.uint8)}, "E_INPUT_SHAPE"),
        ({"image": np.zeros((8, 8, 4), dtype=np.uint8)}, "E_INPUT_SHAPE"),
        ({"image": np.zeros((8, 8), dtype=np.uint8), "frame": {}}, "E_INPUT_TYPE"),
        (
            {
                "image": np.zeros((8, 8), dtype=np.uint8),
                "frame": BBox2D(0, 0, 8, 8, _space(9, 8)).toPayload(),
            },
            "E_INPUT_SHAPE",
        ),
        (
            {
                "image": np.zeros((8, 8), dtype=np.uint8),
                "frame": BBox2D(1, 0, 8, 8, _space(8, 8)).toPayload(),
            },
            "E_INPUT_SHAPE",
        ),
    ],
    ids=[
        "missing", "not-array", "float-pixels", "empty", "four-channels",
        "malformed-frame", "wrong-frame-size", "frame-outside-image",
    ],
)
def testHoughCircleRejectsInvalidImagesAndFrames(
    inputs: dict[str, object], code: str
) -> None:
    result = HoughCircleOperator().executeNode(inputs, {}, {})

    assert result["status"] == "error"
    assert result["error"]["code"] == code
    assert "outputs" not in result


@pytest.mark.parametrize(
    "params",
    [
        {"dp": 0},
        {"minDist": float("nan")},
        {"param1": 0},
        {"param2": -1},
        {"minRadius": -1},
        {"maxRadius": True},
    ],
    ids=["zero-dp", "nan-distance", "zero-gradient", "negative-votes", "radius", "bool-radius"],
)
def testHoughCircleRejectsInvalidParametersWithoutMutatingImage(
    params: dict[str, object],
) -> None:
    image = np.arange(64, dtype=np.uint8).reshape(8, 8)
    original = image.copy()

    result = HoughCircleOperator().executeNode({"image": image}, params, {})

    assert result["status"] == "error"
    assert result["error"]["code"] == "E_PARAM_INVALID"
    assert "outputs" not in result
    np.testing.assert_array_equal(image, original)


def testHoughLineCircleFilterAnnotateChain(monkeypatch: pytest.MonkeyPatch) -> None:
    edge = np.zeros((40, 40), dtype=np.uint8)
    cv2.line(edge, (5, 8), (32, 8), 255, 1)
    monkeypatch.setattr(
        cv2,
        "HoughLinesP",
        lambda *args, **kwargs: np.asarray([[[5, 8, 32, 8]]], dtype=np.int32),
    )
    lineResult = HoughLineOperator().executeNode(
        {"edge": edge}, {"threshold": 5, "minLineLength": 15}, {}
    )

    monkeypatch.setattr(
        cv2,
        "HoughCircles",
        lambda *args, **kwargs: np.asarray([[[20, 25, 7]]], dtype=np.float32),
    )
    circleResult = HoughCircleOperator().executeNode(
        {"image": edge}, {"minRadius": 5}, {}
    )
    filteredLines = CollectionFilterOperator().executeNode(
        {"lines": lineResult["outputs"]["lines"]}, {"minLength": 15}, {}
    )
    filteredCircles = CollectionFilterOperator().executeNode(
        {"circles": circleResult["outputs"]["circles"]}, {"minRadius": 5}, {}
    )
    annotated = AnnotateOperator().executeNode(
        {
            "image": cv2.cvtColor(edge, cv2.COLOR_GRAY2BGR),
            "frame": circleResult["outputs"]["frame"],
            "lines": filteredLines["outputs"]["keptLines"],
            "circles": filteredCircles["outputs"]["keptCircles"],
        },
        {"drawLabels": False, "thickness": 1},
        {},
    )

    assert annotated["status"] == "ok"
    expected = len(
        LineCollection.fromPayload(filteredLines["outputs"]["keptLines"]).items
    ) + len(
        CircleCollection.fromPayload(
            filteredCircles["outputs"]["keptCircles"]
        ).items
    )
    assert annotated["outputs"]["drawnCount"] == expected
    assert expected >= 2
    assert np.any(annotated["outputs"]["overlay"] != cv2.cvtColor(edge, cv2.COLOR_GRAY2BGR))
