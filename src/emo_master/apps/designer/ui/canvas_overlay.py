"""Canvas-local geometry and dragging shared by Designer's floating panels."""
from __future__ import annotations

from PySide2.QtCore import QEvent, QPoint, QRect, QSize, Qt, Signal
from PySide2.QtGui import QColor, QKeySequence, QPainter
from PySide2.QtWidgets import (
    QFrame, QGraphicsDropShadowEffect, QHBoxLayout, QLayout, QPushButton,
    QShortcut, QToolButton, QVBoxLayout, QWidget,
)

from .icon_map import icon
from .widgets import scrollContent


class DragHandle(QWidget):
    def __init__(self, panel, moved, finished, title='工具区'):
        super().__init__(panel)
        self.panel = panel
        self.moved = moved
        self.finished = finished
        self._start = None
        self.setFixedSize(20, 28)
        self.setCursor(Qt.SizeAllCursor)
        self.setToolTip(f'拖动{title}；位置只保存在本机')
        self.setAccessibleName(f'拖动{title}')

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor('#a2adbd'))
        for x in (7, 13):
            for y in (8, 14, 20):
                painter.drawEllipse(QPoint(x, y), 1, 1)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.panel.raise_()
            self._start = (event.globalPos(), self.panel.pos())
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._start is not None and event.buttons() & Qt.LeftButton:
            point, origin = self._start
            self.moved(origin + event.globalPos() - point)
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self._start is not None:
            point, origin = self._start
            self._start = None
            self.moved(origin + event.globalPos() - point)
            self.finished()
            event.accept()
        else:
            super().mouseReleaseEvent(event)

    def hideEvent(self, event):
        self._start = None
        super().hideEvent(event)


