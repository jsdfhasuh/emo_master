from __future__ import annotations

import math
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


_MODES = ("gaussian", "median", "bilateral")
_PARAM_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "mode": {"type": "string", "enum": list(_MODES), "default": "gaussian"},
        "kernelSize": {"type": "integer", "minimum": 3, "default": 5},
        "sigmaX": {"type": "number", "minimum": 0.0, "default": 0.0},
        "sigmaY": {"type": "number", "minimum": 0.0, "default": 0.0},
        "diameter": {"type": "integer", "minimum": 1, "default": 9},
        "sigmaColor": {"type": "number", "exclusiveMinimum": 0.0, "default": 75.0},
        "sigmaSpace": {"type": "number", "exclusiveMinimum": 0.0, "default": 75.0},
    },
}


class BlurOperator:
    meta = OperatorMeta(
        operatorId="vision.preprocess.blur",
        displayName="Blur / Denoise",
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
        mode = params.get("mode", "gaussian")
        kernelSize = params.get("kernelSize", 5)
        diameter = params.get("diameter", 9)
        if not isinstance(mode, str) or mode not in _MODES:
            return _paramError(f"mode must be one of: {', '.join(_MODES)}")
        if not _isInt(kernelSize) or kernelSize < 3 or kernelSize % 2 == 0:
            return _paramError("kernelSize must be an odd integer >= 3")
        if not _isInt(diameter) or diameter < 1:
            return _paramError("diameter must be an integer >= 1")
        for name, default in (("sigmaX", 0.0), ("sigmaY", 0.0)):
            value = params.get(name, default)
            if not _isFiniteNumber(value) or float(value) < 0.0:
                return _paramError(f"{name} must be a finite number >= 0")
        for name, default in (("sigmaColor", 75.0), ("sigmaSpace", 75.0)):
            value = params.get(name, default)
            if not _isFiniteNumber(value) or float(value) <= 0.0:
                return _paramError(f"{name} must be a finite number > 0")
        return None

    def executeNode(
        self,
        inputs: dict[str, object],
        params: dict[str, object],
        runtimeContext: dict[str, object],
    ) -> dict[str, Any]:
        _ = runtimeContext
        startedAt = perf_counter()
        imageError = _validateImage(inputs)
        if imageError is not None:
            return imageError
        image = cast(np.ndarray[Any, Any], inputs["image"])
        paramError = self.validateParams(params)
        if paramError is not None:
            return {"status": "error", "error": paramError}
        height, width = image.shape[:2]
        try:
            frame = frameForInput(inputs, width, height)
        except PayloadValidationError as err:
            return _error("E_INPUT_TYPE", f"invalid frame payload: {err}")
        except ValueError as err:
            return _error("E_INPUT_SHAPE", str(err))

        mode = cast(str, params.get("mode", "gaussian"))
        try:
            if mode == "gaussian":
                kernelSize = cast(int, params.get("kernelSize", 5))
                result = cv2.GaussianBlur(
                    image,
                    (kernelSize, kernelSize),
                    float(cast(int | float, params.get("sigmaX", 0.0))),
                    sigmaY=float(cast(int | float, params.get("sigmaY", 0.0))),
                )
            elif mode == "median":
                result = cv2.medianBlur(
                    image,
                    cast(int, params.get("kernelSize", 5)),
                )
            else:
                result = cv2.bilateralFilter(
                    image,
                    cast(int, params.get("diameter", 9)),
                    float(cast(int | float, params.get("sigmaColor", 75.0))),
                    float(cast(int | float, params.get("sigmaSpace", 75.0))),
                )
        except (cv2.error, OverflowError, ValueError) as err:
            return _error("E_EXEC_FAILED", f"blur failed: {err}")

        elapsedMs = (perf_counter() - startedAt) * 1000.0
        return {
            "status": "ok",
            "outputs": {"image": result, "frame": frame.toPayload()},
            "metrics": {"latencyMs": round(elapsedMs, 3), "mode": mode},
            "diagnostics": {"text": f"Blur completed in {mode} mode"},
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
