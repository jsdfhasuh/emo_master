"""Typed bridges between detection, measurement and coordinate operators.

No overlay image is a measurement source; no implicit target selection, unit
conversion, or Blob-inner-center algorithm is implemented here.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import cv2
import numpy as np

from emo_master.core.contracts.geometry2d import (
    BBox2D, Circle2D, CircleCollection, CircleItem, ContourCollection,
    DetectionCollection, PayloadValidationError, Point2D, ShapeMeasurementCollection,
)


@dataclass(frozen=True)
class OperatorMeta:
    operatorId: str
    displayName: str
    version: str
    inputPorts: dict[str, object]
    outputPorts: dict[str, object]
    paramSchema: dict[str, object]


def port(typeName: str, version: str = "1.x", required: bool = True) -> dict[str, object]:
    return {"type": typeName, "required": required, "nullable": False, "schemaVersion": version}


def error(code: str, message: str) -> dict[str, Any]:
    return {"status": "error", "error": {"code": code, "message": message}}


class MinimumEnclosingCircleOperator:
    meta = OperatorMeta("vision.analysis.minimum_enclosing_circle", "最小外接圆", "1.0.0",
        {"contours": port("contourCollection", "1.2")},
        {"circles": port("circleCollection", "1.2"),
         "circleCount": {"type": "integer", "required": True, "nullable": False}},
        {"type": "object", "properties": {}, "additionalProperties": False})

    def validateParams(self, params):
        return {"code": "E_PARAM_INVALID", "message": "no parameters are supported"} if params else None

    def executeNode(self, inputs, params, runtimeContext):
        if self.validateParams(params):
            return {"status": "error", "error": self.validateParams(params)}
        if "contours" not in inputs:
            return error("E_INPUT_MISSING", "contours is required")
        try:
            contours = ContourCollection.fromPayload(inputs["contours"])
            circles = []
            for item in contours.items:
                coordinates = np.array([(p.x, p.y) for p in item.polygon.points], dtype=np.float32)
                if not np.all(np.isfinite(coordinates)):
                    return error("E_INPUT_SHAPE", "contour exceeds finite float32 measurement range")
                (x, y), radius = cv2.minEnclosingCircle(coordinates.reshape(-1, 1, 2))
                circles.append(CircleItem(f"enclosing:{item.contourId}",
                    Circle2D(Point2D(float(x), float(y), contours.coordinateSpace), float(radius))))
            result = CircleCollection(tuple(circles), contours.coordinateSpace)
            return {"status": "ok", "outputs": {"circles": result.toPayload(), "circleCount": len(circles)},
                    "diagnostics": {"algorithm": "cv2.minEnclosingCircle", "sourceContourIds": [i.contourId for i in contours.items]}}
        except PayloadValidationError as exc:
            return error("E_INPUT_TYPE", str(exc))
        except cv2.error as exc:
            return error("E_EXEC_FAILED", str(exc))


class DetectionBBoxOperator:
    meta = OperatorMeta("vision.geometry.detection_bbox", "检测区域提取（单对象）", "1.0.0",
        {"detections": port("detectionCollection")}, {"bbox": port("bbox2d")},
        {"type": "object", "properties": {}, "additionalProperties": False})

    def validateParams(self, params):
        return {"code": "E_PARAM_INVALID", "message": "filter/select targets before extraction"} if params else None

    def executeNode(self, inputs, params, runtimeContext):
        if self.validateParams(params):
            return {"status": "error", "error": self.validateParams(params)}
        if "detections" not in inputs:
            return error("E_INPUT_MISSING", "detections is required")
        try:
            detections = DetectionCollection.fromPayload(inputs["detections"])
            if len(detections.items) != 1:
                return error("E_INPUT_SHAPE", "exactly one selected detection is required; no implicit first target")
            item = detections.items[0]
            return {"status": "ok", "outputs": {"bbox": item.bbox.toPayload()},
                    "diagnostics": {"detectionId": item.detectionId}}
        except PayloadValidationError as exc:
            return error("E_INPUT_TYPE", str(exc))


class GeometryPointsOperator:
    meta = OperatorMeta("vision.geometry.extract_points", "几何特征取点", "1.0.0",
        {"circles": port("circleCollection", "1.2", False),
         "measurements": port("shapeMeasurementCollection", "1.2", False),
         "bbox": port("bbox2d", required=False)},
        {"points": port("list<point2d>"), "pointCount": {"type": "integer", "required": True, "nullable": False}},
        {"type": "object", "additionalProperties": False, "properties": {
            "feature": {"title": "取点特征", "type": "string", "default": "circleCenter",
                "enum": ["circleCenter", "bboxCenter", "bboxTopLeft", "minAreaRectCenter", "centroid"],
                "xOptionLabels": {"circleCenter": "圆心", "bboxCenter": "轴对齐包围框中心", "bboxTopLeft": "轴对齐包围框左上角",
                    "minAreaRectCenter": "最小面积旋转矩形中心", "centroid": "轮廓质心（不是 Blob 内心）"}}}})

    def validateParams(self, params):
        allowed = self.meta.paramSchema["properties"]["feature"]["enum"]
        if set(params) - {"feature"} or params.get("feature", "circleCenter") not in allowed:
            return {"code": "E_PARAM_INVALID", "message": "unsupported feature"}
        return None

    def executeNode(self, inputs, params, runtimeContext):
        if self.validateParams(params):
            return {"status": "error", "error": self.validateParams(params)}
        names = [name for name in ("circles", "measurements", "bbox") if name in inputs]
        if len(names) != 1:
            return error("E_INPUT_SHAPE", "exactly one geometry collection or bbox is required")
        feature = params.get("feature", "circleCenter")
        name = names[0]
        try:
            if name == "circles" and feature == "circleCenter":
                points = [item.circle.center for item in CircleCollection.fromPayload(inputs[name]).items]
            elif name == "bbox" and feature in {"bboxCenter", "bboxTopLeft"}:
                box = BBox2D.fromPayload(inputs[name])
                points = [self._boxPoint(box, feature)]
            elif name == "measurements" and feature in {"bboxCenter", "bboxTopLeft", "minAreaRectCenter", "centroid"}:
                points = []
                for item in ShapeMeasurementCollection.fromPayload(inputs[name]).items:
                    if feature == "centroid":
                        points.append(item.centroid)
                    elif feature == "minAreaRectCenter":
                        box = item.minAreaRect
                        points.append(Point2D(box.centerX, box.centerY, box.coordinateSpace))
                    else:
                        points.append(self._boxPoint(item.bbox, feature))
            else:
                return error("E_INPUT_SHAPE", f"{feature} does not apply to {name}")
            return {"status": "ok", "outputs": {"points": [p.toPayload() for p in points], "pointCount": len(points)}}
        except PayloadValidationError as exc:
            return error("E_INPUT_TYPE", str(exc))

    @staticmethod
    def _boxPoint(box, feature):
        return Point2D(box.x + (box.width / 2 if feature == "bboxCenter" else 0),
                       box.y + (box.height / 2 if feature == "bboxCenter" else 0), box.coordinateSpace)


class PointsReframeOperator:
    meta = OperatorMeta("vision.geometry.reframe_points", "点坐标回源／换图像空间", "1.0.0",
        {"points": port("list<point2d>"), "frame": port("bbox2d")}, {"points": port("list<point2d>")},
        {"type": "object", "properties": {}, "additionalProperties": False})

    def validateParams(self, params):
        return {"code": "E_PARAM_INVALID", "message": "target space comes from frame, not a label override"} if params else None

    def executeNode(self, inputs, params, runtimeContext):
        if self.validateParams(params):
            return {"status": "error", "error": self.validateParams(params)}
        if "points" not in inputs or "frame" not in inputs:
            return error("E_INPUT_MISSING", "points and target frame are required")
        if not isinstance(inputs["points"], list):
            return error("E_INPUT_TYPE", "points must be an array")
        try:
            target = BBox2D.fromPayload(inputs["frame"]).coordinateSpace
            inverse = np.linalg.inv(np.array(target.matrixToSource(), dtype=np.float64).reshape(3, 3))
            points = []
            for raw in inputs["points"]:
                point = Point2D.fromPayload(raw)
                if point.coordinateSpace.sourceId != target.sourceId or point.coordinateSpace.unit != target.unit:
                    return error("E_INPUT_SHAPE", "source identity/units do not match target frame")
                sourceX, sourceY = point.coordinateSpace.mapPointToSource(point.x, point.y)
                mapped = inverse @ np.array([sourceX, sourceY, 1.0])
                if not np.all(np.isfinite(mapped)) or abs(mapped[2]) <= 1e-12:
                    return error("E_RESULT_INVALID", "point maps to infinity")
                points.append(Point2D(float(mapped[0] / mapped[2]), float(mapped[1] / mapped[2]), target).toPayload())
            return {"status": "ok", "outputs": {"points": points}}
        except PayloadValidationError as exc:
            return error("E_INPUT_TYPE", str(exc))
        except (ValueError, np.linalg.LinAlgError) as exc:
            return error("E_INPUT_SHAPE", str(exc))
