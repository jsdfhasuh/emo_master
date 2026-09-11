from __future__ import annotations

import csv
import math
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter
from typing import Any, cast

from emo_master.core.contracts.geometry2d import (
    BBox2D,
    CoordinateSpace2D,
    PayloadValidationError,
    Point2D,
    Polygon2D,
    Vector2D,
)
from emo_master.plugins.builtins._image_frame import coordinateSpacesEquivalent


@dataclass(frozen=True)
class OperatorMeta:
    operatorId: str
    displayName: str
    version: str
    inputPorts: dict[str, object]
    outputPorts: dict[str, object]
    paramSchema: dict[str, object]


_TEXT_ENCODINGS = frozenset({"utf-8", "utf-8-sig", "gb18030", "ascii"})
_FILE_FORMATS = frozenset({"auto", "csv", "txt"})
_DELIMITERS = frozenset({"auto", "comma", "tab", "space", "semicolon"})
_HEADER_MODES = frozenset({"auto", "present", "absent"})
_INVALID_ROW_MODES = frozenset({"error", "skip"})
_PIVOT_MODES = frozenset({"origin", "centroid", "first", "custom"})
_CALCULATOR_MODES = frozenset(
    {"measure", "transform", "subtract", "dot", "scalarMultiply"}
)
_PAIRING_MODES = frozenset({"strict", "broadcast"})
_IDENTITY_AFFINE = [1.0, 0.0, 0.0, 1.0, 0.0, 0.0]


COORDINATE_READER_PARAM_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "filePath": {
            "type": "string",
            "default": "",
            "xWidget": "file",
            "xFileMode": "open",
            "xFilter": "Coordinate files (*.txt *.csv)",
        },
        "fileFormat": {
            "type": "string",
            "enum": ["auto", "csv", "txt"],
            "default": "auto",
        },
        "encoding": {
            "type": "string",
            "enum": ["utf-8", "utf-8-sig", "gb18030", "ascii"],
            "default": "utf-8",
        },
        "delimiter": {
            "type": "string",
            "enum": ["auto", "comma", "tab", "space", "semicolon"],
            "default": "auto",
        },
        "headerMode": {
            "type": "string",
            "enum": ["auto", "present", "absent"],
            "default": "auto",
        },
        "xColumn": {"type": "integer", "minimum": 0, "maximum": 255, "default": 0},
        "yColumn": {"type": "integer", "minimum": 0, "maximum": 255, "default": 1},
        "commentPrefix": {"type": "string", "default": "#"},
        "onInvalidRow": {
            "type": "string",
            "enum": ["error", "skip"],
            "default": "error",
        },
        "maxPoints": {
            "type": "integer",
            "minimum": 1,
            "maximum": 100000,
            "default": 10000,
        },
        "maxFileBytes": {
            "type": "integer",
            "minimum": 1,
            "maximum": 16777216,
            "default": 4194304,
        },
        "asPolygon": {"type": "boolean", "default": False},
        "sourceId": {"type": "string", "default": ""},
        "imageWidth": {
            "type": "integer",
            "minimum": 0,
            "maximum": 1000000,
            "default": 0,
        },
        "imageHeight": {
            "type": "integer",
            "minimum": 0,
            "maximum": 1000000,
            "default": 0,
        },
        "transformToSource": {
            "type": "array",
            "items": {"type": "number"},
            "minItems": 6,
            "maxItems": 6,
            "default": _IDENTITY_AFFINE,
        },
    },
    "required": ["filePath"],
}


COORDINATE_CALCULATOR_PARAM_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "mode": {
            "type": "string",
            "enum": ["measure", "transform", "subtract", "dot", "scalarMultiply"],
            "default": "transform",
        },
        "pairing": {
            "type": "string",
            "enum": ["strict", "broadcast"],
            "default": "strict",
        },
        "scalarValue": {"type": "number", "default": 1.0},
        "closed": {"type": "boolean", "default": False},
        "offsetX": {"type": "number", "default": 0.0},
        "offsetY": {"type": "number", "default": 0.0},
        "scaleX": {"type": "number", "default": 1.0},
        "scaleY": {"type": "number", "default": 1.0},
        "rotationDegrees": {
            "type": "number",
            "minimum": -360.0,
            "maximum": 360.0,
            "default": 0.0,
        },
        "pivot": {
            "type": "string",
            "enum": ["origin", "centroid", "first", "custom"],
            "default": "origin",
        },
        "pivotX": {"type": "number", "default": 0.0},
        "pivotY": {"type": "number", "default": 0.0},
    },
}


