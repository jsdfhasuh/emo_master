from __future__ import annotations

import cv2
import numpy as np
import pytest

from emo_master.plugins.builtins.threshold.operator import ThresholdOperator


def testThresholdFixedReturnsBinaryMaskAndActualThreshold() -> None:
    image = np.asarray(
        [
            [[0, 0, 0], [100, 100, 100], [101, 101, 101]],
            [[255, 255, 255], [25, 25, 25], [200, 200, 200]],
        ],
        dtype=np.uint8,
    )

    result = ThresholdOperator().executeNode(
        {"image": image},
        {"mode": "fixed", "threshold": 100},
        {},
    )

    assert result["status"] == "ok"
    outputs = result["outputs"]
    assert isinstance(outputs, dict)
    mask = outputs["mask"]
    assert isinstance(mask, np.ndarray)
    assert mask.dtype == np.uint8
    np.testing.assert_array_equal(
        mask,
        np.asarray([[0, 0, 255], [255, 0, 255]], dtype=np.uint8),
    )
    assert outputs["threshold"] == 100.0


def testThresholdInvertReversesTheMask() -> None:
    image = np.asarray([[0, 128, 255]], dtype=np.uint8)
    result = ThresholdOperator().executeNode(
        {"image": image},
        {"mode": "fixed", "threshold": 127, "invert": True},
        {},
    )

    np.testing.assert_array_equal(
        result["outputs"]["mask"],
        np.asarray([[255, 0, 0]], dtype=np.uint8),
    )


@pytest.mark.parametrize("mode", ["otsu", "triangle"])
def testThresholdAutomaticModesReturnMeasuredThreshold(mode: str) -> None:
    image = np.tile(np.arange(256, dtype=np.uint8), (8, 1))

    result = ThresholdOperator().executeNode({"image": image}, {"mode": mode}, {})

    assert result["status"] == "ok"
    outputs = result["outputs"]
    assert isinstance(outputs["threshold"], float)
    mask = outputs["mask"]
    assert isinstance(mask, np.ndarray)
    assert set(np.unique(mask)).issubset({0, 255})


@pytest.mark.parametrize(
    ("mode", "method"),
    [
        ("adaptiveMean", cv2.ADAPTIVE_THRESH_MEAN_C),
        ("adaptiveGaussian", cv2.ADAPTIVE_THRESH_GAUSSIAN_C),
    ],
)
def testThresholdAdaptiveModesReturnNullThreshold(
    mode: str,
    method: int,
) -> None:
    image = np.arange(81, dtype=np.uint8).reshape(9, 9)

    result = ThresholdOperator().executeNode(
        {"image": image},
        {"mode": mode, "blockSize": 3, "constant": 1.5},
        {},
    )

    assert result["status"] == "ok"
    assert result["outputs"]["threshold"] is None
    expected = cv2.adaptiveThreshold(
        image,
        255,
        method,
        cv2.THRESH_BINARY,
        3,
        1.5,
    )
    np.testing.assert_array_equal(result["outputs"]["mask"], expected)


def testThresholdRejectsInvalidInputsAndParameters() -> None:
    operator = ThresholdOperator()
    validImage = np.zeros((4, 4), dtype=np.uint8)

    cases = [
        operator.executeNode({}, {}, {}),
        operator.executeNode({"image": [[0]]}, {}, {}),
        operator.executeNode({"image": np.zeros((2, 2), dtype=np.float32)}, {}, {}),
        operator.executeNode({"image": np.zeros((2, 2, 4), dtype=np.uint8)}, {}, {}),
        operator.executeNode({"image": validImage}, {"mode": "unknown"}, {}),
        operator.executeNode({"image": validImage}, {"threshold": True}, {}),
        operator.executeNode({"image": validImage}, {"blockSize": 4}, {}),
        operator.executeNode({"image": validImage}, {"constant": float("nan")}, {}),
    ]

    assert [item["error"]["code"] for item in cases] == [
        "E_INPUT_MISSING",
        "E_INPUT_TYPE",
        "E_INPUT_TYPE",
        "E_INPUT_SHAPE",
        "E_PARAM_INVALID",
        "E_PARAM_INVALID",
        "E_PARAM_INVALID",
        "E_PARAM_INVALID",
    ]
