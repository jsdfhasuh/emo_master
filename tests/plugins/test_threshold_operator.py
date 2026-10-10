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
        operator.executeNode(
            {"image": validImage}, {"mode": "adaptiveMean", "blockSize": 4}, {}
        ),
        operator.executeNode(
            {"image": validImage},
            {"mode": "adaptiveGaussian", "constant": float("nan")},
            {},
        ),
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


@pytest.mark.parametrize(
    "params",
    [
        {"mode": "fixed", "blockSize": 2, "constant": "unused"},
        {"mode": "otsu", "threshold": 999, "blockSize": 2, "constant": "unused"},
        {"mode": "triangle", "threshold": -1, "blockSize": 2, "constant": "unused"},
        {"mode": "adaptiveMean", "threshold": 999, "blockSize": 3},
        {"mode": "adaptiveGaussian", "threshold": -1, "blockSize": 3},
    ],
)
def testThresholdOnlyUsesParametersForSelectedMode(params: dict[str, object]) -> None:
    image = np.arange(81, dtype=np.uint8).reshape(9, 9)
    operator = ThresholdOperator()

    assert operator.validateParams(params) is None
    result = operator.executeNode({"image": image}, params, {})

    assert result["status"] == "ok"
    assert set(np.unique(result["outputs"]["mask"])).issubset({0, 255})


@pytest.mark.parametrize("mode", ["fixed", "otsu", "triangle"])
def testThresholdDoesNotReadInactiveAdaptiveParameters(mode: str) -> None:
    class Params(dict):
        def get(self, key, default=None):
            if key in {"blockSize", "constant"}:
                raise AssertionError(f"inactive parameter was read: {key}")
            if key == "threshold" and mode != "fixed":
                raise AssertionError("automatic mode read the fixed threshold")
            return super().get(key, default)

    image = np.arange(81, dtype=np.uint8).reshape(9, 9)
    result = ThresholdOperator().executeNode({"image": image}, Params(mode=mode), {})
    assert result["status"] == "ok"


@pytest.mark.parametrize("threshold", [-1, 256, 999, True, 127.5, "127"])
def testFixedThresholdKeepsStrongRangeAndTypeValidation(threshold: object) -> None:
    error = ThresholdOperator().validateParams({"mode": "fixed", "threshold": threshold})
    assert error is not None and error["code"] == "E_PARAM_INVALID"
    assert "threshold" in error["message"]


@pytest.mark.parametrize("mode", ["adaptiveMean", "adaptiveGaussian"])
@pytest.mark.parametrize("blockSize", [0, 1, 2, 4, True, 3.5, "3"])
def testAdaptiveThresholdKeepsOddBlockValidation(mode: str, blockSize: object) -> None:
    error = ThresholdOperator().validateParams({"mode": mode, "blockSize": blockSize})
    assert error is not None and error["code"] == "E_PARAM_INVALID"
    assert "blockSize" in error["message"]


@pytest.mark.parametrize("mode", ["adaptiveMean", "adaptiveGaussian"])
@pytest.mark.parametrize("constant", [float("nan"), float("inf"), -float("inf"), True, "2", 10**400])
def testAdaptiveThresholdKeepsFiniteConstantValidation(mode: str, constant: object) -> None:
    error = ThresholdOperator().validateParams({"mode": mode, "constant": constant})
    assert error is not None and error["code"] == "E_PARAM_INVALID"
    assert "constant" in error["message"]


@pytest.mark.parametrize("params", [{"mode": []}, {"mode": "unknown"}, {"invert": 1}, {"invert": "false"}])
def testThresholdAlwaysProtectsModeAndInvert(params: dict[str, object]) -> None:
    error = ThresholdOperator().validateParams(params)
    assert error is not None and error["code"] == "E_PARAM_INVALID"
