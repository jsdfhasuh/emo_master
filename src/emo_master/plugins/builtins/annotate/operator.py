from __future__ import annotations

import math
from dataclasses import dataclass
from time import perf_counter
from typing import Any, TypeGuard, cast

import cv2
import numpy as np

from emo_master.core.contracts.geometry2d import (
    BlobCollection,
    Circle2D,
    CircleCollection,
    ContourCollection,
    DetectionCollection,
    Geometry2D,
    Line2D,
    LineCollection,
    PayloadValidationError,
    Point2D,
    ShapeMeasurementCollection,
    TemplateMatchCollection,
)
from emo_master.plugins.builtins._collection_ops import (
    CollectionInputError,
    ensureSameCoordinateSpace,
    geometryPoints,
    parseRoi,
)
from emo_master.plugins.builtins._image_frame import frameForInput


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
        "drawBBoxes": {"type": "boolean", "default": True},
        "drawContours": {"type": "boolean", "default": True},
        "drawCentroids": {"type": "boolean", "default": True},
        "drawLabels": {"type": "boolean", "default": True},
        "drawRoi": {"type": "boolean", "default": True},
        "thickness": {"type": "integer", "minimum": 1, "default": 2},
        "fontScale": {"type": "number", "exclusiveMinimum": 0.0, "default": 0.5},
        "blobColor": {
            "type": "array",
            "items": {"type": "integer", "minimum": 0, "maximum": 255},
            "minItems": 3,
            "maxItems": 3,
            "default": [0, 255, 0],
        },
        "detectionColor": {
            "type": "array",
            "items": {"type": "integer", "minimum": 0, "maximum": 255},
            "minItems": 3,
            "maxItems": 3,
            "default": [0, 165, 255],
        },
        "roiColor": {
            "type": "array",
            "items": {"type": "integer", "minimum": 0, "maximum": 255},
            "minItems": 3,
            "maxItems": 3,
            "default": [255, 0, 255],
        },
    },
}


