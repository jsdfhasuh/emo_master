"""Reusable image viewport; operator-specific editing belongs in subclasses."""
import time

from PySide2.QtCore import QByteArray, Qt, Signal
from PySide2.QtGui import QImage, QPainter, QPixmap
from PySide2.QtWidgets import QGraphicsPixmapItem, QGraphicsScene, QGraphicsView, QSizePolicy


class ImageView(QGraphicsView):
    zoomChanged = Signal(float)

    def __init__(self, parent=None, *, minimumZoom=.1, maximumZoom=8):
        super().__init__(parent)
        self.minimumZoom = minimumZoom
        self.maximumZoom = maximumZoom
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
        self.emptyText = ""
        self._panStart = None
        self._preservedCenter = None

    def setImage(self, pixmap, *, preserveView=False):
        preserve = preserveView and not self.fitting
        center = self._preservedCenter if self._preservedCenter is not None else self.mapToScene(self.viewport().rect().center())
        self.item.setPixmap(pixmap if pixmap is not None else QPixmap())
        self.firstImagePaintNs = 0
        self.scene().setSceneRect(self.item.boundingRect())
        if preserve:
            self._preservedCenter = center if self.item.pixmap().isNull() else None
            self.centerOn(center)
        else:
            self._preservedCenter = None
            self.fitting = True
            self.fit()
        self.viewport().update()

    def setImageBytes(self, content, *, preserveView=False):
        self.setImage(QPixmap.fromImage(QImage.fromData(QByteArray(content))), preserveView=preserveView)

    def pixmap(self):
        return self.item.pixmap()

    def setZoom(self, value):
        if self.item.pixmap().isNull():
            return
        self.fitting = False
        # fit() may choose a smaller scale for a large source image.
        self._setScale(max(self.minimumZoom, min(self.maximumZoom, float(value))))

    def _setScale(self, value):
        self.scale(value / self.zoom, value / self.zoom)
        self.zoom = value
        self.zoomChanged.emit(value)

    def fit(self):
        self._preservedCenter = None
        self.fitting = True
        if self.item.pixmap().isNull():
            self.resetTransform()
            self.zoom = 1.0
            self.fitting = True
            self.zoomChanged.emit(self.zoom)
            return
        bounds = self.item.boundingRect()
        available = self.viewport().size()
        scale = min(max(1, available.width() - 8) / bounds.width(),
                    max(1, available.height() - 8) / bounds.height())
        self._setScale(scale)
        self.centerOn(bounds.center())
        self.fitting = True

    def wheelEvent(self, event):
        if self.item.pixmap().isNull():
            super().wheelEvent(event)
            return
        delta = event.angleDelta().y() or event.pixelDelta().y()
        if delta:
            self.setZoom(self.zoom * (1.2 if delta > 0 else 1 / 1.2))
        event.accept()

    def mousePressEvent(self, event):
        if event.button() == Qt.MiddleButton and not self.item.pixmap().isNull():
            self._panStart = event.pos()
            self.viewport().setCursor(Qt.ClosedHandCursor)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._panStart is not None:
            delta = event.pos() - self._panStart
            self._panStart = event.pos()
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - delta.x())
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - delta.y())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MiddleButton and self._panStart is not None:
            self._panStart = None
            self.viewport().setCursor(Qt.OpenHandCursor if self.dragMode() == self.ScrollHandDrag else Qt.ArrowCursor)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.fitting:
            self.fit()

    def paintEvent(self, event):
        super().paintEvent(event)
        self.lastPaintNs = time.monotonic_ns()
        if not self.item.pixmap().isNull():
            if not self.firstImagePaintNs:
                self.firstImagePaintNs = self.lastPaintNs
        elif self.emptyText:
            painter = QPainter(self.viewport())
            painter.setPen(self.palette().text().color())
            painter.drawText(self.viewport().rect(), Qt.AlignCenter | Qt.TextWordWrap, self.emptyText)
