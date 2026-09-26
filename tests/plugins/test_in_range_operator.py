from __future__ import annotations

import numpy as np

from emo_master.plugins.builtins.in_range.operator import InRangeOperator


def testInRangeProducesBinaryMaskForHsvImage() -> None:
    image = np.asarray(
        [[[0, 255, 255], [60, 255, 255], [179, 255, 255]]], dtype=np.uint8
    )
    result = InRangeOperator().executeNode(
        {"image": image},
        {"colorSpace": "HSV", "lower": [0, 200, 200], "upper": [10, 255, 255]},
        {},
    )
    assert result["status"] == "ok"
    np.testing.assert_array_equal(
        result["outputs"]["mask"],
        np.asarray([[255, 0, 0]], dtype=np.uint8),
    )


def testInRangeSupportsGrayscaleBounds() -> None:
    image = np.asarray([[0, 100, 200]], dtype=np.uint8)
    result = InRangeOperator().executeNode(
        {"image": image},
        {"colorSpace": "GRAY", "lower": [50], "upper": [150]},
        {},
    )
    np.testing.assert_array_equal(
        result["outputs"]["mask"],
        np.asarray([[0, 255, 0]], dtype=np.uint8),
    )


def testInRangeRejectsInvalidBoundsAndShape() -> None:
    operator = InRangeOperator()
    image = np.zeros((2, 2, 3), dtype=np.uint8)
    cases = [
        operator.executeNode({"image": image}, {"lower": [0, 0, 0]}, {}),
        operator.executeNode(
            {"image": image},
            {"colorSpace": "HSV", "lower": [0, 0, 0], "upper": [180, 255, 255]},
            {},
        ),
        operator.executeNode(
            {"image": image},
            {"lower": [10, 0, 0], "upper": [5, 255, 255]},
            {},
        ),
        operator.executeNode(
            {"image": image},
            {"colorSpace": "GRAY", "lower": [0], "upper": [255]},
            {},
        ),
    ]
    assert [item["error"]["code"] for item in cases] == [
        "E_PARAM_INVALID",
        "E_PARAM_INVALID",
        "E_PARAM_INVALID",
        "E_INPUT_SHAPE",
    ]
