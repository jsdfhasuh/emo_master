from __future__ import annotations

import math
from dataclasses import dataclass
from time import perf_counter
from typing import Any, TypeGuard, cast

import cv2
import numpy as np

from emo_master.core.contracts.geometry2d import (
    BBox2D,
    CoordinateSpace2D,
    Geometry2D,
    PayloadValidationError,
    Point2D,
    Polygon2D,
    RotatedBox2D,
)
from emo_master.plugins.builtins._collection_ops import geometryPoints
from emo_master.plugins.builtins._image_frame import (
    frameForInput,
    frameMask,
    frameReference,
    transformedFrame,
)


@dataclass(frozen=True)
class OperatorMeta:
    operatorId: str
    displayName: str
    version: str
    inputPorts: dict[str, object]
    outputPorts: dict[str, object]
    paramSchema: dict[str, object]


_PARAM_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "roiType": {
            "type": "string",
            "enum": ["bbox", "rotatedBox", "polygon"],
            "default": "bbox",
        },
        "x": {"type": "number", "default": 0.0},
        "y": {"type": "number", "default": 0.0},
        "width": {"type": "number", "minimum": 0.0, "default": 1.0},
        "height": {"type": "number", "minimum": 0.0, "default": 1.0},
        "centerX": {"type": "number", "default": 0.0},
        "centerY": {"type": "number", "default": 0.0},
        "angleDegrees": {
            "type": "number",
            "minimum": -180.0,
            "exclusiveMaximum": 180.0,
            "default": 0.0,
        },
        "points": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "x": {"type": "number"},
                    "y": {"type": "number"},
                },
                "required": ["x", "y"],
            },
            "default": [],
        },
        "padValue": {
            "type": "integer",
            "minimum": 0,
            "maximum": 255,
            "default": 0,
        },
        "interpolation": {
            "type": "string",
            "enum": ["nearest", "linear", "cubic"],
            "default": "linear",
        },
    },
}


