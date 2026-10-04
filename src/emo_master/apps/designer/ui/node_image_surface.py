"""Fit drawing from a shared QPixmap; no scaled full-image copies."""
import time

from PySide2.QtCore import Qt, QRectF, QSize, Signal
from PySide2.QtGui import QPainter, QPixmap
from PySide2.QtWidgets import QLabel, QSizePolicy


class NodeImageSurface(QLabel):
    activated = Signal()

    def __init__(self, text='', parent=None):
        super().__init__(text, parent)
        self._sourcePixmap = QPixmap()
        self.setAlignment(Qt.AlignCenter)
        self.setWordWrap(True)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Expanding)
        self.setMinimumSize(120, 120)
        self.setObjectName('previewImage')
        self.lastPaintNs = 0

    def setPixmap(self, pixmap):
        self._sourcePixmap = QPixmap(pixmap)  # Implicitly shared, not a bitmap copy.
        super().setText('')
        self.update()

    def pixmap(self):
        return self._sourcePixmap

    def setText(self, message):
        self._sourcePixmap = QPixmap()
        super().setText(message)

    def paintEvent(self, event):
        if self._sourcePixmap.isNull():
            super().paintEvent(event)
        else:
            painter = QPainter(self)
            painter.setRenderHint(QPainter.SmoothPixmapTransform)
            size = self._sourcePixmap.size()
            available = self.contentsRect().adjusted(8, 8, -8, -8)
            size.scale(available.size(), Qt.KeepAspectRatio)
            target = QRectF(available.center().x() - size.width() / 2,
                            available.center().y() - size.height() / 2, size.width(), size.height())
            painter.drawPixmap(target, self._sourcePixmap, QRectF(self._sourcePixmap.rect()))
            painter.end()
        self.lastPaintNs = time.monotonic_ns()

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.LeftButton and not self._sourcePixmap.isNull():
            self.activated.emit()
        super().mouseDoubleClickEvent(event)

    def sizeHint(self):
        return QSize(320, 200)

    def minimumSizeHint(self):
        return QSize(120, 120)