class CoordinateReaderOperator:
    meta = OperatorMeta(
        operatorId="vision.io.coordinate_reader",
        displayName="Coordinate Reader",
        version="1.0.0",
        inputPorts={
            "frame": {
                "type": "bbox2d",
                "required": False,
                "nullable": False,
                "schemaVersion": "1.x",
            }
        },
        outputPorts={
            "points": {
                "type": "list<point2d>",
                "required": True,
                "nullable": False,
                "schemaVersion": "1.x",
            },
            "pointCount": {"type": "integer", "required": True, "nullable": False},
            "polygon": {
                "type": "polygon2d",
                "required": False,
                "nullable": False,
                "schemaVersion": "1.x",
            },
        },
        paramSchema=COORDINATE_READER_PARAM_SCHEMA,
    )

    def validateParams(self, params: dict[str, object]) -> dict[str, str] | None:
        properties = cast(dict[str, object], COORDINATE_READER_PARAM_SCHEMA["properties"])
        unknown = sorted(set(params) - set(properties))
        if unknown:
            return _paramError(f"unknown params: {', '.join(unknown)}")
        filePath = params.get("filePath", "")
        if not isinstance(filePath, str) or filePath.strip() == "":
            return _paramError("filePath must be a non-empty string")
        for name, options, default in (
            ("fileFormat", _FILE_FORMATS, "auto"),
            ("encoding", _TEXT_ENCODINGS, "utf-8"),
            ("delimiter", _DELIMITERS, "auto"),
            ("headerMode", _HEADER_MODES, "auto"),
            ("onInvalidRow", _INVALID_ROW_MODES, "error"),
        ):
            value = params.get(name, default)
            if not isinstance(value, str) or value not in options:
                return _paramError(f"{name} is not supported")
        xColumn = params.get("xColumn", 0)
        yColumn = params.get("yColumn", 1)
        if not _integerInRange(xColumn, 0, 255) or not _integerInRange(
            yColumn, 0, 255
        ):
            return _paramError("xColumn and yColumn must be integers between 0 and 255")
        if xColumn == yColumn:
            return _paramError("xColumn and yColumn must be different")
        commentPrefix = params.get("commentPrefix", "#")
        if not isinstance(commentPrefix, str) or len(commentPrefix) > 16:
            return _paramError("commentPrefix must be a string with at most 16 characters")
        if not _integerInRange(params.get("maxPoints", 10000), 1, 100000):
            return _paramError("maxPoints must be between 1 and 100000")
        if not _integerInRange(params.get("maxFileBytes", 4194304), 1, 16777216):
            return _paramError("maxFileBytes must be between 1 and 16777216")
        if not isinstance(params.get("asPolygon", False), bool):
            return _paramError("asPolygon must be boolean")
        sourceId = params.get("sourceId", "")
        if not isinstance(sourceId, str):
            return _paramError("sourceId must be a string")
        imageWidth = params.get("imageWidth", 0)
        imageHeight = params.get("imageHeight", 0)
        if not _integerInRange(imageWidth, 0, 1000000) or not _integerInRange(
            imageHeight, 0, 1000000
        ):
            return _paramError("imageWidth and imageHeight must be between 0 and 1000000")
        if (imageWidth == 0) != (imageHeight == 0):
            return _paramError("imageWidth and imageHeight must both be zero or both be positive")
        transform = params.get("transformToSource", _IDENTITY_AFFINE)
        if not isinstance(transform, list) or len(transform) != 6 or any(
            not _finiteNumber(value) for value in transform
        ):
            return _paramError("transformToSource must contain six finite numbers")
        try:
            CoordinateSpace2D(transformToSource=tuple(cast(list[float], transform)))
        except PayloadValidationError as exc:
            return _paramError(str(exc))
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
        path = _resolveCoordinatePath(
            cast(str, params["filePath"]), runtimeContext.get("workspacePath")
        )
        if not path.exists() or not path.is_file():
            return _error("E_INPUT_MISSING", f"coordinate file not found: {path}")
        if path.suffix.lower() not in {".txt", ".csv"}:
            return _error("E_PARAM_INVALID", "coordinate file must use .txt or .csv")
        try:
            fileSize = path.stat().st_size
        except OSError as exc:
            return _error("E_INPUT_SHAPE", f"cannot inspect coordinate file: {exc}")
        maxFileBytes = cast(int, params.get("maxFileBytes", 4194304))
        if fileSize > maxFileBytes:
            return _error(
                "E_INPUT_SHAPE",
                f"coordinate file exceeds maxFileBytes={maxFileBytes}",
            )
        try:
            text = path.read_text(encoding=cast(str, params.get("encoding", "utf-8")))
        except (OSError, UnicodeError) as exc:
            return _error("E_INPUT_SHAPE", f"cannot decode coordinate file: {exc}")
        try:
            rows, skippedRows = _readCoordinateRows(text, path, params)
        except (TypeError, ValueError) as exc:
            return _error("E_INPUT_SHAPE", str(exc))
        try:
            space = _coordinateSpace(inputs, params, path)
        except PayloadValidationError as exc:
            return _error("E_INPUT_TYPE", str(exc))
        except (TypeError, ValueError) as exc:
            return _error("E_INPUT_SHAPE", str(exc))
        try:
            points = tuple(Point2D(x, y, space) for x, y in rows)
            outputs: dict[str, object] = {
                "points": [point.toPayload() for point in points],
                "pointCount": len(points),
            }
            if bool(params.get("asPolygon", False)):
                polygonPoints = _polygonPoints(points)
                outputs["polygon"] = Polygon2D(polygonPoints).toPayload()
        except PayloadValidationError as exc:
            return _error("E_RESULT_INVALID", str(exc))
        except ValueError as exc:
            return _error("E_INPUT_SHAPE", str(exc))
        latencyMs = round((perf_counter() - startedAt) * 1000.0, 3)
        return {
            "status": "ok",
            "outputs": outputs,
            "metrics": {
                "latencyMs": latencyMs,
                "fileBytes": fileSize,
                "pointCount": len(points),
                "skippedRows": skippedRows,
            },
            "diagnostics": {"text": f"Loaded {len(points)} coordinates from {path}"},
        }


