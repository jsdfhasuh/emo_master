from __future__ import annotations

import math
from dataclasses import dataclass
from time import perf_counter
from typing import Any, TypeGuard, cast

from emo_master.core.contracts.geometry2d import (
    Blob2D,
    CircleItem,
    ContourItem,
    Detection2D,
    LineItem,
    ShapeMeasurement,
    TemplateMatch,
    TemplateMatchCollection,
)
from emo_master.plugins.builtins._collection_ops import (
    CollectionInputError,
    CollectionItem,
    ParsedCollection,
    axisAlignedBBox,
    bboxContained,
    bboxesIntersect,
    ensureSameCoordinateSpace,
    geometryType,
    itemBBox,
    itemCenter,
    itemField,
    parseCollectionInputs,
    parseRoi,
    pointInGeometry,
)


@dataclass(frozen=True)
class OperatorMeta:
    operatorId: str
    displayName: str
    version: str
    inputPorts: dict[str, object]
    outputPorts: dict[str, object]
    paramSchema: dict[str, object]


_METHODS = [
    "sqdiff",
    "sqdiffNormed",
    "ccorr",
    "ccorrNormed",
    "ccoeff",
    "ccoeffNormed",
]
_SPATIAL_MODES = {"none", "centerInside", "bboxIntersects", "bboxContained"}
_GEOMETRY_TYPES = {"bbox2d", "rotatedBox2d", "polygon2d"}
_PARAM_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "minArea": {"type": "number", "minimum": 0.0, "default": 0.0},
        "maxArea": {"type": "number", "minimum": 0.0, "default": 0.0},
        "minPerimeter": {"type": "number", "minimum": 0.0, "default": 0.0},
        "maxPerimeter": {"type": "number", "minimum": 0.0, "default": 0.0},
        "circularityEnabled": {"type": "boolean", "default": False},
        "minCircularity": {"type": "number", "minimum": 0.0, "maximum": 1.0, "default": 0.0},
        "maxCircularity": {"type": "number", "minimum": 0.0, "maximum": 1.0, "default": 1.0},
        "blobLabels": {"type": "array", "items": {"type": "integer", "minimum": 0}, "default": []},
        "contourMode": {"type": "string", "enum": ["any", "present", "absent"], "default": "any"},
        "minConfidence": {"type": "number", "minimum": 0.0, "maximum": 1.0, "default": 0.0},
        "maxConfidence": {"type": "number", "minimum": 0.0, "maximum": 1.0, "default": 1.0},
        "classIds": {"type": "array", "items": {"type": "integer", "minimum": 0}, "default": []},
        "detectionLabels": {"type": "array", "items": {"type": "string"}, "default": []},
        "geometryTypes": {"type": "array", "items": {"type": "string", "enum": sorted(_GEOMETRY_TYPES)}, "default": []},
        "holeMode": {"type": "string", "enum": ["any", "outer", "hole"], "default": "any"},
        "sourceTypes": {"type": "array", "items": {"type": "string", "enum": ["contour", "blob"]}, "default": []},
        "minLength": {"type": "number", "minimum": 0.0, "default": 0.0},
        "maxLength": {"type": "number", "minimum": 0.0, "default": 0.0},
        "minRadius": {"type": "number", "minimum": 0.0, "default": 0.0},
        "maxRadius": {"type": "number", "minimum": 0.0, "default": 0.0},
        "minQuality": {"type": "number", "minimum": 0.0, "maximum": 1.0, "default": 0.0},
        "maxQuality": {"type": "number", "minimum": 0.0, "maximum": 1.0, "default": 1.0},
        "templateMethods": {"type": "array", "items": {"type": "string", "enum": _METHODS}, "default": []},
        "spatialMode": {"type": "string", "enum": sorted(_SPATIAL_MODES), "default": "none"},
    },
}


def _collectionInputs() -> dict[str, object]:
    return {
        "blobs": {"type": "blobCollection", "required": False, "nullable": False, "schemaVersion": "1.x"},
        "detections": {"type": "detectionCollection", "required": False, "nullable": False, "schemaVersion": "1.x"},
        "contours": {"type": "contourCollection", "required": False, "nullable": False, "schemaVersion": "1.2"},
        "measurements": {"type": "shapeMeasurementCollection", "required": False, "nullable": False, "schemaVersion": "1.2"},
        "lines": {"type": "lineCollection", "required": False, "nullable": False, "schemaVersion": "1.2"},
        "circles": {"type": "circleCollection", "required": False, "nullable": False, "schemaVersion": "1.2"},
        "matches": {"type": "templateMatchCollection", "required": False, "nullable": False, "schemaVersion": "1.2"},
        "roi": {"type": "geometry2d", "required": False, "nullable": False, "schemaVersion": "1.x"},
    }


