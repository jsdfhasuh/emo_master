from __future__ import annotations

import math
from dataclasses import dataclass
from time import perf_counter
from typing import Any, TypeGuard, cast

import cv2
import numpy as np

from emo_master.core.contracts.geometry2d import (
    BBox2D,
    Blob2D,
    BlobCollection,
    CoordinateSpace2D,
    Point2D,
    Polygon2D,
    PayloadValidationError,
)
from emo_master.plugins.builtins._image_frame import frameForInput, frameMask


@dataclass(frozen=True)
class OperatorMeta:
    operatorId: str
    displayName: str
    version: str
    inputPorts: dict[str, object]
    outputPorts: dict[str, object]
    paramSchema: dict[str, object]


_PARAM_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "threshold": {
            "type": "integer",
            "minimum": 0,
            "maximum": 255,
            "default": 127,
        },
        "invert": {"type": "boolean", "default": False},
        "useOtsu": {"type": "boolean", "default": False},
        "connectivity": {"type": "integer", "enum": [4, 8], "default": 8},
        "minArea": {"type": "integer", "minimum": 1, "default": 1},
        "maxArea": {"type": "integer", "minimum": 0, "default": 0},
        "includeContour": {"type": "boolean", "default": True},
        "drawOverlay": {"type": "boolean", "default": False},
    },
}