class RoiOperator:
    meta = OperatorMeta(
        operatorId="vision.preprocess.roi",
        displayName="ROI Extract",
        version="1.1.0",
        inputPorts={
            "image": {"type": "image", "required": True, "nullable": False},
            "frame": {
                "type": "bbox2d",
                "required": False,
                "nullable": False,
                "schemaVersion": "1.x",
            },
        },
        outputPorts={
            "croppedImage": {"type": "image", "required": True, "nullable": False},
            "croppedMask": {"type": "image", "required": True, "nullable": False},
            "croppedFrame": {
                "type": "bbox2d",
                "required": True,
                "nullable": False,
                "schemaVersion": "1.x",
            },
            "maskedImage": {"type": "image", "required": True, "nullable": False},
            "fullMask": {"type": "image", "required": True, "nullable": False},
            "maskedFrame": {
                "type": "bbox2d",
                "required": True,
                "nullable": False,
                "schemaVersion": "1.x",
            },
            "roi": {
                "type": "geometry2d",
                "required": True,
                "nullable": False,
                "schemaVersion": "1.x",
            },
        },
        paramSchema=_PARAM_SCHEMA,
    )

    def validateParams(self, params: dict[str, object]) -> dict[str, str] | None:
        roiType = params.get("roiType", "bbox")
        if roiType not in {"bbox", "rotatedBox", "polygon"}:
            return _paramError("roiType must be bbox, rotatedBox, or polygon")
        interpolation = params.get("interpolation", "linear")
        if interpolation not in {"nearest", "linear", "cubic"}:
            return _paramError("interpolation must be nearest, linear, or cubic")
        padValue = params.get("padValue", 0)
        if not _isInt(padValue) or not 0 <= padValue <= 255:
            return _paramError("padValue must be an integer in [0, 255]")
        if roiType == "bbox":
            return _validateBoxParams(params, rotated=False)
        if roiType == "rotatedBox":
            error = _validateBoxParams(params, rotated=True)
            if error is not None:
                return error
            angle = params.get("angleDegrees", 0.0)
            if not _isFiniteNumber(angle) or not -180.0 <= float(angle) < 180.0:
                return _paramError("angleDegrees must be in [-180, 180)")
            return None
        try:
            _polygonCoordinates(params.get("points", []))
        except ValueError as err:
            return _paramError(str(err))
        return None

    def executeNode(
        self,
        inputs: dict[str, object],
        params: dict[str, object],
        runtimeContext: dict[str, object],
    ) -> dict[str, Any]:
        startedAt = perf_counter()
        imageError = _validateImage(inputs)
        if imageError is not None:
            return imageError
        paramError = self.validateParams(params)
        if paramError is not None:
            return {"status": "error", "error": paramError}
        image = cast(np.ndarray[Any, Any], inputs["image"])
        height, width = image.shape[:2]
        try:
            inputFrame = frameForInput(inputs, width, height)
            geometry = _geometryFromParams(params, inputFrame.coordinateSpace)
            geometryMask = _geometryMask(geometry, width, height)
            fullMask = cv2.bitwise_and(
                geometryMask,
                frameMask(inputFrame, width, height),
            )
            if not np.any(fullMask):
                return _error(
                    "E_INPUT_SHAPE", "ROI does not intersect valid frame content"
                )
            outputWidth, outputHeight, outputToInput = _cropTransform(geometry)
            if outputWidth <= 0 or outputHeight <= 0:
                return _error("E_INPUT_SHAPE", "ROI must have a positive output size")
            interpolation = _interpolation(cast(str, params.get("interpolation", "linear")))
            padValue = cast(int, params.get("padValue", 0))
            borderValue: int | tuple[int, int, int] = (
                padValue if image.ndim == 2 else (padValue, padValue, padValue)
            )
            croppedImage = cv2.warpAffine(
                image,
                outputToInput,
                (outputWidth, outputHeight),
                flags=interpolation | cv2.WARP_INVERSE_MAP,
                borderMode=cv2.BORDER_CONSTANT,
                borderValue=borderValue,  # type: ignore[arg-type]
            )
            croppedMask = cv2.warpAffine(  # type: ignore[call-overload]
                fullMask,
                outputToInput,
                (outputWidth, outputHeight),
                flags=cv2.INTER_NEAREST | cv2.WARP_INVERSE_MAP,
                borderMode=cv2.BORDER_CONSTANT,
                borderValue=0,
            )
            croppedMask = np.where(croppedMask != 0, 255, 0).astype(np.uint8)
            _applyMaskInPlace(croppedImage, croppedMask, padValue)
            maskedImage = image.copy()
            _applyMaskInPlace(maskedImage, fullMask, padValue)
            contentRect = _maskContentRect(croppedMask)
            croppedFrame = transformedFrame(
                inputFrame,
                outputWidth,
                outputHeight,
                _affineTuple(outputToInput),
                contentRect,
                reference=frameReference(runtimeContext, self.meta.operatorId),
            )
        except PayloadValidationError as err:
            return _error("E_INPUT_TYPE", f"invalid frame or ROI payload: {err}")
        except ValueError as err:
            return _error("E_INPUT_SHAPE", f"invalid frame or ROI shape: {err}")
        except (cv2.error, OverflowError) as err:
            return _error("E_EXEC_FAILED", f"ROI extraction failed: {err}")

        return {
            "status": "ok",
            "outputs": {
                "croppedImage": croppedImage,
                "croppedMask": croppedMask,
                "croppedFrame": croppedFrame.toPayload(),
                "maskedImage": maskedImage,
                "fullMask": fullMask,
                "maskedFrame": inputFrame.toPayload(),
                "roi": geometry.toPayload(),
            },
            "metrics": {
                "latencyMs": round((perf_counter() - startedAt) * 1000.0, 3),
                "roiType": cast(str, params.get("roiType", "bbox")),
                "outputWidth": outputWidth,
                "outputHeight": outputHeight,
                "selectedPixels": int(np.count_nonzero(fullMask)),
            },
            "diagnostics": {"text": "ROI extraction completed"},
        }


