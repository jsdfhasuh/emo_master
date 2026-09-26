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


_MODES = (
    "fixed",
    "otsu",
    "triangle",
    "adaptiveMean",
    "adaptiveGaussian",
)

_PARAM_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "mode": {
            "type": "string",
            "enum": list(_MODES),
            "default": "fixed",
        },
        "threshold": {
            "type": "integer",
            "minimum": 0,
            "maximum": 255,
            "default": 127,
        },
        "invert": {"type": "boolean", "default": False},
        "blockSize": {"type": "integer", "minimum": 3, "default": 11},
        "constant": {"type": "number", "default": 2.0},
    },
}


class ThresholdOperator:
    meta = OperatorMeta(
        operatorId="vision.preprocess.threshold",
        displayName="Threshold",
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
            "mask": {"type": "image", "required": True, "nullable": False},
            "threshold": {
                "type": "number",
                "required": True,
                "nullable": True,
            },
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
        mode = params.get("mode", "fixed")
        threshold = params.get("threshold", 127)
        invert = params.get("invert", False)
        blockSize = params.get("blockSize", 11)
        constant = params.get("constant", 2.0)

        if not isinstance(mode, str) or mode not in _MODES:
            return _paramError(f"mode must be one of: {', '.join(_MODES)}")
        if not _isInt(threshold) or not 0 <= threshold <= 255:
            return _paramError("threshold must be an integer in [0, 255]")
        if not isinstance(invert, bool):
            return _paramError("invert must be boolean")
        if not _isInt(blockSize) or blockSize < 3 or blockSize % 2 == 0:
            return _paramError("blockSize must be an odd integer >= 3")
        if not _isFiniteNumber(constant):
            return _paramError("constant must be a finite number")
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
        if image.size == 0 or image.ndim not in (2, 3):
            return _error("E_INPUT_SHAPE", "image must be non-empty grayscale or BGR")
        if image.ndim == 3 and image.shape[2] != 3:
            return _error("E_INPUT_SHAPE", "BGR image must have exactly 3 channels")
        height, width = image.shape[:2]
        try:
            frame = frameForInput(inputs, width, height)
        except PayloadValidationError as err:
            return _error("E_INPUT_TYPE", f"invalid frame payload: {err}")
        except ValueError as err:
            return _error("E_INPUT_SHAPE", str(err))

        paramError = self.validateParams(params)
        if paramError is not None:
            return {"status": "error", "error": paramError}

        mode = cast(str, params.get("mode", "fixed"))
        threshold = cast(int, params.get("threshold", 127))
        invert = cast(bool, params.get("invert", False))
        blockSize = cast(int, params.get("blockSize", 11))
        constant = float(cast(int | float, params.get("constant", 2.0)))

        try:
            gray = image if image.ndim == 2 else cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
            thresholdType = cv2.THRESH_BINARY_INV if invert else cv2.THRESH_BINARY
            actualThreshold: float | None
            if mode == "adaptiveMean" or mode == "adaptiveGaussian":
                adaptiveMethod = (
                    cv2.ADAPTIVE_THRESH_MEAN_C
                    if mode == "adaptiveMean"
                    else cv2.ADAPTIVE_THRESH_GAUSSIAN_C
                )
                mask = cv2.adaptiveThreshold(
                    gray,
                    255,
                    adaptiveMethod,
                    thresholdType,
                    blockSize,
                    constant,
                )
                actualThreshold = None
            else:
                automaticFlag = 0
                thresholdValue = threshold
                if mode == "otsu":
                    automaticFlag = cv2.THRESH_OTSU
                    thresholdValue = 0
                elif mode == "triangle":
                    automaticFlag = cv2.THRESH_TRIANGLE
                    thresholdValue = 0
                measuredThreshold, mask = cv2.threshold(
                    gray,
                    thresholdValue,
                    255,
                    thresholdType | automaticFlag,
                )
                actualThreshold = float(measuredThreshold)
        except (cv2.error, OverflowError, ValueError) as err:
            return _error("E_EXEC_FAILED", f"Thresholding failed: {err}")

        elapsedMs = (perf_counter() - startedAt) * 1000.0
        foregroundPixels = int(np.count_nonzero(mask))
        return {
            "status": "ok",
            "outputs": {
                "mask": mask,
                "threshold": actualThreshold,
                "frame": frame.toPayload(),
            },
            "metrics": {
                "latencyMs": round(elapsedMs, 3),
                "foregroundPixels": foregroundPixels,
                "foregroundRatio": foregroundPixels / int(mask.size),
                "mode": mode,
                "threshold": actualThreshold,
            },
            "diagnostics": {"text": f"Thresholding completed in {mode} mode"},
        }


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
