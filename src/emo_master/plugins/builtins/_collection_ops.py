from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal, Sequence, TypeGuard, cast

import cv2
import numpy as np

from emo_master.core.contracts.geometry2d import (
    BBox2D,
    Blob2D,
    BlobCollection,
    Circle2D,
    CircleCollection,
    CircleItem,
    ContourCollection,
    ContourItem,
    CoordinateSpace2D,
    Detection2D,
    DetectionCollection,
    Geometry2D,
    Line2D,
    LineCollection,
    LineItem,
    PayloadValidationError,
    Point2D,
    Polygon2D,
    RotatedBox2D,
    ShapeMeasurement,
    ShapeMeasurementCollection,
    TemplateMatch,
    TemplateMatchCollection,
    parseGeometry2D,
)
from emo_master.plugins.builtins._image_frame import coordinateSpacesEquivalent


CollectionKind = Literal[
    "blob", "detection", "contour", "measurement", "line", "circle", "match"
]
CollectionItem = (
    Blob2D
    | Detection2D
    | ContourItem
    | ShapeMeasurement
    | LineItem
    | CircleItem
    | TemplateMatch
)
CollectionValue = (
    BlobCollection
    | DetectionCollection
    | ContourCollection
    | ShapeMeasurementCollection
    | LineCollection
    | CircleCollection
    | TemplateMatchCollection
)

COLLECTION_INPUT_TYPES: dict[str, tuple[CollectionKind, type[CollectionValue]]] = {
    "blobs": ("blob", BlobCollection),
    "detections": ("detection", DetectionCollection),
    "contours": ("contour", ContourCollection),
    "measurements": ("measurement", ShapeMeasurementCollection),
    "lines": ("line", LineCollection),
    "circles": ("circle", CircleCollection),
    "matches": ("match", TemplateMatchCollection),
}

COLLECTION_PORT_NAMES: dict[CollectionKind, str] = {
    "blob": "Blobs",
    "detection": "Detections",
    "contour": "Contours",
    "measurement": "Measurements",
    "line": "Lines",
    "circle": "Circles",
    "match": "Matches",
}


class CollectionInputError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ParsedCollection:
    kind: CollectionKind
    items: tuple[CollectionItem, ...]
    coordinateSpace: CoordinateSpace2D
    source: CollectionValue

    def rebuild(self, items: Sequence[CollectionItem]) -> CollectionValue:
        if self.kind == "blob":
            return BlobCollection(tuple(cast(Sequence[Blob2D], items)), self.coordinateSpace)
        if self.kind == "detection":
            return DetectionCollection(
                tuple(cast(Sequence[Detection2D], items)), self.coordinateSpace
            )
        if self.kind == "contour":
            return ContourCollection(
                tuple(cast(Sequence[ContourItem], items)), self.coordinateSpace
            )
        if self.kind == "measurement":
            return ShapeMeasurementCollection(
                tuple(cast(Sequence[ShapeMeasurement], items)), self.coordinateSpace
            )
        if self.kind == "line":
            return LineCollection(
                tuple(cast(Sequence[LineItem], items)), self.coordinateSpace
            )
        if self.kind == "circle":
            return CircleCollection(
                tuple(cast(Sequence[CircleItem], items)), self.coordinateSpace
            )
        original = cast(TemplateMatchCollection, self.source)
        return TemplateMatchCollection(
            method=original.method,
            label=original.label,
            templateWidth=original.templateWidth,
            templateHeight=original.templateHeight,
            items=tuple(cast(Sequence[TemplateMatch], items)),
            coordinateSpace=self.coordinateSpace,
        )

    def payload(
        self, items: Sequence[CollectionItem] | None = None
    ) -> dict[str, object]:
        return self.rebuild(self.items if items is None else items).toPayload()

    def outputName(self, prefix: str) -> str:
        return f"{prefix}{COLLECTION_PORT_NAMES[self.kind]}"


