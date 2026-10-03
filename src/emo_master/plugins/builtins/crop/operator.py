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
    PayloadValidationError,
)
from emo_master.plugins.builtins._image_frame import (
    coordinateSpacesEquivalent,
    frameForInput,
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
        "x": {"type": "number", "default": 0.0},
        "y": {"type": "number", "default": 0.0},
        "width": {"type": "number", "minimum": 0.0, "default": 0.0},
        "height": {"type": "number", "minimum": 0.0, "default": 0.0},
        "clip": {"type": "boolean", "default": True},
        "paddingLeft": {"type": "integer", "minimum": 0, "default": 0},
        "paddingTop": {"type": "integer", "minimum": 0, "default": 0},
        "paddingRight": {"type": "integer", "minimum": 0, "default": 0},
        "paddingBottom": {"type": "integer", "minimum": 0, "default": 0},
        "padValue": {
            "type": "integer",
            "minimum": 0,
            "maximum": 255,
            "default": 0,
        },
    },
}


class CropOperator:
    meta = OperatorMeta(
        operatorId="vision.preprocess.crop",
        displayName="Crop / Pad",
        version="1.0.0",
        inputPorts={
            "image": {"type": "image", "required": True, "nullable": False},
            "roi": {
                "type": "bbox2d",
                "required": False,
                "nullable": False,
                "schemaVersion": "1.x",
            },
            "frame": {
                "type": "bbox2d",
                "required": False,
                "nullable": False,
                "schemaVersion": "1.x",
            },
        },
        outputPorts={
            "image": {"type": "image", "required": True, "nullable": False},
            "frame": {
                "type": "bbox2d",
                "required": True,
                "nullable": False,
                "schemaVersion": "1.x",
            },
        },
        paramSchema=_PARAM_SCHEMA,
    )

    def validateParams(self, params: dict[str, object]) -> dict[str, str] | None:
        for name, default in (
            ("x", 0.0),
            ("y", 0.0),
            ("width", 0.0),
            ("height", 0.0),
        ):
            value = params.get(name, default)
            if not _isFiniteNumber(value):
                return _paramError(f"{name} must be a finite number")
            if name in {"width", "height"} and float(value) < 0.0:
                return _paramError(f"{name} must be >= 0")
        if not isinstance(params.get("clip", True), bool):
            return _paramError("clip must be boolean")
        for name in (
            "paddingLeft",
            "paddingTop",
            "paddingRight",
            "paddingBottom",
        ):
            value = params.get(name, 0)
            if not _isInt(value) or value < 0:
                return _paramError(f"{name} must be an integer >= 0")
        padValue = params.get("padValue", 0)
        if not _isInt(padValue) or not 0 <= padValue <= 255:
            return _paramError("padValue must be an integer in [0, 255]")
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
        image = cast(np.ndarray[Any, Any], inputs["image"])
        paramError = self.validateParams(params)
        if paramError is not None:
            return {"status": "error", "error": paramError}

        imageHeight, imageWidth = image.shape[:2]
        try:
            roi, inputFrame = _roiAndFrame(
                inputs,
                params,
                imageWidth,
                imageHeight,
            )
        except PayloadValidationError as err:
            return _error("E_INPUT_TYPE", f"invalid geometry payload: {err}")
        except ValueError as err:
            return _error("E_INPUT_SHAPE", str(err))

        left = math.floor(roi.x)
        top = math.floor(roi.y)
        right = math.ceil(roi.x + roi.width)
        bottom = math.ceil(roi.y + roi.height)
        if right <= left or bottom <= top:
            return _error("E_INPUT_SHAPE", "crop ROI must have a positive area")
        clip = cast(bool, params.get("clip", True))
        if not clip and (
            left < 0 or top < 0 or right > imageWidth or bottom > imageHeight
        ):
            return _error("E_INPUT_SHAPE", "crop ROI extends outside the image")
        left = max(0, min(imageWidth, left))
        top = max(0, min(imageHeight, top))
        right = max(0, min(imageWidth, right))
        bottom = max(0, min(imageHeight, bottom))
        if right <= left or bottom <= top:
            return _error("E_INPUT_SHAPE", "crop ROI does not intersect the image")

        paddingLeft = cast(int, params.get("paddingLeft", 0))
        paddingTop = cast(int, params.get("paddingTop", 0))
        paddingRight = cast(int, params.get("paddingRight", 0))
        paddingBottom = cast(int, params.get("paddingBottom", 0))
        padValue = cast(int, params.get("padValue", 0))
        cropped = image[top:bottom, left:right].copy()
        borderValue: int | tuple[int, int, int] = (
            padValue if image.ndim == 2 else (padValue, padValue, padValue)
        )
        try:
            output = cv2.copyMakeBorder(
                cropped,
                paddingTop,
                paddingBottom,
                paddingLeft,
                paddingRight,
                cv2.BORDER_CONSTANT,
                value=borderValue,  # type: ignore[arg-type]
            )
        except (cv2.error, OverflowError, ValueError) as err:
            return _error("E_EXEC_FAILED", f"crop failed: {err}")

        validLeft = max(float(left), inputFrame.x)
        validTop = max(float(top), inputFrame.y)
        validRight = min(float(right), inputFrame.x + inputFrame.width)
        validBottom = min(float(bottom), inputFrame.y + inputFrame.height)
        if validRight <= validLeft or validBottom <= validTop:
            return _error(
                "E_INPUT_SHAPE", "crop does not intersect valid frame content"
            )
        contentRect = (
            paddingLeft + validLeft - left,
            paddingTop + validTop - top,
            validRight - validLeft,
            validBottom - validTop,
        )
        outputHeight, outputWidth = output.shape[:2]
        frame = transformedFrame(
            inputFrame,
            outputWidth,
            outputHeight,
            (
                1.0,
                0.0,
                0.0,
                1.0,
                float(left - paddingLeft),
                float(top - paddingTop),
            ),
            contentRect,
            reference=frameReference(runtimeContext, self.meta.operatorId),
        )
        elapsedMs = (perf_counter() - startedAt) * 1000.0
        return {
            "status": "ok",
            "outputs": {"image": output, "frame": frame.toPayload()},
            "metrics": {
                "latencyMs": round(elapsedMs, 3),
                "crop": [left, top, right - left, bottom - top],
                "padding": [
                    paddingLeft,
                    paddingTop,
                    paddingRight,
                    paddingBottom,
                ],
                "outputWidth": outputWidth,
                "outputHeight": outputHeight,
            },
            "diagnostics": {"text": "Crop and padding completed"},
        }


