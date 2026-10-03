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
    interpolationFlag,
    requireBinaryMask,
    requireImage,
)
from emo_master.plugins.builtins._image_frame import (
    AffineTransform2D,
    Transform2D,
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
    inputPorts: Mapping[str, object]
    outputPorts: Mapping[str, object]
    paramSchema: Mapping[str, object]


_TRANSFORM_INPUTS = {
    "image": {"type": "image", "required": True, "nullable": False},
    "frame": {
        "type": "bbox2d",
        "required": False,
        "nullable": False,
        "schemaVersion": "1.x",
    },
    "validMask": {"type": "image", "required": False, "nullable": False},
}
_TRANSFORM_OUTPUTS = {
    "image": {"type": "image", "required": True, "nullable": False},
    "frame": {
        "type": "bbox2d",
        "required": True,
        "nullable": False,
        "schemaVersion": "1.x",
    },
    "validMask": {"type": "image", "required": True, "nullable": False},
}


class RotateOperator:
    _PARAM_SCHEMA = {
        "type": "object",
        "properties": {
            "angleDegrees": {
                "type": "number",
                "minimum": -180.0,
                "exclusiveMaximum": 180.0,
                "default": 0.0,
            },
            "expand": {"type": "boolean", "default": True},
            "interpolation": {
                "type": "string",
                "enum": ["nearest", "linear", "cubic"],
                "default": "linear",
            },
            "padValue": {
                "type": "integer",
                "minimum": 0,
                "maximum": 255,
                "default": 0,
            },
        },
    }
    meta = OperatorMeta(
        "vision.preprocess.rotate",
        "Rotate",
        "1.0.0",
        _TRANSFORM_INPUTS,
        _TRANSFORM_OUTPUTS,
        _PARAM_SCHEMA,
    )

    def validateParams(self, params: dict[str, object]) -> dict[str, str] | None:
        angle = params.get("angleDegrees", 0.0)
        if not _finite(angle) or not -180.0 <= float(angle) < 180.0:
            return _paramError("angleDegrees must be finite and in [-180, 180)")
        if not isinstance(params.get("expand", True), bool):
            return _paramError("expand must be boolean")
        return _commonParams(params)

    def executeNode(
        self,
        inputs: dict[str, object],
        params: dict[str, object],
        runtimeContext: dict[str, object],
    ) -> dict[str, Any]:
        error = self.validateParams(params)
        if error is not None:
            return {"status": "error", "error": error}
        try:
            image, sourceMask, frame = _transformInputs(inputs)
            height, width = image.shape[:2]
            center = ((width - 1.0) / 2.0, (height - 1.0) / 2.0)
            forward = cv2.getRotationMatrix2D(
                center,
                -float(cast(int | float, params.get("angleDegrees", 0.0))),
                1.0,
            )
            if cast(bool, params.get("expand", True)):
                cosine = abs(float(forward[0, 0]))
                sine = abs(float(forward[0, 1]))
                outputWidth = max(
                    1,
                    int(math.ceil(width * cosine + height * sine - 1e-12)),
                )
                outputHeight = max(
                    1,
                    int(math.ceil(width * sine + height * cosine - 1e-12)),
                )
                forward[0, 2] += (outputWidth - 1.0) / 2.0 - center[0]
                forward[1, 2] += (outputHeight - 1.0) / 2.0 - center[1]
            else:
                outputWidth, outputHeight = width, height
            return _warpAffineResult(
                self.meta.operatorId,
                image,
                sourceMask,
                frame,
                forward,
                outputWidth,
                outputHeight,
                params,
                runtimeContext,
            )
        except BuiltinInputError as err:
            return errorResult(err.code, str(err))
        except PayloadValidationError as err:
            return errorResult("E_INPUT_TYPE", str(err))
        except (cv2.error, ValueError, OverflowError) as err:
            return errorResult("E_EXEC_FAILED", f"rotate failed: {err}")


class FlipOperator:
    _PARAM_SCHEMA = {
        "type": "object",
        "properties": {
            "mode": {
                "type": "string",
                "enum": ["horizontal", "vertical", "both"],
                "default": "horizontal",
            }
        },
    }
    meta = OperatorMeta(
        "vision.preprocess.flip",
        "Flip",
        "1.0.0",
        _TRANSFORM_INPUTS,
        _TRANSFORM_OUTPUTS,
        _PARAM_SCHEMA,
    )

    def validateParams(self, params: dict[str, object]) -> dict[str, str] | None:
        if params.get("mode", "horizontal") not in {
            "horizontal",
            "vertical",
            "both",
        }:
            return _paramError("mode must be horizontal, vertical, or both")
        return None

    def executeNode(
        self,
        inputs: dict[str, object],
        params: dict[str, object],
        runtimeContext: dict[str, object],
    ) -> dict[str, Any]:
        error = self.validateParams(params)
        if error is not None:
            return {"status": "error", "error": error}
        started = perf_counter()
        try:
            image, sourceMask, frame = _transformInputs(inputs)
            height, width = image.shape[:2]
            mode = cast(str, params.get("mode", "horizontal"))
            flipCode = {"horizontal": 1, "vertical": 0, "both": -1}[mode]
            result = cv2.flip(image, flipCode)
            mask = cv2.flip(sourceMask, flipCode)
            if mode == "horizontal":
                outputToInput: AffineTransform2D = (
                    -1.0,
                    0.0,
                    0.0,
                    1.0,
                    width - 1.0,
                    0.0,
                )
            elif mode == "vertical":
                outputToInput = (1.0, 0.0, 0.0, -1.0, 0.0, height - 1.0)
            else:
                outputToInput = (
                    -1.0,
                    0.0,
                    0.0,
                    -1.0,
                    width - 1.0,
                    height - 1.0,
                )
            outputFrame = _frameForWarp(
                frame,
                width,
                height,
                outputToInput,
                mask,
                runtimeContext,
                self.meta.operatorId,
            )
            return _success(
                result,
                outputFrame.toPayload(),
                mask,
                started,
                {"mode": mode},
            )
        except BuiltinInputError as err:
            return errorResult(err.code, str(err))
        except PayloadValidationError as err:
            return errorResult("E_INPUT_TYPE", str(err))
        except (cv2.error, ValueError, OverflowError) as err:
            return errorResult("E_EXEC_FAILED", f"flip failed: {err}")


class AffineOperator:
    _PARAM_SCHEMA = {
        "type": "object",
        "properties": {
            "srcPoints": {"type": "array", "minItems": 3, "maxItems": 3},
            "dstPoints": {"type": "array", "minItems": 3, "maxItems": 3},
            "outputWidth": {"type": "integer", "minimum": 0, "default": 0},
            "outputHeight": {"type": "integer", "minimum": 0, "default": 0},
            "interpolation": {
                "type": "string",
                "enum": ["nearest", "linear", "cubic"],
                "default": "linear",
            },
            "padValue": {
                "type": "integer",
                "minimum": 0,
                "maximum": 255,
                "default": 0,
            },
        },
        "required": ["srcPoints", "dstPoints"],
    }
    meta = OperatorMeta(
        "vision.preprocess.affine",
        "Affine Transform",
        "1.0.0",
        _TRANSFORM_INPUTS,
        _TRANSFORM_OUTPUTS,
        _PARAM_SCHEMA,
    )

    def validateParams(self, params: dict[str, object]) -> dict[str, str] | None:
        try:
            source = _points(params.get("srcPoints"), 3, "srcPoints")
            target = _points(params.get("dstPoints"), 3, "dstPoints")
        except BuiltinInputError as err:
            return _paramError(str(err))
        if abs(cv2.contourArea(source)) <= 1e-12:
            return _paramError("srcPoints must not be collinear")
        if abs(cv2.contourArea(target)) <= 1e-12:
            return _paramError("dstPoints must not be collinear")
        sizeError = _sizeParams(params, minimum=0)
        return sizeError if sizeError is not None else _commonParams(params)

    def executeNode(
        self,
        inputs: dict[str, object],
        params: dict[str, object],
        runtimeContext: dict[str, object],
    ) -> dict[str, Any]:
        error = self.validateParams(params)
        if error is not None:
            return {"status": "error", "error": error}
        try:
            image, sourceMask, frame = _transformInputs(inputs)
            height, width = image.shape[:2]
            outputWidth = cast(int, params.get("outputWidth", 0)) or width
            outputHeight = cast(int, params.get("outputHeight", 0)) or height
            forward = cv2.getAffineTransform(
                _points(params["srcPoints"], 3, "srcPoints"),
                _points(params["dstPoints"], 3, "dstPoints"),
            )
            return _warpAffineResult(
                self.meta.operatorId,
                image,
                sourceMask,
                frame,
                forward,
                outputWidth,
                outputHeight,
                params,
                runtimeContext,
            )
        except BuiltinInputError as err:
            return errorResult(err.code, str(err))
        except PayloadValidationError as err:
            return errorResult("E_INPUT_TYPE", str(err))
        except (cv2.error, ValueError, OverflowError) as err:
            return errorResult("E_EXEC_FAILED", f"affine transform failed: {err}")


class PerspectiveOperator:
    _PARAM_SCHEMA = {
        "type": "object",
        "properties": {
            "srcPoints": {"type": "array", "minItems": 4, "maxItems": 4},
            "outputWidth": {"type": "integer", "minimum": 2},
            "outputHeight": {"type": "integer", "minimum": 2},
            "interpolation": {
                "type": "string",
                "enum": ["nearest", "linear", "cubic"],
                "default": "linear",
            },
            "padValue": {
                "type": "integer",
                "minimum": 0,
                "maximum": 255,
                "default": 0,
            },
        },
        "required": ["srcPoints", "outputWidth", "outputHeight"],
    }
    meta = OperatorMeta(
        "vision.preprocess.perspective",
        "Perspective Transform",
        "1.0.0",
        _TRANSFORM_INPUTS,
        _TRANSFORM_OUTPUTS,
        _PARAM_SCHEMA,
    )

    def validateParams(self, params: dict[str, object]) -> dict[str, str] | None:
        try:
            points = _points(params.get("srcPoints"), 4, "srcPoints")
        except BuiltinInputError as err:
            return _paramError(str(err))
        if not cv2.isContourConvex(points.astype(np.float32)):
            return _paramError("srcPoints must form a convex non-self-intersecting quad")
        if abs(cv2.contourArea(points)) <= 1e-12:
            return _paramError("srcPoints must have non-zero area")
        sizeError = _sizeParams(params, minimum=2, required=True)
        return sizeError if sizeError is not None else _commonParams(params)

    def executeNode(
        self,
        inputs: dict[str, object],
        params: dict[str, object],
        runtimeContext: dict[str, object],
    ) -> dict[str, Any]:
        error = self.validateParams(params)
        if error is not None:
            return {"status": "error", "error": error}
        started = perf_counter()
        try:
            image, sourceMask, frame = _transformInputs(inputs)
            outputWidth = cast(int, params["outputWidth"])
            outputHeight = cast(int, params["outputHeight"])
            source = _points(params["srcPoints"], 4, "srcPoints")
            target = np.asarray(
                [
                    [0.0, 0.0],
                    [outputWidth - 1.0, 0.0],
                    [outputWidth - 1.0, outputHeight - 1.0],
                    [0.0, outputHeight - 1.0],
                ],
                dtype=np.float32,
            )
            forward = cv2.getPerspectiveTransform(source, target)
            result = cv2.warpPerspective(  # type: ignore[call-overload]
                image,
                forward,
                (outputWidth, outputHeight),
                flags=interpolationFlag(params.get("interpolation", "linear")),
                borderMode=cv2.BORDER_CONSTANT,
                borderValue=int(cast(int, params.get("padValue", 0))),
            )
            mask = cv2.warpPerspective(  # type: ignore[call-overload]
                sourceMask,
                forward,
                (outputWidth, outputHeight),
                flags=cv2.INTER_NEAREST,
                borderMode=cv2.BORDER_CONSTANT,
                borderValue=0,
            )
            mask = np.where(mask != 0, 255, 0).astype(np.uint8)
            inverse = np.linalg.inv(cast(Any, forward))
            outputToInput = tuple(float(value) for value in inverse.reshape(-1))
            outputFrame = _frameForWarp(
                frame,
                outputWidth,
                outputHeight,
                cast(Transform2D, outputToInput),
                mask,
                runtimeContext,
                self.meta.operatorId,
            )
            return _success(
                result,
                outputFrame.toPayload(),
                mask,
                started,
                {"transform": "perspective"},
            )
        except BuiltinInputError as err:
            return errorResult(err.code, str(err))
        except PayloadValidationError as err:
            return errorResult("E_INPUT_TYPE", str(err))
        except (cv2.error, ValueError, OverflowError, np.linalg.LinAlgError) as err:
            return errorResult("E_EXEC_FAILED", f"perspective transform failed: {err}")


def _transformInputs(
    inputs: dict[str, object],
) -> tuple[np.ndarray[Any, Any], np.ndarray[Any, Any], Any]:
    image = requireImage(inputs)
    height, width = image.shape[:2]
    try:
        frame = frameForInput(inputs, width, height)
    except PayloadValidationError as err:
        raise BuiltinInputError("E_INPUT_TYPE", f"invalid frame payload: {err}") from err
    except ValueError as err:
        raise BuiltinInputError("E_INPUT_SHAPE", str(err)) from err
    mask = frameMask(frame, width, height)
    if "validMask" in inputs:
        mask = cv2.bitwise_and(
            mask,
            requireBinaryMask(inputs["validMask"], width, height, "validMask"),
        )
    if not np.any(mask):
        raise BuiltinInputError("E_INPUT_SHAPE", "validMask contains no valid pixels")
    return image, mask, frame


def _warpAffineResult(
    operatorId: str,
    image: np.ndarray[Any, Any],
    sourceMask: np.ndarray[Any, Any],
    frame: Any,
    forward: np.ndarray[Any, Any],
    outputWidth: int,
    outputHeight: int,
    params: dict[str, object],
    runtimeContext: dict[str, object],
) -> dict[str, Any]:
    started = perf_counter()
    result = cv2.warpAffine(  # type: ignore[call-overload]
        image,
        forward,
        (outputWidth, outputHeight),
        flags=interpolationFlag(params.get("interpolation", "linear")),
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=int(cast(int, params.get("padValue", 0))),
    )
    mask = cv2.warpAffine(  # type: ignore[call-overload]
        sourceMask,
        forward,
        (outputWidth, outputHeight),
        flags=cv2.INTER_NEAREST,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=0,
    )
    mask = np.where(mask != 0, 255, 0).astype(np.uint8)
    inverse = cv2.invertAffineTransform(forward)
    outputToInput: AffineTransform2D = (
        float(inverse[0, 0]),
        float(inverse[1, 0]),
        float(inverse[0, 1]),
        float(inverse[1, 1]),
        float(inverse[0, 2]),
        float(inverse[1, 2]),
    )
    outputFrame = _frameForWarp(
        frame,
        outputWidth,
        outputHeight,
        outputToInput,
        mask,
        runtimeContext,
        operatorId,
    )
    return _success(
        result,
        outputFrame.toPayload(),
        mask,
        started,
        {"transform": operatorId.rsplit(".", 1)[-1]},
    )


def _frameForWarp(
    inputFrame: Any,
    outputWidth: int,
    outputHeight: int,
    outputToInput: Transform2D,
    mask: np.ndarray[Any, Any],
    runtimeContext: dict[str, object],
    operatorId: str,
) -> Any:
    ys, xs = np.nonzero(mask)
    if xs.size == 0:
        raise BuiltinInputError("E_INPUT_SHAPE", "transformed validMask is empty")
    content = (
        float(xs.min()),
        float(ys.min()),
        float(xs.max() - xs.min() + 1),
        float(ys.max() - ys.min() + 1),
    )
    return transformedFrame(
        inputFrame,
        outputWidth,
        outputHeight,
        outputToInput,
        content,
        reference=frameReference(runtimeContext, operatorId),
    )


def _points(value: object, count: int, name: str) -> np.ndarray[Any, Any]:
    if not isinstance(value, list) or len(value) != count:
        raise BuiltinInputError(
            "E_PARAM_INVALID", f"{name} must contain exactly {count} points"
        )
    points: list[tuple[float, float]] = []
    for index, item in enumerate(value):
        if not isinstance(item, dict) or set(item) != {"x", "y"}:
            raise BuiltinInputError(
                "E_PARAM_INVALID", f"{name}[{index}] must contain x and y"
            )
        x, y = item["x"], item["y"]
        if not _finite(x) or not _finite(y):
            raise BuiltinInputError(
                "E_PARAM_INVALID", f"{name}[{index}] coordinates must be finite"
            )
        points.append((float(cast(int | float, x)), float(cast(int | float, y))))
    if len(set(points)) != count:
        raise BuiltinInputError("E_PARAM_INVALID", f"{name} points must be unique")
    return np.asarray(points, dtype=np.float32)


def _commonParams(params: dict[str, object]) -> dict[str, str] | None:
    try:
        interpolationFlag(params.get("interpolation", "linear"))
    except BuiltinInputError as err:
        return _paramError(str(err))
    pad = params.get("padValue", 0)
    if not _integer(pad) or not 0 <= pad <= 255:
        return _paramError("padValue must be an integer in [0, 255]")
    return None


def _sizeParams(
    params: dict[str, object], *, minimum: int, required: bool = False
) -> dict[str, str] | None:
    for name in ("outputWidth", "outputHeight"):
        if required and name not in params:
            return _paramError(f"{name} is required")
        value = params.get(name, 0)
        if not _integer(value) or value < minimum:
            return _paramError(f"{name} must be an integer >= {minimum}")
    return None


def _success(
    image: np.ndarray[Any, Any],
    frame: dict[str, object],
    mask: np.ndarray[Any, Any],
    started: float,
    metrics: dict[str, object],
) -> dict[str, Any]:
    return {
        "status": "ok",
        "outputs": {"image": image, "frame": frame, "validMask": mask},
        "metrics": {
            "latencyMs": round((perf_counter() - started) * 1000.0, 3),
            **metrics,
        },
        "diagnostics": {"text": "Geometric transform completed"},
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
