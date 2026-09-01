from __future__ import annotations

import csv
import io
import json
import math
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter
from typing import Any, TypeGuard, cast

from emo_master.core.contracts.geometry2d import (
    BBox2D,
    BlobCollection,
    Circle2D,
    CircleCollection,
    ColorStatistics,
    ContourCollection,
    DetectionCollection,
    Geometry2D,
    Histogram,
    Line2D,
    LineCollection,
    PayloadValidationError,
    Point2D,
    Polygon2D,
    RotatedBox2D,
    ShapeMeasurementCollection,
    TemplateMatchCollection,
    parseGeometry2D,
)


@dataclass(frozen=True)
class OperatorMeta:
    operatorId: str
    displayName: str
    version: str
    inputPorts: dict[str, object]
    outputPorts: dict[str, object]
    paramSchema: dict[str, object]


@dataclass(frozen=True)
class WriterValue:
    kind: str
    value: object
    payload: object

    @property
    def recordCount(self) -> int:
        if isinstance(self.value, _COLLECTION_TYPES):
            return len(self.value.items)
        return 1


_COLLECTION_TYPES = (
    BlobCollection,
    DetectionCollection,
    ContourCollection,
    ShapeMeasurementCollection,
    LineCollection,
    CircleCollection,
    TemplateMatchCollection,
)


_INPUT_NAMES = (
    "blobs",
    "detections",
    "contours",
    "measurements",
    "histogram",
    "lines",
    "circles",
    "matches",
    "statistics",
    "geometry",
    "numberValue",
    "booleanValue",
    "stringValue",
)
_PARAM_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "format": {
            "type": "string",
            "enum": ["json", "jsonl", "csv"],
            "default": "json",
        },
        "relativePath": {"type": "string", "default": "results/result"},
        "overwrite": {"type": "boolean", "default": False},
    },
}


class ResultWriterOperator:
    meta = OperatorMeta(
        operatorId="vision.io.result_writer",
        displayName="Result Writer",
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
            "histogram": {"type": "histogram", "required": False, "nullable": False, "schemaVersion": "1.2"},
            "lines": {"type": "lineCollection", "required": False, "nullable": False, "schemaVersion": "1.2"},
            "circles": {"type": "circleCollection", "required": False, "nullable": False, "schemaVersion": "1.2"},
            "matches": {"type": "templateMatchCollection", "required": False, "nullable": False, "schemaVersion": "1.2"},
            "statistics": {
                "type": "colorStatistics",
                "required": False,
                "nullable": False,
                "schemaVersion": "1.x",
            },
            "geometry": {
                "type": "geometry2d",
                "required": False,
                "nullable": False,
                "schemaVersion": "1.x",
            },
            "numberValue": {"type": "number", "required": False, "nullable": False},
            "booleanValue": {
                "type": "boolean",
                "required": False,
                "nullable": False,
            },
            "stringValue": {"type": "string", "required": False, "nullable": False},
        },
        outputPorts={
            "result": {"type": "json", "required": True, "nullable": False}
        },
        paramSchema=_PARAM_SCHEMA,
    )

    def validateParams(self, params: dict[str, object]) -> dict[str, str] | None:
        if params.get("format", "json") not in {"json", "jsonl", "csv"}:
            return _paramError("format must be json, jsonl, or csv")
        relativePath = params.get("relativePath", "results/result")
        if not isinstance(relativePath, str) or relativePath.strip() == "":
            return _paramError("relativePath must be a non-empty string")
        if not isinstance(params.get("overwrite", False), bool):
            return _paramError("overwrite must be boolean")
        return None

    def executeNode(
        self,
        inputs: dict[str, object],
        params: dict[str, object],
        runtimeContext: dict[str, object],
    ) -> dict[str, Any]:
        startedAt = perf_counter()
        paramError = self.validateParams(params)
        if paramError is not None:
            return {"status": "error", "error": paramError}
        try:
            writerValue = _writerValue(inputs)
        except ValueError as err:
            return _error("E_INPUT_SHAPE", str(err))
        except (PayloadValidationError, TypeError) as err:
            return _error("E_INPUT_TYPE", f"invalid result input: {err}")

        formatName = cast(str, params.get("format", "json"))
        workspaceValue = runtimeContext.get("workspacePath")
        if not isinstance(workspaceValue, (str, Path)) or str(workspaceValue) == "":
            return _error("E_EXEC_FAILED", "runtime workspacePath is unavailable")
        try:
            target = _resolveTarget(
                Path(workspaceValue),
                cast(str, params.get("relativePath", "results/result")),
                formatName,
            )
        except ValueError as err:
            return _error("E_PARAM_INVALID", str(err))
        if target.exists() and not cast(bool, params.get("overwrite", False)):
            return _error("E_OUTPUT_EXISTS", f"output already exists: {target}")
        try:
            content, recordCount = _serialize(writerValue, formatName)
            _atomicWrite(target, content)
        except (OSError, TypeError, ValueError) as err:
            return _error("E_OUTPUT_WRITE_FAILED", f"failed to write result: {err}")

        receipt = {
            "saved": True,
            "path": str(target),
            "format": formatName,
            "recordCount": recordCount,
        }
        return {
            "status": "ok",
            "outputs": {"result": receipt},
            "metrics": {
                "latencyMs": round((perf_counter() - startedAt) * 1000.0, 3),
                "recordCount": recordCount,
                "format": formatName,
            },
            "diagnostics": {"text": f"Wrote {recordCount} records to {target.name}"},
        }