def _collectionOutputs() -> dict[str, object]:
    result: dict[str, object] = {
        "keptCount": {"type": "integer", "required": True, "nullable": False},
        "rejectedCount": {"type": "integer", "required": True, "nullable": False},
    }
    for name, typeName, version in (
        ("Blobs", "blobCollection", "1.x"),
        ("Detections", "detectionCollection", "1.x"),
        ("Contours", "contourCollection", "1.2"),
        ("Measurements", "shapeMeasurementCollection", "1.2"),
        ("Lines", "lineCollection", "1.2"),
        ("Circles", "circleCollection", "1.2"),
        ("Matches", "templateMatchCollection", "1.2"),
    ):
        for prefix in ("kept", "rejected"):
            result[f"{prefix}{name}"] = {
                "type": typeName,
                "required": False,
                "nullable": False,
                "schemaVersion": version,
            }
    return result


class CollectionFilterOperator:
    meta = OperatorMeta(
        operatorId="vision.collection.filter",
        displayName="Filter Collection",
        version="1.1.0",
        inputPorts=_collectionInputs(),
        outputPorts=_collectionOutputs(),
        paramSchema=_PARAM_SCHEMA,
    )

    def validateParams(self, params: dict[str, object]) -> dict[str, str] | None:
        for minimumName, maximumName in (
            ("minArea", "maxArea"),
            ("minPerimeter", "maxPerimeter"),
            ("minLength", "maxLength"),
            ("minRadius", "maxRadius"),
        ):
            minimum = params.get(minimumName, 0.0)
            maximum = params.get(maximumName, 0.0)
            if not _nonNegativeNumber(minimum) or not _nonNegativeNumber(maximum):
                return _paramError(f"{minimumName} and {maximumName} must be >= 0")
            if float(maximum) != 0.0 and float(maximum) < float(minimum):
                return _paramError(f"{maximumName} must be 0 or >= {minimumName}")
        for minimumName, maximumName in (
            ("minCircularity", "maxCircularity"),
            ("minConfidence", "maxConfidence"),
            ("minQuality", "maxQuality"),
        ):
            minimum = params.get(minimumName, 0.0)
            maximum = params.get(maximumName, 1.0)
            if not _unitNumber(minimum) or not _unitNumber(maximum):
                return _paramError(f"{minimumName} and {maximumName} must be in [0, 1]")
            if float(minimum) > float(maximum):
                return _paramError(f"{maximumName} must be >= {minimumName}")
        if not isinstance(params.get("circularityEnabled", False), bool):
            return _paramError("circularityEnabled must be boolean")
        if params.get("contourMode", "any") not in {"any", "present", "absent"}:
            return _paramError("contourMode must be any, present, or absent")
        if params.get("holeMode", "any") not in {"any", "outer", "hole"}:
            return _paramError("holeMode must be any, outer, or hole")
        if params.get("spatialMode", "none") not in _SPATIAL_MODES:
            return _paramError("spatialMode is invalid")
        for name in ("blobLabels", "classIds"):
            if not _integerList(params.get(name, [])):
                return _paramError(f"{name} must contain non-negative integers")
        if not _stringList(params.get("detectionLabels", [])):
            return _paramError("detectionLabels must contain strings")
        if not _enumList(params.get("geometryTypes", []), _GEOMETRY_TYPES):
            return _paramError("geometryTypes contains an unsupported type")
        if not _enumList(params.get("sourceTypes", []), {"contour", "blob"}):
            return _paramError("sourceTypes contains an unsupported type")
        if not _enumList(params.get("templateMethods", []), set(_METHODS)):
            return _paramError("templateMethods contains an unsupported method")
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
            collection = parseCollectionInputs(inputs)
            roi = None
            spatialMode = cast(str, params.get("spatialMode", "none"))
            if spatialMode != "none":
                if "roi" not in inputs:
                    return _error(
                        "E_INPUT_MISSING", "input 'roi' is required for spatial filtering"
                    )
                roi = parseRoi(inputs["roi"])
                ensureSameCoordinateSpace(
                    collection.coordinateSpace,
                    roi.coordinateSpace,
                    message="collection and ROI coordinate spaces do not match",
                )
            kept: list[CollectionItem] = []
            rejected: list[CollectionItem] = []
            for item in collection.items:
                matches = _matchesItem(item, collection, params)
                if matches and roi is not None:
                    if spatialMode == "centerInside":
                        centerX, centerY = itemCenter(item)
                        matches = pointInGeometry(centerX, centerY, roi)
                    else:
                        itemBox = itemBBox(item)
                        roiBox = axisAlignedBBox(roi)
                        matches = (
                            bboxesIntersect(itemBox, roiBox)
                            if spatialMode == "bboxIntersects"
                            else bboxContained(itemBox, roiBox)
                        )
                (kept if matches else rejected).append(item)
        except CollectionInputError as err:
            return _error(err.code, str(err))
        outputs: dict[str, object] = {
            collection.outputName("kept"): collection.payload(kept),
            collection.outputName("rejected"): collection.payload(rejected),
            "keptCount": len(kept),
            "rejectedCount": len(rejected),
        }
        return {
            "status": "ok",
            "outputs": outputs,
            "metrics": {
                "latencyMs": round((perf_counter() - started) * 1000.0, 3),
                "collectionKind": collection.kind,
                "keptCount": len(kept),
                "rejectedCount": len(rejected),
            },
            "diagnostics": {"text": f"Kept {len(kept)} of {len(collection.items)} items"},
        }