class CoordinateCalculatorOperator:
    meta = OperatorMeta(
        operatorId="vision.geometry.coordinate_calculator",
        displayName="Coordinate Calculator",
        version="1.1.0",
        inputPorts={
            "points": {
                "type": "list<point2d>",
                "required": True,
                "nullable": False,
                "schemaVersion": "1.x",
            },
            "otherPoints": {
                "type": "list<point2d>",
                "required": False,
                "nullable": False,
                "schemaVersion": "1.x",
            },
            "scalar": {"type": "number", "required": False, "nullable": False},
        },
        outputPorts={
            "points": {
                "type": "list<point2d>",
                "required": False,
                "nullable": False,
                "schemaVersion": "1.x",
            },
            "centroid": {
                "type": "point2d",
                "required": False,
                "nullable": False,
                "schemaVersion": "1.x",
            },
            "bounds": {
                "type": "bbox2d",
                "required": False,
                "nullable": False,
                "schemaVersion": "1.x",
            },
            "segmentLengths": {
                "type": "list<number>",
                "required": False,
                "nullable": False,
            },
            "pathLength": {"type": "number", "required": False, "nullable": False},
            "perimeter": {"type": "number", "required": False, "nullable": False},
            "signedArea": {"type": "number", "required": False, "nullable": False},
            "area": {"type": "number", "required": False, "nullable": False},
            "firstToLastDistance": {
                "type": "number",
                "required": False,
                "nullable": False,
            },
            "firstToLastAngleDegrees": {
                "type": "number",
                "required": False,
                "nullable": False,
            },
            "polygon": {
                "type": "polygon2d",
                "required": False,
                "nullable": False,
                "schemaVersion": "1.x",
            },
            "vectors": {
                "type": "list<vector2d>",
                "required": False,
                "nullable": False,
                "schemaVersion": "1.x",
            },
            "magnitudes": {
                "type": "list<number>",
                "required": False,
                "nullable": False,
            },
            "values": {
                "type": "list<number>",
                "required": False,
                "nullable": False,
            },
            "dotSum": {"type": "number", "required": False, "nullable": False},
            "resultCount": {"type": "integer", "required": True, "nullable": False},
        },
        paramSchema=COORDINATE_CALCULATOR_PARAM_SCHEMA,
    )

    def validateParams(self, params: dict[str, object]) -> dict[str, str] | None:
        properties = cast(
            dict[str, object], COORDINATE_CALCULATOR_PARAM_SCHEMA["properties"]
        )
        unknown = sorted(set(params) - set(properties))
        if unknown:
            return _paramError(f"unknown params: {', '.join(unknown)}")
        if not isinstance(params.get("closed", False), bool):
            return _paramError("closed must be boolean")
        mode = params.get("mode", "transform")
        if not isinstance(mode, str) or mode not in _CALCULATOR_MODES:
            return _paramError("mode is not supported")
        pairing = params.get("pairing", "strict")
        if not isinstance(pairing, str) or pairing not in _PAIRING_MODES:
            return _paramError("pairing is not supported")
        for name, default in (
            ("scalarValue", 1.0),
            ("offsetX", 0.0),
            ("offsetY", 0.0),
            ("scaleX", 1.0),
            ("scaleY", 1.0),
            ("rotationDegrees", 0.0),
            ("pivotX", 0.0),
            ("pivotY", 0.0),
        ):
            if not _finiteNumber(params.get(name, default)):
                return _paramError(f"{name} must be a finite number")
        rotation = float(cast(float, params.get("rotationDegrees", 0.0)))
        if rotation < -360.0 or rotation > 360.0:
            return _paramError("rotationDegrees must be between -360 and 360")
        pivot = params.get("pivot", "origin")
        if not isinstance(pivot, str) or pivot not in _PIVOT_MODES:
            return _paramError("pivot is not supported")
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
        unknownInputs = sorted(set(inputs) - {"points", "otherPoints", "scalar"})
        if unknownInputs:
            return _error("E_INPUT_SHAPE", f"unknown inputs: {', '.join(unknownInputs)}")
        if "points" not in inputs:
            return _error("E_INPUT_MISSING", "points input is required")
        try:
            points = _pointList(inputs["points"], "points")
        except TypeError as exc:
            return _error("E_INPUT_TYPE", str(exc))
        except (PayloadValidationError, ValueError) as exc:
            return _error("E_INPUT_SHAPE", str(exc))
        mode = cast(str, params.get("mode", "transform"))
        pairing = cast(str, params.get("pairing", "strict"))
        if mode in {"subtract", "dot"}:
            if "otherPoints" not in inputs:
                return _error(
                    "E_INPUT_MISSING", f"otherPoints is required for mode={mode}"
                )
            if "scalar" in inputs:
                return _error("E_INPUT_SHAPE", f"scalar is not used by mode={mode}")
            try:
                otherPoints = _pointList(inputs["otherPoints"], "otherPoints")
                pairs = _pointPairs(points, otherPoints, pairing)
            except TypeError as exc:
                return _error("E_INPUT_TYPE", str(exc))
            except (PayloadValidationError, ValueError) as exc:
                return _error("E_INPUT_SHAPE", str(exc))
            if mode == "subtract":
                try:
                    vectors = tuple(
                        Vector2D(
                            left.x - right.x,
                            left.y - right.y,
                            left.coordinateSpace,
                        )
                        for left, right in pairs
                    )
                except PayloadValidationError as exc:
                    return _error("E_RESULT_INVALID", str(exc))
                magnitudes = [math.hypot(vector.dx, vector.dy) for vector in vectors]
                if any(not math.isfinite(value) for value in magnitudes):
                    return _error("E_RESULT_INVALID", "vector magnitude is not finite")
                outputs: dict[str, object] = {
                    "vectors": [vector.toPayload() for vector in vectors],
                    "magnitudes": magnitudes,
                    "resultCount": len(vectors),
                }
            else:
                values = [left.x * right.x + left.y * right.y for left, right in pairs]
                if any(not math.isfinite(value) for value in values):
                    return _error("E_RESULT_INVALID", "dot product is not finite")
                dotSum = sum(values)
                if not math.isfinite(dotSum):
                    return _error("E_RESULT_INVALID", "dot product sum is not finite")
                outputs = {
                    "values": values,
                    "dotSum": dotSum,
                    "resultCount": len(values),
                }
            return _coordinateResult(startedAt, mode, outputs)

        if "otherPoints" in inputs:
            return _error("E_INPUT_SHAPE", f"otherPoints is not used by mode={mode}")
        if mode == "scalarMultiply":
            scalarValue = inputs.get("scalar", params.get("scalarValue", 1.0))
            if not _finiteNumber(scalarValue):
                return _error("E_INPUT_TYPE", "scalar must be a finite number")
            scalar = float(cast(int | float, scalarValue))
        else:
            if "scalar" in inputs:
                return _error("E_INPUT_SHAPE", f"scalar is not used by mode={mode}")
            scalar = 1.0
        closed = cast(bool, params.get("closed", False))
        try:
            calculationPoints = _polygonPoints(points) if closed else points
        except ValueError as exc:
            return _error("E_INPUT_SHAPE", str(exc))
        try:
            if mode == "transform":
                transformed = _transformPoints(calculationPoints, params)
            elif mode == "scalarMultiply":
                transformed = tuple(
                    Point2D(
                        point.x * scalar,
                        point.y * scalar,
                        point.coordinateSpace,
                    )
                    for point in calculationPoints
                )
            else:
                transformed = calculationPoints
        except PayloadValidationError as exc:
            return _error("E_RESULT_INVALID", str(exc))
        measurementOutputs = _measurementOutputs(transformed, closed)
        if isinstance(measurementOutputs, str):
            return _error("E_RESULT_INVALID", measurementOutputs)
        return _coordinateResult(startedAt, mode, measurementOutputs)