def _writerValue(inputs: dict[str, object]) -> WriterValue:
    present = [name for name in _INPUT_NAMES if name in inputs]
    if len(present) != 1:
        raise ValueError("exactly one result input must be connected")
    name = present[0]
    raw = inputs[name]
    if name == "blobs":
        blobValue = (
            raw if isinstance(raw, BlobCollection) else BlobCollection.fromPayload(raw)
        )
        return WriterValue("blobCollection", blobValue, blobValue.toPayload())
    if name == "detections":
        detectionValue = (
            raw
            if isinstance(raw, DetectionCollection)
            else DetectionCollection.fromPayload(raw)
        )
        return WriterValue(
            "detectionCollection", detectionValue, detectionValue.toPayload()
        )
    if name == "contours":
        contourValue = (
            raw
            if isinstance(raw, ContourCollection)
            else ContourCollection.fromPayload(raw)
        )
        return WriterValue(
            "contourCollection", contourValue, contourValue.toPayload()
        )
    if name == "measurements":
        measurementValue = (
            raw
            if isinstance(raw, ShapeMeasurementCollection)
            else ShapeMeasurementCollection.fromPayload(raw)
        )
        return WriterValue(
            "shapeMeasurementCollection",
            measurementValue,
            measurementValue.toPayload(),
        )
    if name == "histogram":
        histogramValue = raw if isinstance(raw, Histogram) else Histogram.fromPayload(raw)
        return WriterValue("histogram", histogramValue, histogramValue.toPayload())
    if name == "lines":
        lineValue = (
            raw if isinstance(raw, LineCollection) else LineCollection.fromPayload(raw)
        )
        return WriterValue("lineCollection", lineValue, lineValue.toPayload())
    if name == "circles":
        circleValue = (
            raw if isinstance(raw, CircleCollection) else CircleCollection.fromPayload(raw)
        )
        return WriterValue(
            "circleCollection", circleValue, circleValue.toPayload()
        )
    if name == "matches":
        matchValue = (
            raw
            if isinstance(raw, TemplateMatchCollection)
            else TemplateMatchCollection.fromPayload(raw)
        )
        return WriterValue(
            "templateMatchCollection", matchValue, matchValue.toPayload()
        )
    if name == "statistics":
        statisticsValue = (
            raw
            if isinstance(raw, ColorStatistics)
            else ColorStatistics.fromPayload(raw)
        )
        return WriterValue(
            "colorStatistics", statisticsValue, statisticsValue.toPayload()
        )
    if name == "geometry":
        geometryValue = raw if _isGeometry(raw) else parseGeometry2D(raw)
        return WriterValue(
            "geometry2d", geometryValue, geometryValue.toPayload()
        )
    if name == "numberValue":
        if not _isFiniteNumber(raw):
            raise TypeError("numberValue must be a finite number")
        return WriterValue("number", raw, raw)
    if name == "booleanValue":
        if not isinstance(raw, bool):
            raise TypeError("booleanValue must be boolean")
        return WriterValue("boolean", raw, raw)
    if not isinstance(raw, str):
        raise TypeError("stringValue must be string")
    return WriterValue("string", raw, raw)


