from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from time import perf_counter
from typing import Any, TypeGuard, cast

import cv2
import numpy as np

from emo_master.core.contracts.geometry2d import PayloadValidationError
from emo_master.plugins.builtins._classic_vision import (
    BuiltinInputError,
    errorResult,
    requireBinaryMask,
    requireImage,
)
from emo_master.plugins.builtins._image_frame import (
    coordinateSpacesEquivalent,
    frameForInput,
    intersectFrames,
)


@dataclass(frozen=True)
class OperatorMeta:
    operatorId: str
    displayName: str
    version: str
    inputPorts: Mapping[str, object]
    outputPorts: Mapping[str, object]
    paramSchema: Mapping[str, object]


_DOUBLE_IMAGE_INPUTS = {
    "imageA": {"type": "image", "required": True, "nullable": False},
    "imageB": {"type": "image", "required": True, "nullable": False},
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
}
_IMAGE_FRAME_OUTPUTS = {
    "image": {"type": "image", "required": True, "nullable": False},
    "frame": {
        "type": "bbox2d",
        "required": True,
        "nullable": False,
        "schemaVersion": "1.x",
    },
}


class AbsDiffOperator:
    _PARAM_SCHEMA = {"type": "object", "properties": {}}
    meta = OperatorMeta(
        "vision.image.absdiff",
        "Absolute Difference",
        "1.0.0",
        _DOUBLE_IMAGE_INPUTS,
        _IMAGE_FRAME_OUTPUTS,
        _PARAM_SCHEMA,
    )

    def validateParams(self, params: dict[str, object]) -> dict[str, str] | None:
        _ = params
        return None

    def executeNode(
        self,
        inputs: dict[str, object],
        params: dict[str, object],
        runtimeContext: dict[str, object],
    ) -> dict[str, Any]:
        _ = params, runtimeContext
        return _binaryImage(inputs, "absdiff", (0.0, 0.0, 0.0))


class AddWeightedOperator:
    _PARAM_SCHEMA = {
        "type": "object",
        "properties": {
            "alpha": {"type": "number", "default": 0.5},
            "beta": {"type": "number", "default": 0.5},
            "gamma": {"type": "number", "default": 0.0},
        },
    }
    meta = OperatorMeta(
        "vision.image.add_weighted",
        "Add Weighted",
        "1.0.0",
        _DOUBLE_IMAGE_INPUTS,
        _IMAGE_FRAME_OUTPUTS,
        _PARAM_SCHEMA,
    )

    def validateParams(self, params: dict[str, object]) -> dict[str, str] | None:
        for name, default in (("alpha", 0.5), ("beta", 0.5), ("gamma", 0.0)):
            if not _finite(params.get(name, default)):
                return _paramError(f"{name} must be a finite number")
        return None

    def executeNode(
        self,
        inputs: dict[str, object],
        params: dict[str, object],
        runtimeContext: dict[str, object],
    ) -> dict[str, Any]:
        _ = runtimeContext
        error = self.validateParams(params)
        if error is not None:
            return {"status": "error", "error": error}
        return _binaryImage(
            inputs,
            "addWeighted",
            (
                float(cast(int | float, params.get("alpha", 0.5))),
                float(cast(int | float, params.get("beta", 0.5))),
                float(cast(int | float, params.get("gamma", 0.0))),
            ),
        )


