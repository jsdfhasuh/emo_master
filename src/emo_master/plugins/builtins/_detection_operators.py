from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from time import perf_counter
from typing import Any, TypeGuard, cast

import cv2
import numpy as np

from emo_master.core.contracts.geometry2d import (
    BBox2D,
    Circle2D,
    CircleCollection,
    Geometry2D,
    CircleItem,
    Line2D,
    LineCollection,
    LineItem,
    PayloadValidationError,
    Point2D,
    Polygon2D,
    RotatedBox2D,
    TemplateMatch,
    TemplateMatchCollection,
    parseGeometry2D,
)
from emo_master.plugins.builtins._classic_vision import (
    BuiltinInputError,
    errorResult,
    geometryMask,
    requireImage,
)
from emo_master.plugins.builtins._image_frame import (
    coordinateSpacesEquivalent,
    frameForInput,
    frameMask,
)


@dataclass(frozen=True)
class OperatorMeta:
    operatorId: str
    displayName: str
    version: str
    inputPorts: Mapping[str, object]
    outputPorts: Mapping[str, object]
    paramSchema: Mapping[str, object]


_METHODS = {
    "sqdiff": cv2.TM_SQDIFF,
    "sqdiffNormed": cv2.TM_SQDIFF_NORMED,
    "ccorr": cv2.TM_CCORR,
    "ccorrNormed": cv2.TM_CCORR_NORMED,
    "ccoeff": cv2.TM_CCOEFF,
    "ccoeffNormed": cv2.TM_CCOEFF_NORMED,
}


