from __future__ import annotations

from dataclasses import dataclass
from time import perf_counter
from typing import Any, cast

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
        "sourceSpace": {
            "type": "string",
            "enum": list(_SPACES),
            "default": "BGR",
        },
        "targetSpace": {
            "type": "string",
            "enum": list(_SPACES),
            "default": "GRAY",
        },
    },
}


class ColorConvertOperator:
    meta = OperatorMeta(
        operatorId="vision.preprocess.color_convert",
        displayName="Color Convert",
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
        for name, default in (("sourceSpace", "BGR"), ("targetSpace", "GRAY")):
            value = params.get(name, default)
            if not isinstance(value, str) or value not in _SPACES:
                return _paramError(f"{name} must be one of: {', '.join(_SPACES)}")
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
        sourceSpace = cast(str, params.get("sourceSpace", "BGR"))
        targetSpace = cast(str, params.get("targetSpace", "GRAY"))
        shapeError = _shapeError(image, sourceSpace)
        if shapeError is not None:
            return _error("E_INPUT_SHAPE", shapeError)
        height, width = image.shape[:2]
        try:
            frame = frameForInput(inputs, width, height)
        except PayloadValidationError as err:
            return _error("E_INPUT_TYPE", f"invalid frame payload: {err}")
        except ValueError as err:
            return _error("E_INPUT_SHAPE", str(err))

        try:
            if sourceSpace == targetSpace:
                converted = image.copy()
            else:
                bgr = _toBgr(image, sourceSpace)
                converted = _fromBgr(bgr, targetSpace)
        except cv2.error as err:
            return _error("E_EXEC_FAILED", f"color conversion failed: {err}")

        elapsedMs = (perf_counter() - startedAt) * 1000.0
        return {
            "status": "ok",
            "outputs": {"image": converted, "frame": frame.toPayload()},
            "metrics": {
                "latencyMs": round(elapsedMs, 3),
                "sourceSpace": sourceSpace,
                "targetSpace": targetSpace,
            },
            "diagnostics": {"text": f"Converted {sourceSpace} image to {targetSpace}"},
        }


def _shapeError(image: np.ndarray[Any, Any], sourceSpace: str) -> str | None:
    if image.size == 0:
        return "image must be non-empty"
    if sourceSpace == "GRAY":
        return None if image.ndim == 2 else "GRAY image must be two-dimensional"
    if image.ndim != 3 or image.shape[2] != 3:
        return f"{sourceSpace} image must have exactly 3 channels"
    return None


def _toBgr(image: np.ndarray[Any, Any], sourceSpace: str) -> np.ndarray[Any, Any]:
    conversion = {
        "RGB": cv2.COLOR_RGB2BGR,
        "GRAY": cv2.COLOR_GRAY2BGR,
        "HSV": cv2.COLOR_HSV2BGR,
        "LAB": cv2.COLOR_LAB2BGR,
    }
    return (
        image.copy()
        if sourceSpace == "BGR"
        else cv2.cvtColor(image, conversion[sourceSpace])
    )


def _fromBgr(image: np.ndarray[Any, Any], targetSpace: str) -> np.ndarray[Any, Any]:
    conversion = {
        "RGB": cv2.COLOR_BGR2RGB,
        "GRAY": cv2.COLOR_BGR2GRAY,
        "HSV": cv2.COLOR_BGR2HSV,
        "LAB": cv2.COLOR_BGR2LAB,
    }
    return (
        image.copy()
        if targetSpace == "BGR"
        else cv2.cvtColor(image, conversion[targetSpace])
    )


def _paramError(message: str) -> dict[str, str]:
    return {"code": "E_PARAM_INVALID", "message": message}


def _error(code: str, message: str) -> dict[str, Any]:
    return {"status": "error", "error": {"code": code, "message": message}}