class CanvasOverlay(QFrame):
    def __init__(self, host, viewport, settings, positionKey, *, right=False,
                 expandedSize=QSize(308, 560), collapsedSize=QSize(148, 46)):
        super().__init__(host)
        self.viewport = viewport
        self.settings = settings
        self.positionKey = positionKey
        self.expandedSize = expandedSize
        self.collapsedSize = collapsedSize
        self._right = right
        self._expanded = False
        self._positioning = False
        self._watchedAncestors = set()
        # Horizontal position is an inset from the chosen canvas edge, so a
        # right-side handle expands leftward and stays reachable after resize.
        self._preferredPosition = self._readPosition()
        self.viewport.installEventFilter(self)
        self._watchAncestors()

    def _readPosition(self):
        value = self.settings.value(self.positionKey, [12, 12])
        if isinstance(value, (list, tuple)) and len(value) == 2:
            try:
                x, y = (int(item) for item in value)
                if 0 <= x <= 1_000_000 and 0 <= y <= 1_000_000:
                    return QPoint(x, y)
            except (ValueError, TypeError, OverflowError):
                pass
        return QPoint(12, 12)

    def _savePosition(self):
        self.moveWithinCanvas(self.pos())
        self.settings.setValue(self.positionKey,
                               [self._preferredPosition.x(), self._preferredPosition.y()])

    def isExpanded(self):
        return self._expanded

    def moveWithinCanvas(self, position):
        x = self.viewportRect().right() - position.x() - self.width() + 1 if self._right else position.x()
        self._preferredPosition = QPoint(x, position.y())
        self._reposition()

    def viewportRect(self):
        if self.viewport is self.parentWidget():
            return self.viewport.contentsRect()
        return QRect(self.viewport.mapTo(self.parentWidget(), QPoint()), self.viewport.size())

    def visibleCanvasRect(self):
        view = self.parentWidget()
        area = self.viewportRect()
        ancestor = view.parentWidget()
        while ancestor is not None:
            bounds = ancestor.contentsRect()
            origin = view.mapFromGlobal(ancestor.mapToGlobal(bounds.topLeft()))
            area = area.intersected(QRect(origin, bounds.size()))
            ancestor = ancestor.parentWidget()
        return area

    def _reposition(self):
        if self._positioning:
            return
        self._positioning = True
        try:
            area = self.visibleCanvasRect()
            margin = min(12, max(0, min(area.width(), area.height()) // 8))
            if self._expanded and area.height() < 180:
                margin = 0
            area = area.adjusted(margin, margin, -margin, -margin)
            size = self.expandedSize if self._expanded else self.collapsedSize
            width = min(size.width(), max(1, area.width()))
            height = min(size.height(), max(1, area.height()))
            preferredX = (self.viewportRect().right() - self._preferredPosition.x() - width + 1
                          if self._right else self._preferredPosition.x())
            x = max(area.left(), min(preferredX, area.right() - width + 1))
            y = max(area.top(), min(self._preferredPosition.y(), area.bottom() - height + 1))
            self.setGeometry(x, y, width, height)
        finally:
            self._positioning = False

    def _watchAncestors(self):
        import shiboken2
        ancestors = set()
        widget = self.parentWidget()
        while widget is not None:
            ancestors.add(widget)
            widget = widget.parentWidget()
        for widget in self._watchedAncestors - ancestors:
            if shiboken2.isValid(widget):
                widget.removeEventFilter(self)
        for widget in ancestors - self._watchedAncestors:
            widget.installEventFilter(self)
        self._watchedAncestors = ancestors

    def eventFilter(self, watched, event):
        if ((watched is self.viewport or watched in self._watchedAncestors)
                and event.type() in (QEvent.Resize, QEvent.Move, QEvent.Show, QEvent.ParentChange)):
            self._watchAncestors()
            self._reposition()
            self.raise_()
        return super().eventFilter(watched, event)

    def showEvent(self, event):
        super().showEvent(event)
        self._watchAncestors()
        self._reposition()


class FloatingPanel(CanvasOverlay):
    expandedChanged = Signal(bool)

    def __init__(self, host, viewport, settings, key, title, content, *, right=False,
                 symbol='layout-grid', expanded=False, expandedSize=QSize(340, 600),
                 collapsedSize=QSize(164, 46)):
        super().__init__(host, viewport, settings, key + '/position', right=right,
                         expandedSize=expandedSize, collapsedSize=collapsedSize)
        self.title = title
        self.stateKey = key + '/expanded'
        self.setObjectName('floatingPanel')
        self.setAccessibleName(title)
        self.setStyleSheet('''
            QFrame#floatingPanel { background: white; border: 1px solid #dce2eb; border-radius: 8px; }
            QPushButton#floatingPanelToggle {
                background: transparent; border: none; text-align: left;
                font-weight: 600; padding: 4px 6px;
            }
            QPushButton#floatingPanelToggle:hover { background: #edf3ff; }
            QToolButton#floatingPanelClose { background: transparent; border: none; padding: 4px; }
        ''')
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(18)
        shadow.setOffset(0, 3)
        shadow.setColor(QColor(42, 58, 86, 32))
        self.setGraphicsEffect(shadow)
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 6, 8, 8)
        root.setSpacing(4)
        root.setSizeConstraint(QLayout.SetNoConstraint)
        self.header = QHBoxLayout()
        self.header.setSpacing(0)
        self.dragHandle = DragHandle(self, self.moveWithinCanvas, self._savePosition, title)
        self.header.addWidget(self.dragHandle)
        self.toggleButton = QPushButton(title)
        self.toggleButton.setObjectName('floatingPanelToggle')
        self.toggleButton.setIcon(icon(symbol, '#2563eb'))
        self.toggleButton.setIconSize(QSize(18, 18))
        self.toggleButton.setFixedHeight(30)
        self.toggleButton.clicked.connect(self.toggle)
        self.header.addWidget(self.toggleButton, 1)
        self.closeButton = QToolButton()
        self.closeButton.setObjectName('floatingPanelClose')
        self.closeButton.setIcon(icon('panel-left-close'))
        self.closeButton.setToolTip(f'收起{title} (Esc)')
        self.closeButton.setAccessibleName(f'收起{title}')
        self.closeButton.clicked.connect(lambda: self.setExpanded(False))
        self.header.addWidget(self.closeButton)
        root.addLayout(self.header)
        self.content = content
        self.contentScroll = scrollContent(content)
        self.contentScroll.setMinimumSize(0, 0)
        root.addWidget(self.contentScroll, 1)
        self.escape = QShortcut(QKeySequence(Qt.Key_Escape), self)
        self.escape.setContext(Qt.WidgetWithChildrenShortcut)
        self.escape.activated.connect(lambda: self.setExpanded(False))
        saved = settings.value(self.stateKey, expanded)
        self.setExpanded(saved in (True, 1, 'true'), persist=False)

    def toggle(self):
        self.setExpanded(not self._expanded)

    def setExpanded(self, expanded, *, persist=True):
        changed = self._expanded != bool(expanded)
        self._expanded = bool(expanded)
        self.contentScroll.setVisible(self._expanded)
        self.closeButton.setVisible(self._expanded)
        self.toggleButton.setToolTip(('收起' if expanded else '展开') + self.title)
        self.toggleButton.setAccessibleName(self.toggleButton.toolTip())
        self._reposition()
        self.raise_()
        if persist:
            self.settings.setValue(self.stateKey, self._expanded)
        if changed:
            self.expandedChanged.emit(self._expanded)