class AnnotateOperator:
    meta = OperatorMeta(
        operatorId="vision.render.annotate",
        displayName="Annotate",
        version="1.1.0",
        inputPorts={
            "image": {"type": "image", "required": True, "nullable": False},
            "frame": {
                "type": "bbox2d",
                "required": False,
                "nullable": False,
                "schemaVersion": "1.x",
            },
            "blobs": {
                "type": "blobCollection",
                "required": False,
                "nullable": False,
                "schemaVersion": "1.x",
            },
            "detections": {
                "type": "detectionCollection",
                "required": False,
                "nullable": False,
                "schemaVersion": "1.x",
            },
            "contours": {"type": "contourCollection", "required": False, "nullable": False, "schemaVersion": "1.2"},
            "measurements": {"type": "shapeMeasurementCollection", "required": False, "nullable": False, "schemaVersion": "1.2"},
            "lines": {"type": "lineCollection", "required": False, "nullable": False, "schemaVersion": "1.2"},
            "circles": {"type": "circleCollection", "required": False, "nullable": False, "schemaVersion": "1.2"},
            "matches": {"type": "templateMatchCollection", "required": False, "nullable": False, "schemaVersion": "1.2"},
            "roi": {
                "type": "geometry2d",
                "required": False,
                "nullable": False,
                "schemaVersion": "1.x",
            },
        },
        outputPorts={
            "overlay": {"type": "image", "required": True, "nullable": False},
            "frame": {
                "type": "bbox2d",
                "required": True,
                "nullable": False,
                "schemaVersion": "1.x",
            },
            "drawnCount": {"type": "integer", "required": True, "nullable": False},
        },
        paramSchema=_PARAM_SCHEMA,
    )

    def validateParams(self, params: dict[str, object]) -> dict[str, str] | None:
        for name in (
            "drawBBoxes",
            "drawContours",
            "drawCentroids",
            "drawLabels",
            "drawRoi",
        ):
            if not isinstance(params.get(name, True), bool):
                return _paramError(f"{name} must be boolean")
        thickness = params.get("thickness", 2)
        if not _isInt(thickness) or thickness < 1:
            return _paramError("thickness must be an integer >= 1")
        fontScale = params.get("fontScale", 0.5)
        if not _isFiniteNumber(fontScale) or float(fontScale) <= 0.0:
            return _paramError("fontScale must be a finite number > 0")
        for name, default in (
            ("blobColor", [0, 255, 0]),
            ("detectionColor", [0, 165, 255]),
            ("roiColor", [255, 0, 255]),
        ):
            if not _isColor(params.get(name, default)):
                return _paramError(f"{name} must contain three integers in [0, 255]")
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
        paramError = self.validateParams(params)
        if paramError is not None:
            return {"status": "error", "error": paramError}
        image = cast(np.ndarray[Any, Any], inputs["image"])
        height, width = image.shape[:2]
        try:
            frame = frameForInput(inputs, width, height)
            blobs = _blobCollection(inputs.get("blobs")) if "blobs" in inputs else None
            detections = (
                _detectionCollection(inputs.get("detections"))
                if "detections" in inputs
                else None
            )
            contours = _typedCollection(inputs, "contours", ContourCollection)
            measurements = _typedCollection(
                inputs, "measurements", ShapeMeasurementCollection
            )
            lines = _typedCollection(inputs, "lines", LineCollection)
            circles = _typedCollection(inputs, "circles", CircleCollection)
            matches = _typedCollection(inputs, "matches", TemplateMatchCollection)
            roi = parseRoi(inputs["roi"]) if "roi" in inputs else None
            for space, name in (
                (None if blobs is None else blobs.coordinateSpace, "Blob collection"),
                (
                    None if detections is None else detections.coordinateSpace,
                    "Detection collection",
                ),
                (None if roi is None else roi.coordinateSpace, "ROI"),
                (None if contours is None else contours.coordinateSpace, "Contour collection"),
                (None if measurements is None else measurements.coordinateSpace, "Measurement collection"),
                (None if lines is None else lines.coordinateSpace, "Line collection"),
                (None if circles is None else circles.coordinateSpace, "Circle collection"),
                (None if matches is None else matches.coordinateSpace, "Template match collection"),
            ):
                if space is not None:
                    ensureSameCoordinateSpace(
                        frame.coordinateSpace,
                        space,
                        message=f"{name} and image frame coordinate spaces do not match",
                    )
        except PayloadValidationError as err:
            return _error("E_INPUT_TYPE", f"invalid semantic input: {err}")
        except CollectionInputError as err:
            return _error(err.code, str(err))
        except ValueError as err:
            return _error("E_INPUT_SHAPE", f"invalid image frame: {err}")

        overlay = image.copy()
        thickness = cast(int, params.get("thickness", 2))
        fontScale = float(cast(int | float, params.get("fontScale", 0.5)))
        blobColor = _outputColor(image, cast(list[int], params.get("blobColor", [0, 255, 0])))
        detectionColor = _outputColor(
            image, cast(list[int], params.get("detectionColor", [0, 165, 255]))
        )
        roiColor = _outputColor(image, cast(list[int], params.get("roiColor", [255, 0, 255])))
        drawnCount = 0

        if blobs is not None:
            for blob in blobs.items:
                if cast(bool, params.get("drawBBoxes", True)):
                    _drawGeometry(overlay, blob.bbox, blobColor, thickness)
                if cast(bool, params.get("drawContours", True)) and blob.contour is not None:
                    _drawGeometry(overlay, blob.contour, blobColor, thickness)
                if cast(bool, params.get("drawCentroids", True)):
                    cv2.circle(
                        overlay,
                        (int(round(blob.centroid.x)), int(round(blob.centroid.y))),
                        max(2, thickness + 1),
                        blobColor,  # type: ignore[arg-type]
                        -1,
                    )
                if cast(bool, params.get("drawLabels", True)):
                    _drawText(
                        overlay,
                        f"{blob.blobId} area={blob.area:g}",
                        blob.bbox.x,
                        blob.bbox.y,
                        blobColor,
                        fontScale,
                        thickness,
                    )
                drawnCount += 1
        if detections is not None:
            for detection in detections.items:
                geometry = (
                    detection.geometry
                    if detection.geometry is not None
                    else detection.bbox
                )
                if cast(bool, params.get("drawBBoxes", True)):
                    _drawGeometry(overlay, geometry, detectionColor, thickness)
                if cast(bool, params.get("drawLabels", True)):
                    _drawText(
                        overlay,
                        f"{detection.label} {detection.confidence:.2f}",
                        detection.bbox.x,
                        detection.bbox.y,
                        detectionColor,
                        fontScale,
                        thickness,
                    )
                drawnCount += 1
        if contours is not None:
            for contour in contours.items:
                _drawGeometry(overlay, contour.polygon, detectionColor, thickness)
                if cast(bool, params.get("drawLabels", True)):
                    boxPoints = geometryPoints(contour.polygon)
                    _drawText(
                        overlay,
                        contour.contourId,
                        min(point[0] for point in boxPoints),
                        min(point[1] for point in boxPoints),
                        detectionColor,
                        fontScale,
                        thickness,
                    )
                drawnCount += 1
        if measurements is not None:
            for measurement in measurements.items:
                _drawGeometry(
                    overlay, measurement.minAreaRect, detectionColor, thickness
                )
                cv2.circle(
                    overlay,
                    (
                        int(round(measurement.centroid.x)),
                        int(round(measurement.centroid.y)),
                    ),
                    max(2, thickness + 1),
                    detectionColor,  # type: ignore[arg-type]
                    -1,
                )
                if cast(bool, params.get("drawLabels", True)):
                    _drawText(
                        overlay,
                        f"A={measurement.area:g} P={measurement.perimeter:g} C={measurement.circularity:.3f}",
                        measurement.bbox.x,
                        measurement.bbox.y,
                        detectionColor,
                        fontScale,
                        thickness,
                    )
                drawnCount += 1
        if lines is not None:
            for item in lines.items:
                _drawGeometry(overlay, item.line, detectionColor, thickness)
                drawnCount += 1
        if circles is not None:
            for item in circles.items:
                _drawGeometry(overlay, item.circle, detectionColor, thickness)
                drawnCount += 1
        if matches is not None:
            for match in matches.items:
                _drawGeometry(overlay, match.bbox, detectionColor, thickness)
                if cast(bool, params.get("drawLabels", True)):
                    _drawText(
                        overlay,
                        f"{matches.label} {match.quality:.2f}",
                        match.bbox.x,
                        match.bbox.y,
                        detectionColor,
                        fontScale,
                        thickness,
                    )
                drawnCount += 1
        if roi is not None and cast(bool, params.get("drawRoi", True)):
            _drawGeometry(overlay, roi, roiColor, thickness)
            drawnCount += 1

        return {
            "status": "ok",
            "outputs": {
                "overlay": overlay,
                "frame": frame.toPayload(),
                "drawnCount": drawnCount,
            },
            "metrics": {
                "latencyMs": round((perf_counter() - startedAt) * 1000.0, 3),
                "drawnCount": drawnCount,
            },
            "diagnostics": {"text": f"Annotated {drawnCount} items"},
        }