def _pointList(value: object, name: str) -> tuple[Point2D, ...]:
    if not isinstance(value, list):
        raise TypeError(f"{name} must be a list<point2d>")
    if not value:
        raise ValueError(f"{name} cannot be empty")
    points = tuple(Point2D.fromPayload(item) for item in value)
    space = points[0].coordinateSpace
    if any(
        not coordinateSpacesEquivalent(point.coordinateSpace, space)
        for point in points[1:]
    ):
        raise ValueError(f"all {name} items must use one coordinateSpace")
    return points


def _pointPairs(
    points: tuple[Point2D, ...],
    otherPoints: tuple[Point2D, ...],
    pairing: str,
) -> tuple[tuple[Point2D, Point2D], ...]:
    if len(points) == len(otherPoints):
        pairs = tuple(zip(points, otherPoints, strict=True))
    elif pairing == "broadcast" and len(points) == 1:
        pairs = tuple((points[0], other) for other in otherPoints)
    elif pairing == "broadcast" and len(otherPoints) == 1:
        pairs = tuple((point, otherPoints[0]) for point in points)
    else:
        raise ValueError(
            "points and otherPoints must have equal lengths; "
            "pairing=broadcast also permits one side to contain one point"
        )
    for index, (left, right) in enumerate(pairs):
        if not coordinateSpacesEquivalent(left.coordinateSpace, right.coordinateSpace):
            raise ValueError(f"point pair {index} uses different coordinate spaces")
    return pairs


