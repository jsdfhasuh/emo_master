from __future__ import annotations

import numpy as np

from emo_master.core.contracts.geometry2d import (
    BBox2D,
    CoordinateSpace2D,
)
from emo_master.plugins.builtins.resize.operator import ResizeOperator


def testResizeStretchReturnsFrameThatMapsToSource() -> None:
    image = np.arange(8, dtype=np.uint8).reshape(2, 4)

    result = ResizeOperator().executeNode(
        {"image": image},
        {"width": 8, "height": 4, "mode": "stretch", "interpolation": "nearest"},
        {"nodeId": "resize"},
    )

    assert result["status"] == "ok"
    output = result["outputs"]["image"]
    assert output.shape == (4, 8)
    frame = BBox2D.fromPayload(result["outputs"]["frame"])
    assert (frame.x, frame.y, frame.width, frame.height) == (0.0, 0.0, 8.0, 4.0)
    assert frame.coordinateSpace.reference == "node:resize"
    assert frame.coordinateSpace.mapPointToSource(2.0, 2.0) == (1.0, 1.0)


def testResizeLetterboxUsesCenteredPaddingAndTracksContent() -> None:
    image = np.full((2, 4, 3), 255, dtype=np.uint8)

    result = ResizeOperator().executeNode(
        {"image": image},
        {
            "width": 6,
            "height": 6,
            "mode": "letterbox",
            "interpolation": "nearest",
            "padValue": 114,
        },
        {},
    )

    output = result["outputs"]["image"]
    assert output.shape == (6, 6, 3)
    np.testing.assert_array_equal(output[0, 0], np.asarray([114, 114, 114]))
    frame = BBox2D.fromPayload(result["outputs"]["frame"])
    assert (frame.x, frame.y, frame.width, frame.height) == (0.0, 1.0, 6.0, 3.0)
    assert np.allclose(frame.coordinateSpace.mapPointToSource(0.0, 1.0), (0.0, 0.0))
    assert result["metrics"]["padding"] == [0, 1, 0, 2]


def testResizeComposesAnExistingSourceTransform() -> None:
    image = np.zeros((2, 2), dtype=np.uint8)
    sourceSpace = CoordinateSpace2D(
        imageWidth=2,
        imageHeight=2,
        sourceId="camera-1",
        transformToSource=(2, 0, 0, 2, 10, 20),
    )
    inputFrame = BBox2D(0, 0, 2, 2, sourceSpace)

    result = ResizeOperator().executeNode(
        {"image": image, "frame": inputFrame.toPayload()},
        {"width": 4, "height": 4},
        {},
    )

    frame = BBox2D.fromPayload(result["outputs"]["frame"])
    assert frame.coordinateSpace.sourceId == "camera-1"
    assert frame.coordinateSpace.mapPointToSource(2, 2) == (12.0, 22.0)


def testResizeRejectsInvalidParametersAndImages() -> None:
    operator = ResizeOperator()
    valid = np.zeros((2, 2), dtype=np.uint8)
    cases = [
        operator.executeNode({}, {"width": 2, "height": 2}, {}),
        operator.executeNode({"image": valid}, {"height": 2}, {}),
        operator.executeNode({"image": valid}, {"width": 0, "height": 2}, {}),
        operator.executeNode(
            {"image": valid}, {"width": 2, "height": 2, "mode": "bad"}, {}
        ),
        operator.executeNode(
            {"image": np.zeros((2, 2), dtype=np.float32)},
            {"width": 2, "height": 2},
            {},
        ),
    ]
    assert [item["error"]["code"] for item in cases] == [
        "E_INPUT_MISSING",
        "E_PARAM_INVALID",
        "E_PARAM_INVALID",
        "E_PARAM_INVALID",
        "E_INPUT_TYPE",
    ]
