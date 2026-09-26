from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from time import perf_counter
from typing import Any, TypeGuard, cast

import cv2
import numpy as np

from emo_master.core.contracts.geometry2d import (
    BBox2D,
    Circle2D,
    Histogram,
    HistogramChannel,
    Line2D,
    PayloadValidationError,
    Point2D,
    Polygon2D,
    RotatedBox2D,
    parseGeometry2D,
)
from emo_master.plugins.builtins._classic_vision import (
    BuiltinInputError,
    errorResult,
    geometryMask,
    requireBinaryMask,
    requireImage,
)
from emo_master.plugins.builtins._image_frame import (
    coordinateSpacesEquivalent,
    frameForInput,
    frameMask,
)


@dataclass(frozen=True)
class OperatorMeta:
    operatorId: str
    displayName: str
    version: str
    inputPorts: Mapping[str, object]
    outputPorts: Mapping[str, object]
    paramSchema: Mapping[str, object]


_IMAGE_FRAME_INPUTS = {
    "image": {"type": "image", "required": True, "nullable": False},
    "frame": {
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


class HistogramOperator:
    _PARAM_SCHEMA = {
        "type": "object",
        "properties": {
            "bins": {"type": "integer", "minimum": 1, "maximum": 256, "default": 256},
            "normalization": {
                "type": "string",
                "enum": ["counts", "probability"],
                "default": "counts",
            },
        },
    }
    meta = OperatorMeta(
        operatorId="vision.analysis.histogram",
        displayName="Histogram",
        version="1.0.0",
        inputPorts={
            **_IMAGE_FRAME_INPUTS,
            "roi": {
                "type": "geometry2d",
                "required": False,
                "nullable": False,
                "schemaVersion": "1.x",
            },
            "mask": {"type": "image", "required": False, "nullable": False},
            "maskFrame": {
                "type": "bbox2d",
                "required": False,
                "nullable": False,
                "schemaVersion": "1.x",
            },
        },
        outputPorts={
            "histogram": {
                "type": "histogram",
                "required": True,
                "nullable": False,
                "schemaVersion": "1.2",
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
        bins = params.get("bins", 256)
        if not _integer(bins) or not 1 <= bins <= 256:
            return _paramError("bins must be an integer in [1, 256]")
        if params.get("normalization", "counts") not in {"counts", "probability"}:
            return _paramError("normalization must be counts or probability")
        return None

    def executeNode(
        self,
        inputs: dict[str, object],
        params: dict[str, object],
        runtimeContext: dict[str, object],
    ) -> dict[str, Any]:
        _ = runtimeContext
        started = perf_counter()
        paramError = self.validateParams(params)
        if paramError is not None:
            return {"status": "error", "error": paramError}
        try:
            image = requireImage(inputs)
            height, width = image.shape[:2]
            frame = frameForInput(inputs, width, height)
            selection = frameMask(frame, width, height)
            if "roi" in inputs:
                raw = inputs["roi"]
                roi = raw if _isGeometry(raw) else parseGeometry2D(raw)
                if not coordinateSpacesEquivalent(
                    roi.coordinateSpace, frame.coordinateSpace
                ):
                    raise BuiltinInputError(
                        "E_INPUT_SHAPE", "ROI coordinate space does not match image"
                    )
                selection = cv2.bitwise_and(
                    selection, geometryMask(roi, width, height)
                )
            if "maskFrame" in inputs and "mask" not in inputs:
                raise BuiltinInputError(
                    "E_INPUT_SHAPE", "maskFrame can only be supplied with mask"
                )
            if "mask" in inputs:
                mask = requireBinaryMask(inputs["mask"], width, height, "mask")
                if "maskFrame" in inputs:
                    maskFrame = frameForInput(
                        inputs, width, height, portName="maskFrame"
                    )
                    if not coordinateSpacesEquivalent(
                        maskFrame.coordinateSpace, frame.coordinateSpace
                    ):
                        raise BuiltinInputError(
                            "E_INPUT_SHAPE",
                            "maskFrame coordinate space does not match image",
                        )
                    mask = cv2.bitwise_and(mask, frameMask(maskFrame, width, height))
                selection = cv2.bitwise_and(selection, mask)
            selected = selection != 0
            pixelCount = int(np.count_nonzero(selected))
            bins = cast(int, params.get("bins", 256))
            normalization = cast(str, params.get("normalization", "counts"))
            edges = tuple(float(value) for value in np.linspace(0.0, 256.0, bins + 1))
            arrays = [image] if image.ndim == 2 else list(cv2.split(image))
            names = ("GRAY",) if image.ndim == 2 else ("B", "G", "R")
            channels: list[HistogramChannel] = []
            for name, channel in zip(names, arrays, strict=True):
                counts, _ = np.histogram(channel[selected], bins=bins, range=(0, 256))
                values = counts.astype(np.float64)
                if normalization == "probability" and pixelCount > 0:
                    values /= float(pixelCount)
                channels.append(
                    HistogramChannel(name, tuple(float(value) for value in values))
                )
            histogram = Histogram(
                coordinateSpace=frame.coordinateSpace,
                colorSpace="GRAY" if image.ndim == 2 else "BGR",
                normalization=normalization,
                pixelCount=pixelCount,
                binEdges=edges,
                channels=tuple(channels),
            )
            return {
                "status": "ok",
                "outputs": {
                    "histogram": histogram.toPayload(),
                    "frame": frame.toPayload(),
                },
                "metrics": {
                    "latencyMs": round((perf_counter() - started) * 1000.0, 3),
                    "pixelCount": pixelCount,
                    "bins": bins,
                },
                "diagnostics": {"text": f"Histogram used {pixelCount} pixels"},
            }
        except BuiltinInputError as err:
            return errorResult(err.code, str(err))
        except PayloadValidationError as err:
            return errorResult("E_INPUT_TYPE", f"invalid semantic input: {err}")
        except ValueError as err:
            return errorResult("E_INPUT_SHAPE", str(err))
        except cv2.error as err:
            return errorResult("E_EXEC_FAILED", f"histogram failed: {err}")


class EqualizeOperator:
    _PARAM_SCHEMA = {"type": "object", "properties": {}}
    meta = OperatorMeta(
        "vision.preprocess.equalize",
        "Histogram Equalize",
        "1.0.0",
        _IMAGE_FRAME_INPUTS,
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
        return _enhance(inputs, None)


class ClaheOperator:
    _PARAM_SCHEMA = {
        "type": "object",
        "properties": {
            "clipLimit": {"type": "number", "exclusiveMinimum": 0.0, "default": 2.0},
            "tileGridWidth": {
                "type": "integer",
                "minimum": 1,
                "maximum": 64,
                "default": 8,
            },
            "tileGridHeight": {
                "type": "integer",
                "minimum": 1,
                "maximum": 64,
                "default": 8,
            },
        },
    }
    meta = OperatorMeta(
        "vision.preprocess.clahe",
        "CLAHE",
        "1.0.0",
        _IMAGE_FRAME_INPUTS,
        _IMAGE_FRAME_OUTPUTS,
        _PARAM_SCHEMA,
    )

    def validateParams(self, params: dict[str, object]) -> dict[str, str] | None:
        clip = params.get("clipLimit", 2.0)
        if not _finite(clip) or float(clip) <= 0.0:
            return _paramError("clipLimit must be a finite number > 0")
        for name in ("tileGridWidth", "tileGridHeight"):
            value = params.get(name, 8)
            if not _integer(value) or not 1 <= value <= 64:
                return _paramError(f"{name} must be an integer in [1, 64]")
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
        clahe = cv2.createCLAHE(
            clipLimit=float(cast(int | float, params.get("clipLimit", 2.0))),
            tileGridSize=(
                cast(int, params.get("tileGridWidth", 8)),
                cast(int, params.get("tileGridHeight", 8)),
            ),
        )
        return _enhance(inputs, clahe)


def _enhance(inputs: dict[str, object], clahe: Any | None) -> dict[str, Any]:
    started = perf_counter()
    try:
        image = requireImage(inputs)
        height, width = image.shape[:2]
        frame = frameForInput(inputs, width, height)
        apply = cv2.equalizeHist if clahe is None else clahe.apply
        if image.ndim == 2:
            result = apply(image)
        else:
            lab = cv2.cvtColor(image, cv2.COLOR_BGR2LAB)
            lightness, channelA, channelB = cv2.split(lab)
            result = cv2.cvtColor(
                cv2.merge((apply(lightness), channelA, channelB)),
                cv2.COLOR_LAB2BGR,
            )
        name = "CLAHE" if clahe is not None else "Histogram equalization"
        return {
            "status": "ok",
            "outputs": {"image": result, "frame": frame.toPayload()},
            "metrics": {
                "latencyMs": round((perf_counter() - started) * 1000.0, 3)
            },
            "diagnostics": {"text": f"{name} completed"},
        }
    except BuiltinInputError as err:
        return errorResult(err.code, str(err))
    except PayloadValidationError as err:
        return errorResult("E_INPUT_TYPE", f"invalid frame payload: {err}")
    except ValueError as err:
        return errorResult("E_INPUT_SHAPE", str(err))
    except cv2.error as err:
        return errorResult("E_EXEC_FAILED", f"intensity enhancement failed: {err}")


def _isGeometry(value: object) -> TypeGuard[
    Point2D | BBox2D | RotatedBox2D | Polygon2D | Line2D | Circle2D
]:
    return isinstance(
        value, (Point2D, BBox2D, RotatedBox2D, Polygon2D, Line2D, Circle2D)
    )


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
