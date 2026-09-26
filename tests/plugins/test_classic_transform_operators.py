import numpy as np
import pytest

from emo_master.core.contracts.geometry2d import BBox2D, CoordinateSpace2D
from emo_master.plugins.builtins._transform_operators import (
    AffineOperator,
    FlipOperator,
    PerspectiveOperator,
    RotateOperator,
)


def _frame(width: int, height: int) -> BBox2D:
    space = CoordinateSpace2D(
        sourceId="camera:0",
        imageWidth=width,
        imageHeight=height,
    )
    return BBox2D(0, 0, width, height, space)


def testRotateClockwiseExpandPreservesLandmarkAndMaskContract() -> None:
    image = np.zeros((3, 5), dtype=np.uint8)
    image[0, 1] = 255
    original = image.copy()

    result = RotateOperator().executeNode(
        {"image": image, "frame": _frame(5, 3).toPayload()},
        {"angleDegrees": 90, "expand": True, "interpolation": "nearest"},
        {"nodeId": "rotate"},
    )

    assert result["status"] == "ok"
    output = result["outputs"]["image"]
    validMask = result["outputs"]["validMask"]
    assert output.shape == (5, 3)
    assert np.argwhere(output == 255).tolist() == [[1, 2]]
    assert set(np.unique(validMask)) <= {0, 255}
    frame = BBox2D.fromPayload(result["outputs"]["frame"])
    assert frame.coordinateSpace.mapPointToSource(2, 1) == pytest.approx((1, 0))
    np.testing.assert_array_equal(image, original)


def testFlipModesKeepSizeAndComposeOutputToSource() -> None:
    image = np.arange(12, dtype=np.uint8).reshape(3, 4)
    operator = FlipOperator()

    horizontal = operator.executeNode(
        {"image": image}, {"mode": "horizontal"}, {}
    )
    both = operator.executeNode({"image": image}, {"mode": "both"}, {})

    np.testing.assert_array_equal(horizontal["outputs"]["image"], np.fliplr(image))
    np.testing.assert_array_equal(both["outputs"]["image"], np.flip(image))
    horizontalFrame = BBox2D.fromPayload(horizontal["outputs"]["frame"])
    bothFrame = BBox2D.fromPayload(both["outputs"]["frame"])
    assert horizontalFrame.coordinateSpace.mapPointToSource(0, 1) == (3, 1)
    assert bothFrame.coordinateSpace.mapPointToSource(0, 0) == (3, 2)


def testAffineUsesConfiguredForwardMapAndPreservesRequestedCanvas() -> None:
    image = np.zeros((6, 6), dtype=np.uint8)
    image[1, 1] = 255
    params = {
        "srcPoints": [{"x": 0, "y": 0}, {"x": 5, "y": 0}, {"x": 0, "y": 5}],
        "dstPoints": [{"x": 1, "y": 2}, {"x": 6, "y": 2}, {"x": 1, "y": 7}],
        "outputWidth": 8,
        "outputHeight": 9,
        "interpolation": "nearest",
        "padValue": 17,
    }

    result = AffineOperator().executeNode({"image": image}, params, {})

    assert result["status"] == "ok"
    assert result["outputs"]["image"].shape == (9, 8)
    assert np.argwhere(result["outputs"]["image"] == 255).tolist() == [[3, 2]]
    assert result["outputs"]["image"][0, 0] == 17
    frame = BBox2D.fromPayload(result["outputs"]["frame"])
    assert frame.coordinateSpace.mapPointToSource(2, 3) == pytest.approx((1, 1))
    assert set(np.unique(result["outputs"]["validMask"])) == {0, 255}


