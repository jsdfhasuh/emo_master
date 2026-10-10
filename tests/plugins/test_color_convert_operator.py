from __future__ import annotations

import cv2
import numpy as np

from emo_master.core.contracts.geometry2d import BBox2D
from emo_master.plugins.builtins.color_convert.operator import ColorConvertOperator


def testColorConvertBgrToHsvMatchesOpenCvAndEmitsFrame() -> None:
    image = np.asarray([[[0, 0, 255], [0, 255, 0]]], dtype=np.uint8)
    result = ColorConvertOperator().executeNode(
        {"image": image},
        {"sourceSpace": "BGR", "targetSpace": "HSV"},
        {},
    )
    np.testing.assert_array_equal(
        result["outputs"]["image"],
        cv2.cvtColor(image, cv2.COLOR_BGR2HSV),
    )
    frame = BBox2D.fromPayload(result["outputs"]["frame"])
    assert (frame.width, frame.height) == (2.0, 1.0)


def testColorConvertGrayToRgbAndSameSpaceCopies() -> None:
    gray = np.asarray([[0, 255]], dtype=np.uint8)
    operator = ColorConvertOperator()
    converted = operator.executeNode(
        {"image": gray},
        {"sourceSpace": "GRAY", "targetSpace": "RGB"},
        {},
    )["outputs"]["image"]
    copied = operator.executeNode(
        {"image": gray},
        {"sourceSpace": "GRAY", "targetSpace": "GRAY"},
        {},
    )["outputs"]["image"]
    assert converted.shape == (1, 2, 3)
    assert copied is not gray
    np.testing.assert_array_equal(copied, gray)


def testColorConvertRejectsShapeTypeAndSpaceErrors() -> None:
    operator = ColorConvertOperator()
    cases = [
        operator.executeNode({}, {}, {}),
        operator.executeNode({"image": np.zeros((2, 2), dtype=np.float32)}, {}, {}),
        operator.executeNode(
            {"image": np.zeros((2, 2), dtype=np.uint8)},
            {"sourceSpace": "BGR"},
            {},
        ),
        operator.executeNode(
            {"image": np.zeros((2, 2, 3), dtype=np.uint8)},
            {"targetSpace": "XYZ"},
            {},
        ),
    ]
    assert [item["error"]["code"] for item in cases] == [
        "E_INPUT_MISSING",
        "E_INPUT_TYPE",
        "E_INPUT_SHAPE",
        "E_PARAM_INVALID",
    ]
