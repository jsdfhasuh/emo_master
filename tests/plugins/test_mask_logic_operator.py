from __future__ import annotations

import numpy as np
import pytest

from emo_master.core.contracts.geometry2d import BBox2D, CoordinateSpace2D
from emo_master.plugins.builtins.mask_logic.operator import MaskLogicOperator


@pytest.mark.parametrize(
    ("operation", "expected"),
    [
        ("and", [[0, 0, 255, 0]]),
        ("or", [[255, 255, 255, 0]]),
        ("xor", [[255, 255, 0, 0]]),
        ("subtract", [[255, 0, 0, 0]]),
    ],
)
def testMaskLogicBinaryOperations(operation: str, expected: list[list[int]]) -> None:
    first = np.asarray([[1, 0, 7, 0]], dtype=np.uint8)
    second = np.asarray([[0, 3, 9, 0]], dtype=np.uint8)
    result = MaskLogicOperator().executeNode(
        {"maskA": first, "maskB": second},
        {"operation": operation},
        {},
    )
    np.testing.assert_array_equal(
        result["outputs"]["mask"],
        np.asarray(expected, dtype=np.uint8),
    )


def testMaskLogicNotNeedsOnlyFirstInput() -> None:
    first = np.asarray([[0, 1]], dtype=np.uint8)
    result = MaskLogicOperator().executeNode(
        {"maskA": first},
        {"operation": "not"},
        {},
    )
    np.testing.assert_array_equal(
        result["outputs"]["mask"],
        np.asarray([[255, 0]], dtype=np.uint8),
    )


def testMaskLogicRejectsMissingSecondAndMismatchedShapes() -> None:
    operator = MaskLogicOperator()
    first = np.zeros((2, 2), dtype=np.uint8)
    missing = operator.executeNode({"maskA": first}, {"operation": "and"}, {})
    mismatch = operator.executeNode(
        {"maskA": first, "maskB": np.zeros((3, 2), dtype=np.uint8)},
        {"operation": "or"},
        {},
    )
    invalid = operator.executeNode(
        {"maskA": first},
        {"operation": "nand"},
        {},
    )
    assert missing["error"]["code"] == "E_INPUT_MISSING"
    assert mismatch["error"]["code"] == "E_INPUT_SHAPE"
    assert invalid["error"]["code"] == "E_PARAM_INVALID"


def testMaskLogicIntersectsAlignedFramesAndRejectsDifferentTransforms() -> None:
    first = np.ones((2, 3), dtype=np.uint8)
    second = np.ones((2, 3), dtype=np.uint8)
    space = CoordinateSpace2D(imageWidth=3, imageHeight=2, sourceId="camera")
    frameA = BBox2D(0, 0, 3, 2, space)
    frameB = BBox2D(1, 0, 2, 2, space)
    operator = MaskLogicOperator()

    aligned = operator.executeNode(
        {
            "maskA": first,
            "maskB": second,
            "frameA": frameA.toPayload(),
            "frameB": frameB.toPayload(),
        },
        {"operation": "and"},
        {},
    )
    outputFrame = BBox2D.fromPayload(aligned["outputs"]["frame"])
    assert (outputFrame.x, outputFrame.width) == (1.0, 2.0)

    shiftedSpace = CoordinateSpace2D(
        imageWidth=3,
        imageHeight=2,
        sourceId="camera",
        transformToSource=(1, 0, 0, 1, 1, 0),
    )
    rejected = operator.executeNode(
        {
            "maskA": first,
            "maskB": second,
            "frameA": frameA.toPayload(),
            "frameB": BBox2D(0, 0, 3, 2, shiftedSpace).toPayload(),
        },
        {"operation": "and"},
        {},
    )
    assert rejected["error"]["code"] == "E_INPUT_SHAPE"