def parseCollectionInputs(inputs: dict[str, object]) -> ParsedCollection:
    present = [name for name in COLLECTION_INPUT_TYPES if name in inputs]
    if not present:
        raise CollectionInputError("E_INPUT_MISSING", "exactly one collection input is required")
    if len(present) != 1:
        raise CollectionInputError(
            "E_INPUT_SHAPE", "collection inputs cannot be connected together"
        )
    portName = present[0]
    kind, collectionType = COLLECTION_INPUT_TYPES[portName]
    try:
        raw = inputs[portName]
        collection = (
            raw
            if isinstance(raw, collectionType)
            else collectionType.fromPayload(raw)  # type: ignore[attr-defined]
        )
        value = cast(CollectionValue, collection)
        return ParsedCollection(
            kind,
            tuple(cast(Sequence[CollectionItem], value.items)),
            value.coordinateSpace,
            value,
        )
    except PayloadValidationError as err:
        raise CollectionInputError(
            "E_INPUT_TYPE", f"invalid collection payload: {err}"
        ) from err


def parseRoi(value: object) -> Geometry2D:
    try:
        return value if _isGeometry(value) else parseGeometry2D(value)
    except PayloadValidationError as err:
        raise CollectionInputError("E_INPUT_TYPE", f"invalid ROI payload: {err}") from err


def ensureSameCoordinateSpace(
    first: CoordinateSpace2D,
    second: CoordinateSpace2D,
    *,
    message: str = "coordinate spaces do not match",
) -> None:
    if not coordinateSpacesEquivalent(first, second):
        raise CollectionInputError("E_INPUT_SHAPE", message)


def axisAlignedBBox(geometry: Geometry2D) -> BBox2D:
    if isinstance(geometry, Point2D):
        return BBox2D(geometry.x, geometry.y, 0.0, 0.0, geometry.coordinateSpace)
    if isinstance(geometry, BBox2D):
        return geometry
    if isinstance(geometry, Circle2D):
        return BBox2D(
            geometry.center.x - geometry.radius,
            geometry.center.y - geometry.radius,
            geometry.radius * 2.0,
            geometry.radius * 2.0,
            geometry.coordinateSpace,
        )
    points = geometryPoints(geometry)
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    minimumX = min(xs)
    minimumY = min(ys)
    return BBox2D(
        minimumX,
        minimumY,
        max(xs) - minimumX,
        max(ys) - minimumY,
        geometry.coordinateSpace,
    )


def geometryPoints(geometry: Geometry2D) -> tuple[tuple[float, float], ...]:
    if isinstance(geometry, Point2D):
        return ((geometry.x, geometry.y),)
    if isinstance(geometry, BBox2D):
        return (
            (geometry.x, geometry.y),
            (geometry.x + geometry.width, geometry.y),
            (geometry.x + geometry.width, geometry.y + geometry.height),
            (geometry.x, geometry.y + geometry.height),
        )
    if isinstance(geometry, Polygon2D):
        return tuple((point.x, point.y) for point in geometry.points)
    if isinstance(geometry, Line2D):
        return ((geometry.start.x, geometry.start.y), (geometry.end.x, geometry.end.y))
    if isinstance(geometry, Circle2D):
        return tuple(
            (
                geometry.center.x + geometry.radius * math.cos(math.radians(angle)),
                geometry.center.y + geometry.radius * math.sin(math.radians(angle)),
            )
            for angle in (0.0, 90.0, 180.0, 270.0)
        )
    radians = math.radians(geometry.angleDegrees)
    cosine = math.cos(radians)
    sine = math.sin(radians)
    halfWidth = geometry.width / 2.0
    halfHeight = geometry.height / 2.0
    return tuple(
        (
            geometry.centerX + dx * cosine - dy * sine,
            geometry.centerY + dx * sine + dy * cosine,
        )
        for dx, dy in (
            (-halfWidth, -halfHeight),
            (halfWidth, -halfHeight),
            (halfWidth, halfHeight),
            (-halfWidth, halfHeight),
        )
    )


