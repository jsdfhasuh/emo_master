from __future__ import annotations

from dataclasses import dataclass
from time import perf_counter
from typing import Any

from emo_master.plugins.builtins._collection_ops import (
    CollectionInputError,
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


_INPUT_PORTS: dict[str, object] = {
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
    "contours": {
        "type": "contourCollection",
        "required": False,
        "nullable": False,
        "schemaVersion": "1.2",
    },
    "measurements": {
        "type": "shapeMeasurementCollection",
        "required": False,
        "nullable": False,
        "schemaVersion": "1.2",
    },
    "lines": {
        "type": "lineCollection",
        "required": False,
        "nullable": False,
        "schemaVersion": "1.2",
    },
    "circles": {
        "type": "circleCollection",
        "required": False,
        "nullable": False,
        "schemaVersion": "1.2",
    },
    "matches": {
        "type": "templateMatchCollection",
        "required": False,
        "nullable": False,
        "schemaVersion": "1.2",
    },
}
_PARAM_SCHEMA: dict[str, object] = {"type": "object", "properties": {}}


class CollectionCountOperator:
    meta = OperatorMeta(
        operatorId="vision.collection.count",
        displayName="Count Collection",
        version="1.1.0",
        inputPorts=_INPUT_PORTS,
        outputPorts={
            "count": {"type": "integer", "required": True, "nullable": False}
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
        startedAt = perf_counter()
        try:
            collection = parseCollectionInputs(inputs)
        except CollectionInputError as err:
            return _error(err.code, str(err))
        count = len(collection.items)
        return {
            "status": "ok",
            "outputs": {"count": count},
            "metrics": {
                "latencyMs": round((perf_counter() - startedAt) * 1000.0, 3),
                "collectionKind": collection.kind,
                "count": count,
            },
            "diagnostics": {"text": f"Collection contains {count} items"},
        }


def _error(code: str, message: str) -> dict[str, Any]:
    return {"status": "error", "error": {"code": code, "message": message}}