def _geometryFromParams(
    params: dict[str, object], coordinateSpace: CoordinateSpace2D
) -> Geometry2D:
    roiType = cast(str, params.get("roiType", "bbox"))
    if roiType == "bbox":
        return BBox2D(
            float(cast(int | float, params.get("x", 0.0))),
            float(cast(int | float, params.get("y", 0.0))),
            float(cast(int | float, params.get("width", 1.0))),
            float(cast(int | float, params.get("height", 1.0))),
            coordinateSpace,
        )
    if roiType == "rotatedBox":
        return RotatedBox2D(
            float(cast(int | float, params.get("centerX", 0.0))),
            float(cast(int | float, params.get("centerY", 0.0))),
            float(cast(int | float, params.get("width", 1.0))),
            float(cast(int | float, params.get("height", 1.0))),
            float(cast(int | float, params.get("angleDegrees", 0.0))),
            coordinateSpace,
        )
    return Polygon2D(
        tuple(Point2D(x, y, coordinateSpace) for x, y in _polygonCoordinates(params["points"]))
    )


def _geometryMask(geometry: Geometry2D, width: int, height: int) -> np.ndarray[Any, Any]:
    mask = np.zeros((height, width), dtype=np.uint8)
    if isinstance(geometry, BBox2D):
        left = max(0, min(width, math.floor(geometry.x)))
        top = max(0, min(height, math.floor(geometry.y)))
        right = max(0, min(width, math.ceil(geometry.x + geometry.width)))
        bottom = max(0, min(height, math.ceil(geometry.y + geometry.height)))
        mask[top:bottom, left:right] = 255
        return mask
    points = np.rint(np.asarray(geometryPoints(geometry))).astype(np.int32)
    cv2.fillPoly(mask, [points], 255)  # type: ignore[call-overload]
    return mask


def _cropTransform(geometry: Geometry2D) -> tuple[int, int, np.ndarray[Any, Any]]:
    if isinstance(geometry, RotatedBox2D):
        outputWidth = max(1, math.ceil(geometry.width))
        outputHeight = max(1, math.ceil(geometry.height))
        radians = math.radians(geometry.angleDegrees)
        cosine = math.cos(radians)
        sine = math.sin(radians)
        centerX = (outputWidth - 1) / 2.0
        centerY = (outputHeight - 1) / 2.0
        matrix = np.asarray(
            [
                [
                    cosine,
                    -sine,
                    geometry.centerX - cosine * centerX + sine * centerY,
                ],
                [
                    sine,
                    cosine,
                    geometry.centerY - sine * centerX - cosine * centerY,
                ],
            ],
            dtype=np.float64,
        )
        return outputWidth, outputHeight, matrix
    points = geometryPoints(geometry)
    left = math.floor(min(point[0] for point in points))
    top = math.floor(min(point[1] for point in points))
    right = math.ceil(max(point[0] for point in points))
    bottom = math.ceil(max(point[1] for point in points))
    return (
        right - left,
        bottom - top,
        np.asarray([[1.0, 0.0, float(left)], [0.0, 1.0, float(top)]], dtype=np.float64),
    )


def _maskContentRect(mask: np.ndarray[Any, Any]) -> tuple[float, float, float, float]:
    ys, xs = np.nonzero(mask)
    if xs.size == 0:
        raise ValueError("cropped ROI has no valid pixels")
    left = int(xs.min())
    top = int(ys.min())
    right = int(xs.max()) + 1
    bottom = int(ys.max()) + 1
    return float(left), float(top), float(right - left), float(bottom - top)


def _affineTuple(
    matrix: np.ndarray[Any, Any],
) -> tuple[float, float, float, float, float, float]:
    return (
        float(matrix[0, 0]),
        float(matrix[1, 0]),
        float(matrix[0, 1]),
        float(matrix[1, 1]),
        float(matrix[0, 2]),
        float(matrix[1, 2]),
    )


def _applyMaskInPlace(image: np.ndarray[Any, Any], mask: np.ndarray[Any, Any], value: int) -> None:
    image[mask == 0] = value


def _polygonCoordinates(value: object) -> tuple[tuple[float, float], ...]:
    if not isinstance(value, list) or len(value) < 3:
        raise ValueError("points must contain at least three {x, y} entries")
    points: list[tuple[float, float]] = []
    for index, item in enumerate(value):
        if not isinstance(item, dict) or set(item) != {"x", "y"}:
            raise ValueError(f"points[{index}] must contain only finite x and y")
        x = item["x"]
        y = item["y"]
        if not _isFiniteNumber(x) or not _isFiniteNumber(y):
            raise ValueError(f"points[{index}] must contain only finite x and y")
        points.append((float(x), float(y)))
    if len(set(points)) != len(points):
        raise ValueError("polygon points must be unique")
    contour = np.asarray(points, dtype=np.float32)
    if abs(float(cv2.contourArea(contour))) <= 1e-9:
        raise ValueError("polygon must have a non-zero area")
    if _selfIntersects(points):
        raise ValueError("polygon must not self-intersect")
    return tuple(points)