def _measurementOutputs(
    points: tuple[Point2D, ...],
    closed: bool,
) -> dict[str, object] | str:
    space = points[0].coordinateSpace
    xs = [point.x for point in points]
    ys = [point.y for point in points]
    try:
        centroid = Point2D(sum(xs) / len(xs), sum(ys) / len(ys), space)
        bounds = BBox2D(
            min(xs),
            min(ys),
            max(xs) - min(xs),
            max(ys) - min(ys),
            space,
        )
    except PayloadValidationError as exc:
        return str(exc)
    segmentLengths = [
        math.hypot(right.x - left.x, right.y - left.y)
        for left, right in zip(points, points[1:])
    ]
    pathLength = sum(segmentLengths)
    deltaX = points[-1].x - points[0].x
    deltaY = points[-1].y - points[0].y
    firstToLastDistance = math.hypot(deltaX, deltaY)
    firstToLastAngle = (
        math.degrees(math.atan2(deltaY, deltaX))
        if firstToLastDistance > 0.0
        else 0.0
    )
    signedArea = _signedArea(points) if closed else 0.0
    perimeter = pathLength + (firstToLastDistance if closed else 0.0)
    numericResults = (
        *segmentLengths,
        pathLength,
        firstToLastDistance,
        firstToLastAngle,
        signedArea,
        perimeter,
    )
    if any(not math.isfinite(value) for value in numericResults):
        return "coordinate measurement produced a non-finite result"
    outputs: dict[str, object] = {
        "points": [point.toPayload() for point in points],
        "centroid": centroid.toPayload(),
        "bounds": bounds.toPayload(),
        "segmentLengths": segmentLengths,
        "pathLength": pathLength,
        "perimeter": perimeter,
        "signedArea": signedArea,
        "area": abs(signedArea),
        "firstToLastDistance": firstToLastDistance,
        "firstToLastAngleDegrees": firstToLastAngle,
        "resultCount": len(points),
    }
    if closed:
        try:
            outputs["polygon"] = Polygon2D(_polygonPoints(points)).toPayload()
        except (PayloadValidationError, ValueError) as exc:
            return str(exc)
    return outputs


