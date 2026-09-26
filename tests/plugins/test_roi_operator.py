import numpy as np
import pytest

from emo_master.core.contracts.geometry2d import BBox2D, Polygon2D, RotatedBox2D
from emo_master.plugins.builtins._image_frame import defaultFrame
from emo_master.plugins.builtins.roi.operator import RoiOperator


def testBboxRoiProducesTightAndFullSizeOutputsWithPadding() -> None:
    image = np.arange(20, dtype=np.uint8).reshape(4, 5)
    original = image.copy()

    result = RoiOperator().executeNode(
        {"image": image},
        {
            "roiType": "bbox",
            "x": -1,
            "y": 1,
            "width": 4,
            "height": 3,
            "padValue": 99,
            "interpolation": "nearest",
        },
        {"nodeId": "roi"},
    )

    assert result["status"] == "ok"
    outputs = result["outputs"]
    assert outputs["croppedImage"].shape == (3, 4)
    assert outputs["maskedImage"].shape == image.shape
    np.testing.assert_array_equal(outputs["croppedImage"][:, 0], [99, 99, 99])
    np.testing.assert_array_equal(outputs["croppedImage"][:, 1:], image[1:4, 0:3])
    assert set(np.unique(outputs["croppedMask"])) <= {0, 255}
    assert int(np.count_nonzero(outputs["fullMask"])) == 9
    frame = BBox2D.fromPayload(outputs["croppedFrame"])
    assert frame.coordinateSpace.mapPointToSource(1.0, 0.0) == (0.0, 1.0)
    roi = BBox2D.fromPayload(outputs["roi"])
    assert (roi.x, roi.y, roi.width, roi.height) == (-1.0, 1.0, 4.0, 3.0)
    np.testing.assert_array_equal(image, original)


def testRotatedBoxRoiRectifiesImageAndTracksAffine() -> None:
    image = np.full((7, 7, 3), 200, dtype=np.uint8)

    result = RoiOperator().executeNode(
        {"image": image},
        {
            "roiType": "rotatedBox",
            "centerX": 3,
            "centerY": 3,
            "width": 4,
            "height": 2,
            "angleDegrees": 90,
            "interpolation": "nearest",
        },
        {},
    )

    assert result["status"] == "ok"
    outputs = result["outputs"]
    assert outputs["croppedImage"].shape == (2, 4, 3)
    assert isinstance(RotatedBox2D.fromPayload(outputs["roi"]), RotatedBox2D)
    frame = BBox2D.fromPayload(outputs["croppedFrame"])
    sourceCenter = frame.coordinateSpace.mapPointToSource(1.5, 0.5)
    assert sourceCenter == pytest.approx((3.0, 3.0))
    assert np.all(outputs["croppedMask"] == 255)


def testPolygonRoiSupportsConcaveShapeAndRejectsSelfIntersection() -> None:
    image = np.full((6, 6, 3), 50, dtype=np.uint8)
    points = [
        {"x": 1, "y": 1},
        {"x": 5, "y": 1},
        {"x": 3, "y": 3},
        {"x": 5, "y": 5},
        {"x": 1, "y": 5},
    ]

    result = RoiOperator().executeNode(
        {"image": image},
        {"roiType": "polygon", "points": points, "padValue": 7},
        {},
    )

    assert result["status"] == "ok"
    assert result["outputs"]["croppedImage"].shape == (4, 4, 3)
    assert isinstance(Polygon2D.fromPayload(result["outputs"]["roi"]), Polygon2D)
    assert np.any(result["outputs"]["croppedMask"] == 0)
    assert np.any(result["outputs"]["croppedMask"] == 255)

    invalid = RoiOperator().executeNode(
        {"image": image},
        {
            "roiType": "polygon",
            "points": [
                {"x": 0, "y": 0},
                {"x": 4, "y": 4},
                {"x": 0, "y": 4},
                {"x": 4, "y": 0},
            ],
        },
        {},
    )
    assert invalid["error"]["code"] == "E_PARAM_INVALID"


def testRoiRejectsRegionOutsideValidImageAndInvalidInput() -> None:
    image = np.zeros((4, 4), dtype=np.uint8)
    outside = RoiOperator().executeNode(
        {"image": image},
        {"roiType": "bbox", "x": 10, "y": 10, "width": 2, "height": 2},
        {},
    )
    invalidImage = RoiOperator().executeNode(
        {"image": image.astype(np.float32)}, {}, {}
    )

    assert outside["error"]["code"] == "E_INPUT_SHAPE"
    assert invalidImage["error"]["code"] == "E_INPUT_TYPE"


def testRoiReportsInvalidFrameBoundsAsInputShape() -> None:
    image = np.zeros((4, 4), dtype=np.uint8)
    frame = defaultFrame(4, 4)
    invalidFrame = BBox2D(
        0,
        0,
        5,
        4,
        frame.coordinateSpace,
    ).toPayload()

    result = RoiOperator().executeNode(
        {"image": image, "frame": invalidFrame},
        {"roiType": "bbox", "x": 0, "y": 0, "width": 2, "height": 2},
        {},
    )

    assert result["error"]["code"] == "E_INPUT_SHAPE"