def _blobCollection(value: object) -> BlobCollection:
    return value if isinstance(value, BlobCollection) else BlobCollection.fromPayload(value)


def _detectionCollection(value: object) -> DetectionCollection:
    return (
        value
        if isinstance(value, DetectionCollection)
        else DetectionCollection.fromPayload(value)
    )


def _typedCollection(inputs: dict[str, object], name: str, collectionType: type) -> Any:
    if name not in inputs:
        return None
    value = inputs[name]
    return (
        value
        if isinstance(value, collectionType)
        else cast(Any, collectionType).fromPayload(value)
    )


def _drawGeometry(
    image: np.ndarray[Any, Any],
    geometry: Geometry2D,
    color: int | tuple[int, int, int],
    thickness: int,
) -> None:
    if isinstance(geometry, Point2D):
        cv2.circle(
            image,
            (int(round(geometry.x)), int(round(geometry.y))),
            max(2, thickness + 1),
            color,  # type: ignore[arg-type]
            -1,
        )
        return
    if isinstance(geometry, Line2D):
        cv2.line(
            image,
            (int(round(geometry.start.x)), int(round(geometry.start.y))),
            (int(round(geometry.end.x)), int(round(geometry.end.y))),
            color,  # type: ignore[arg-type]
            thickness,
            cv2.LINE_AA,
        )
        return
    if isinstance(geometry, Circle2D):
        cv2.circle(
            image,
            (int(round(geometry.center.x)), int(round(geometry.center.y))),
            max(1, int(round(geometry.radius))),
            color,  # type: ignore[arg-type]
            thickness,
            cv2.LINE_AA,
        )
        return
    points = np.rint(np.asarray(geometryPoints(geometry))).astype(np.int32)
    cv2.polylines(
        image,
        [points],
        True,
        color,  # type: ignore[arg-type]
        thickness,
        lineType=cv2.LINE_AA,
    )


def _drawText(
    image: np.ndarray[Any, Any],
    text: str,
    x: float,
    y: float,
    color: int | tuple[int, int, int],
    fontScale: float,
    thickness: int,
) -> None:
    cv2.putText(
        image,
        text,
        (max(0, int(round(x))), max(10, int(round(y)) - 4)),
        cv2.FONT_HERSHEY_SIMPLEX,
        fontScale,
        color,  # type: ignore[arg-type]
        max(1, thickness),
        cv2.LINE_AA,
    )


def _outputColor(
    image: np.ndarray[Any, Any], value: list[int]
) -> int | tuple[int, int, int]:
    return max(value) if image.ndim == 2 else (value[0], value[1], value[2])


def _isColor(value: object) -> bool:
    return (
        isinstance(value, list)
        and len(value) == 3
        and all(_isInt(item) and 0 <= item <= 255 for item in value)
    )


def _validateImage(inputs: dict[str, object]) -> dict[str, Any] | None:
    if "image" not in inputs:
        return _error("E_INPUT_MISSING", "input 'image' is required")
    image = inputs["image"]
    if not isinstance(image, np.ndarray) or image.dtype != np.uint8:
        return _error("E_INPUT_TYPE", "image must be a uint8 numpy.ndarray")
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
