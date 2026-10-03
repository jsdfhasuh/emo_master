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


_OPERATIONS = ("erode", "dilate", "open", "close", "gradient", "tophat", "blackhat")
_KERNEL_SHAPES = ("rect", "ellipse", "cross")
_PARAM_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "operation": {
            "type": "string",
            "enum": list(_OPERATIONS),
            "default": "open",
        },
        "kernelShape": {
            "type": "string",
            "enum": list(_KERNEL_SHAPES),
            "default": "rect",
        },
        "kernelSize": {"type": "integer", "minimum": 1, "default": 3},
        "iterations": {"type": "integer", "minimum": 1, "default": 1},
    },
}


class MorphologyOperator:
    meta = OperatorMeta(
        operatorId="vision.preprocess.morphology",
        displayName="Morphology",
        version="1.0.0",
        inputPorts={
            "mask": {"type": "image", "required": True, "nullable": False},
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
        operation = params.get("operation", "open")
        kernelShape = params.get("kernelShape", "rect")
        kernelSize = params.get("kernelSize", 3)
        iterations = params.get("iterations", 1)
        if not isinstance(operation, str) or operation not in _OPERATIONS:
            return _paramError(f"operation must be one of: {', '.join(_OPERATIONS)}")
        if not isinstance(kernelShape, str) or kernelShape not in _KERNEL_SHAPES:
            return _paramError(
                f"kernelShape must be one of: {', '.join(_KERNEL_SHAPES)}"
            )
        if not _isInt(kernelSize) or kernelSize < 1 or kernelSize % 2 == 0:
            return _paramError("kernelSize must be an odd integer >= 1")
        if not _isInt(iterations) or iterations < 1:
            return _paramError("iterations must be an integer >= 1")
        return None

    def executeNode(
        self,
        inputs: dict[str, object],
        params: dict[str, object],
        runtimeContext: dict[str, object],
    ) -> dict[str, Any]:
        _ = runtimeContext
        startedAt = perf_counter()
        maskError = _validateMask(inputs)
        if maskError is not None:
            return maskError
        mask = cast(np.ndarray[Any, Any], inputs["mask"])
        paramError = self.validateParams(params)
        if paramError is not None:
            return {"status": "error", "error": paramError}
        height, width = mask.shape
        try:
            frame = frameForInput(inputs, width, height)
        except PayloadValidationError as err:
            return _error("E_INPUT_TYPE", f"invalid frame payload: {err}")
        except ValueError as err:
            return _error("E_INPUT_SHAPE", str(err))

        operation = cast(str, params.get("operation", "open"))
        kernelShape = cast(str, params.get("kernelShape", "rect"))
        kernelSize = cast(int, params.get("kernelSize", 3))
        iterations = cast(int, params.get("iterations", 1))
        binary = np.where(mask != 0, 255, 0).astype(np.uint8)
        shapeCode = {
            "rect": cv2.MORPH_RECT,
            "ellipse": cv2.MORPH_ELLIPSE,
            "cross": cv2.MORPH_CROSS,
        }[kernelShape]
        kernel = cv2.getStructuringElement(shapeCode, (kernelSize, kernelSize))
        try:
            if operation == "erode":
                result = cv2.erode(binary, kernel, iterations=iterations)
            elif operation == "dilate":
                result = cv2.dilate(binary, kernel, iterations=iterations)
            else:
                operationCode = {
                    "open": cv2.MORPH_OPEN,
                    "close": cv2.MORPH_CLOSE,
                    "gradient": cv2.MORPH_GRADIENT,
                    "tophat": cv2.MORPH_TOPHAT,
                    "blackhat": cv2.MORPH_BLACKHAT,
                }[operation]
                result = cv2.morphologyEx(
                    binary,
                    operationCode,
                    kernel,
                    iterations=iterations,
                )
        except cv2.error as err:
            return _error("E_EXEC_FAILED", f"morphology failed: {err}")

        elapsedMs = (perf_counter() - startedAt) * 1000.0
        return {
            "status": "ok",
            "outputs": {"mask": result, "frame": frame.toPayload()},
            "metrics": {
                "latencyMs": round(elapsedMs, 3),
                "operation": operation,
                "foregroundPixels": int(np.count_nonzero(result)),
            },
            "diagnostics": {"text": f"Applied {operation} morphology"},
        }


def _validateMask(inputs: dict[str, object]) -> dict[str, Any] | None:
    if "mask" not in inputs:
        return _error("E_INPUT_MISSING", "input 'mask' is required")
    mask = inputs["mask"]
    if not isinstance(mask, np.ndarray):
        return _error("E_INPUT_TYPE", "input 'mask' must be numpy.ndarray")
    if mask.dtype != np.uint8:
        return _error("E_INPUT_TYPE", "input 'mask' must use uint8 pixels")
    if mask.size == 0 or mask.ndim != 2:
        return _error("E_INPUT_SHAPE", "mask must be a non-empty single-channel image")
    return None


def _isInt(value: object) -> TypeGuard[int]:
    return isinstance(value, int) and not isinstance(value, bool)


def _paramError(message: str) -> dict[str, str]:
    return {"code": "E_PARAM_INVALID", "message": message}


def _error(code: str, message: str) -> dict[str, Any]:
    return {"status": "error", "error": {"code": code, "message": message}}