def _resolveTarget(workspace: Path, relativePath: str, formatName: str) -> Path:
    workspace = workspace.resolve()
    requested = Path(relativePath)
    if requested.is_absolute() or ".." in requested.parts:
        raise ValueError("relativePath must stay inside the Job workspace")
    expectedSuffix = f".{formatName}"
    if requested.suffix == "":
        requested = requested.with_suffix(expectedSuffix)
    elif requested.suffix.lower() != expectedSuffix:
        raise ValueError(f"relativePath extension must be {expectedSuffix}")
    target = (workspace / requested).resolve()
    if workspace != target and workspace not in target.parents:
        raise ValueError("relativePath must stay inside the Job workspace")
    if target.exists() and target.is_dir():
        raise ValueError("relativePath must identify a file")
    return target


def _serialize(value: WriterValue, formatName: str) -> tuple[str, int]:
    if formatName == "json":
        return (
            json.dumps(value.payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            value.recordCount,
        )
    if formatName == "jsonl":
        records = _jsonlRecords(value)
        return "".join(
            json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n"
            for record in records
        ), len(records)
    headers, rows = _csvRows(value)
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=headers, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue(), len(rows)


def _jsonlRecords(value: WriterValue) -> list[dict[str, object]]:
    if isinstance(value.value, _COLLECTION_TYPES):
        payload = cast(dict[str, object], value.payload)
        coordinateSpace = cast(dict[str, object], payload["coordinateSpace"])
        items = cast(list[dict[str, object]], payload["items"])
        metadata = {
            key: item
            for key, item in payload.items()
            if key not in {"type", "schemaVersion", "coordinateSpace", "items"}
        }
        return [
            {
                "collectionType": value.kind,
                "schemaVersion": payload["schemaVersion"],
                "coordinateSpace": coordinateSpace,
                **({"collectionMetadata": metadata} if metadata else {}),
                "item": item,
            }
            for item in items
        ]
    return [{"valueType": value.kind, "value": value.payload}]


def _csvRows(value: WriterValue) -> tuple[list[str], list[dict[str, object]]]:
    if isinstance(value.value, BlobCollection):
        headers = [
            "id",
            "area",
            "centroid_x",
            "centroid_y",
            "bbox_x",
            "bbox_y",
            "bbox_width",
            "bbox_height",
            "label",
            "contour",
            "attributes",
        ]
        return headers, [
            {
                "id": item.blobId,
                "area": item.area,
                "centroid_x": item.centroid.x,
                "centroid_y": item.centroid.y,
                "bbox_x": item.bbox.x,
                "bbox_y": item.bbox.y,
                "bbox_width": item.bbox.width,
                "bbox_height": item.bbox.height,
                "label": "" if item.label is None else item.label,
                "contour": ""
                if item.contour is None
                else json.dumps(item.contour.toPayload(), ensure_ascii=False, sort_keys=True),
                "attributes": json.dumps(
                    dict(item.attributes), ensure_ascii=False, sort_keys=True
                ),
            }
            for item in value.value.items
        ]
    if isinstance(value.value, DetectionCollection):
        headers = [
            "id",
            "class_id",
            "label",
            "confidence",
            "geometry_type",
            "bbox_x",
            "bbox_y",
            "bbox_width",
            "bbox_height",
            "geometry",
            "attributes",
        ]
        return headers, [
            {
                "id": item.detectionId,
                "class_id": item.classId,
                "label": item.label,
                "confidence": item.confidence,
                "geometry_type": cast(Geometry2D, item.geometry).toPayload()["type"],
                "bbox_x": item.bbox.x,
                "bbox_y": item.bbox.y,
                "bbox_width": item.bbox.width,
                "bbox_height": item.bbox.height,
                "geometry": json.dumps(
                    cast(Geometry2D, item.geometry).toPayload(),
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                "attributes": json.dumps(
                    dict(item.attributes), ensure_ascii=False, sort_keys=True
                ),
            }
            for item in value.value.items
        ]
    if isinstance(value.value, ContourCollection):
        headers = [
            "id", "parent_id", "first_child_id", "previous_sibling_id",
            "next_sibling_id", "depth", "is_hole", "polygon",
        ]
        return headers, [
            {
                "id": item.contourId,
                "parent_id": item.parentId or "",
                "first_child_id": item.firstChildId or "",
                "previous_sibling_id": item.previousSiblingId or "",
                "next_sibling_id": item.nextSiblingId or "",
                "depth": item.depth,
                "is_hole": item.isHole,
                "polygon": json.dumps(
                    item.polygon.toPayload(), ensure_ascii=False, sort_keys=True
                ),
            }
            for item in value.value.items
        ]
    if isinstance(value.value, ShapeMeasurementCollection):
        headers = [
            "id", "source_id", "source_type", "area", "perimeter", "circularity",
            "centroid_x", "centroid_y", "bbox_x", "bbox_y", "bbox_width",
            "bbox_height", "min_area_rect",
        ]
        return headers, [
            {
                "id": item.measurementId,
                "source_id": item.sourceId,
                "source_type": item.sourceType,
                "area": item.area,
                "perimeter": item.perimeter,
                "circularity": item.circularity,
                "centroid_x": item.centroid.x,
                "centroid_y": item.centroid.y,
                "bbox_x": item.bbox.x,
                "bbox_y": item.bbox.y,
                "bbox_width": item.bbox.width,
                "bbox_height": item.bbox.height,
                "min_area_rect": json.dumps(
                    item.minAreaRect.toPayload(), ensure_ascii=False, sort_keys=True
                ),
            }
            for item in value.value.items
        ]
    if isinstance(value.value, LineCollection):
        headers = [
            "id", "start_x", "start_y", "end_x", "end_y", "length", "angle",
        ]
        return headers, [
            {
                "id": item.lineId,
                "start_x": item.line.start.x,
                "start_y": item.line.start.y,
                "end_x": item.line.end.x,
                "end_y": item.line.end.y,
                "length": math.hypot(
                    item.line.end.x - item.line.start.x,
                    item.line.end.y - item.line.start.y,
                ),
                "angle": math.degrees(
                    math.atan2(
                        item.line.end.y - item.line.start.y,
                        item.line.end.x - item.line.start.x,
                    )
                ),
            }
            for item in value.value.items
        ]
    if isinstance(value.value, CircleCollection):
        headers = ["id", "center_x", "center_y", "radius"]
        return headers, [
            {
                "id": item.circleId,
                "center_x": item.circle.center.x,
                "center_y": item.circle.center.y,
                "radius": item.circle.radius,
            }
            for item in value.value.items
        ]
    if isinstance(value.value, TemplateMatchCollection):
        headers = [
            "id", "method", "label", "raw_score", "quality", "bbox_x", "bbox_y",
            "bbox_width", "bbox_height",
        ]
        return headers, [
            {
                "id": item.matchId,
                "method": value.value.method,
                "label": value.value.label,
                "raw_score": item.rawScore,
                "quality": item.quality,
                "bbox_x": item.bbox.x,
                "bbox_y": item.bbox.y,
                "bbox_width": item.bbox.width,
                "bbox_height": item.bbox.height,
            }
            for item in value.value.items
        ]
    if isinstance(value.value, Histogram):
        headers = [
            "color_space", "normalization", "pixel_count", "channel", "bin_index",
            "bin_start", "bin_end", "value",
        ]
        return headers, [
            {
                "color_space": value.value.colorSpace,
                "normalization": value.value.normalization,
                "pixel_count": value.value.pixelCount,
                "channel": channel.name,
                "bin_index": index,
                "bin_start": value.value.binEdges[index],
                "bin_end": value.value.binEdges[index + 1],
                "value": binValue,
            }
            for channel in value.value.channels
            for index, binValue in enumerate(channel.values)
        ]
    return ["value_type", "value"], [
        {
            "value_type": value.kind,
            "value": json.dumps(value.payload, ensure_ascii=False, sort_keys=True),
        }
    ]


def _atomicWrite(target: Path, content: str) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temporaryPath: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="",
            dir=target.parent,
            prefix=f".{target.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporaryPath = Path(temporary.name)
            temporary.write(content)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporaryPath, target)
    finally:
        if temporaryPath is not None and temporaryPath.exists():
            temporaryPath.unlink()


def _isGeometry(value: object) -> TypeGuard[Geometry2D]:
    return isinstance(
        value,
        (Point2D, BBox2D, RotatedBox2D, Polygon2D, Line2D, Circle2D),
    )


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
