from __future__ import annotations

import json
import math
from typing import Any

from PySide2.QtCore import QByteArray, QPointF, Qt
from PySide2.QtGui import QColor, QImage, QPainter, QPen, QPolygonF
from PySide2.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QLabel,
    QPlainTextEdit,
    QSpinBox,
    QWidget,
)

from emo_master.plugins.builtins._editor_support import (
    PurePreviewControllerBase,
    requiredChild,
    setImageLabel,
)


class _RoiCanvas(QWidget):
    def __init__(self, changed) -> None:
        super().__init__()
        self._changed = changed
        self._image = QImage()
        self._roiType = "bbox"
        self._geometry: dict[str, Any] = {}
        self._dragStart: tuple[float, float] | None = None
        self._panStart = None
        self._pan = QPointF(0.0, 0.0)
        self._zoom = 1.0
        self.setMinimumSize(640, 360)
        self.setMouseTracking(True)

    def setImageBytes(self, content: bytes) -> None:
        image = QImage.fromData(QByteArray(content))
        self._image = image.convertToFormat(QImage.Format_RGB888)
        self._pan = QPointF(0.0, 0.0)
        self._zoom = 1.0
        self.update()

    def setRoi(self, roiType: str, geometry: dict[str, Any]) -> None:
        self._roiType = roiType
        self._geometry = dict(geometry)
        self.update()

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        if self._image.isNull():
            return
        if event.button() == Qt.MiddleButton:
            self._panStart = event.pos()
            return
        if event.button() == Qt.RightButton and self._roiType == "polygon":
            self._changed(dict(self._geometry))
            return
        if event.button() != Qt.LeftButton:
            return
        point = self._toImage(event.pos())
        if self._roiType == "polygon":
            points = list(self._geometry.get("points", []))
            points.append({"x": point[0], "y": point[1]})
            self._geometry = {"points": points}
            self._changed(dict(self._geometry))
            self.update()
            return
        self._dragStart = point

    def mouseMoveEvent(self, event) -> None:  # type: ignore[override]
        if self._panStart is not None:
            delta = event.pos() - self._panStart
            self._pan += QPointF(delta)
            self._panStart = event.pos()
            self.update()
            return
        if self._dragStart is None:
            return
        current = self._toImage(event.pos())
        left = min(self._dragStart[0], current[0])
        top = min(self._dragStart[1], current[1])
        width = abs(current[0] - self._dragStart[0])
        height = abs(current[1] - self._dragStart[1])
        if self._roiType == "rotatedBox":
            self._geometry = {
                "centerX": left + width / 2.0,
                "centerY": top + height / 2.0,
                "width": width,
                "height": height,
                "angleDegrees": float(self._geometry.get("angleDegrees", 0.0)),
            }
        else:
            self._geometry = {"x": left, "y": top, "width": width, "height": height}
        self._changed(dict(self._geometry))
        self.update()

    def mouseReleaseEvent(self, event) -> None:  # type: ignore[override]
        if event.button() == Qt.MiddleButton:
            self._panStart = None
        elif event.button() == Qt.LeftButton:
            self._dragStart = None

    def wheelEvent(self, event) -> None:  # type: ignore[override]
        self._zoom = max(0.1, min(20.0, self._zoom * (1.15 if event.angleDelta().y() > 0 else 1 / 1.15)))
        self.update()

    def paintEvent(self, event) -> None:  # type: ignore[override]
        _ = event
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#171a20"))
        if self._image.isNull():
            painter.setPen(QColor("#a8afbd"))
            painter.drawText(self.rect(), Qt.AlignCenter, "请选择图源")
            return
        scale, origin = self._transform()
        targetWidth = self._image.width() * scale
        targetHeight = self._image.height() * scale
        painter.drawImage(
            QPointF(origin[0], origin[1]),
            self._image.scaled(
                round(targetWidth),
                round(targetHeight),
                Qt.IgnoreAspectRatio,
                Qt.SmoothTransformation,
            ),
        )
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setPen(QPen(QColor("#37d5ff"), 2.0))
        points = [self._toWidget(point) for point in self._geometryPoints()]
        if len(points) >= 2:
            polygon = QPolygonF([QPointF(x, y) for x, y in points])
            painter.drawPolygon(polygon)
        for x, y in points:
            painter.drawEllipse(QPointF(x, y), 3.0, 3.0)

    def _transform(self) -> tuple[float, tuple[float, float]]:
        fit = min(
            self.width() / max(1, self._image.width()),
            self.height() / max(1, self._image.height()),
        )
        scale = fit * self._zoom
        originX = (self.width() - self._image.width() * scale) / 2.0 + self._pan.x()
        originY = (self.height() - self._image.height() * scale) / 2.0 + self._pan.y()
        return scale, (originX, originY)

    def _toImage(self, point) -> tuple[float, float]:
        scale, origin = self._transform()
        x = (point.x() - origin[0]) / max(scale, 1e-12)
        y = (point.y() - origin[1]) / max(scale, 1e-12)
        return (
            max(0.0, min(float(self._image.width()), x)),
            max(0.0, min(float(self._image.height()), y)),
        )

    def _toWidget(self, point: tuple[float, float]) -> tuple[float, float]:
        scale, origin = self._transform()
        return origin[0] + point[0] * scale, origin[1] + point[1] * scale

    def _geometryPoints(self) -> list[tuple[float, float]]:
        if self._roiType == "polygon":
            result = []
            for point in self._geometry.get("points", []):
                if isinstance(point, dict):
                    result.append((float(point.get("x", 0.0)), float(point.get("y", 0.0))))
            return result
        width = float(self._geometry.get("width", 0.0))
        height = float(self._geometry.get("height", 0.0))
        if self._roiType == "bbox":
            x = float(self._geometry.get("x", 0.0))
            y = float(self._geometry.get("y", 0.0))
            return [(x, y), (x + width, y), (x + width, y + height), (x, y + height)]
        cx = float(self._geometry.get("centerX", 0.0))
        cy = float(self._geometry.get("centerY", 0.0))
        angle = math.radians(float(self._geometry.get("angleDegrees", 0.0)))
        cosine, sine = math.cos(angle), math.sin(angle)
        result = []
        for dx, dy in ((-width / 2, -height / 2), (width / 2, -height / 2), (width / 2, height / 2), (-width / 2, height / 2)):
            result.append((cx + dx * cosine - dy * sine, cy + dx * sine + dy * cosine))
        return result


