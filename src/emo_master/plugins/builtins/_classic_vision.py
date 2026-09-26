from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

import cv2
import numpy as np

from emo_master.core.contracts.geometry2d import (
    BBox2D,
    Circle2D,
    CoordinateSpace2D,
    Geometry2D,
    Line2D,
    Point2D,
    Polygon2D,
    RotatedBox2D,
)


class BuiltinInputError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def requireImage(
    inputs: Mapping[str, object],
    portName: str = "image",
    *,
    grayscaleOnly: bool = False,
) -> np.ndarray[Any, Any]:
    if portName not in inputs:
        raise BuiltinInputError("E_INPUT_MISSING", f"input '{portName}' is required")
    value = inputs[portName]
    if not isinstance(value, np.ndarray):
        raise BuiltinInputError(
            "E_INPUT_TYPE", f"input '{portName}' must be numpy.ndarray"
        )
    if value.dtype != np.uint8:
        raise BuiltinInputError(
            "E_INPUT_TYPE", f"input '{portName}' must use uint8 pixels"
        )
    if value.size == 0:
        raise BuiltinInputError("E_INPUT_SHAPE", f"input '{portName}' must not be empty")
    if grayscaleOnly:
        if value.ndim != 2:
            raise BuiltinInputError(
                "E_INPUT_SHAPE", f"input '{portName}' must be single-channel"
            )
        return value
    if value.ndim not in (2, 3) or (value.ndim == 3 and value.shape[2] != 3):
        raise BuiltinInputError(
            "E_INPUT_SHAPE",
            f"input '{portName}' must be non-empty grayscale or BGR",
        )
    return value


def requireBinaryMask(
    value: object,
    width: int,
    height: int,
    portName: str,
) -> np.ndarray[Any, Any]:
    if not isinstance(value, np.ndarray):
        raise BuiltinInputError(
            "E_INPUT_TYPE", f"input '{portName}' must be numpy.ndarray"
        )
    if value.dtype != np.uint8:
        raise BuiltinInputError(
            "E_INPUT_TYPE", f"input '{portName}' must use uint8 pixels"
        )
    if value.ndim != 2 or value.shape != (height, width):
        raise BuiltinInputError(
            "E_INPUT_SHAPE",
            f"input '{portName}' must be a same-size single-channel mask",
        )
    return np.where(value != 0, 255, 0).astype(np.uint8)


def interpolationFlag(name: object) -> int:
    flags = {
        "nearest": cv2.INTER_NEAREST,
        "linear": cv2.INTER_LINEAR,
        "cubic": cv2.INTER_CUBIC,
    }
    if not isinstance(name, str) or name not in flags:
        raise BuiltinInputError(
            "E_PARAM_INVALID", "interpolation must be nearest, linear, or cubic"
        )
    return flags[name]


def geometryMask(
    geometry: Geometry2D,
    width: int,
    height: int,
) -> np.ndarray[Any, Any]:
    mask = np.zeros((height, width), dtype=np.uint8)
    if isinstance(geometry, Point2D):
        x = int(round(geometry.x))
        y = int(round(geometry.y))
        if 0 <= x < width and 0 <= y < height:
            mask[y, x] = 255
        return mask
    if isinstance(geometry, BBox2D):
        left = max(0, min(width, math.floor(geometry.x)))
        top = max(0, min(height, math.floor(geometry.y)))
        right = max(0, min(width, math.ceil(geometry.x + geometry.width)))
        bottom = max(0, min(height, math.ceil(geometry.y + geometry.height)))
        mask[top:bottom, left:right] = 255
        return mask
    if isinstance(geometry, Line2D):
        cv2.line(  # type: ignore[call-overload]
            mask,
            (int(round(geometry.start.x)), int(round(geometry.start.y))),
            (int(round(geometry.end.x)), int(round(geometry.end.y))),
            255,
            1,
            cv2.LINE_8,
        )
        return mask
    if isinstance(geometry, Circle2D):
        cv2.circle(  # type: ignore[call-overload]
            mask,
            (int(round(geometry.center.x)), int(round(geometry.center.y))),
            max(1, int(round(geometry.radius))),
            255,
            -1,
            cv2.LINE_8,
        )
        return mask
    if isinstance(geometry, RotatedBox2D):
        points = cv2.boxPoints(
            (
                (geometry.centerX, geometry.centerY),
                (geometry.width, geometry.height),
                geometry.angleDegrees,
            )
        )
    elif isinstance(geometry, Polygon2D):
        points = np.asarray(
            [(point.x, point.y) for point in geometry.points], dtype=np.float32
        )
    else:  # pragma: no cover - Geometry2D is exhaustive
        raise BuiltinInputError("E_INPUT_SHAPE", "unsupported ROI geometry")
    cv2.fillPoly(  # type: ignore[call-overload]
        mask, [np.rint(points).astype(np.int32)], 255
    )
    return mask


def frameFromMask(
    mask: np.ndarray[Any, Any], space: CoordinateSpace2D
) -> BBox2D:
    ys, xs = np.nonzero(mask)
    if xs.size == 0:
        raise BuiltinInputError("E_INPUT_SHAPE", "validMask contains no valid pixels")
    return BBox2D(
        float(xs.min()),
        float(ys.min()),
        float(xs.max() - xs.min() + 1),
        float(ys.max() - ys.min() + 1),
        space,
    )


def errorResult(code: str, message: str) -> dict[str, object]:
    return {"status": "error", "error": {"code": code, "message": message}}
