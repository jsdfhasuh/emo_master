"""One nonmodal native image viewer, following the panel's shared selection."""
import time

from PySide2.QtCore import Qt, Signal
from PySide2.QtGui import QPixmap, QPainter
from PySide2.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QToolButton, QGraphicsView,
                              QGraphicsScene, QGraphicsPixmapItem, QLabel, QSizePolicy)

from .widgets import WrapLabel


class ImageView(QGraphicsView):
    zoomChanged = Signal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setScene(QGraphicsScene(self))
        self.item = QGraphicsPixmapItem()
        self.item.setShapeMode(QGraphicsPixmapItem.BoundingRectShape)
        self.scene().addItem(self.item)
        self.setRenderHints(QPainter.SmoothPixmapTransform)
        self.setDragMode(QGraphicsView.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setBackgroundBrush(Qt.black)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.zoom = 1.0
        self.fitting = True
        self.lastPaintNs = 0
        self.firstImagePaintNs = 0

    def setImage(self, pixmap):
        self.item.setPixmap(pixmap if pixmap is not None else QPixmap())
        self.firstImagePaintNs = 0
        self.scene().setSceneRect(self.item.boundingRect())
        self.fitting = True
        self.fit()

    def setZoom(self, value):
        if self.item.pixmap().isNull():
            return
        self.fitting = False
        value = max(.1, min(8, float(value)))
        self.scale(value / self.zoom, value / self.zoom)
        self.zoom = value
        self.zoomChanged.emit(value)

    def fit(self):
        if self.item.pixmap().isNull():
            self.resetTransform()
            self.zoom = 1
            return
        bounds = self.item.boundingRect()
        available = self.viewport().size()
        self.setZoom(min((available.width() - 8) / bounds.width(), (available.height() - 8) / bounds.height()))
        self.centerOn(bounds.center())
        self.fitting = True

    def wheelEvent(self, event):
        if not self.item.pixmap().isNull():
            delta = event.angleDelta().y() or event.pixelDelta().y()
            self.setZoom(self.zoom * (1.2 if delta > 0 else 1 / 1.2))
            event.accept()
        else:
            super().wheelEvent(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.fitting:
            self.fit()

    def paintEvent(self, event):
        super().paintEvent(event)
        self.lastPaintNs = time.monotonic_ns()
        if not self.item.pixmap().isNull() and not self.firstImagePaintNs:
            self.firstImagePaintNs = self.lastPaintNs


class NodeImageViewer(QDialog):
    closed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent, Qt.Window)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setModal(False)
        self.setWindowTitle('节点结果 · 大图查看')
        self.resize(1000, 740)
        self.identity = None
        self.imageSetNs = 0
        root = QVBoxLayout(self)
        self.origin = WrapLabel('尚无图片')
        self.origin.setTextFormat(Qt.PlainText)
        root.addWidget(self.origin)
        tools = QHBoxLayout()
        self.zoomText = QLabel('100%')
        for name, text, callback in (
            ('zoomIn', '放大', lambda: self.view.setZoom(self.view.zoom * 1.2)),
            ('zoomOut', '缩小', lambda: self.view.setZoom(self.view.zoom / 1.2)),
            ('actual', '100%', lambda: self.view.setZoom(1)),
            ('fitButton', '适配', lambda: self.view.fit()),
            ('fullScreen', '全屏', self.toggleFullScreen)):
            button = QToolButton()
            button.setText(text)
            button.clicked.connect(callback)
            setattr(self, name, button)
            tools.addWidget(button)
        tools.addWidget(self.zoomText)
        tools.addStretch(1)
        root.addLayout(tools)
        self.message = WrapLabel('尚无图片')
        self.message.setAlignment(Qt.AlignCenter)
        root.addWidget(self.message)
        self.view = ImageView()
        self.view.zoomChanged.connect(lambda value: self.zoomText.setText(f'{value * 100:.0f}%'))
        root.addWidget(self.view, 1)

    def showResult(self, pixmap, text, identity):
        self.identity = identity
        self.origin.setText(text or '所选节点尚无有效图片')
        self.origin.setToolTip(str(identity))
        self.view.setImage(pixmap)
        self.imageSetNs = time.monotonic_ns()
        valid = pixmap is not None and not pixmap.isNull()
        self.message.setVisible(not valid)
        self.message.setText(text or '所选节点尚无有效图片')
        for button in (self.zoomIn, self.zoomOut, self.actual, self.fitButton):
            button.setEnabled(valid)

    def toggleFullScreen(self):
        self.showNormal() if self.isFullScreen() else self.showFullScreen()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            if self.isFullScreen():
                self.showNormal()
            event.accept()  # Esc outside full screen does not silently dismiss.
            return
        super().keyPressEvent(event)

    def closeEvent(self, event):
        self.view.setImage(None)
        self.identity = None
        self.closed.emit()
        super().closeEvent(event)