class RoiEditorController(PurePreviewControllerBase):
    def __init__(self) -> None:
        super().__init__()
        self._loading = False
        self.canvas: _RoiCanvas | None = None
        self.controls: dict[str, Any] = {}
        self.outputLabels: dict[str, QLabel] = {}

    def bind(self, rootWidget: object, context: Any) -> None:
        self.bindPreviewBase(rootWidget, context)
        placeholder = requiredChild(rootWidget, QWidget, "canvasPlaceholder")
        self.canvas = _RoiCanvas(self._canvasChanged)
        placeholder.layout().addWidget(self.canvas)
        self.controls = {
            "roiType": requiredChild(rootWidget, QComboBox, "roiTypeCombo"),
            "x": requiredChild(rootWidget, QDoubleSpinBox, "xSpin"),
            "y": requiredChild(rootWidget, QDoubleSpinBox, "ySpin"),
            "width": requiredChild(rootWidget, QDoubleSpinBox, "widthSpin"),
            "height": requiredChild(rootWidget, QDoubleSpinBox, "heightSpin"),
            "centerX": requiredChild(rootWidget, QDoubleSpinBox, "centerXSpin"),
            "centerY": requiredChild(rootWidget, QDoubleSpinBox, "centerYSpin"),
            "angleDegrees": requiredChild(rootWidget, QDoubleSpinBox, "angleSpin"),
            "points": requiredChild(rootWidget, QPlainTextEdit, "pointsEdit"),
            "padValue": requiredChild(rootWidget, QSpinBox, "padValueSpin"),
            "interpolation": requiredChild(rootWidget, QComboBox, "interpolationCombo"),
        }
        for name in ("x", "y", "width", "height", "centerX", "centerY"):
            control = self.controls[name]
            control.setRange(-1_000_000_000.0, 1_000_000_000.0)
            control.setDecimals(3)
            control.valueChanged.connect(self._paramsChanged)
        self.controls["angleDegrees"].valueChanged.connect(self._paramsChanged)
        self.controls["padValue"].valueChanged.connect(self._paramsChanged)
        self.controls["roiType"].currentTextChanged.connect(self._paramsChanged)
        self.controls["interpolation"].currentTextChanged.connect(self._paramsChanged)
        self.controls["points"].textChanged.connect(self._paramsChanged)
        self.outputLabels = {
            "croppedImage": requiredChild(rootWidget, QLabel, "croppedImageLabel"),
            "croppedMask": requiredChild(rootWidget, QLabel, "croppedMaskLabel"),
            "fullMask": requiredChild(rootWidget, QLabel, "fullMaskLabel"),
        }

    def loadParams(self, params: dict[str, Any]) -> None:
        self._loading = True
        try:
            self.controls["roiType"].setCurrentText(str(params.get("roiType", "bbox")))
            for name in ("x", "y", "width", "height", "centerX", "centerY", "angleDegrees"):
                self.controls[name].setValue(float(params.get(name, 0.0 if name not in {"width", "height"} else 1.0)))
            self.controls["padValue"].setValue(int(params.get("padValue", 0)))
            self.controls["interpolation"].setCurrentText(str(params.get("interpolation", "linear")))
            self.controls["points"].setPlainText(json.dumps(params.get("points", []), ensure_ascii=False))
        finally:
            self._loading = False
        self._syncCanvas()

    def collectParams(self) -> dict[str, Any]:
        try:
            points = json.loads(self.controls["points"].toPlainText().strip() or "[]")
        except json.JSONDecodeError:
            points = []
        return {
            "roiType": self.controls["roiType"].currentText(),
            "x": float(self.controls["x"].value()),
            "y": float(self.controls["y"].value()),
            "width": float(self.controls["width"].value()),
            "height": float(self.controls["height"].value()),
            "centerX": float(self.controls["centerX"].value()),
            "centerY": float(self.controls["centerY"].value()),
            "angleDegrees": float(self.controls["angleDegrees"].value()),
            "points": points if isinstance(points, list) else [],
            "padValue": int(self.controls["padValue"].value()),
            "interpolation": self.controls["interpolation"].currentText(),
        }

    def validate(self) -> object:
        params = self.collectParams()
        if params["roiType"] == "polygon":
            if len(params["points"]) < 3:
                return "Polygon 至少需要三个点"
        elif float(params["width"]) <= 0 or float(params["height"]) <= 0:
            return "ROI width/height 必须大于 0"
        return None

    def onSourceImage(self, content: bytes, mimeType: str) -> None:
        _ = mimeType
        if self.canvas is not None:
            self.canvas.setImageBytes(content)

    def handlePreviewResult(self, outputs: dict[str, object], assets: dict[str, str]) -> None:
        _ = outputs
        for port, label in self.outputLabels.items():
            assetId = assets.get(port)
            if not assetId:
                continue
            try:
                content, _mime = self.context.downloadPreviewAsset(assetId)
                setImageLabel(label, content)
            except Exception as err:
                self.context.setError(str(err))
                return
        self.context.setStatus("ROI 预览已刷新")

    def onClose(self) -> None:
        super().onClose()

    def dispose(self) -> None:
        self.disposePreviewBase()

    def _paramsChanged(self, *args) -> None:
        _ = args
        if self._loading:
            return
        self._syncCanvas()
        self.schedulePreview(markDirty=True)

    def _syncCanvas(self) -> None:
        if self.canvas is None or not self.controls:
            return
        params = self.collectParams()
        roiType = str(params["roiType"])
        if roiType == "polygon":
            geometry = {"points": params["points"]}
        elif roiType == "rotatedBox":
            geometry = {name: params[name] for name in ("centerX", "centerY", "width", "height", "angleDegrees")}
        else:
            geometry = {name: params[name] for name in ("x", "y", "width", "height")}
        self.canvas.setRoi(roiType, geometry)

    def _canvasChanged(self, geometry: dict[str, Any]) -> None:
        self._loading = True
        try:
            for name, value in geometry.items():
                if name == "points":
                    self.controls["points"].setPlainText(json.dumps(value, ensure_ascii=False))
                elif name in self.controls:
                    self.controls[name].setValue(float(value))
        finally:
            self._loading = False
        self.context.markDirty()
        self.schedulePreview(markDirty=False)