class ApplyMaskOperator:
    _PARAM_SCHEMA = {
        "type": "object",
        "properties": {
            "invert": {"type": "boolean", "default": False},
            "fillValue": {
                "type": "integer",
                "minimum": 0,
                "maximum": 255,
                "default": 0,
            },
        },
    }
    meta = OperatorMeta(
        operatorId="vision.mask.apply",
        displayName="Apply Mask",
        version="1.0.0",
        inputPorts={
            "image": {"type": "image", "required": True, "nullable": False},
            "mask": {"type": "image", "required": True, "nullable": False},
            "frame": {
                "type": "bbox2d",
                "required": False,
                "nullable": False,
                "schemaVersion": "1.x",
            },
            "maskFrame": {
                "type": "bbox2d",
                "required": False,
                "nullable": False,
                "schemaVersion": "1.x",
            },
        },
        outputPorts=_IMAGE_FRAME_OUTPUTS,
        paramSchema=_PARAM_SCHEMA,
    )

    def validateParams(self, params: dict[str, object]) -> dict[str, str] | None:
        if not isinstance(params.get("invert", False), bool):
            return _paramError("invert must be boolean")
        fill = params.get("fillValue", 0)
        if not _integer(fill) or not 0 <= fill <= 255:
            return _paramError("fillValue must be an integer in [0, 255]")
        return None

    def executeNode(
        self,
        inputs: dict[str, object],
        params: dict[str, object],
        runtimeContext: dict[str, object],
    ) -> dict[str, Any]:
        _ = runtimeContext
        started = perf_counter()
        error = self.validateParams(params)
        if error is not None:
            return {"status": "error", "error": error}
        try:
            image = requireImage(inputs)
            height, width = image.shape[:2]
            if "mask" not in inputs:
                raise BuiltinInputError("E_INPUT_MISSING", "input 'mask' is required")
            mask = requireBinaryMask(inputs["mask"], width, height, "mask")
            frame = frameForInput(inputs, width, height)
            if "maskFrame" in inputs:
                maskFrame = frameForInput(inputs, width, height, portName="maskFrame")
                if not coordinateSpacesEquivalent(
                    frame.coordinateSpace, maskFrame.coordinateSpace
                ):
                    raise BuiltinInputError(
                        "E_INPUT_SHAPE", "maskFrame coordinate space does not match image"
                    )
            if cast(bool, params.get("invert", False)):
                mask = cv2.bitwise_not(mask)
            fill = cast(int, params.get("fillValue", 0))
            result = np.full_like(image, fill)
            if image.ndim == 2:
                result[mask != 0] = image[mask != 0]
            else:
                result[mask != 0, :] = image[mask != 0, :]
            return _success(result, frame.toPayload(), started, "applyMask")
        except BuiltinInputError as err:
            return errorResult(err.code, str(err))
        except PayloadValidationError as err:
            return errorResult("E_INPUT_TYPE", f"invalid frame payload: {err}")
        except ValueError as err:
            return errorResult("E_INPUT_SHAPE", str(err))
        except cv2.error as err:
            return errorResult("E_EXEC_FAILED", f"apply mask failed: {err}")


def _binaryImage(
    inputs: dict[str, object],
    operation: str,
    weights: tuple[float, float, float],
) -> dict[str, Any]:
    started = perf_counter()
    try:
        imageA = requireImage(inputs, "imageA")
        imageB = requireImage(inputs, "imageB")
        if imageA.shape != imageB.shape:
            raise BuiltinInputError(
                "E_INPUT_SHAPE", "imageA and imageB must have identical shapes"
            )
        height, width = imageA.shape[:2]
        frameA = frameForInput(inputs, width, height, portName="frameA")
        frameB = frameForInput(inputs, width, height, portName="frameB")
        frame = intersectFrames(frameA, frameB)
        if operation == "absdiff":
            result = cv2.absdiff(imageA, imageB)
        else:
            result = cv2.addWeighted(
                imageA, weights[0], imageB, weights[1], weights[2]
            )
        return _success(result, frame.toPayload(), started, operation)
    except BuiltinInputError as err:
        return errorResult(err.code, str(err))
    except PayloadValidationError as err:
        return errorResult("E_INPUT_TYPE", f"invalid frame payload: {err}")
    except ValueError as err:
        return errorResult("E_INPUT_SHAPE", str(err))
    except cv2.error as err:
        return errorResult("E_EXEC_FAILED", f"{operation} failed: {err}")


def _success(
    image: np.ndarray[Any, Any],
    frame: dict[str, object],
    started: float,
    operation: str,
) -> dict[str, Any]:
    return {
        "status": "ok",
        "outputs": {"image": image, "frame": frame},
        "metrics": {
            "latencyMs": round((perf_counter() - started) * 1000.0, 3),
            "operation": operation,
        },
        "diagnostics": {"text": f"{operation} completed"},
    }


def _paramError(message: str) -> dict[str, str]:
    return {"code": "E_PARAM_INVALID", "message": message}


def _integer(value: object) -> TypeGuard[int]:
    return isinstance(value, int) and not isinstance(value, bool)


def _finite(value: object) -> TypeGuard[int | float]:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )
