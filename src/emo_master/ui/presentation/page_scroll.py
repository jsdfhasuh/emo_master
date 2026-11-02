"""Page scrollbars drawn above the viewport, without changing layout width."""
from PySide2.QtCore import QEvent, QSize, Qt
from PySide2.QtWidgets import QScrollArea, QScrollBar


def pageContentSize(layout, minimum):
    """Measure the full page at its layout width, independently of view zoom."""
    measured = layout.minimumSize()
    width = max(minimum.width(), measured.width())
    height = layout.heightForWidth(width) if layout.hasHeightForWidth() else measured.height()
    return QSize(width, max(minimum.height(), measured.height(), height))


class PageScrollArea(QScrollArea):
    """Qt owns ranges and scrolling; the edge bars are views of that state."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.canvasExtension = 0
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.overlayHorizontal = self._overlay(Qt.Horizontal, self.horizontalScrollBar())
        self.overlayVertical = self._overlay(Qt.Vertical, self.verticalScrollBar())
        self.viewport().installEventFilter(self)

    def setCanvasExtension(self, height):
        self.canvasExtension = height
        self._sizeCanvas()

    def _sizeCanvas(self):
        if self.widget() is not None:
            self.widget().setMinimumHeight(self.viewport().height() + self.canvasExtension
                                           if self.canvasExtension else 0)

    def _overlay(self, orientation, source):
        bar = QScrollBar(orientation, self.viewport())
        bar.setObjectName('pageOverlayScrollBar')
        bar.setProperty('editorDecoration', True)
        bar.setFocusPolicy(Qt.NoFocus)
        bar.setAccessibleName('页面水平滚动' if orientation == Qt.Horizontal else '页面垂直滚动')
        bar.hide()
        source.rangeChanged.connect(lambda _low, _high: self._syncBars())
        source.valueChanged.connect(bar.setValue)
        bar.valueChanged.connect(source.setValue)
        return bar

    def _syncBars(self):
        if not hasattr(self, 'overlayVertical'):
            return
        for source, overlay in ((self.horizontalScrollBar(), self.overlayHorizontal),
                                (self.verticalScrollBar(), self.overlayVertical)):
            overlay.setRange(source.minimum(), source.maximum())
            overlay.setPageStep(source.pageStep())
            overlay.setSingleStep(source.singleStep())
            overlay.setValue(source.value())
            overlay.setVisible(source.maximum() > source.minimum())
        extent = max(8, self.overlayVertical.sizeHint().width())
        horizontalExtent = max(8, self.overlayHorizontal.sizeHint().height())
        area = self.viewport().rect()
        vertical = self.verticalScrollBar().maximum() > self.verticalScrollBar().minimum()
        horizontal = self.horizontalScrollBar().maximum() > self.horizontalScrollBar().minimum()
        self.overlayVertical.setGeometry(max(0, area.width() - extent), 0, extent,
            max(0, area.height() - (horizontalExtent if horizontal else 0)))
        self.overlayHorizontal.setGeometry(0, max(0, area.height() - horizontalExtent),
            max(0, area.width() - (extent if vertical else 0)), horizontalExtent)
        self.overlayHorizontal.raise_()
        self.overlayVertical.raise_()

    def eventFilter(self, obj, event):
        if obj is self.viewport() and event.type() in (QEvent.Resize, QEvent.Show, QEvent.LayoutRequest):
            self._sizeCanvas()
            self._syncBars()
        return super().eventFilter(obj, event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._sizeCanvas()
        self._syncBars()

    def scrollContentsBy(self, dx, dy):
        super().scrollContentsBy(dx, dy)
        self._syncBars()

    def wheelEvent(self, event):
        # QAbstractScrollArea keeps its normal wheel/trackpad handling even
        # though the native bars are hidden by policy.
        super().wheelEvent(event)
        self._syncBars()