def _coordinateResult(
    startedAt: float,
    mode: str,
    outputs: dict[str, object],
) -> dict[str, object]:
    resultCount = cast(int, outputs["resultCount"])
    latencyMs = round((perf_counter() - startedAt) * 1000.0, 3)
    return {
        "status": "ok",
        "outputs": outputs,
        "metrics": {
            "latencyMs": latencyMs,
            "mode": mode,
            "resultCount": resultCount,
        },
        "diagnostics": {
            "text": f"Coordinate calculator mode={mode} produced {resultCount} result(s)"
        },
    }


def _resolveCoordinatePath(filePath: str, workspaceValue: object) -> Path:
    requested = Path(filePath).expanduser()
    if requested.is_absolute():
        return requested.resolve()
    if isinstance(workspaceValue, (str, Path)) and str(workspaceValue) != "":
        return (Path(workspaceValue) / requested).resolve()
    return requested.resolve()


def _coordinateSpace(
    inputs: dict[str, object],
    params: dict[str, object],
    path: Path,
) -> CoordinateSpace2D:
    unknownInputs = sorted(set(inputs) - {"frame"})
    if unknownInputs:
        raise TypeError(f"unknown inputs: {', '.join(unknownInputs)}")
    if "frame" in inputs:
        return BBox2D.fromPayload(inputs["frame"]).coordinateSpace
    imageWidth = cast(int, params.get("imageWidth", 0))
    imageHeight = cast(int, params.get("imageHeight", 0))
    sourceId = cast(str, params.get("sourceId", "")).strip() or str(path)
    transform = cast(
        list[int | float], params.get("transformToSource", _IDENTITY_AFFINE)
    )
    return CoordinateSpace2D(
        reference="coordinateFile",
        sourceId=sourceId,
        imageWidth=imageWidth or None,
        imageHeight=imageHeight or None,
        transformToSource=tuple(float(value) for value in transform),
    )


def _readCoordinateRows(
    text: str,
    path: Path,
    params: dict[str, object],
) -> tuple[list[tuple[float, float]], int]:
    commentPrefix = cast(str, params.get("commentPrefix", "#"))
    sourceLines = [
        (lineNumber, line)
        for lineNumber, line in enumerate(text.splitlines(), start=1)
        if line.strip()
        and not (commentPrefix and line.lstrip().startswith(commentPrefix))
    ]
    if not sourceLines:
        raise ValueError("coordinate file contains no data rows")
    fileFormat = cast(str, params.get("fileFormat", "auto"))
    if fileFormat == "auto":
        fileFormat = "csv" if path.suffix.lower() == ".csv" else "txt"
    delimiter = _delimiterCharacter(
        cast(str, params.get("delimiter", "auto")),
        fileFormat,
        "\n".join(line for _, line in sourceLines[:20]),
    )
    xColumn = cast(int, params.get("xColumn", 0))
    yColumn = cast(int, params.get("yColumn", 1))
    headerMode = cast(str, params.get("headerMode", "auto"))
    invalidMode = cast(str, params.get("onInvalidRow", "error"))
    maxPoints = cast(int, params.get("maxPoints", 10000))
    rows: list[tuple[float, float]] = []
    skippedRows = 0
    for index, (lineNumber, line) in enumerate(sourceLines):
        try:
            cells = _splitCoordinateLine(line, delimiter)
            if max(xColumn, yColumn) >= len(cells):
                raise ValueError(
                    f"requires column {max(xColumn, yColumn)}, got {len(cells)} columns"
                )
            x = float(cells[xColumn])
            y = float(cells[yColumn])
            if not math.isfinite(x) or not math.isfinite(y):
                raise ValueError("coordinates must be finite")
        except (ValueError, csv.Error) as exc:
            isHeader = index == 0 and headerMode in {"auto", "present"}
            if isHeader:
                continue
            if invalidMode == "skip":
                skippedRows += 1
                continue
            raise ValueError(f"invalid coordinate row {lineNumber}: {exc}") from exc
        if index == 0 and headerMode == "present":
            continue
        rows.append((x, y))
        if len(rows) > maxPoints:
            raise ValueError(f"coordinate count exceeds maxPoints={maxPoints}")
    if not rows:
        raise ValueError("coordinate file contains no valid coordinates")
    return rows, skippedRows