def pointInGeometry(x: float, y: float, geometry: Geometry2D) -> bool:
    if isinstance(geometry, Point2D):
        return math.isclose(x, geometry.x) and math.isclose(y, geometry.y)
    if isinstance(geometry, BBox2D):
        return (
            geometry.x <= x <= geometry.x + geometry.width
            and geometry.y <= y <= geometry.y + geometry.height
        )
    if isinstance(geometry, Circle2D):
        return math.hypot(x - geometry.center.x, y - geometry.center.y) <= geometry.radius
    if isinstance(geometry, Line2D):
        return _distanceToSegment(
            x, y, geometry.start.x, geometry.start.y, geometry.end.x, geometry.end.y
        ) <= 1e-6
    if isinstance(geometry, RotatedBox2D):
        radians = math.radians(-geometry.angleDegrees)
        cosine = math.cos(radians)
        sine = math.sin(radians)
        dx = x - geometry.centerX
        dy = y - geometry.centerY
        localX = dx * cosine - dy * sine
        localY = dx * sine + dy * cosine
        return (
            abs(localX) <= geometry.width / 2.0
            and abs(localY) <= geometry.height / 2.0
        )
    contour = np.asarray(geometryPoints(geometry), dtype=np.float32)
    return cv2.pointPolygonTest(contour, (float(x), float(y)), False) >= 0


def bboxesIntersect(first: BBox2D, second: BBox2D) -> bool:
    return (
        max(first.x, second.x) <= min(first.x + first.width, second.x + second.width)
        and max(first.y, second.y)
        <= min(first.y + first.height, second.y + second.height)
    )


def bboxContained(inner: BBox2D, outer: BBox2D) -> bool:
    return (
        inner.x >= outer.x
        and inner.y >= outer.y
        and inner.x + inner.width <= outer.x + outer.width
        and inner.y + inner.height <= outer.y + outer.height
    )


def itemBBox(item: CollectionItem) -> BBox2D:
    if isinstance(item, (Blob2D, Detection2D, ShapeMeasurement, TemplateMatch)):
        return item.bbox
    if isinstance(item, ContourItem):
        return axisAlignedBBox(item.polygon)
    if isinstance(item, LineItem):
        return axisAlignedBBox(item.line)
    return axisAlignedBBox(cast(CircleItem, item).circle)


def itemCenter(item: CollectionItem) -> tuple[float, float]:
    if isinstance(item, Blob2D):
        return item.centroid.x, item.centroid.y
    if isinstance(item, ShapeMeasurement):
        return item.centroid.x, item.centroid.y
    if isinstance(item, LineItem):
        return (
            (item.line.start.x + item.line.end.x) / 2.0,
            (item.line.start.y + item.line.end.y) / 2.0,
        )
    if isinstance(item, CircleItem):
        return item.circle.center.x, item.circle.center.y
    box = itemBBox(item)
    return box.x + box.width / 2.0, box.y + box.height / 2.0