class TemplateMatchingOperator:
    _PARAM_SCHEMA = {
        "type": "object",
        "properties": {
            "method": {
                "type": "string",
                "enum": list(_METHODS),
                "default": "ccoeffNormed",
            },
            "colorMode": {
                "type": "string",
                "enum": ["gray", "native"],
                "default": "gray",
            },
            "thresholdMode": {
                "type": "string",
                "enum": ["quality", "raw"],
                "default": "quality",
            },
            "threshold": {"type": "number", "default": 0.8},
            "peakKernelSize": {
                "type": "integer",
                "minimum": 1,
                "maximum": 31,
                "default": 3,
            },
            "nmsIouThreshold": {
                "type": "number",
                "minimum": 0.0,
                "maximum": 1.0,
                "default": 0.3,
            },
            "maxMatches": {
                "type": "integer",
                "minimum": 1,
                "maximum": 10000,
                "default": 100,
            },
            "label": {"type": "string", "default": "template"},
        },
    }
    meta = OperatorMeta(
        operatorId="vision.analysis.template_match",
        displayName="Template Matching",
        version="1.0.0",
        inputPorts={
            "image": {"type": "image", "required": True, "nullable": False},
            "template": {"type": "image", "required": True, "nullable": False},
            "frame": {
                "type": "bbox2d",
                "required": False,
                "nullable": False,
                "schemaVersion": "1.x",
            },
            "roi": {
                "type": "geometry2d",
                "required": False,
                "nullable": False,
                "schemaVersion": "1.x",
            },
        },
        outputPorts={
            "matches": {
                "type": "templateMatchCollection",
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
        if params.get("method", "ccoeffNormed") not in _METHODS:
            return _paramError("method is not supported")
        if params.get("colorMode", "gray") not in {"gray", "native"}:
            return _paramError("colorMode must be gray or native")
        thresholdMode = params.get("thresholdMode", "quality")
        if thresholdMode not in {"quality", "raw"}:
            return _paramError("thresholdMode must be quality or raw")
        threshold = params.get("threshold", 0.8)
        if not _finite(threshold):
            return _paramError("threshold must be finite")
        if thresholdMode == "quality" and not 0.0 <= float(threshold) <= 1.0:
            return _paramError("quality threshold must be in [0, 1]")
        peak = params.get("peakKernelSize", 3)
        if not _integer(peak) or not 1 <= peak <= 31 or peak % 2 == 0:
            return _paramError("peakKernelSize must be an odd integer in [1, 31]")
        nms = params.get("nmsIouThreshold", 0.3)
        if not _finite(nms) or not 0.0 <= float(nms) <= 1.0:
            return _paramError("nmsIouThreshold must be in [0, 1]")
        maximum = params.get("maxMatches", 100)
        if not _integer(maximum) or not 1 <= maximum <= 10000:
            return _paramError("maxMatches must be an integer in [1, 10000]")
        if not isinstance(params.get("label", "template"), str):
            return _paramError("label must be a string")
        return None

    def executeNode(
        self,
        inputs: dict[str, object],
        params: dict[str, object],
        runtimeContext: dict[str, object],
    ) -> dict[str, Any]:
        _ = runtimeContext
        started = perf_counter()
        error = self.validateParams(params)
        if error is not None:
            return {"status": "error", "error": error}
        try:
            image = requireImage(inputs)
            template = requireImage(inputs, "template")
            height, width = image.shape[:2]
            templateHeight, templateWidth = template.shape[:2]
            if templateWidth > width or templateHeight > height:
                raise BuiltinInputError(
                    "E_INPUT_SHAPE", "template must not be larger than the search image"
                )
            frame = frameForInput(inputs, width, height)
            colorMode = cast(str, params.get("colorMode", "gray"))
            if colorMode == "gray":
                searchValue = (
                    image
                    if image.ndim == 2
                    else cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
                )
                templateValue = (
                    template
                    if template.ndim == 2
                    else cv2.cvtColor(template, cv2.COLOR_BGR2GRAY)
                )
            else:
                if image.ndim != template.ndim or (
                    image.ndim == 3 and image.shape[2] != template.shape[2]
                ):
                    raise BuiltinInputError(
                        "E_INPUT_SHAPE",
                        "native matching requires identical channel structures",
                    )
                searchValue, templateValue = image, template
            validPixels = frameMask(frame, width, height)
            if "roi" in inputs:
                rawRoi = inputs["roi"]
                roi = rawRoi if _isGeometry(rawRoi) else parseGeometry2D(rawRoi)
                if not coordinateSpacesEquivalent(
                    roi.coordinateSpace, frame.coordinateSpace
                ):
                    raise BuiltinInputError(
                        "E_INPUT_SHAPE", "ROI coordinate space does not match image"
                    )
                validPixels = cv2.bitwise_and(
                    validPixels, geometryMask(roi, width, height)
                )
            validPositions = _validTemplatePositions(
                validPixels, templateWidth, templateHeight
            )
            method = cast(str, params.get("method", "ccoeffNormed"))
            response = cv2.matchTemplate(searchValue, templateValue, _METHODS[method])
            quality = _quality(response, validPositions, method)
            threshold = float(cast(int | float, params.get("threshold", 0.8)))
            if params.get("thresholdMode", "quality") == "quality":
                accepted = quality >= threshold
            elif method.startswith("sqdiff"):
                accepted = response <= threshold
            else:
                accepted = response >= threshold
            accepted &= validPositions
            peakSize = cast(int, params.get("peakKernelSize", 3))
            localMaximum = cv2.dilate(
                quality, np.ones((peakSize, peakSize), dtype=np.uint8)
            )
            accepted &= quality >= localMaximum - 1e-12
            ys, xs = np.nonzero(accepted)
            candidates = sorted(
                (
                    (float(quality[y, x]), int(y), int(x), float(response[y, x]))
                    for y, x in zip(ys, xs, strict=True)
                ),
                key=lambda item: (-item[0], item[1], item[2]),
            )
            selected: list[tuple[float, int, int, float]] = []
            nms = float(cast(int | float, params.get("nmsIouThreshold", 0.3)))
            maximum = cast(int, params.get("maxMatches", 100))
            for candidate in candidates:
                if all(
                    _iou(candidate[2], candidate[1], kept[2], kept[1], templateWidth, templateHeight)
                    <= nms
                    for kept in selected
                ):
                    selected.append(candidate)
                    if len(selected) >= maximum:
                        break
            items = tuple(
                TemplateMatch(
                    matchId=f"match-{index + 1}",
                    bbox=BBox2D(
                        float(x),
                        float(y),
                        float(templateWidth),
                        float(templateHeight),
                        frame.coordinateSpace,
                    ),
                    rawScore=raw,
                    quality=score,
                )
                for index, (score, y, x, raw) in enumerate(selected)
            )
            collection = TemplateMatchCollection(
                method=method,
                label=cast(str, params.get("label", "template")),
                templateWidth=templateWidth,
                templateHeight=templateHeight,
                items=items,
                coordinateSpace=frame.coordinateSpace,
            )
            return {
                "status": "ok",
                "outputs": {
                    "matches": collection.toPayload(),
                    "frame": frame.toPayload(),
                },
                "metrics": {
                    "latencyMs": round((perf_counter() - started) * 1000.0, 3),
                    "matchCount": len(items),
                    "method": method,
                },
                "diagnostics": {"text": f"Found {len(items)} template matches"},
            }
        except BuiltinInputError as err:
            return errorResult(err.code, str(err))
        except PayloadValidationError as err:
            return errorResult("E_INPUT_TYPE", f"invalid semantic input: {err}")
        except ValueError as err:
            return errorResult("E_INPUT_SHAPE", str(err))
        except cv2.error as err:
            return errorResult("E_EXEC_FAILED", f"template matching failed: {err}")


class HoughLineOperator:
    _PARAM_SCHEMA = {
        "type": "object",
        "properties": {
            "rho": {"type": "number", "exclusiveMinimum": 0.0, "default": 1.0},
            "thetaDegrees": {
                "type": "number",
                "exclusiveMinimum": 0.0,
                "maximum": 180.0,
                "default": 1.0,
            },
            "threshold": {"type": "integer", "minimum": 1, "default": 50},
            "minLineLength": {"type": "number", "minimum": 0.0, "default": 0.0},
            "maxLineGap": {"type": "number", "minimum": 0.0, "default": 0.0},
        },
    }
    meta = OperatorMeta(
        operatorId="vision.analysis.hough_line",
        displayName="Hough Line",
        version="1.0.0",
        inputPorts={
            "edge": {"type": "image", "required": True, "nullable": False},
            "frame": {
                "type": "bbox2d",
                "required": False,
                "nullable": False,
                "schemaVersion": "1.x",
            },
        },
        outputPorts={
            "lines": {
                "type": "lineCollection",
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
        rho = params.get("rho", 1.0)
        theta = params.get("thetaDegrees", 1.0)
        threshold = params.get("threshold", 50)
        if not _finite(rho) or float(rho) <= 0.0:
            return _paramError("rho must be > 0")
        if not _finite(theta) or not 0.0 < float(theta) <= 180.0:
            return _paramError("thetaDegrees must be in (0, 180]")
        if not _integer(threshold) or threshold < 1:
            return _paramError("threshold must be an integer >= 1")
        for name in ("minLineLength", "maxLineGap"):
            value = params.get(name, 0.0)
            if not _finite(value) or float(value) < 0.0:
                return _paramError(f"{name} must be >= 0")
        return None

    def executeNode(
        self,
        inputs: dict[str, object],
        params: dict[str, object],
        runtimeContext: dict[str, object],
    ) -> dict[str, Any]:
        _ = runtimeContext
        started = perf_counter()
        error = self.validateParams(params)
        if error is not None:
            return {"status": "error", "error": error}
        try:
            edge = requireImage(inputs, "edge", grayscaleOnly=True)
            height, width = edge.shape
            frame = frameForInput(inputs, width, height)
            value = cv2.bitwise_and(edge, frameMask(frame, width, height))
            raw = cv2.HoughLinesP(
                value,
                float(cast(int | float, params.get("rho", 1.0))),
                math.radians(
                    float(cast(int | float, params.get("thetaDegrees", 1.0)))
                ),
                cast(int, params.get("threshold", 50)),
                minLineLength=float(
                    cast(int | float, params.get("minLineLength", 0.0))
                ),
                maxLineGap=float(cast(int | float, params.get("maxLineGap", 0.0))),
            )
            segments: list[tuple[float, float, float, float]] = []
            if raw is not None:
                for values in raw.reshape(-1, 4):
                    first = (float(values[0]), float(values[1]))
                    second = (float(values[2]), float(values[3]))
                    if (first[1], first[0]) > (second[1], second[0]):
                        first, second = second, first
                    if first != second:
                        segments.append((*first, *second))
            segments.sort(
                key=lambda item: (
                    -math.hypot(item[2] - item[0], item[3] - item[1]),
                    item[1],
                    item[0],
                    item[3],
                    item[2],
                )
            )
            items = tuple(
                LineItem(
                    f"line-{index + 1}",
                    Line2D(
                        Point2D(x1, y1, frame.coordinateSpace),
                        Point2D(x2, y2, frame.coordinateSpace),
                    ),
                )
                for index, (x1, y1, x2, y2) in enumerate(segments)
            )
            collection = LineCollection(items, frame.coordinateSpace)
            return _collectionResult(
                "lines", collection.toPayload(), frame.toPayload(), len(items), started
            )
        except BuiltinInputError as err:
            return errorResult(err.code, str(err))
        except PayloadValidationError as err:
            return errorResult("E_INPUT_TYPE", str(err))
        except ValueError as err:
            return errorResult("E_INPUT_SHAPE", str(err))
        except cv2.error as err:
            return errorResult("E_EXEC_FAILED", f"Hough line failed: {err}")


class HoughCircleOperator:
    _PARAM_SCHEMA = {
        "type": "object",
        "properties": {
            "dp": {"type": "number", "exclusiveMinimum": 0.0, "default": 1.0},
            "minDist": {"type": "number", "exclusiveMinimum": 0.0, "default": 20.0},
            "param1": {"type": "number", "exclusiveMinimum": 0.0, "default": 100.0},
            "param2": {"type": "number", "exclusiveMinimum": 0.0, "default": 30.0},
            "minRadius": {"type": "integer", "minimum": 0, "default": 0},
            "maxRadius": {"type": "integer", "minimum": 0, "default": 0},
        },
    }
    meta = OperatorMeta(
        operatorId="vision.analysis.hough_circle",
        displayName="Hough Circle",
        version="1.0.0",
        inputPorts={
            "image": {"type": "image", "required": True, "nullable": False},
            "frame": {
                "type": "bbox2d",
                "required": False,
                "nullable": False,
                "schemaVersion": "1.x",
            },
        },
        outputPorts={
            "circles": {
                "type": "circleCollection",
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
        for name, default in (
            ("dp", 1.0),
            ("minDist", 20.0),
            ("param1", 100.0),
            ("param2", 30.0),
        ):
            value = params.get(name, default)
            if not _finite(value) or float(value) <= 0.0:
                return _paramError(f"{name} must be a finite number > 0")
        minimum = params.get("minRadius", 0)
        maximum = params.get("maxRadius", 0)
        if not _integer(minimum) or minimum < 0:
            return _paramError("minRadius must be an integer >= 0")
        if not _integer(maximum) or maximum < 0:
            return _paramError("maxRadius must be an integer >= 0")
        if maximum > 0 and minimum > maximum:
            return _paramError("minRadius must be <= maxRadius")
        return None

    def executeNode(
        self,
        inputs: dict[str, object],
        params: dict[str, object],
        runtimeContext: dict[str, object],
    ) -> dict[str, Any]:
        _ = runtimeContext
        started = perf_counter()
        error = self.validateParams(params)
        if error is not None:
            return {"status": "error", "error": error}
        try:
            image = requireImage(inputs)
            gray = image if image.ndim == 2 else cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
            height, width = gray.shape
            frame = frameForInput(inputs, width, height)
            gray = cv2.bitwise_and(gray, frameMask(frame, width, height))
            raw = cv2.HoughCircles(
                gray,
                cv2.HOUGH_GRADIENT,
                dp=float(cast(int | float, params.get("dp", 1.0))),
                minDist=float(cast(int | float, params.get("minDist", 20.0))),
                param1=float(cast(int | float, params.get("param1", 100.0))),
                param2=float(cast(int | float, params.get("param2", 30.0))),
                minRadius=cast(int, params.get("minRadius", 0)),
                maxRadius=cast(int, params.get("maxRadius", 0)),
            )
            values = [] if raw is None else [tuple(map(float, row)) for row in raw[0]]
            values = [value for value in values if value[2] > 0.0]
            values.sort(key=lambda item: (-item[2], item[1], item[0]))
            items = tuple(
                CircleItem(
                    f"circle-{index + 1}",
                    Circle2D(
                        Point2D(centerX, centerY, frame.coordinateSpace), radius
                    ),
                )
                for index, (centerX, centerY, radius) in enumerate(values)
            )
            collection = CircleCollection(items, frame.coordinateSpace)
            return _collectionResult(
                "circles", collection.toPayload(), frame.toPayload(), len(items), started
            )
        except BuiltinInputError as err:
            return errorResult(err.code, str(err))
        except PayloadValidationError as err:
            return errorResult("E_INPUT_TYPE", str(err))
        except ValueError as err:
            return errorResult("E_INPUT_SHAPE", str(err))
        except cv2.error as err:
            return errorResult("E_EXEC_FAILED", f"Hough circle failed: {err}")


def _validTemplatePositions(
    mask: np.ndarray[Any, Any], templateWidth: int, templateHeight: int
) -> np.ndarray[Any, Any]:
    binary = (mask != 0).astype(np.uint8)
    integral = cv2.integral(binary, sdepth=cv2.CV_64F)
    sums = (
        integral[templateHeight:, templateWidth:]
        - integral[:-templateHeight, templateWidth:]
        - integral[templateHeight:, :-templateWidth]
        + integral[:-templateHeight, :-templateWidth]
    )
    return sums == float(templateWidth * templateHeight)


def _quality(
    response: np.ndarray[Any, Any], valid: np.ndarray[Any, Any], method: str
) -> np.ndarray[Any, Any]:
    raw = response.astype(np.float64)
    if method == "sqdiffNormed":
        return 1.0 - np.clip(raw, 0.0, 1.0)
    if method == "ccorrNormed":
        return np.clip(raw, 0.0, 1.0)
    if method == "ccoeffNormed":
        return np.clip((raw + 1.0) / 2.0, 0.0, 1.0)
    values = raw[valid]
    quality = np.zeros(raw.shape, dtype=np.float64)
    if values.size == 0:
        return quality
    minimum = float(values.min())
    maximum = float(values.max())
    if math.isclose(minimum, maximum, rel_tol=1e-12, abs_tol=1e-12):
        return quality
    quality = (raw - minimum) / (maximum - minimum)
    if method == "sqdiff":
        quality = 1.0 - quality
    return np.clip(quality, 0.0, 1.0)


def _iou(
    firstX: int,
    firstY: int,
    secondX: int,
    secondY: int,
    width: int,
    height: int,
) -> float:
    intersectionWidth = max(0, min(firstX + width, secondX + width) - max(firstX, secondX))
    intersectionHeight = max(
        0, min(firstY + height, secondY + height) - max(firstY, secondY)
    )
    intersection = intersectionWidth * intersectionHeight
    return intersection / float(2 * width * height - intersection)


def _collectionResult(
    portName: str,
    collection: dict[str, object],
    frame: dict[str, object],
    count: int,
    started: float,
) -> dict[str, Any]:
    return {
        "status": "ok",
        "outputs": {portName: collection, "frame": frame},
        "metrics": {
            "latencyMs": round((perf_counter() - started) * 1000.0, 3),
            "count": count,
        },
        "diagnostics": {"text": f"Detected {count} {portName}"},
    }


def _paramError(message: str) -> dict[str, str]:
    return {"code": "E_PARAM_INVALID", "message": message}


def _integer(value: object) -> TypeGuard[int]:
    return isinstance(value, int) and not isinstance(value, bool)


def _finite(value: object) -> TypeGuard[int | float]:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def _isGeometry(value: object) -> TypeGuard[Geometry2D]:
    return isinstance(
        value,
        (Point2D, BBox2D, RotatedBox2D, Polygon2D, Line2D, Circle2D),
    )
