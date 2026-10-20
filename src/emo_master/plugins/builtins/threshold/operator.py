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
            "title": "阈值模式",
            "type": "string",
            "enum": ["fixed", "otsu", "triangle", "adaptiveMean", "adaptiveGaussian"],
            "default": "fixed",
            "xOptionLabels": {
                "fixed": "固定阈值",
                "otsu": "大津法（自动阈值）",
                "triangle": "三角法（自动阈值）",
                "adaptiveMean": "自适应均值",
                "adaptiveGaussian": "自适应高斯",
            },
            "description": "固定模式使用指定阈值；自动模式由图像计算阈值；自适应模式按局部邻域计算阈值。",
        },
        "threshold": {
            "title": "阈值",
            "type": "integer",
            "default": 127,
            "xMinimum": 0,
            "xMaximum": 255,
            "xEnabledWhen": {"mode": ["fixed"]},
            "xUnit": "灰度级",
            "xExample": 127,
            "description": "仅固定阈值模式生效，填写 0～255 的整数。",
        },
        "invert": {
            "title": "反向阈值",
            "type": "boolean",
            "default": False,
            "description": "开启后反转二值掩码的前景与背景，输出仍为 0/255。",
        },
        "blockSize": {
            "title": "自适应邻域大小",
            "type": "integer",
            "default": 11,
            "xMinimum": 3,
            "xOdd": True,
            "xEnabledWhen": {"mode": ["adaptiveMean", "adaptiveGaussian"]},
            "xUnit": "像素",
            "xExample": 11,
            "description": "仅自适应模式生效，必须是大于等于 3 的奇数，如 3、5、11。",
        },
        "constant": {
            "title": "自适应阈值偏移量",
            "type": "number",
            "default": 2.0,
            "xEnabledWhen": {"mode": ["adaptiveMean", "adaptiveGaussian"]},
            "xUnit": "灰度级",
            "xExample": 2.0,
            "description": "仅自适应模式生效，从局部均值或加权均值中减去此有限数值，可为负数或小数。",
        },
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
        invert = params.get("invert", False)

        if not isinstance(mode, str) or mode not in _MODES:
            return _paramError(f"mode must be one of: {', '.join(_MODES)}")
        if not isinstance(invert, bool):
            return _paramError("invert must be boolean")
        # These are algorithm constraints, independent of the UI's x* hints.
        if mode == "fixed":
            threshold = params.get("threshold", 127)
            if not _isInt(threshold) or not 0 <= threshold <= 255:
                return _paramError("threshold must be an integer in [0, 255]")
        elif mode in ("adaptiveMean", "adaptiveGaussian"):
            blockSize = params.get("blockSize", 11)
            if not _isInt(blockSize) or blockSize < 3 or blockSize % 2 == 0:
                return _paramError("blockSize must be an odd integer >= 3")
            if not _isFiniteNumber(params.get("constant", 2.0)):
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
        invert = cast(bool, params.get("invert", False))

        try:
            gray = image if image.ndim == 2 else cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
            thresholdType = cv2.THRESH_BINARY_INV if invert else cv2.THRESH_BINARY
            actualThreshold: float | None
            if mode == "adaptiveMean" or mode == "adaptiveGaussian":
                blockSize = cast(int, params.get("blockSize", 11))
                constant = float(cast(int | float, params.get("constant", 2.0)))
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
                thresholdValue = 0
                if mode == "otsu":
                    automaticFlag = cv2.THRESH_OTSU
                elif mode == "triangle":
                    automaticFlag = cv2.THRESH_TRIANGLE
                else:
                    thresholdValue = cast(int, params.get("threshold", 127))
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
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    try:
        return math.isfinite(float(value))
    except OverflowError:
        return False


def _paramError(message: str) -> dict[str, str]:
    return {"code": "E_PARAM_INVALID", "message": message}


def _error(code: str, message: str) -> dict[str, Any]:
    return {"status": "error", "error": {"code": code, "message": message}}
