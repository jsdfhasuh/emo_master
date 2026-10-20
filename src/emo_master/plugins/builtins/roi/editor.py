from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from PySide2.QtCore import QByteArray, QPointF, Qt
from PySide2.QtGui import QColor, QImage, QPainter, QPen, QPolygonF, QPixmap
from PySide2.QtWidgets import (
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)
from emo_master.plugins.builtins.roi.preview_result import PreviewImage, RoiPreviewSnapshot, atomicWrite

from emo_master.apps.designer.ui.param_form import applyParameterLabel
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
        self.setMinimumSize(240, 180)
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
        painter.fillRect(self.rect(), QColor("#f0f2f5"))
        if self._image.isNull():
            painter.setPen(QColor("#626b78"))
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
        self._previewSnapshot: RoiPreviewSnapshot | None = None
        self._resultViewers: list[QDialog] = []
        self._resultButtons: list[QPushButton] = []

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
        labels = {
            "roiType": "roiTypeLabel", "x": "xLabel", "y": "yLabel",
            "width": "widthLabel", "height": "heightLabel",
            "centerX": "centerXLabel", "centerY": "centerYLabel",
            "angleDegrees": "angleLabel", "points": "pointsLabel",
            "padValue": "padLabel", "interpolation": "interpolationLabel",
        }
        for name, labelName in labels.items():
            applyParameterLabel(
                requiredChild(rootWidget, QLabel, labelName), self.controls[name],
                name, context.paramSchema,
            )
        for name in ("x", "y", "width", "height", "centerX", "centerY"):
            control = self.controls[name]
            control.setDecimals(3)
            control.setRange(.001 if name in ("width", "height") else -1_000_000_000.0, 1_000_000_000.0)
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
            "maskedImage": requiredChild(rootWidget, QLabel, "maskedImageLabel"),
        }
        self.outputCombo = QComboBox()
        self.outputCombo.setObjectName("roiOutputCombo")
        for port in self.outputLabels:
            self.outputCombo.addItem(port, port)
            self.outputLabels[port].setToolTip(f"{port} 缩略图；使用下方 1:1 查看 / PNG 导出查看完整像素")
        self.viewImageButton = QPushButton("1:1 查看")
        self.exportImageButton = QPushButton("导出 PNG")
        self.viewResultButton = QPushButton("结果与身份")
        self.exportJsonButton = QPushButton("导出 JSON")
        self.exportArchiveButton = QPushButton("本次预览 ZIP")
        tools = QHBoxLayout()
        tools.addWidget(self.outputCombo)
        for button, name, callback in (
            (self.viewImageButton, "roiViewFullImageButton", self._viewFullImage),
            (self.exportImageButton, "roiExportImageButton", self._exportImage),
            (self.viewResultButton, "roiViewResultButton", self._viewResult),
            (self.exportJsonButton, "roiExportJsonButton", self._exportJson),
            (self.exportArchiveButton, "roiExportArchiveButton", self._exportArchive),
        ):
            button.setObjectName(name)
            button.clicked.connect(callback)
            button.setEnabled(False)
            tools.addWidget(button)
            self._resultButtons.append(button)
        self.resultIdentityLabel = QLabel("尚无本次预览结果")
        self.resultIdentityLabel.setObjectName("roiPreviewIdentityLabel")
        self.resultIdentityLabel.setWordWrap(True)
        # Do not let the expanding image splitter clip the parameter-provenance
        # notice below its first line (verified with the native Qt renderer).
        self.resultIdentityLabel.setMinimumHeight(self.resultIdentityLabel.fontMetrics().lineSpacing() * 3 + 4)
        self.exportArchiveButton.setToolTip("四幅原始 PNG、frame/roi 等结构化结果及不可变请求身份；不重新执行")
        self.exportJsonButton.setToolTip("结构化结果、四幅图像描述与请求身份（不包含 PNG 字节）")
        self.exportImageButton.setToolTip("仅保存所选端口原始 PNG，不包含身份；完整身份请导出本次预览 ZIP")
        self.root.layout().addLayout(tools)
        self.root.layout().addWidget(self.resultIdentityLabel)

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
        self._clearRoiResult()
        if not outputs and not assets:
            return
        try:
            if self._previewMetadata is None:
                raise ValueError("缺少本次草稿预览身份，请重新预览")
            metadataJson = json.dumps(self._previewMetadata, ensure_ascii=False, allow_nan=False)
            outputsJson = json.dumps(outputs, ensure_ascii=False, allow_nan=False)
            images = []
            for port in self.outputLabels:
                assetId = assets.get(port)
                if not assetId:
                    raise ValueError(f"预览缺少图像输出：{port}")
                # The restricted session bridge is the sole byte source. Never
                # read a Runtime cache path or save a scaled label pixmap.
                content, mime = self.context.downloadPreviewAsset(assetId)
                image = QImage.fromData(QByteArray(content))
                if mime != "image/png" or image.isNull() or not content.startswith(b"\x89PNG\r\n\x1a\n"):
                    raise ValueError(f"预览输出不是有效的完整 PNG：{port}")
                images.append(PreviewImage(port, assetId, bytes(content), image.width(), image.height()))
            snapshot = RoiPreviewSnapshot(metadataJson, outputsJson, tuple(images))
            self._previewSnapshot = snapshot
            self._validateSnapshot(snapshot)
            for previewImage in snapshot.images:
                setImageLabel(self.outputLabels[previewImage.port], previewImage.content)
            for button in self._resultButtons:
                button.setEnabled(True)
            self.resultIdentityLabel.setText(
                f"本次纯预览 {self._previewMetadata['requestId']} · 四幅完整图像及 frame/roi\n"
                "PNG 仅图像；ZIP 含本次身份。参数为请求值；Runtime 未返回实际解析参数。")
            self.context.setStatus("ROI 预览已刷新（四幅原图 / 结构化结果可查看与导出）")
        except Exception as error:
            self._clearRoiResult()
            self.context.setError(str(error))

    def onPreviewInvalidated(self) -> None:
        super().onPreviewInvalidated()
        self._clearRoiResult()

    def _clearRoiResult(self) -> None:
        self._previewSnapshot = None
        for button in self._resultButtons:
            button.setEnabled(False)
        for port, label in self.outputLabels.items():
            label.clear()
            label.setText(port + "（等待本次预览）")
        for viewer in self._resultViewers:
            viewer.close()
            viewer.deleteLater()
        self._resultViewers.clear()
        if hasattr(self, "resultIdentityLabel"):
            self.resultIdentityLabel.setText("尚无本次预览结果；更改参数或图源后旧结果不可导出")

    def _validateSnapshot(self, snapshot: RoiPreviewSnapshot) -> None:
        try:
            metadata = json.loads(snapshot.metadataJson)
            if (self._disposed or self._previewSnapshot is not snapshot
                    or metadata["generation"] != self._generation):
                raise ValueError("预览已失效，请等待本次结果")
            self.context.validatePurePreviewResult()
            if self.context.purePreviewIdentity() != metadata["context"]:
                raise ValueError("预览会话或 Runtime 已变更，请重新预览")
        except Exception:
            self.onPreviewInvalidated()
            raise

    def _selectedImage(self, snapshot):
        return next(image for image in snapshot.images if image.port == self.outputCombo.currentData())

    def _viewFullImage(self) -> None:
        snapshot = self._previewSnapshot
        if snapshot is None:
            return
        try:
            self._validateSnapshot(snapshot)
            image = self._selectedImage(snapshot)
            viewer = self._newViewer(f"{image.port} · {image.width}×{image.height} · 1:1", snapshot)
            scroll = QScrollArea(viewer)
            label = QLabel()
            pixmap = QPixmap.fromImage(QImage.fromData(QByteArray(image.content)))
            label.setPixmap(pixmap)
            label.setFixedSize(pixmap.size())
            scroll.setWidget(label)
            viewer.layout().addWidget(scroll)
            viewer.show()
        except Exception as error:
            self.context.setError(str(error))

    def _viewResult(self) -> None:
        snapshot = self._previewSnapshot
        if snapshot is None:
            return
        try:
            self._validateSnapshot(snapshot)
            viewer = self._newViewer("结构化结果与本次请求身份", snapshot)
            text = QPlainTextEdit()
            text.setReadOnly(True)
            text.setPlainText(snapshot.resultJson().decode("utf-8"))
            viewer.layout().addWidget(text)
            viewer.show()
        except Exception as error:
            self.context.setError(str(error))

    def _newViewer(self, title, snapshot):
        viewer = QDialog(self.root)
        viewer.setWindowTitle(f"{title} · {json.loads(snapshot.metadataJson)['requestId']}")
        viewer.setLayout(QVBoxLayout())
        viewer.resize(800, 600)
        self._resultViewers.append(viewer)
        return viewer

    def _exportImage(self) -> None:
        snapshot = self._previewSnapshot
        if snapshot is not None:
            image = self._selectedImage(snapshot)
            self._saveSnapshot(snapshot, image.port + ".png", "PNG (*.png)", ".png",
                               lambda stream: stream.write(image.content))

    def _exportJson(self) -> None:
        snapshot = self._previewSnapshot
        if snapshot is not None:
            self._saveSnapshot(snapshot, "roi-result.json", "JSON (*.json)", ".json",
                               lambda stream: stream.write(snapshot.resultJson()))

    def _exportArchive(self) -> None:
        snapshot = self._previewSnapshot
        if snapshot is not None:
            self._saveSnapshot(snapshot, "roi-preview.zip", "ZIP (*.zip)", ".zip", snapshot.writeArchive)

    def _saveSnapshot(self, snapshot, filename, filters, suffix, writer):
        try:
            self._validateSnapshot(snapshot)
            selected, _ = QFileDialog.getSaveFileName(self.root, "导出本次 ROI 纯预览", filename, filters)
            if not selected:
                return
            path = Path(selected)
            # GTK/native pickers need not append the filter suffix.
            if path.suffix.lower() != suffix:
                path = path.with_name(path.name + suffix)
            atomicWrite(path, writer, lambda: self._validateSnapshot(snapshot))
            self.context.setStatus(f"已导出本次 ROI 预览：{path}")
        except Exception as error:
            # No partial file replaces an existing destination, and no stale
            # selection is silently re-exported after a nested modal event.
            if not self._disposed:
                self.context.setError(f"ROI 导出失败：{error}")

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