class BlobAnalysisOperator:
    meta = OperatorMeta(
        operatorId="vision.analysis.blob",
        displayName="Blob Analysis",
        version="1.2.0",
        inputPorts={
            "image": {"type": "image", "required": True, "nullable": False},
            "mask": {"type": "image", "required": False, "nullable": False},
            "frame": {
                "type": "bbox2d",
                "required": False,
                "nullable": False,
                "schemaVersion": "1.x",
            },
        },
        outputPorts={
            "blobs": {
                "type": "blobCollection",
                "required": True,
                "schemaVersion": "1.x",
            },
            "mask": {"type": "image", "required": True},
            "overlay": {"type": "image", "required": False},
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
        threshold = params.get("threshold", 127)
        connectivity = params.get("connectivity", 8)
        minArea = params.get("minArea", 1)
        maxArea = params.get("maxArea", 0)
        if not _isInt(threshold) or not 0 <= threshold <= 255:
            return _paramError("threshold must be an integer in [0, 255]")
        if not _isInt(connectivity) or connectivity not in (4, 8):
            return _paramError("connectivity must be 4 or 8")
        if not _isInt(minArea) or minArea < 1:
            return _paramError("minArea must be an integer >= 1")
        if not _isInt(maxArea) or maxArea < 0:
            return _paramError("maxArea must be an integer >= 0")
        if maxArea != 0 and maxArea < minArea:
            return _paramError("maxArea must be 0 or >= minArea")
        for name, default in (
            ("invert", False),
            ("useOtsu", False),
            ("includeContour", True),
            ("drawOverlay", False),
        ):
            if not isinstance(params.get(name, default), bool):
                return _paramError(f"{name} must be boolean")
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

        externalMask: np.ndarray[Any, Any] | None = None
        if "mask" in inputs:
            mask = inputs["mask"]
            if not isinstance(mask, np.ndarray):
                return _error("E_INPUT_TYPE", "input 'mask' must be numpy.ndarray")
            if mask.dtype != np.uint8:
                return _error("E_INPUT_TYPE", "input 'mask' must use uint8 pixels")
            if mask.size == 0 or mask.ndim != 2:
                return _error(
                    "E_INPUT_SHAPE",
                    "input 'mask' must be a non-empty single-channel image",
                )
            if mask.shape != image.shape[:2]:
                return _error(
                    "E_INPUT_SHAPE",
                    "input 'mask' must have the same width and height as image",
                )
            externalMask = mask

        paramError = self.validateParams(params)
        if paramError is not None:
            return {"status": "error", "error": paramError}

        threshold = cast(int, params.get("threshold", 127))
        connectivity = cast(int, params.get("connectivity", 8))
        minArea = cast(int, params.get("minArea", 1))
        maxArea = cast(int, params.get("maxArea", 0))
        invert = cast(bool, params.get("invert", False))
        useOtsu = cast(bool, params.get("useOtsu", False))
        includeContour = cast(bool, params.get("includeContour", True))
        drawOverlay = cast(bool, params.get("drawOverlay", False))

        try:
            actualThreshold: float | None
            if externalMask is None:
                gray = (
                    image
                    if image.ndim == 2
                    else cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
                )
                thresholdMode = cv2.THRESH_BINARY_INV if invert else cv2.THRESH_BINARY
                if useOtsu:
                    thresholdMode |= cv2.THRESH_OTSU
                measuredThreshold, binaryMask = cv2.threshold(
                    gray, threshold, 255, thresholdMode
                )
                actualThreshold = float(measuredThreshold)
            else:
                binaryMask = np.where(externalMask != 0, 255, 0).astype(np.uint8)
                actualThreshold = None
            binaryMask = cv2.bitwise_and(
                binaryMask,
                frameMask(frame, width, height),
            )
            componentCount, labels, stats, centroids = cv2.connectedComponentsWithStats(
                binaryMask,
                connectivity=connectivity,
                ltype=cv2.CV_32S,
            )
        except cv2.error as err:
            return _error("E_EXEC_FAILED", f"Blob analysis failed: {err}")

        coordinateSpace = frame.coordinateSpace
        filteredMask = np.zeros_like(binaryMask)
        blobs: list[Blob2D] = []
        overlayContours: list[np.ndarray[Any, Any] | None] = []

        for componentLabel in range(1, componentCount):
            area = int(stats[componentLabel, cv2.CC_STAT_AREA])
            if area < minArea or (maxArea != 0 and area > maxArea):
                continue
            x = int(stats[componentLabel, cv2.CC_STAT_LEFT])
            y = int(stats[componentLabel, cv2.CC_STAT_TOP])
            boxWidth = int(stats[componentLabel, cv2.CC_STAT_WIDTH])
            boxHeight = int(stats[componentLabel, cv2.CC_STAT_HEIGHT])
            centerX, centerY = centroids[componentLabel]
            componentMask = np.where(labels == componentLabel, 255, 0).astype(np.uint8)
            filteredMask[labels == componentLabel] = 255
            contourValue, perimeter, circularity = _componentContour(
                componentMask,
                coordinateSpace,
                float(area),
                includeContour,
            )
            overlayContours.append(
                _largestContour(componentMask) if drawOverlay else None
            )
            blobs.append(
                Blob2D(
                    blobId=f"blob-{componentLabel}",
                    label=componentLabel,
                    area=float(area),
                    centroid=Point2D(float(centerX), float(centerY), coordinateSpace),
                    bbox=BBox2D(
                        float(x),
                        float(y),
                        float(boxWidth),
                        float(boxHeight),
                        coordinateSpace,
                    ),
                    contour=contourValue,
                    attributes={
                        "perimeter": perimeter,
                        "circularity": circularity,
                    },
                )
            )

        outputs: dict[str, object] = {
            "blobs": BlobCollection(tuple(blobs), coordinateSpace).toPayload(),
            "mask": filteredMask,
            "frame": frame.toPayload(),
        }
        if drawOverlay:
            outputs["overlay"] = _drawOverlay(image, blobs, overlayContours)
        elapsedMs = (perf_counter() - startedAt) * 1000.0
        return {
            "status": "ok",
            "outputs": outputs,
            "metrics": {
                "latencyMs": round(elapsedMs, 3),
                "blobCount": len(blobs),
                "foregroundPixels": int(np.count_nonzero(filteredMask)),
                "threshold": actualThreshold,
                "maskSource": "external" if externalMask is not None else "image",
            },
            "diagnostics": {"text": f"Detected {len(blobs)} Blob(s)"},
        }


def _isInt(value: object) -> TypeGuard[int]:
    return isinstance(value, int) and not isinstance(value, bool)


def _paramError(message: str) -> dict[str, str]:
    return {"code": "E_PARAM_INVALID", "message": message}


def _error(code: str, message: str) -> dict[str, Any]:
    return {"status": "error", "error": {"code": code, "message": message}}


def _largestContour(mask: np.ndarray[Any, Any]) -> np.ndarray[Any, Any] | None:
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    return max(contours, key=cv2.contourArea) if contours else None


def _componentContour(
    mask: np.ndarray[Any, Any],
    coordinateSpace: CoordinateSpace2D,
    area: float,
    includeContour: bool,
) -> tuple[Polygon2D | None, float, float]:
    contour = _largestContour(mask)
    if contour is None:
        return None, 0.0, 0.0
    perimeter = float(cv2.arcLength(contour, True))
    circularity = 0.0 if perimeter == 0 else 4.0 * math.pi * area / perimeter**2
    if not includeContour:
        return None, perimeter, circularity
    rawPoints = contour.reshape(-1, 2)
    if len(rawPoints) < 3:
        return None, perimeter, circularity
    polygon = Polygon2D(
        tuple(
            Point2D(float(point[0]), float(point[1]), coordinateSpace)
            for point in rawPoints
        )
    )
    return polygon, perimeter, circularity


def _drawOverlay(
    image: np.ndarray[Any, Any],
    blobs: list[Blob2D],
    contours: list[np.ndarray[Any, Any] | None],
) -> np.ndarray[Any, Any]:
    overlay = (
        image.copy() if image.ndim == 3 else cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
    )
    for blob, contour in zip(blobs, contours, strict=True):
        left = int(round(blob.bbox.x))
        top = int(round(blob.bbox.y))
        right = int(round(blob.bbox.x + blob.bbox.width - 1))
        bottom = int(round(blob.bbox.y + blob.bbox.height - 1))
        cv2.rectangle(overlay, (left, top), (right, bottom), (0, 255, 0), 1)
        cv2.circle(
            overlay,
            (int(round(blob.centroid.x)), int(round(blob.centroid.y))),
            2,
            (0, 0, 255),
            -1,
        )
        if contour is not None:
            cv2.drawContours(overlay, [contour], -1, (255, 0, 0), 1)
    return overlay
