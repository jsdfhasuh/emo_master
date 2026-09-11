from __future__ import annotations

from dataclasses import dataclass
from time import perf_counter
from typing import Any, TypeGuard, cast

import cv2
import numpy as np

from emo_master.core.contracts.geometry2d import PayloadValidationError
from emo_master.plugins.builtins._image_frame import frameForInput


@dataclass(frozen=True)
class OperatorMeta:
    operatorId: str
    displayName: str
    version: str
    inputPorts: dict[str, object]
    outputPorts: dict[str, object]
    paramSchema: dict[str, object]


_SPACES = ("BGR", "RGB", "GRAY", "HSV", "LAB")
_PARAM_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "colorSpace": {
            "type": "string",
            "enum": list(_SPACES),
            "default": "BGR",
        },
        "lower": {
            "type": "array",
            "items": {"type": "integer", "minimum": 0, "maximum": 255},
        },
        "upper": {
            "type": "array",
            "items": {"type": "integer", "minimum": 0, "maximum": 255},
        },
    },
    "required": ["lower", "upper"],
}


class InRangeOperator:
    meta = OperatorMeta(
        operatorId="vision.segment.in_range",
        displayName="In Range",
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
            "mask": {"type": "image", "required": True, "nullable": False},
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
        colorSpace = params.get("colorSpace", "BGR")
        if not isinstance(colorSpace, str) or colorSpace not in _SPACES:
            return _paramError(f"colorSpace must be one of: {', '.join(_SPACES)}")
        channelCount = 1 if colorSpace == "GRAY" else 3
        maxima = [179, 255, 255] if colorSpace == "HSV" else [255] * channelCount
        bounds: dict[str, list[int]] = {}
        for name in ("lower", "upper"):
            value = params.get(name)
            if (
                not isinstance(value, list)
                or len(value) != channelCount
                or any(not _isInt(item) for item in value)
            ):
                return _paramError(
                    f"{name} must contain {channelCount} integer channel value(s)"
                )
            typed = cast(list[int], value)
            if any(
                item < 0 or item > maxima[index] for index, item in enumerate(typed)
            ):
                return _paramError(
                    f"{name} contains a value outside {colorSpace} range"
                )
            bounds[name] = typed
        if any(
            lower > upper
            for lower, upper in zip(bounds["lower"], bounds["upper"], strict=True)
        ):
            return _paramError("each lower channel value must be <= upper")
        return None

    def executeNode(
        self,
        inputs: dict[str, object],
        params: dict[str, object],
        runtimeContext: dict[str, object],
    ) -> dict[str, Any]:
        _ = runtimeContext
        startedAt = perf_counter()
        if "image" not in inputs:
            return _error("E_INPUT_MISSING", "input 'image' is required")
        image = inputs["image"]
        if not isinstance(image, np.ndarray):
            return _error("E_INPUT_TYPE", "input 'image' must be numpy.ndarray")
        if image.dtype != np.uint8:
            return _error("E_INPUT_TYPE", "input 'image' must use uint8 pixels")
        paramError = self.validateParams(params)
        if paramError is not None:
            return {"status": "error", "error": paramError}
        colorSpace = cast(str, params.get("colorSpace", "BGR"))
        shapeError = _shapeError(image, colorSpace)
        if shapeError is not None:
            return _error("E_INPUT_SHAPE", shapeError)
        height, width = image.shape[:2]
        try:
            frame = frameForInput(inputs, width, height)
        except PayloadValidationError as err:
            return _error("E_INPUT_TYPE", f"invalid frame payload: {err}")
        except ValueError as err:
            return _error("E_INPUT_SHAPE", str(err))

        lower = cast(list[int], params["lower"])
        upper = cast(list[int], params["upper"])
        lowerValue = np.asarray(lower, dtype=np.uint8)
        upperValue = np.asarray(upper, dtype=np.uint8)
        try:
            mask = cv2.inRange(image, lowerValue, upperValue)
        except cv2.error as err:
            return _error("E_EXEC_FAILED", f"in-range segmentation failed: {err}")

        elapsedMs = (perf_counter() - startedAt) * 1000.0
        return {
            "status": "ok",
            "outputs": {"mask": mask, "frame": frame.toPayload()},
            "metrics": {
                "latencyMs": round(elapsedMs, 3),
                "foregroundPixels": int(np.count_nonzero(mask)),
                "colorSpace": colorSpace,
            },
            "diagnostics": {"text": f"Applied {colorSpace} range segmentation"},
        }


def _shapeError(image: np.ndarray[Any, Any], colorSpace: str) -> str | None:
    if image.size == 0:
        return "image must be non-empty"
    if colorSpace == "GRAY":
        return None if image.ndim == 2 else "GRAY image must be two-dimensional"
    if image.ndim != 3 or image.shape[2] != 3:
        return f"{colorSpace} image must have exactly 3 channels"
    return None


def _isInt(value: object) -> TypeGuard[int]:
    return isinstance(value, int) and not isinstance(value, bool)


def _paramError(message: str) -> dict[str, str]:
    return {"code": "E_PARAM_INVALID", "message": message}


def _error(code: str, message: str) -> dict[str, Any]:
    return {"status": "error", "error": {"code": code, "message": message}}