def _matchesItem(
    item: CollectionItem, collection: ParsedCollection, params: dict[str, object]
) -> bool:
    if isinstance(item, Blob2D):
        if not _range(item.area, params, "minArea", "maxArea"):
            return False
        labels = cast(list[int], params.get("blobLabels", []))
        if labels and item.label not in labels:
            return False
        mode = cast(str, params.get("contourMode", "any"))
        if mode == "present" and item.contour is None:
            return False
        if mode == "absent" and item.contour is not None:
            return False
        if cast(bool, params.get("circularityEnabled", False)):
            missing, value = itemField(item, "circularity")
            return not missing and _unitRange(float(value), params, "minCircularity", "maxCircularity")
        return True
    if isinstance(item, Detection2D):
        if not _unitRange(item.confidence, params, "minConfidence", "maxConfidence"):
            return False
        classIds = cast(list[int], params.get("classIds", []))
        detectionLabels = cast(list[str], params.get("detectionLabels", []))
        types = cast(list[str], params.get("geometryTypes", []))
        geometry = item.geometry if item.geometry is not None else item.bbox
        return (
            (not classIds or item.classId in classIds)
            and (not detectionLabels or item.label in detectionLabels)
            and (not types or geometryType(geometry) in types)
        )
    if isinstance(item, ContourItem):
        area = float(itemField(item, "area")[1])
        perimeter = float(itemField(item, "perimeter")[1])
        holeMode = cast(str, params.get("holeMode", "any"))
        return (
            _range(area, params, "minArea", "maxArea")
            and _range(perimeter, params, "minPerimeter", "maxPerimeter")
            and (holeMode == "any" or (holeMode == "hole") == item.isHole)
        )
    if isinstance(item, ShapeMeasurement):
        sourceTypes = cast(list[str], params.get("sourceTypes", []))
        result = (
            _range(item.area, params, "minArea", "maxArea")
            and _range(item.perimeter, params, "minPerimeter", "maxPerimeter")
            and (not sourceTypes or item.sourceType in sourceTypes)
        )
        if result and cast(bool, params.get("circularityEnabled", False)):
            result = _unitRange(
                item.circularity, params, "minCircularity", "maxCircularity"
            )
        return result
    if isinstance(item, LineItem):
        return _range(
            float(itemField(item, "length")[1]), params, "minLength", "maxLength"
        )
    if isinstance(item, CircleItem):
        return _range(item.circle.radius, params, "minRadius", "maxRadius")
    match = cast(TemplateMatch, item)
    methods = cast(list[str], params.get("templateMethods", []))
    source = cast(TemplateMatchCollection, collection.source)
    return _unitRange(match.quality, params, "minQuality", "maxQuality") and (
        not methods or source.method in methods
    )


def _range(
    value: float,
    params: dict[str, object],
    minimumName: str,
    maximumName: str,
) -> bool:
    minimum = float(cast(int | float, params.get(minimumName, 0.0)))
    maximum = float(cast(int | float, params.get(maximumName, 0.0)))
    return value >= minimum and (maximum == 0.0 or value <= maximum)


def _unitRange(
    value: float,
    params: dict[str, object],
    minimumName: str,
    maximumName: str,
) -> bool:
    return float(cast(int | float, params.get(minimumName, 0.0))) <= value <= float(
        cast(int | float, params.get(maximumName, 1.0))
    )


def _integerList(value: object) -> bool:
    return isinstance(value, list) and all(_integer(item) and item >= 0 for item in value)


def _stringList(value: object) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def _enumList(value: object, allowed: set[str]) -> bool:
    return isinstance(value, list) and all(item in allowed for item in value)


def _nonNegativeNumber(value: object) -> TypeGuard[int | float]:
    return _finite(value) and float(value) >= 0.0


def _unitNumber(value: object) -> TypeGuard[int | float]:
    return _finite(value) and 0.0 <= float(value) <= 1.0


def _integer(value: object) -> TypeGuard[int]:
    return isinstance(value, int) and not isinstance(value, bool)


def _finite(value: object) -> TypeGuard[int | float]:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def _paramError(message: str) -> dict[str, str]:
    return {"code": "E_PARAM_INVALID", "message": message}


def _error(code: str, message: str) -> dict[str, Any]:
    return {"status": "error", "error": {"code": code, "message": message}}