def _delimiterCharacter(mode: str, fileFormat: str, sample: str) -> str | None:
    explicit = {"comma": ",", "tab": "\t", "semicolon": ";", "space": None}
    if mode != "auto":
        return explicit[mode]
    try:
        return csv.Sniffer().sniff(sample, delimiters=",;\t").delimiter
    except csv.Error:
        return "," if fileFormat == "csv" else None


def _splitCoordinateLine(line: str, delimiter: str | None) -> list[str]:
    if delimiter is None:
        return line.split()
    return [cell.strip() for cell in next(csv.reader([line], delimiter=delimiter))]


def _polygonPoints(points: tuple[Point2D, ...]) -> tuple[Point2D, ...]:
    polygonPoints = points
    if len(points) > 1 and points[0].x == points[-1].x and points[0].y == points[-1].y:
        polygonPoints = points[:-1]
    if len(polygonPoints) < 3:
        raise ValueError("polygon output requires at least 3 coordinates")
    if abs(_signedArea(polygonPoints)) <= 1e-12:
        raise ValueError("polygon output requires a non-zero area")
    return polygonPoints


def _transformPoints(
    points: tuple[Point2D, ...],
    params: dict[str, object],
) -> tuple[Point2D, ...]:
    pivotMode = cast(str, params.get("pivot", "origin"))
    if pivotMode == "centroid":
        pivotX = sum(point.x for point in points) / len(points)
        pivotY = sum(point.y for point in points) / len(points)
    elif pivotMode == "first":
        pivotX, pivotY = points[0].x, points[0].y
    elif pivotMode == "custom":
        pivotX = float(cast(float, params.get("pivotX", 0.0)))
        pivotY = float(cast(float, params.get("pivotY", 0.0)))
    else:
        pivotX = pivotY = 0.0
    offsetX = float(cast(float, params.get("offsetX", 0.0)))
    offsetY = float(cast(float, params.get("offsetY", 0.0)))
    scaleX = float(cast(float, params.get("scaleX", 1.0)))
    scaleY = float(cast(float, params.get("scaleY", 1.0)))
    radians = math.radians(float(cast(float, params.get("rotationDegrees", 0.0))))
    cosine = math.cos(radians)
    sine = math.sin(radians)
    space = points[0].coordinateSpace
    transformed: list[Point2D] = []
    for point in points:
        localX = (point.x - pivotX) * scaleX
        localY = (point.y - pivotY) * scaleY
        transformed.append(
            Point2D(
                pivotX + localX * cosine - localY * sine + offsetX,
                pivotY + localX * sine + localY * cosine + offsetY,
                space,
            )
        )
    return tuple(transformed)


def _signedArea(points: tuple[Point2D, ...]) -> float:
    return 0.5 * sum(
        left.x * right.y - right.x * left.y
        for left, right in zip(points, (*points[1:], points[0]))
    )


def _integerInRange(value: object, minimum: int, maximum: int) -> bool:
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and minimum <= value <= maximum
    )


def _finiteNumber(value: object) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def _paramError(message: str) -> dict[str, str]:
    return {"code": "E_PARAM_INVALID", "message": message}


def _error(code: str, message: str) -> dict[str, object]:
    return {"status": "error", "error": {"code": code, "message": message}}
