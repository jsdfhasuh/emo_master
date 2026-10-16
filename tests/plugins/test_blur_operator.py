from __future__ import annotations

import cv2
import numpy as np
import pytest

from emo_master.plugins.builtins.blur.operator import BlurOperator


@pytest.mark.parametrize("mode", ["gaussian", "median", "bilateral"])
def testBlurModesMatchOpenCv(mode: str) -> None:
    image = np.arange(75, dtype=np.uint8).reshape(5, 5, 3)
    params: dict[str, object] = {
        "mode": mode,
        "kernelSize": 3,
        "sigmaX": 1.2,
        "sigmaY": 0.5,
        "diameter": 3,
        "sigmaColor": 20.0,
        "sigmaSpace": 30.0,
    }
    result = BlurOperator().executeNode({"image": image}, params, {})
    if mode == "gaussian":
        expected = cv2.GaussianBlur(image, (3, 3), 1.2, sigmaY=0.5)
    elif mode == "median":
        expected = cv2.medianBlur(image, 3)
    else:
        expected = cv2.bilateralFilter(image, 3, 20.0, 30.0)
    np.testing.assert_array_equal(result["outputs"]["image"], expected)


def testBlurPreservesGrayscaleShapeAndFrame() -> None:
    image = np.zeros((5, 7), dtype=np.uint8)
    result = BlurOperator().executeNode({"image": image}, {}, {})
    assert result["outputs"]["image"].shape == image.shape
    assert result["outputs"]["frame"]["width"] == 7.0


def testBlurRejectsInvalidInputsAndParameters() -> None:
    operator = BlurOperator()
    valid = np.zeros((5, 5), dtype=np.uint8)
    cases = [
        operator.executeNode({}, {}, {}),
        operator.executeNode({"image": valid.astype(np.float32)}, {}, {}),
        operator.executeNode({"image": valid}, {"kernelSize": 4}, {}),
        operator.executeNode({"image": valid}, {"diameter": 0}, {}),
        operator.executeNode({"image": valid}, {"sigmaColor": 0}, {}),
    ]
    assert [item["error"]["code"] for item in cases] == [
        "E_INPUT_MISSING",
        "E_INPUT_TYPE",
        "E_PARAM_INVALID",
        "E_PARAM_INVALID",
        "E_PARAM_INVALID",
    ]