def itemField(item: CollectionItem, key: str) -> tuple[bool, float | int | str]:
    if key == "id":
        return False, itemId(item)
    box = itemBBox(item)
    bboxFields = {
        "bboxX": box.x,
        "bboxY": box.y,
        "bboxWidth": box.width,
        "bboxHeight": box.height,
    }
    if key in bboxFields:
        return False, bboxFields[key]
    if isinstance(item, Blob2D):
        if key == "area":
            return False, item.area
        if key == "label":
            return item.label is None, -1 if item.label is None else item.label
        if key == "centroidX":
            return False, item.centroid.x
        if key == "centroidY":
            return False, item.centroid.y
        if key == "circularity":
            value = item.attributes.get("circularity")
            return (True, 0.0) if not _finiteNumber(value) else (False, float(value))
    elif isinstance(item, Detection2D):
        fields: dict[str, float | int | str] = {
            "confidence": item.confidence,
            "classId": item.classId,
            "label": item.label,
        }
        if key in fields:
            return False, fields[key]
    elif isinstance(item, ContourItem):
        if key in {"area", "perimeter"}:
            contour = _contourArray(item.polygon)
            value = (
                abs(float(cv2.contourArea(contour)))
                if key == "area"
                else float(cv2.arcLength(contour, True))
            )
            return False, value
        if key == "depth":
            return False, item.depth
        if key == "isHole":
            return False, int(item.isHole)
    elif isinstance(item, ShapeMeasurement):
        fields2: dict[str, float | str] = {
            "area": item.area,
            "perimeter": item.perimeter,
            "circularity": item.circularity,
            "centroidX": item.centroid.x,
            "centroidY": item.centroid.y,
            "minRectWidth": item.minAreaRect.width,
            "minRectHeight": item.minAreaRect.height,
            "minRectAngle": item.minAreaRect.angleDegrees,
            "sourceId": item.sourceId,
            "sourceType": item.sourceType,
        }
        if key in fields2:
            return False, fields2[key]
    elif isinstance(item, LineItem):
        line = item.line
        fields3 = {
            "length": math.hypot(line.end.x - line.start.x, line.end.y - line.start.y),
            "angle": math.degrees(
                math.atan2(line.end.y - line.start.y, line.end.x - line.start.x)
            ),
            "startX": line.start.x,
            "startY": line.start.y,
            "endX": line.end.x,
            "endY": line.end.y,
        }
        if key in fields3:
            return False, fields3[key]
    elif isinstance(item, CircleItem):
        circle = item.circle
        fields4 = {
            "radius": circle.radius,
            "diameter": circle.radius * 2.0,
            "area": math.pi * circle.radius * circle.radius,
            "centerX": circle.center.x,
            "centerY": circle.center.y,
        }
        if key in fields4:
            return False, fields4[key]
    else:
        match = cast(TemplateMatch, item)
        if key == "quality":
            return False, match.quality
        if key == "rawScore":
            return False, match.rawScore
    raise KeyError(key)


def itemId(item: CollectionItem) -> str:
    if isinstance(item, Blob2D):
        return item.blobId
    if isinstance(item, Detection2D):
        return item.detectionId
    if isinstance(item, ContourItem):
        return item.contourId
    if isinstance(item, ShapeMeasurement):
        return item.measurementId
    if isinstance(item, LineItem):
        return item.lineId
    if isinstance(item, CircleItem):
        return item.circleId
    return cast(TemplateMatch, item).matchId


def geometryType(geometry: Geometry2D) -> str:
    if isinstance(geometry, BBox2D):
        return "bbox2d"
    if isinstance(geometry, RotatedBox2D):
        return "rotatedBox2d"
    if isinstance(geometry, Polygon2D):
        return "polygon2d"
    if isinstance(geometry, Line2D):
        return "line2d"
    if isinstance(geometry, Circle2D):
        return "circle2d"
    return "point2d"


def _distanceToSegment(
    x: float, y: float, x1: float, y1: float, x2: float, y2: float
) -> float:
    dx = x2 - x1
    dy = y2 - y1
    denominator = dx * dx + dy * dy
    if denominator <= 1e-24:
        return math.hypot(x - x1, y - y1)
    position = max(0.0, min(1.0, ((x - x1) * dx + (y - y1) * dy) / denominator))
    return math.hypot(x - (x1 + position * dx), y - (y1 + position * dy))


def _contourArray(polygon: Polygon2D) -> np.ndarray:
    return np.asarray(
        [(point.x, point.y) for point in polygon.points], dtype=np.float32
    ).reshape(-1, 1, 2)


def _finiteNumber(value: object) -> TypeGuard[int | float]:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def _isGeometry(value: object) -> TypeGuard[Geometry2D]:
    return isinstance(
        value, (Point2D, BBox2D, RotatedBox2D, Polygon2D, Line2D, Circle2D)
    )
