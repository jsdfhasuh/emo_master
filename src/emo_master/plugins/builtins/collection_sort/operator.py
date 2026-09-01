from __future__ import annotations

from dataclasses import dataclass
from time import perf_counter
from typing import Any, cast

from emo_master.plugins.builtins._collection_ops import (
    CollectionInputError,
    CollectionItem,
    itemField,
    parseCollectionInputs,
)


@dataclass(frozen=True)
class OperatorMeta:
    operatorId: str
    displayName: str
    version: str
    inputPorts: dict[str, object]
    outputPorts: dict[str, object]
    paramSchema: dict[str, object]


_BLOB_KEYS = frozenset(
    {
        "id",
        "area",
        "circularity",
        "label",
        "centroidX",
        "centroidY",
        "bboxX",
        "bboxY",
        "bboxWidth",
        "bboxHeight",
    }
)
_DETECTION_KEYS = frozenset(
    {
        "id",
        "confidence",
        "classId",
        "label",
        "bboxX",
        "bboxY",
        "bboxWidth",
        "bboxHeight",
    }
)
_CONTOUR_KEYS = frozenset(
    {"id", "area", "perimeter", "depth", "isHole", "bboxX", "bboxY", "bboxWidth", "bboxHeight"}
)
_MEASUREMENT_KEYS = frozenset(
    {
        "id", "sourceId", "sourceType", "area", "perimeter", "circularity",
        "centroidX", "centroidY", "bboxX", "bboxY", "bboxWidth", "bboxHeight",
        "minRectWidth", "minRectHeight", "minRectAngle",
    }
)
_LINE_KEYS = frozenset(
    {"id", "length", "angle", "startX", "startY", "endX", "endY", "bboxX", "bboxY", "bboxWidth", "bboxHeight"}
)
_CIRCLE_KEYS = frozenset(
    {"id", "radius", "diameter", "area", "centerX", "centerY", "bboxX", "bboxY", "bboxWidth", "bboxHeight"}
)
_MATCH_KEYS = frozenset(
    {"id", "quality", "rawScore", "bboxX", "bboxY", "bboxWidth", "bboxHeight"}
)
_KEYS_BY_KIND = {
    "blob": _BLOB_KEYS,
    "detection": _DETECTION_KEYS,
    "contour": _CONTOUR_KEYS,
    "measurement": _MEASUREMENT_KEYS,
    "line": _LINE_KEYS,
    "circle": _CIRCLE_KEYS,
    "match": _MATCH_KEYS,
}
_ALL_KEYS = sorted(set().union(*_KEYS_BY_KIND.values()))
_PARAM_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "key": {"type": "string", "enum": _ALL_KEYS, "default": "area"},
        "direction": {
            "type": "string",
            "enum": ["ascending", "descending"],
            "default": "descending",
        },
    },
}


class CollectionSortOperator:
    meta = OperatorMeta(
        operatorId="vision.collection.sort",
        displayName="Sort Collection",
        version="1.1.0",
        inputPorts={
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
        },
        outputPorts={
            "sortedBlobs": {
                "type": "blobCollection",
                "required": False,
                "nullable": False,
                "schemaVersion": "1.x",
            },
            "sortedDetections": {
                "type": "detectionCollection",
                "required": False,
                "nullable": False,
                "schemaVersion": "1.x",
            },
            "sortedContours": {"type": "contourCollection", "required": False, "nullable": False, "schemaVersion": "1.2"},
            "sortedMeasurements": {"type": "shapeMeasurementCollection", "required": False, "nullable": False, "schemaVersion": "1.2"},
            "sortedLines": {"type": "lineCollection", "required": False, "nullable": False, "schemaVersion": "1.2"},
            "sortedCircles": {"type": "circleCollection", "required": False, "nullable": False, "schemaVersion": "1.2"},
            "sortedMatches": {"type": "templateMatchCollection", "required": False, "nullable": False, "schemaVersion": "1.2"},
        },
        paramSchema=_PARAM_SCHEMA,
    )

    def validateParams(self, params: dict[str, object]) -> dict[str, str] | None:
        key = params.get("key", "area")
        if not isinstance(key, str) or key not in _ALL_KEYS:
            return _paramError("key is not a supported collection field")
        if params.get("direction", "descending") not in {"ascending", "descending"}:
            return _paramError("direction must be ascending or descending")
        return None

    def executeNode(
        self,
        inputs: dict[str, object],
        params: dict[str, object],
        runtimeContext: dict[str, object],
    ) -> dict[str, Any]:
        _ = runtimeContext
        startedAt = perf_counter()
        paramError = self.validateParams(params)
        if paramError is not None:
            return {"status": "error", "error": paramError}
        try:
            collection = parseCollectionInputs(inputs)
        except CollectionInputError as err:
            return _error(err.code, str(err))
        key = cast(str, params.get("key", "area"))
        allowed = _KEYS_BY_KIND[collection.kind]
        if key not in allowed:
            return _error(
                "E_PARAM_INVALID", f"key {key!r} is not valid for {collection.kind} items"
            )
        present: list[CollectionItem] = []
        missing: list[CollectionItem] = []
        for item in collection.items:
            isMissing, _ = itemField(item, key)
            (missing if isMissing else present).append(item)
        reverse = params.get("direction", "descending") == "descending"
        present.sort(key=lambda item: itemField(item, key)[1], reverse=reverse)
        sortedItems = [*present, *missing]
        outputName = collection.outputName("sorted")
        return {
            "status": "ok",
            "outputs": {outputName: collection.payload(sortedItems)},
            "metrics": {
                "latencyMs": round((perf_counter() - startedAt) * 1000.0, 3),
                "collectionKind": collection.kind,
                "count": len(sortedItems),
                "missingValues": len(missing),
            },
            "diagnostics": {"text": f"Sorted {len(sortedItems)} items by {key}"},
        }


def _paramError(message: str) -> dict[str, str]:
    return {"code": "E_PARAM_INVALID", "message": message}


def _error(code: str, message: str) -> dict[str, Any]:
    return {"status": "error", "error": {"code": code, "message": message}}
