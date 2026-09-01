from __future__ import annotations

import json
from typing import Any

from PySide2.QtCore import QPointF, Qt
from PySide2.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide2.QtWidgets import QComboBox, QLabel, QSpinBox, QWidget

from emo_master.plugins.builtins._editor_support import (
    PurePreviewControllerBase,
    requiredChild,
    setImageLabel,
)


class _HistogramChart(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self._histogram: dict[str, object] = {}
        self.setMinimumSize(640, 300)

    def setHistogram(self, histogram: dict[str, object]) -> None:
        self._histogram = dict(histogram)
        self.update()

    def paintEvent(self, event) -> None:  # type: ignore[override]
        _ = event
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#171a20"))
        margin = 28.0
        width = max(1.0, self.width() - margin * 2)
        height = max(1.0, self.height() - margin * 2)
        painter.setPen(QPen(QColor("#5f6673"), 1.0))
        painter.drawLine(QPointF(margin, margin), QPointF(margin, margin + height))
        painter.drawLine(QPointF(margin, margin + height), QPointF(margin + width, margin + height))
        channels = self._histogram.get("channels", [])
        if not isinstance(channels, list) or not channels:
            painter.setPen(QColor("#a8afbd"))
            painter.drawText(self.rect(), Qt.AlignCenter, "空选区 / 无直方图数据")
            return
        valuesByChannel = []
        maximum = 0.0
        for channel in channels:
            if not isinstance(channel, dict):
                continue
            values = channel.get("values", [])
            if not isinstance(values, list):
                continue
            numeric = [max(0.0, float(value)) for value in values]
            if numeric:
                maximum = max(maximum, max(numeric))
                valuesByChannel.append((str(channel.get("name", "GRAY")), numeric))
        if not valuesByChannel or maximum <= 0:
            painter.setPen(QColor("#a8afbd"))
            painter.drawText(self.rect(), Qt.AlignCenter, "空选区 / 全零直方图")
            return
        colors = {
            "B": QColor("#3293ff"),
            "G": QColor("#42d477"),
            "R": QColor("#ff5252"),
            "GRAY": QColor("#d4d7de"),
        }
        painter.setRenderHint(QPainter.Antialiasing, True)
        for name, values in valuesByChannel:
            path = QPainterPath()
            denominator = max(1, len(values) - 1)
            for index, value in enumerate(values):
                x = margin + width * index / denominator
                y = margin + height * (1.0 - value / maximum)
                if index == 0:
                    path.moveTo(x, y)
                else:
                    path.lineTo(x, y)
            painter.setPen(QPen(colors.get(name, QColor("#d4d7de")), 1.5))
            painter.drawPath(path)


class HistogramEditorController(PurePreviewControllerBase):
    def __init__(self) -> None:
        super().__init__()
        self._loading = False
        self.binsSpin: Any = None
        self.normalizationCombo: Any = None
        self.sourceImageLabel: QLabel | None = None
        self.pixelCountLabel: QLabel | None = None
        self.chart: _HistogramChart | None = None

    def bind(self, rootWidget: object, context: Any) -> None:
        self.bindPreviewBase(rootWidget, context)
        self.binsSpin = requiredChild(rootWidget, QSpinBox, "binsSpin")
        self.normalizationCombo = requiredChild(
            rootWidget, QComboBox, "normalizationCombo"
        )
        self.sourceImageLabel = requiredChild(
            rootWidget, QLabel, "sourceImageLabel"
        )
        self.pixelCountLabel = requiredChild(rootWidget, QLabel, "pixelCountLabel")
        placeholder = requiredChild(rootWidget, QWidget, "chartPlaceholder")
        self.chart = _HistogramChart()
        placeholder.layout().addWidget(self.chart)
        self.binsSpin.valueChanged.connect(self._paramsChanged)
        self.normalizationCombo.currentTextChanged.connect(self._paramsChanged)

    def loadParams(self, params: dict[str, Any]) -> None:
        self._loading = True
        try:
            self.binsSpin.setValue(int(params.get("bins", 256)))
            self.normalizationCombo.setCurrentText(
                str(params.get("normalization", "counts"))
            )
        finally:
            self._loading = False

    def collectParams(self) -> dict[str, object]:
        return {
            "bins": int(self.binsSpin.value()),
            "normalization": self.normalizationCombo.currentText(),
        }

    def validate(self) -> object:
        return None

    def onSourceImage(self, content: bytes, mimeType: str) -> None:
        _ = mimeType
        if self.sourceImageLabel is not None:
            setImageLabel(self.sourceImageLabel, content)

    def onStructuredSource(self, content: bytes, mimeType: str) -> None:
        _ = mimeType
        try:
            payload = json.loads(content.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as err:
            self.context.setError(f"Histogram 快照无效：{err}")
            return
        if not isinstance(payload, dict):
            self.context.setError("Histogram 快照必须是 JSON 对象")
            return
        if self.chart is not None:
            self.chart.setHistogram(payload)
        pixelCount = _safePixelCount(payload.get("pixelCount", 0))
        if self.pixelCountLabel is not None:
            suffix = "（空选区）" if pixelCount == 0 else ""
            self.pixelCountLabel.setText(f"pixelCount: {pixelCount}{suffix}")
        self.context.setStatus("已加载当前节点最近一次 Histogram 结果")

    def handlePreviewResult(self, outputs: dict[str, object], assets: dict[str, str]) -> None:
        _ = assets
        histogram = outputs.get("histogram", {})
        payload = histogram if isinstance(histogram, dict) else {}
        if self.chart is not None:
            self.chart.setHistogram(payload)
        pixelCount = _safePixelCount(payload.get("pixelCount", 0))
        if self.pixelCountLabel is not None:
            suffix = "（空选区）" if pixelCount == 0 else ""
            self.pixelCountLabel.setText(f"pixelCount: {pixelCount}{suffix}")
        self.context.setStatus("Histogram 预览已刷新")

    def onClose(self) -> None:
        super().onClose()

    def dispose(self) -> None:
        self.disposePreviewBase()

    def _paramsChanged(self, *args) -> None:
        _ = args
        if not self._loading:
            self.schedulePreview(markDirty=True)


def _safePixelCount(value: object) -> int:
    if isinstance(value, bool):
        return 0
    try:
        return max(0, int(value))  # type: ignore[call-overload]
    except (TypeError, ValueError, OverflowError):
        return 0