def testPerspectiveWritesHomographyAndMapsOutputCornersBackToSource() -> None:
    image = np.arange(36, dtype=np.uint8).reshape(6, 6)
    original = image.copy()
    params = {
        "srcPoints": [{"x": 1, "y": 1}, {"x": 4, "y": 1}, {"x": 4, "y": 4}, {"x": 1, "y": 4}],
        "outputWidth": 4,
        "outputHeight": 4,
        "interpolation": "nearest",
    }

    result = PerspectiveOperator().executeNode({"image": image}, params, {})

    assert result["status"] == "ok"
    np.testing.assert_array_equal(
        result["outputs"]["image"], image[1:5, 1:5]
    )
    framePayload = result["outputs"]["frame"]
    assert framePayload["schemaVersion"] == "1.2"
    frame = BBox2D.fromPayload(framePayload)
    assert frame.coordinateSpace.homographyToSource is not None
    assert frame.coordinateSpace.mapPointToSource(0, 0) == pytest.approx((1, 1))
    assert frame.coordinateSpace.mapPointToSource(3, 3) == pytest.approx((4, 4))
    assert set(np.unique(result["outputs"]["validMask"])) == {255}
    np.testing.assert_array_equal(image, original)


def testTransformValidationRejectsDegeneratePointsAndEmptyValidMask() -> None:
    image = np.zeros((5, 5), dtype=np.uint8)
    affine = AffineOperator().executeNode(
        {"image": image},
        {
            "srcPoints": [{"x": 0, "y": 0}, {"x": 1, "y": 1}, {"x": 2, "y": 2}],
            "dstPoints": [{"x": 0, "y": 0}, {"x": 1, "y": 0}, {"x": 0, "y": 1}],
        },
        {},
    )
    empty = FlipOperator().executeNode(
        {"image": image, "validMask": np.zeros_like(image)},
        {},
        {},
    )
    invalidQuad = PerspectiveOperator().executeNode(
        {"image": image},
        {
            "srcPoints": [{"x": 0, "y": 0}, {"x": 4, "y": 4}, {"x": 4, "y": 0}, {"x": 0, "y": 4}],
            "outputWidth": 5,
            "outputHeight": 5,
        },
        {},
    )

    assert affine["error"]["code"] == "E_PARAM_INVALID"
    assert empty["error"]["code"] == "E_INPUT_SHAPE"
    assert invalidQuad["error"]["code"] == "E_PARAM_INVALID"


def testRotateAffinePerspectiveChainKeepsValidMaskAndLandmarkSourceMapping() -> None:
    image = np.zeros((10, 10), dtype=np.uint8)
    image[4, 3] = 255
    rotated = RotateOperator().executeNode(
        {"image": image},
        {"angleDegrees": 0, "expand": True, "interpolation": "nearest"},
        {"nodeId": "rotate"},
    )
    affine = AffineOperator().executeNode(
        {
            "image": rotated["outputs"]["image"],
            "frame": rotated["outputs"]["frame"],
            "validMask": rotated["outputs"]["validMask"],
        },
        {
            "srcPoints": [{"x": 0, "y": 0}, {"x": 9, "y": 0}, {"x": 0, "y": 9}],
            "dstPoints": [{"x": 1, "y": 2}, {"x": 10, "y": 2}, {"x": 1, "y": 11}],
            "outputWidth": 12,
            "outputHeight": 13,
            "interpolation": "nearest",
        },
        {"nodeId": "affine"},
    )
    perspective = PerspectiveOperator().executeNode(
        {
            "image": affine["outputs"]["image"],
            "frame": affine["outputs"]["frame"],
            "validMask": affine["outputs"]["validMask"],
        },
        {
            "srcPoints": [{"x": 0, "y": 0}, {"x": 11, "y": 0}, {"x": 11, "y": 12}, {"x": 0, "y": 12}],
            "outputWidth": 12,
            "outputHeight": 13,
            "interpolation": "nearest",
        },
        {"nodeId": "perspective"},
    )

    assert perspective["status"] == "ok"
    landmark = np.argwhere(perspective["outputs"]["image"] == 255)
    assert landmark.tolist() == [[6, 4]]
    finalFrame = BBox2D.fromPayload(perspective["outputs"]["frame"])
    assert finalFrame.coordinateSpace.homographyToSource is not None
    assert finalFrame.coordinateSpace.mapPointToSource(4, 6) == pytest.approx((3, 4))
    assert set(np.unique(perspective["outputs"]["validMask"])) <= {0, 255}
