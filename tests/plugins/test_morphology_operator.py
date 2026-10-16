from __future__ import annotations

import cv2
import numpy as np
import pytest

from emo_master.plugins.builtins.morphology.operator import MorphologyOperator


@pytest.mark.parametrize(
    ("operation", "operationCode"),
    [
        ("open", cv2.MORPH_OPEN),
        ("close", cv2.MORPH_CLOSE),
        ("gradient", cv2.MORPH_GRADIENT),
        ("tophat", cv2.MORPH_TOPHAT),
        ("blackhat", cv2.MORPH_BLACKHAT),
    ],
)
def testMorphologyExtendedOperationsMatchOpenCv(
    operation: str,
    operationCode: int,
) -> None:
    mask = np.zeros((7, 7), dtype=np.uint8)
    mask[2:5, 2:5] = 1
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
    result = MorphologyOperator().executeNode(
        {"mask": mask},
        {"operation": operation, "kernelSize": 3},
        {},
    )
    expected = cv2.morphologyEx(
        np.where(mask != 0, 255, 0).astype(np.uint8),
        operationCode,
        kernel,
    )
    np.testing.assert_array_equal(result["outputs"]["mask"], expected)


def testMorphologyErodeAndDilateNormalizeMask() -> None:
    mask = np.zeros((5, 5), dtype=np.uint8)
    mask[1:4, 1:4] = 7
    operator = MorphologyOperator()
    eroded = operator.executeNode(
        {"mask": mask}, {"operation": "erode", "kernelSize": 3}, {}
    )
    dilated = operator.executeNode(
        {"mask": mask}, {"operation": "dilate", "kernelSize": 3}, {}
    )
    assert set(np.unique(eroded["outputs"]["mask"])).issubset({0, 255})
    assert int(np.count_nonzero(dilated["outputs"]["mask"])) > int(
        np.count_nonzero(eroded["outputs"]["mask"])
    )


def testMorphologyRejectsInvalidMaskAndParameters() -> None:
    operator = MorphologyOperator()
    valid = np.zeros((3, 3), dtype=np.uint8)
    cases = [
        operator.executeNode({}, {}, {}),
        operator.executeNode({"mask": valid.astype(np.float32)}, {}, {}),
        operator.executeNode({"mask": valid[:, :, None]}, {}, {}),
        operator.executeNode({"mask": valid}, {"kernelSize": 2}, {}),
        operator.executeNode({"mask": valid}, {"iterations": 0}, {}),
    ]
    assert [item["error"]["code"] for item in cases] == [
        "E_INPUT_MISSING",
        "E_INPUT_TYPE",
        "E_INPUT_SHAPE",
        "E_PARAM_INVALID",
        "E_PARAM_INVALID",
    ]
