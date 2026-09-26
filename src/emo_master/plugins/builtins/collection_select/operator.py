from __future__ import annotations

from dataclasses import dataclass
from time import perf_counter
from typing import Any, TypeGuard, cast

from emo_master.plugins.builtins._collection_ops import (
    CollectionInputError,
    CollectionItem,
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


_MODES = frozenset({"first", "last", "index", "topK"})
_PARAM_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "mode": {
            "type": "string",
            "enum": ["first", "last", "index", "topK"],
            "default": "first",
        },
        "index": {"type": "integer", "minimum": 0, "default": 0},
        "count": {"type": "integer", "minimum": 1, "default": 1},
        "onOutOfRange": {
            "type": "string",
            "enum": ["empty", "error"],
            "default": "empty",
        },
    },
}


class CollectionSelectOperator:
    meta = OperatorMeta(
        operatorId="vision.collection.select",
        displayName="Select Collection",
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
            "selectedBlobs": {
                "type": "blobCollection",
                "required": False,
                "nullable": False,
                "schemaVersion": "1.x",
            },
            "selectedDetections": {
                "type": "detectionCollection",
                "required": False,
                "nullable": False,
                "schemaVersion": "1.x",
            },
            "selectedContours": {"type": "contourCollection", "required": False, "nullable": False, "schemaVersion": "1.2"},
            "selectedMeasurements": {"type": "shapeMeasurementCollection", "required": False, "nullable": False, "schemaVersion": "1.2"},
            "selectedLines": {"type": "lineCollection", "required": False, "nullable": False, "schemaVersion": "1.2"},
            "selectedCircles": {"type": "circleCollection", "required": False, "nullable": False, "schemaVersion": "1.2"},
            "selectedMatches": {"type": "templateMatchCollection", "required": False, "nullable": False, "schemaVersion": "1.2"},
            "selectedCount": {
                "type": "integer",
                "required": True,
                "nullable": False,
            },
        },
        paramSchema=_PARAM_SCHEMA,
    )

    def validateParams(self, params: dict[str, object]) -> dict[str, str] | None:
        if params.get("mode", "first") not in _MODES:
            return _paramError("mode must be first, last, index, or topK")
        index = params.get("index", 0)
        if not _isInt(index) or index < 0:
            return _paramError("index must be an integer >= 0")
        count = params.get("count", 1)
        if not _isInt(count) or count < 1:
            return _paramError("count must be an integer >= 1")
        if params.get("onOutOfRange", "empty") not in {"empty", "error"}:
            return _paramError("onOutOfRange must be empty or error")
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
        mode = cast(str, params.get("mode", "first"))
        selected: list[CollectionItem]
        if mode == "topK":
            selected = list(collection.items[: cast(int, params.get("count", 1))])
        elif not collection.items:
            selected = []
        elif mode == "first":
            selected = [collection.items[0]]
        elif mode == "last":
            selected = [collection.items[-1]]
        else:
            index = cast(int, params.get("index", 0))
            if index >= len(collection.items):
                if params.get("onOutOfRange", "empty") == "error":
                    return _error(
                        "E_PARAM_INVALID",
                        f"index {index} is outside collection of size {len(collection.items)}",
                    )
                selected = []
            else:
                selected = [collection.items[index]]
        outputName = collection.outputName("selected")
        return {
            "status": "ok",
            "outputs": {
                outputName: collection.payload(selected),
                "selectedCount": len(selected),
            },
            "metrics": {
                "latencyMs": round((perf_counter() - startedAt) * 1000.0, 3),
                "collectionKind": collection.kind,
                "selectedCount": len(selected),
            },
            "diagnostics": {"text": f"Selected {len(selected)} items using {mode}"},
        }


def _isInt(value: object) -> TypeGuard[int]:
    return isinstance(value, int) and not isinstance(value, bool)


def _paramError(message: str) -> dict[str, str]:
    return {"code": "E_PARAM_INVALID", "message": message}


def _error(code: str, message: str) -> dict[str, Any]:
    return {"status": "error", "error": {"code": code, "message": message}}
