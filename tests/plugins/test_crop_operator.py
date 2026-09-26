from __future__ import annotations

import numpy as np

from emo_master.core.contracts.geometry2d import (
    BBox2D,
    CoordinateSpace2D,
)
from emo_master.plugins.builtins.crop.operator import CropOperator


def testCropByParametersPadsAndMapsOutputCoordinates() -> None:
    image = np.arange(20, dtype=np.uint8).reshape(4, 5)

    result = CropOperator().executeNode(
        {"image": image},
        {
            "x": 1,
            "y": 1,
            "width": 2,
            "height": 2,
            "paddingLeft": 1,
            "paddingTop": 2,
            "paddingRight": 1,
            "padValue": 99,
        },
        {"nodeId": "crop"},
    )

    assert result["status"] == "ok"
    output = result["outputs"]["image"]
    assert output.shape == (4, 4)
    assert output[0, 0] == 99
    np.testing.assert_array_equal(output[2:4, 1:3], image[1:3, 1:3])
    frame = BBox2D.fromPayload(result["outputs"]["frame"])
    assert (frame.x, frame.y, frame.width, frame.height) == (1.0, 2.0, 2.0, 2.0)
    assert frame.coordinateSpace.mapPointToSource(1, 2) == (1.0, 1.0)


def testCropAcceptsTypedBboxAndComposesItsCoordinateSpace() -> None:
    image = np.zeros((5, 6, 3), dtype=np.uint8)
    space = CoordinateSpace2D(
        imageWidth=6,
        imageHeight=5,
        sourceId="camera",
        transformToSource=(2, 0, 0, 2, 10, 20),
    )
    roi = BBox2D(2.2, 1.1, 2.0, 2.0, space)

    result = CropOperator().executeNode(
        {"image": image, "roi": roi.toPayload()},
        {},
        {},
    )

    output = result["outputs"]["image"]
    assert output.shape == (3, 3, 3)
    frame = BBox2D.fromPayload(result["outputs"]["frame"])
    assert frame.coordinateSpace.sourceId == "camera"
    assert frame.coordinateSpace.mapPointToSource(0, 0) == (14.0, 22.0)


def testCropCanClipOrRejectOutOfBoundsRoi() -> None:
    image = np.zeros((4, 4), dtype=np.uint8)
    operator = CropOperator()

    clipped = operator.executeNode(
        {"image": image},
        {"x": -1, "y": -1, "width": 3, "height": 3},
        {},
    )
    rejected = operator.executeNode(
        {"image": image},
        {"x": -1, "y": -1, "width": 3, "height": 3, "clip": False},
        {},
    )

    assert clipped["outputs"]["image"].shape == (2, 2)
    assert rejected["error"]["code"] == "E_INPUT_SHAPE"


def testCropRejectsInvalidPaddingAndMismatchedRoiSpace() -> None:
    image = np.zeros((4, 4), dtype=np.uint8)
    operator = CropOperator()
    badPadding = operator.executeNode(
        {"image": image},
        {"paddingLeft": -1},
        {},
    )
    badSpace = CoordinateSpace2D(imageWidth=5, imageHeight=5)
    mismatched = operator.executeNode(
        {"image": image, "roi": BBox2D(0, 0, 1, 1, badSpace).toPayload()},
        {},
        {},
    )
    assert badPadding["error"]["code"] == "E_PARAM_INVALID"
    assert mismatched["error"]["code"] == "E_INPUT_SHAPE"