def _roiAndFrame(
    inputs: dict[str, object],
    params: dict[str, object],
    width: int,
    height: int,
) -> tuple[BBox2D, BBox2D]:
    rawRoi = inputs.get("roi") if "roi" in inputs else None
    parsedRoi = None if rawRoi is None else BBox2D.fromPayload(rawRoi)
    fallback: BBox2D | None = None
    if parsedRoi is not None:
        roiSpace = _spaceForImage(parsedRoi.coordinateSpace, width, height)
        parsedRoi = BBox2D(
            parsedRoi.x,
            parsedRoi.y,
            parsedRoi.width,
            parsedRoi.height,
            roiSpace,
        )
        fallback = BBox2D(0.0, 0.0, float(width), float(height), roiSpace)
    frame = frameForInput(inputs, width, height, fallback=fallback)
    if parsedRoi is not None:
        if not coordinateSpacesEquivalent(
            parsedRoi.coordinateSpace,
            frame.coordinateSpace,
        ):
            raise ValueError("ROI and frame coordinate spaces do not match")
        return parsedRoi, frame

    x = float(cast(int | float, params.get("x", 0.0)))
    y = float(cast(int | float, params.get("y", 0.0)))
    roiWidth = float(cast(int | float, params.get("width", 0.0)))
    roiHeight = float(cast(int | float, params.get("height", 0.0)))
    if roiWidth == 0.0:
        roiWidth = width - x
    if roiHeight == 0.0:
        roiHeight = height - y
    return BBox2D(x, y, roiWidth, roiHeight, frame.coordinateSpace), frame


def _spaceForImage(
    space: CoordinateSpace2D,
    width: int,
    height: int,
) -> CoordinateSpace2D:
    if space.imageWidth is not None and (
        space.imageWidth != width or space.imageHeight != height
    ):
        raise ValueError("ROI coordinateSpace dimensions do not match the image")
    return CoordinateSpace2D(
        origin=space.origin,
        xAxis=space.xAxis,
        yAxis=space.yAxis,
        unit=space.unit,
        reference=space.reference,
        sourceId=space.sourceId,
        imageWidth=width,
        imageHeight=height,
        transformToSource=space.transformToSource,
        homographyToSource=space.homographyToSource,
    )


def _validateImage(inputs: dict[str, object]) -> dict[str, Any] | None:
    if "image" not in inputs:
        return _error("E_INPUT_MISSING", "input 'image' is required")
    image = inputs["image"]
    if not isinstance(image, np.ndarray):
        return _error("E_INPUT_TYPE", "input 'image' must be numpy.ndarray")
    if image.dtype != np.uint8:
        return _error("E_INPUT_TYPE", "input 'image' must use uint8 pixels")
    if image.size == 0 or image.ndim not in (2, 3):
        return _error("E_INPUT_SHAPE", "image must be non-empty grayscale or BGR")
    if image.ndim == 3 and image.shape[2] != 3:
        return _error("E_INPUT_SHAPE", "BGR image must have exactly 3 channels")
    return None


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