def _selfIntersects(points: list[tuple[float, float]]) -> bool:
    count = len(points)
    for first in range(count):
        a1 = points[first]
        a2 = points[(first + 1) % count]
        for second in range(first + 1, count):
            if second in {first, (first + 1) % count}:
                continue
            if first == 0 and second == count - 1:
                continue
            b1 = points[second]
            b2 = points[(second + 1) % count]
            if _segmentsIntersect(a1, a2, b1, b2):
                return True
    return False


def _segmentsIntersect(
    a1: tuple[float, float],
    a2: tuple[float, float],
    b1: tuple[float, float],
    b2: tuple[float, float],
) -> bool:
    def orientation(
        first: tuple[float, float],
        second: tuple[float, float],
        third: tuple[float, float],
    ) -> float:
        return (second[0] - first[0]) * (third[1] - first[1]) - (
            second[1] - first[1]
        ) * (third[0] - first[0])

    values = (
        orientation(a1, a2, b1),
        orientation(a1, a2, b2),
        orientation(b1, b2, a1),
        orientation(b1, b2, a2),
    )
    epsilon = 1e-9
    if all(abs(value) > epsilon for value in values):
        return (values[0] > 0) != (values[1] > 0) and (values[2] > 0) != (
            values[3] > 0
        )
    return any(
        abs(value) <= epsilon and _pointOnSegment(point, start, end)
        for value, point, start, end in (
            (values[0], b1, a1, a2),
            (values[1], b2, a1, a2),
            (values[2], a1, b1, b2),
            (values[3], a2, b1, b2),
        )
    )


def _pointOnSegment(
    point: tuple[float, float],
    start: tuple[float, float],
    end: tuple[float, float],
) -> bool:
    epsilon = 1e-9
    return (
        min(start[0], end[0]) - epsilon
        <= point[0]
        <= max(start[0], end[0]) + epsilon
        and min(start[1], end[1]) - epsilon
        <= point[1]
        <= max(start[1], end[1]) + epsilon
    )


def _validateBoxParams(
    params: dict[str, object], *, rotated: bool
) -> dict[str, str] | None:
    positionNames = ("centerX", "centerY") if rotated else ("x", "y")
    for name in (*positionNames, "width", "height"):
        value = params.get(name, 0.0 if name in positionNames else 1.0)
        if not _isFiniteNumber(value):
            return _paramError(f"{name} must be a finite number")
        if name in {"width", "height"} and float(value) <= 0.0:
            return _paramError(f"{name} must be > 0")
    return None


def _validateImage(inputs: dict[str, object]) -> dict[str, Any] | None:
    if "image" not in inputs:
        return _error("E_INPUT_MISSING", "input 'image' is required")
    image = inputs["image"]
    if not isinstance(image, np.ndarray) or image.dtype != np.uint8:
        return _error("E_INPUT_TYPE", "image must be a uint8 numpy.ndarray")
    if image.size == 0 or image.ndim not in (2, 3):
        return _error("E_INPUT_SHAPE", "image must be non-empty grayscale or BGR")
    if image.ndim == 3 and image.shape[2] != 3:
        return _error("E_INPUT_SHAPE", "BGR image must have exactly 3 channels")
    return None


def _interpolation(value: str) -> int:
    return {"nearest": cv2.INTER_NEAREST, "linear": cv2.INTER_LINEAR, "cubic": cv2.INTER_CUBIC}[value]


def _isInt(value: object) -> TypeGuard[int]:
    return isinstance(value, int) and not isinstance(value, bool)


def _isFiniteNumber(value: object) -> TypeGuard[int | float]:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def _paramError(message: str) -> dict[str, str]:
    return {"code": "E_PARAM_INVALID", "message": message}


def _error(code: str, message: str) -> dict[str, Any]:
    return {"status": "error", "error": {"code": code, "message": message}}
