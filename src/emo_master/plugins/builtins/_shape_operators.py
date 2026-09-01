from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from time import perf_counter
from typing import Any, cast

import cv2
import numpy as np

from emo_master.core.contracts.geometry2d import (
    BBox2D,
    BlobCollection,
    ContourCollection,
    ContourItem,
    PayloadValidationError,
    Point2D,
    Polygon2D,
    RotatedBox2D,
    ShapeMeasurement,
    ShapeMeasurementCollection,
)
from emo_master.plugins.builtins._classic_vision import (
    BuiltinInputError,
    errorResult,
    requireImage,
)
from emo_master.plugins.builtins._image_frame import frameForInput, frameMask


@dataclass(frozen=True)
class OperatorMeta:
    operatorId: str
    displayName: str
    version: str
    inputPorts: Mapping[str, object]
    outputPorts: Mapping[str, object]
    paramSchema: Mapping[str, object]


class ContourExtractionOperator:
    _PARAM_SCHEMA = {
        "type": "object",
        "properties": {
            "retrievalMode": {
                "type": "string",
                "enum": ["external", "list", "ccomp", "tree"],
                "default": "tree",
            },
            "approximation": {
                "type": "string",
                "enum": ["none", "simple"],
                "default": "simple",
            },
        },
    }
    meta = OperatorMeta(
        operatorId="vision.analysis.contour",
        displayName="Contour Extraction",
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
            "polygons": {
                "type": "list<polygon2d>",
                "required": True,
                "nullable": False,
                "schemaVersion": "1.x",
            },
            "contours": {
                "type": "contourCollection",
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
        if params.get("retrievalMode", "tree") not in {
            "external",
            "list",
            "ccomp",
            "tree",
        }:
            return _paramError("retrievalMode must be external, list, ccomp, or tree")
        if params.get("approximation", "simple") not in {"none", "simple"}:
            return _paramError("approximation must be none or simple")
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
            mask = requireImage(inputs, "mask", grayscaleOnly=True)
            height, width = mask.shape
            frame = frameForInput(inputs, width, height)
            effectiveMask: np.ndarray[Any, Any] = np.where(
                mask != 0, 255, 0
            ).astype(np.uint8)
            effectiveMask = cv2.bitwise_and(
                effectiveMask, frameMask(frame, width, height)
            )
            retrieval = {
                "external": cv2.RETR_EXTERNAL,
                "list": cv2.RETR_LIST,
                "ccomp": cv2.RETR_CCOMP,
                "tree": cv2.RETR_TREE,
            }[cast(str, params.get("retrievalMode", "tree"))]
            approximation = {
                "none": cv2.CHAIN_APPROX_NONE,
                "simple": cv2.CHAIN_APPROX_SIMPLE,
            }[cast(str, params.get("approximation", "simple"))]
            rawContours, rawHierarchy = cv2.findContours(
                effectiveMask.copy(), retrieval, approximation
            )
            hierarchy = (
                np.empty((0, 4), dtype=np.int32)
                if rawHierarchy is None
                else rawHierarchy[0]
            )
            kept: list[tuple[int, Polygon2D]] = []
            dropped = 0
            for rawIndex, contour in enumerate(rawContours):
                points = contour.reshape(-1, 2)
                unique = np.unique(points, axis=0)
                if len(unique) < 3 or abs(float(cv2.contourArea(contour))) <= 1e-12:
                    dropped += 1
                    continue
                polygon = Polygon2D(
                    tuple(
                        Point2D(float(x), float(y), frame.coordinateSpace)
                        for x, y in points
                    )
                )
                kept.append((rawIndex, polygon))
            ids = {
                rawIndex: f"contour-{index + 1}"
                for index, (rawIndex, _) in enumerate(kept)
            }
            items: list[ContourItem] = []
            for rawIndex, polygon in kept:
                links = hierarchy[rawIndex] if rawIndex < len(hierarchy) else [-1] * 4
                nextIndex, previousIndex, childIndex, parentIndex = (
                    int(value) for value in links
                )
                depth = _hierarchyDepth(hierarchy, rawIndex)
                items.append(
                    ContourItem(
                        contourId=ids[rawIndex],
                        polygon=polygon,
                        parentId=ids.get(parentIndex),
                        firstChildId=ids.get(childIndex),
                        previousSiblingId=ids.get(previousIndex),
                        nextSiblingId=ids.get(nextIndex),
                        depth=depth,
                        isHole=depth % 2 == 1,
                    )
                )
            collection = ContourCollection(tuple(items), frame.coordinateSpace)
            return {
                "status": "ok",
                "outputs": {
                    "polygons": [polygon.toPayload() for _, polygon in kept],
                    "contours": collection.toPayload(),
                    "frame": frame.toPayload(),
                },
                "metrics": {
                    "latencyMs": round((perf_counter() - started) * 1000.0, 3),
                    "contourCount": len(items),
                    "droppedDegenerateCount": dropped,
                },
                "diagnostics": {
                    "text": f"Extracted {len(items)} contours; dropped {dropped} degenerate contours"
                },
            }
        except BuiltinInputError as err:
            return errorResult(err.code, str(err))
        except PayloadValidationError as err:
            return errorResult("E_INPUT_TYPE", f"invalid frame or contour: {err}")
        except ValueError as err:
            return errorResult("E_INPUT_SHAPE", str(err))
        except cv2.error as err:
            return errorResult("E_EXEC_FAILED", f"contour extraction failed: {err}")


class ShapeMeasurementOperator:
    _PARAM_SCHEMA = {"type": "object", "properties": {}}
    meta = OperatorMeta(
        operatorId="vision.analysis.shape_measurement",
        displayName="Shape Measurement",
        version="1.0.0",
        inputPorts={
            "contours": {
                "type": "contourCollection",
                "required": False,
                "nullable": False,
                "schemaVersion": "1.2",
            },
            "blobs": {
                "type": "blobCollection",
                "required": False,
                "nullable": False,
                "schemaVersion": "1.x",
            },
        },
        outputPorts={
            "measurements": {
                "type": "shapeMeasurementCollection",
                "required": True,
                "nullable": False,
                "schemaVersion": "1.2",
            }
        },
        paramSchema=_PARAM_SCHEMA,
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
        started = perf_counter()
        present = [name for name in ("contours", "blobs") if name in inputs]
        if not present:
            return errorResult(
                "E_INPUT_MISSING", "exactly one of contours or blobs is required"
            )
        if len(present) != 1:
            return errorResult(
                "E_INPUT_SHAPE", "contours and blobs cannot be connected together"
            )
        try:
            sources: list[tuple[str, str, Polygon2D]] = []
            if present[0] == "contours":
                raw = inputs["contours"]
                collection = (
                    raw
                    if isinstance(raw, ContourCollection)
                    else ContourCollection.fromPayload(raw)
                )
                space = collection.coordinateSpace
                sources = [
                    (item.contourId, "contour", item.polygon)
                    for item in collection.items
                ]
            else:
                raw = inputs["blobs"]
                blobs = (
                    raw
                    if isinstance(raw, BlobCollection)
                    else BlobCollection.fromPayload(raw)
                )
                space = blobs.coordinateSpace
                for blob in blobs.items:
                    if blob.contour is None:
                        raise BuiltinInputError(
                            "E_INPUT_SHAPE",
                            f"blob '{blob.blobId}' does not contain a contour",
                        )
                    sources.append((blob.blobId, "blob", blob.contour))
            measurements = tuple(
                _measure(index, sourceId, sourceType, polygon)
                for index, (sourceId, sourceType, polygon) in enumerate(sources)
            )
            result = ShapeMeasurementCollection(measurements, space)
            return {
                "status": "ok",
                "outputs": {"measurements": result.toPayload()},
                "metrics": {
                    "latencyMs": round((perf_counter() - started) * 1000.0, 3),
                    "measurementCount": len(measurements),
                },
                "diagnostics": {
                    "text": f"Measured {len(measurements)} shapes"
                },
            }
        except BuiltinInputError as err:
            return errorResult(err.code, str(err))
        except PayloadValidationError as err:
            return errorResult("E_INPUT_TYPE", f"invalid collection payload: {err}")
        except cv2.error as err:
            return errorResult("E_EXEC_FAILED", f"shape measurement failed: {err}")


def _measure(
    index: int,
    sourceId: str,
    sourceType: str,
    polygon: Polygon2D,
) -> ShapeMeasurement:
    contour = np.asarray(
        [(point.x, point.y) for point in polygon.points], dtype=np.float32
    ).reshape(-1, 1, 2)
    area = abs(float(cv2.contourArea(contour)))
    perimeter = float(cv2.arcLength(contour, True))
    moments = cv2.moments(contour)
    if area <= 1e-12 or perimeter <= 1e-12 or abs(moments["m00"]) <= 1e-12:
        raise BuiltinInputError("E_INPUT_SHAPE", f"shape '{sourceId}' is degenerate")
    centroid = Point2D(
        moments["m10"] / moments["m00"],
        moments["m01"] / moments["m00"],
        polygon.coordinateSpace,
    )
    points = contour.reshape(-1, 2)
    minimum = points.min(axis=0)
    maximum = points.max(axis=0)
    bbox = BBox2D(
        float(minimum[0]),
        float(minimum[1]),
        float(maximum[0] - minimum[0]),
        float(maximum[1] - minimum[1]),
        polygon.coordinateSpace,
    )
    (centerX, centerY), (rectWidth, rectHeight), angle = cv2.minAreaRect(contour)
    if rectWidth < rectHeight:
        rectWidth, rectHeight = rectHeight, rectWidth
        angle += 90.0
    while angle >= 90.0:
        angle -= 180.0
    while angle < -90.0:
        angle += 180.0
    minAreaRect = RotatedBox2D(
        float(centerX),
        float(centerY),
        float(rectWidth),
        float(rectHeight),
        float(angle),
        polygon.coordinateSpace,
    )
    circularity = max(0.0, min(1.0, 4.0 * math.pi * area / (perimeter * perimeter)))
    return ShapeMeasurement(
        measurementId=f"measurement-{index + 1}",
        sourceId=sourceId,
        sourceType=sourceType,
        area=area,
        perimeter=perimeter,
        centroid=centroid,
        bbox=bbox,
        minAreaRect=minAreaRect,
        circularity=circularity,
    )


def _hierarchyDepth(hierarchy: np.ndarray[Any, Any], index: int) -> int:
    depth = 0
    seen: set[int] = set()
    parent = int(hierarchy[index][3]) if index < len(hierarchy) else -1
    while parent >= 0 and parent not in seen and parent < len(hierarchy):
        seen.add(parent)
        depth += 1
        parent = int(hierarchy[parent][3])
    return depth


def _paramError(message: str) -> dict[str, str]:
    return {"code": "E_PARAM_INVALID", "message": message}
