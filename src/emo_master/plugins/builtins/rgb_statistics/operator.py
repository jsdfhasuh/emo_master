from __future__ import annotations

import math
from dataclasses import dataclass
from time import perf_counter
from typing import Any, TypeGuard, cast

import cv2
import numpy as np

from emo_master.core.contracts.geometry2d import (
    BBox2D,
    ChannelStatistics,
    Circle2D,
    ColorStatistics,
    CoordinateSpace2D,
    Geometry2D,
    Line2D,
    PayloadValidationError,
    Point2D,
    Polygon2D,
    RotatedBox2D,
    parseGeometry2D,
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
    inputPorts: dict[str, object]
    outputPorts: dict[str, object]
    paramSchema: dict[str, object]


_PARAM_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "ddof": {"type": "integer", "enum": [0, 1], "default": 0},
        "clipRoi": {"type": "boolean", "default": True},
        "emitMask": {"type": "boolean", "default": False},
    },
}


class RgbStatisticsOperator:
    meta = OperatorMeta(
        operatorId="vision.color.rgb_statistics",
        displayName="RGB Statistics",
        version="1.1.0",
        inputPorts={
            "image": {"type": "image", "required": True, "nullable": False},
            "roi": {
                "type": "geometry2d",
                "required": False,
                "nullable": True,
                "schemaVersion": "1.x",
            },
            "frame": {
                "type": "bbox2d",
                "required": False,
                "nullable": False,
                "schemaVersion": "1.x",
            },
        },
        outputPorts={
            "statistics": {
                "type": "colorStatistics",
                "required": True,
                "schemaVersion": "1.x",
            },
            "mask": {"type": "image", "required": False},
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
        ddof = params.get("ddof", 0)
        if not isinstance(ddof, int) or isinstance(ddof, bool) or ddof not in (0, 1):
            return _paramError("ddof must be 0 or 1")
        for name, default in (("clipRoi", True), ("emitMask", False)):
            if not isinstance(params.get(name, default), bool):
                return _paramError(f"{name} must be boolean")
        return None

    def executeNode(
        self,
        inputs: dict[str, object],
        params: dict[str, object],
        runtimeContext: dict[str, object],
    ) -> dict[str, Any]:
        _ = runtimeContext
        startedAt = perf_counter()
        if "image" not in inputs:
            return _error("E_INPUT_MISSING", "input 'image' is required")
        image = inputs["image"]
        if not isinstance(image, np.ndarray):
            return _error("E_INPUT_TYPE", "input 'image' must be numpy.ndarray")
        if image.size == 0 or image.ndim != 3 or image.shape[2] != 3:
            return _error("E_INPUT_SHAPE", "image must be a non-empty BGR image")
        if (
            not np.issubdtype(image.dtype, np.number)
            or np.issubdtype(image.dtype, np.bool_)
            or np.issubdtype(image.dtype, np.complexfloating)
        ):
            return _error("E_INPUT_TYPE", "image pixels must be real numeric values")
        if not bool(np.all(np.isfinite(image))):
            return _error("E_INPUT_TYPE", "image pixels must be finite")

        paramError = self.validateParams(params)
        if paramError is not None:
            return {"status": "error", "error": paramError}
        ddof = cast(int, params.get("ddof", 0))
        clipRoi = cast(bool, params.get("clipRoi", True))
        emitMask = cast(bool, params.get("emitMask", False))

        height, width = image.shape[:2]
        rawRoi = inputs.get("roi")
        try:
            parsedRegion = (
                None
                if rawRoi is None
                else rawRoi
                if _isGeometry(rawRoi)
                else parseGeometry2D(rawRoi)
            )
            if parsedRegion is not None:
                coordinateError = _coordinateSpaceError(
                    parsedRegion.coordinateSpace, width, height
                )
                if coordinateError is not None:
                    return _error("E_INPUT_SHAPE", coordinateError)
                fallback = BBox2D(
                    0.0,
                    0.0,
                    float(width),
                    float(height),
                    parsedRegion.coordinateSpace,
                )
            else:
                fallback = None
            frame = frameForInput(inputs, width, height, fallback=fallback)
            if parsedRegion is None:
                region: Geometry2D = frame
                mask = frameMask(frame, width, height)
            else:
                region = parsedRegion
                if not coordinateSpacesEquivalent(
                    region.coordinateSpace,
                    frame.coordinateSpace,
                ):
                    return _error(
                        "E_INPUT_SHAPE",
                        "ROI and frame coordinate spaces do not match",
                    )
                mask = _geometryMask(region, width, height, clipRoi)
        except PayloadValidationError as err:
            return _error("E_INPUT_TYPE", f"invalid geometry payload: {err}")
        except ValueError as err:
            return _error("E_INPUT_SHAPE", str(err))

        selected = image[mask > 0]
        pixelCount = int(selected.shape[0])
        if pixelCount == 0:
            return _error("E_INPUT_SHAPE", "ROI does not contain any image pixels")
        if ddof >= pixelCount:
            return _error("E_INPUT_SHAPE", "ddof must be smaller than ROI pixel count")

        rgbPixels = selected[:, ::-1].astype(np.float64, copy=False)
        channels = {
            name: _channelStatistics(rgbPixels[:, index], ddof)
            for index, name in enumerate(("r", "g", "b"))
        }
        statistics = ColorStatistics(
            channels=channels,
            pixelCount=pixelCount,
            colorSpace="RGB",
            region=region,
            attributes={"ddof": ddof},
        )
        outputs: dict[str, object] = {
            "statistics": statistics.toPayload(),
            "frame": frame.toPayload(),
        }
        if emitMask:
            outputs["mask"] = mask
        elapsedMs = (perf_counter() - startedAt) * 1000.0
        return {
            "status": "ok",
            "outputs": outputs,
            "metrics": {
                "latencyMs": round(elapsedMs, 3),
                "pixelCount": pixelCount,
            },
            "diagnostics": {"text": f"Computed RGB statistics for {pixelCount} pixels"},
        }


def _channelStatistics(values: np.ndarray[Any, Any], ddof: int) -> ChannelStatistics:
    return ChannelStatistics(
        minimum=float(np.min(values)),
        maximum=float(np.max(values)),
        mean=float(np.mean(values)),
        standardDeviation=float(np.std(values, ddof=ddof)),
    )


def _coordinateSpaceError(
    coordinateSpace: CoordinateSpace2D,
    width: int,
    height: int,
) -> str | None:
    if coordinateSpace.imageWidth is not None and (
        coordinateSpace.imageWidth != width or coordinateSpace.imageHeight != height
    ):
        return "ROI coordinateSpace dimensions do not match the input image"
    return None


def _geometryMask(
    geometry: Geometry2D,
    width: int,
    height: int,
    clipRoi: bool,
) -> np.ndarray[Any, Any]:
    mask = np.zeros((height, width), dtype=np.uint8)
    if isinstance(geometry, Point2D):
        x = math.floor(geometry.x)
        y = math.floor(geometry.y)
        if not 0 <= x < width or not 0 <= y < height:
            raise ValueError("point ROI lies outside the input image")
        mask[y, x] = 255
        return mask
    if isinstance(geometry, BBox2D):
        left = math.floor(geometry.x)
        top = math.floor(geometry.y)
        right = math.ceil(geometry.x + geometry.width)
        bottom = math.ceil(geometry.y + geometry.height)
        if not clipRoi and (left < 0 or top < 0 or right > width or bottom > height):
            raise ValueError("bbox ROI extends outside the input image")
        left = max(0, min(width, left))
        right = max(0, min(width, right))
        top = max(0, min(height, top))
        bottom = max(0, min(height, bottom))
        mask[top:bottom, left:right] = 255
        return mask
    if isinstance(geometry, Line2D):
        linePoints = (
            (geometry.start.x, geometry.start.y),
            (geometry.end.x, geometry.end.y),
        )
        if not clipRoi and any(
            x < 0 or x >= width or y < 0 or y >= height for x, y in linePoints
        ):
            raise ValueError("line ROI extends outside the input image")
        cv2.line(  # type: ignore[call-overload]
            mask,
            (int(round(geometry.start.x)), int(round(geometry.start.y))),
            (int(round(geometry.end.x)), int(round(geometry.end.y))),
            255,
            1,
            cv2.LINE_8,
        )
        return mask
    if isinstance(geometry, Circle2D):
        if not clipRoi and (
            geometry.center.x - geometry.radius < 0
            or geometry.center.y - geometry.radius < 0
            or geometry.center.x + geometry.radius >= width
            or geometry.center.y + geometry.radius >= height
        ):
            raise ValueError("circle ROI extends outside the input image")
        cv2.circle(  # type: ignore[call-overload]
            mask,
            (int(round(geometry.center.x)), int(round(geometry.center.y))),
            max(1, int(round(geometry.radius))),
            255,
            -1,
            cv2.LINE_8,
        )
        return mask

    points = _geometryPoints(geometry)
    if not clipRoi and any(
        x < 0 or x > width or y < 0 or y > height for x, y in points
    ):
        raise ValueError("ROI extends outside the input image")
    polygon = np.rint(np.asarray(points, dtype=np.float64)).astype(np.int32)
    cv2.fillPoly(mask, [polygon], 255)  # type: ignore[call-overload]
    return mask


def _geometryPoints(
    geometry: Polygon2D | RotatedBox2D,
) -> list[tuple[float, float]]:
    if isinstance(geometry, Polygon2D):
        return [(point.x, point.y) for point in geometry.points]
    radians = math.radians(geometry.angleDegrees)
    cosine = math.cos(radians)
    sine = math.sin(radians)
    halfWidth = geometry.width / 2.0
    halfHeight = geometry.height / 2.0
    return [
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
    ]


def _isGeometry(value: object) -> TypeGuard[Geometry2D]:
    return isinstance(
        value,
        (Point2D, BBox2D, RotatedBox2D, Polygon2D, Line2D, Circle2D),
    )


def _paramError(message: str) -> dict[str, str]:
    return {"code": "E_PARAM_INVALID", "message": message}


def _error(code: str, message: str) -> dict[str, Any]:
    return {"status": "error", "error": {"code": code, "message": message}}
