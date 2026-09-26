from __future__ import annotations

from dataclasses import dataclass
from time import perf_counter
from typing import Any, cast

import cv2
import numpy as np

from emo_master.core.contracts.geometry2d import PayloadValidationError
from emo_master.plugins.builtins._image_frame import (
    frameForInput,
    intersectFrames,
)


@dataclass(frozen=True)
class OperatorMeta:
    operatorId: str
    displayName: str
    version: str
    inputPorts: dict[str, object]
    outputPorts: dict[str, object]
    paramSchema: dict[str, object]


_OPERATIONS = ("and", "or", "xor", "not", "subtract")
_PARAM_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "operation": {
            "type": "string",
            "enum": list(_OPERATIONS),
            "default": "and",
        }
    },
}


class MaskLogicOperator:
    meta = OperatorMeta(
        operatorId="vision.mask.logic",
        displayName="Mask Logic",
        version="1.0.0",
        inputPorts={
            "maskA": {"type": "image", "required": True, "nullable": False},
            "maskB": {"type": "image", "required": False, "nullable": False},
            "frameA": {
                "type": "bbox2d",
                "required": False,
                "nullable": False,
                "schemaVersion": "1.x",
            },
            "frameB": {
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
        operation = params.get("operation", "and")
        if not isinstance(operation, str) or operation not in _OPERATIONS:
            return _paramError(f"operation must be one of: {', '.join(_OPERATIONS)}")
        return None

    def executeNode(
        self,
        inputs: dict[str, object],
        params: dict[str, object],
        runtimeContext: dict[str, object],
    ) -> dict[str, Any]:
        _ = runtimeContext
        startedAt = perf_counter()
        firstError = _validateMask(inputs, "maskA")
        if firstError is not None:
            return firstError
        paramError = self.validateParams(params)
        if paramError is not None:
            return {"status": "error", "error": paramError}
        operation = cast(str, params.get("operation", "and"))
        first = cast(np.ndarray[Any, Any], inputs["maskA"])
        height, width = first.shape
        try:
            frameA = frameForInput(inputs, width, height, portName="frameA")
        except PayloadValidationError as err:
            return _error("E_INPUT_TYPE", f"invalid frameA payload: {err}")
        except ValueError as err:
            return _error("E_INPUT_SHAPE", str(err))
        binaryA = np.where(first != 0, 255, 0).astype(np.uint8)

        if operation == "not":
            result = cv2.bitwise_not(binaryA)
            outputFrame = frameA
        else:
            secondError = _validateMask(inputs, "maskB")
            if secondError is not None:
                return secondError
            second = cast(np.ndarray[Any, Any], inputs["maskB"])
            if second.shape != first.shape:
                return _error("E_INPUT_SHAPE", "maskA and maskB must have equal shapes")
            try:
                frameB = (
                    frameForInput(inputs, width, height, portName="frameB")
                    if "frameB" in inputs
                    else frameA
                )
                outputFrame = intersectFrames(frameA, frameB)
            except PayloadValidationError as err:
                return _error("E_INPUT_TYPE", f"invalid frameB payload: {err}")
            except ValueError as err:
                return _error("E_INPUT_SHAPE", str(err))
            binaryB = np.where(second != 0, 255, 0).astype(np.uint8)
            if operation == "and":
                result = cv2.bitwise_and(binaryA, binaryB)
            elif operation == "or":
                result = cv2.bitwise_or(binaryA, binaryB)
            elif operation == "xor":
                result = cv2.bitwise_xor(binaryA, binaryB)
            else:
                result = cv2.bitwise_and(binaryA, cv2.bitwise_not(binaryB))

        elapsedMs = (perf_counter() - startedAt) * 1000.0
        return {
            "status": "ok",
            "outputs": {"mask": result, "frame": outputFrame.toPayload()},
            "metrics": {
                "latencyMs": round(elapsedMs, 3),
                "operation": operation,
                "foregroundPixels": int(np.count_nonzero(result)),
            },
            "diagnostics": {"text": f"Applied mask operation {operation}"},
        }


def _validateMask(
    inputs: dict[str, object],
    name: str,
) -> dict[str, Any] | None:
    if name not in inputs:
        return _error("E_INPUT_MISSING", f"input '{name}' is required")
    mask = inputs[name]
    if not isinstance(mask, np.ndarray):
        return _error("E_INPUT_TYPE", f"input '{name}' must be numpy.ndarray")
    if mask.dtype != np.uint8:
        return _error("E_INPUT_TYPE", f"input '{name}' must use uint8 pixels")
    if mask.size == 0 or mask.ndim != 2:
        return _error(
            "E_INPUT_SHAPE",
            f"input '{name}' must be a non-empty single-channel image",
        )
    return None


def _paramError(message: str) -> dict[str, str]:
    return {"code": "E_PARAM_INVALID", "message": message}


def _error(code: str, message: str) -> dict[str, Any]:
    return {"status": "error", "error": {"code": code, "message": message}}
