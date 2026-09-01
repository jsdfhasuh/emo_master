from __future__ import annotations

from dataclasses import dataclass
from time import perf_counter
from typing import Any, TypeGuard, cast

import cv2
import numpy as np

from emo_master.core.contracts.geometry2d import PayloadValidationError
from emo_master.plugins.builtins._image_frame import (
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


_MODES = ("stretch", "fit", "letterbox")
_INTERPOLATIONS = {
    "nearest": cv2.INTER_NEAREST,
    "linear": cv2.INTER_LINEAR,
    "area": cv2.INTER_AREA,
    "cubic": cv2.INTER_CUBIC,
}
_PARAM_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "width": {"type": "integer", "minimum": 1},
        "height": {"type": "integer", "minimum": 1},
        "mode": {"type": "string", "enum": list(_MODES), "default": "stretch"},
        "interpolation": {
            "type": "string",
            "enum": list(_INTERPOLATIONS),
            "default": "linear",
        },
        "padValue": {
            "type": "integer",
            "minimum": 0,
            "maximum": 255,
            "default": 0,
        },
    },
    "required": ["width", "height"],
}


class ResizeOperator:
    meta = OperatorMeta(
        operatorId="vision.preprocess.resize",
        displayName="Resize / Letterbox",
        version="1.0.0",
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
        width = params.get("width")
        height = params.get("height")
        mode = params.get("mode", "stretch")
        interpolation = params.get("interpolation", "linear")
        padValue = params.get("padValue", 0)
        if not _isInt(width) or width < 1:
            return _paramError("width must be an integer >= 1")
        if not _isInt(height) or height < 1:
            return _paramError("height must be an integer >= 1")
        if not isinstance(mode, str) or mode not in _MODES:
            return _paramError(f"mode must be one of: {', '.join(_MODES)}")
        if not isinstance(interpolation, str) or interpolation not in _INTERPOLATIONS:
            return _paramError(
                f"interpolation must be one of: {', '.join(_INTERPOLATIONS)}"
            )
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

        sourceHeight, sourceWidth = image.shape[:2]
        try:
            inputFrame = frameForInput(inputs, sourceWidth, sourceHeight)
        except PayloadValidationError as err:
            return _error("E_INPUT_TYPE", f"invalid frame payload: {err}")
        except ValueError as err:
            return _error("E_INPUT_SHAPE", str(err))

        width = cast(int, params["width"])
        height = cast(int, params["height"])
        mode = cast(str, params.get("mode", "stretch"))
        interpolationName = cast(str, params.get("interpolation", "linear"))
        padValue = cast(int, params.get("padValue", 0))
        interpolation = _INTERPOLATIONS[interpolationName]

        try:
            if mode == "stretch":
                output = cv2.resize(image, (width, height), interpolation=interpolation)
                resizedWidth, resizedHeight = width, height
                left = top = right = bottom = 0
            else:
                scale = min(width / sourceWidth, height / sourceHeight)
                resizedWidth = min(width, max(1, int(round(sourceWidth * scale))))
                resizedHeight = min(height, max(1, int(round(sourceHeight * scale))))
                resized = cv2.resize(
                    image,
                    (resizedWidth, resizedHeight),
                    interpolation=interpolation,
                )
                left = top = right = bottom = 0
                if mode == "fit":
                    output = resized
                else:
                    horizontal = width - resizedWidth
                    vertical = height - resizedHeight
                    left = horizontal // 2
                    right = horizontal - left
                    top = vertical // 2
                    bottom = vertical - top
                    borderValue: int | tuple[int, int, int] = (
                        padValue if image.ndim == 2 else (padValue, padValue, padValue)
                    )
                    output = cv2.copyMakeBorder(
                        resized,
                        top,
                        bottom,
                        left,
                        right,
                        cv2.BORDER_CONSTANT,
                        value=borderValue,  # type: ignore[arg-type]
                    )
        except (cv2.error, OverflowError, ValueError) as err:
            return _error("E_EXEC_FAILED", f"resize failed: {err}")

        outputHeight, outputWidth = output.shape[:2]
        scaleX = resizedWidth / sourceWidth
        scaleY = resizedHeight / sourceHeight
        contentRect = (
            left + inputFrame.x * scaleX,
            top + inputFrame.y * scaleY,
            inputFrame.width * scaleX,
            inputFrame.height * scaleY,
        )
        outputToInput = (
            1.0 / scaleX,
            0.0,
            0.0,
            1.0 / scaleY,
            -left / scaleX,
            -top / scaleY,
        )
        frame = transformedFrame(
            inputFrame,
            outputWidth,
            outputHeight,
            outputToInput,
            contentRect,
            reference=frameReference(runtimeContext, self.meta.operatorId),
        )
        elapsedMs = (perf_counter() - startedAt) * 1000.0
        return {
            "status": "ok",
            "outputs": {"image": output, "frame": frame.toPayload()},
            "metrics": {
                "latencyMs": round(elapsedMs, 3),
                "mode": mode,
                "sourceWidth": sourceWidth,
                "sourceHeight": sourceHeight,
                "outputWidth": outputWidth,
                "outputHeight": outputHeight,
                "scaleX": scaleX,
                "scaleY": scaleY,
                "padding": [left, top, right, bottom],
            },
            "diagnostics": {"text": f"Resize completed in {mode} mode"},
        }


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


def _paramError(message: str) -> dict[str, str]:
    return {"code": "E_PARAM_INVALID", "message": message}


def _error(code: str, message: str) -> dict[str, Any]:
    return {"status": "error", "error": {"code": code, "message": message}}
