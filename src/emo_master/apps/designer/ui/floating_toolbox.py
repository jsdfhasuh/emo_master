"""A canvas-owned toolbox; its widgets retain the existing command/model owners."""
from __future__ import annotations

from typing import Callable

from PySide2.QtCore import QEvent, QPoint, QRect, QSize, Qt
from PySide2.QtGui import QColor, QKeySequence, QPainter
from PySide2.QtWidgets import (
    QFrame, QGraphicsDropShadowEffect, QHBoxLayout, QLayout, QShortcut,
    QSizePolicy, QTabWidget, QToolButton, QVBoxLayout, QWidget,
)

from .icon_map import icon
from .widgets import scrollContent


class _DragHandle(QWidget):
    def __init__(self, toolbox: FloatingToolbox,
                 moved: Callable[[QPoint], None], finished: Callable[[], None]) -> None:
        super().__init__(toolbox)
        self.toolbox = toolbox
        self.moved = moved
        self.finished = finished
        self._start: tuple[QPoint, QPoint] | None = None
        self.setFixedSize(20, 28)
        self.setCursor(Qt.SizeAllCursor)
        self.setToolTip('拖动工具区；位置只保存在本机')
        self.setAccessibleName('拖动工具区')

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
            self._start = (event.globalPos(), self.toolbox.pos())
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


class _ToolboxTabs(QTabWidget):
    def minimumSizeHint(self):
        # Each tab has its own scroll surface; its content must not force a
        # larger tab body than the visible canvas on short screens.
        return QSize(0, 0)


class FloatingToolbox(QFrame):
    """Overlay on the graphics viewport, without taking space from the canvas."""

    positionKey = 'ui/floating_toolbox_position'

    def __init__(self, window) -> None:
        self.owner = window
        self.viewport = window.flowView.viewport()
        # QGraphicsView scrolls viewport children along with the scene. Own the
        # overlay on the view itself and constrain it to the viewport rectangle.
        super().__init__(window.flowView)
        self.setObjectName('floatingToolbox')
        self.setAccessibleName('流程工具区')
        self._expanded = False
        self._positioning = False
        self._watchedAncestors: set[QWidget] = set()
        self._preferredPosition = self._readPosition()
        self.setStyleSheet('''
            QFrame#floatingToolbox {
                background: #ffffff; border: 1px solid #dce2eb; border-radius: 10px;
            }
            QPushButton#floatingToolboxToggle {
                background: transparent; border: none; text-align: left;
                font-weight: 600; padding: 4px 8px;
            }
            QPushButton#floatingToolboxToggle:hover { background: #edf3ff; }
            QToolButton#floatingToolboxClose {
                border: none; background: transparent; padding: 4px;
            }
            QTabWidget#floatingToolboxTabs::pane { border: none; }
            QTabWidget#floatingToolboxTabs QTabBar::tab {
                background: transparent; border: none;
                border-bottom: 2px solid transparent; color: #626b78;
                padding: 6px 12px; min-width: 32px;
            }
            QTabWidget#floatingToolboxTabs QTabBar::tab:selected {
                background: #edf3ff; border-bottom-color: #2563eb; color: #1748b5;
            }
            QTabWidget#floatingToolboxTabs QTabBar::tab:hover {
                background: #f0f4fa;
            }
            QPushButton#sideCategoryButton {
                padding: 3px 6px; min-height: 20px;
            }
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
        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(0)
        self.dragHandle = _DragHandle(self, self.moveWithinCanvas, self._savePosition)
        header.addWidget(self.dragHandle)
        self.toggleButton = window.sidebarToggleButton
        self.toggleButton.setObjectName('floatingToolboxToggle')
        self.toggleButton.setFixedHeight(30)
        self.toggleButton.setIcon(icon('layout-grid', '#2563eb'))
        self.toggleButton.setIconSize(QSize(18, 18))
        self.toggleButton.setText('工具区')
        header.addWidget(self.toggleButton, 1)
        self.closeButton = QToolButton()
        self.closeButton.setObjectName('floatingToolboxClose')
        self.closeButton.setIcon(icon('panel-left-close'))
        self.closeButton.setToolTip('收起工具区 (Esc)')
        self.closeButton.setAccessibleName('收起工具区')
        self.closeButton.clicked.connect(window.collapseSidebar)
        header.addWidget(self.closeButton)
        root.addLayout(header)

        self.tabs = _ToolboxTabs()
        self.tabs.setObjectName('floatingToolboxTabs')
        self.tabs.setMinimumSize(0, 0)
        self.tabs.tabBar().setDrawBase(False)
        root.addWidget(self.tabs, 1)
        bubble = window.operatorBubble
        bubble.setEmbedded(self, window.collapseSidebar)
        bubble.layout().insertWidget(1, window.categoryPanel)
        window.categoryPanel.show()
        bubble.setMinimumHeight(330)
        # On short/high-DPI screens the whole library remains reachable. The
        # existing inner scroll still bounds the operator-card/icon viewport.
        self.libraryScroll = scrollContent(bubble)
        self.libraryScroll.setMinimumSize(0, 0)
        self.tabs.addTab(self.libraryScroll, '算子')
        for title, container, tree in (
            ('关系', window.dependencyTreeContainer, window.workflowDependencyTree),
            ('节点', window.nodeListContainer, window.nodeListWidget),
        ):
            tree.setMinimumHeight(60)
            tree.setMaximumHeight(16777215)
            tree.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
            container.layout().setContentsMargins(8, 8, 8, 8)
            surface = scrollContent(container)
            surface.setMinimumSize(0, 0)
            self.tabs.addTab(surface, title)
        self.tabs.currentChanged.connect(self._refreshLibrary)
        self.escape = QShortcut(QKeySequence(Qt.Key_Escape), self)
        self.escape.setContext(Qt.WidgetWithChildrenShortcut)
        self.escape.activated.connect(window.collapseSidebar)
        self.viewport.installEventFilter(self)
        self._watchAncestors()
        self.setExpanded(False)

    def _readPosition(self) -> QPoint:
        value = self.owner.settingsStore.value(self.positionKey, [12, 12])
        if isinstance(value, (list, tuple)) and len(value) == 2:
            try:
                x, y = (int(item) for item in value)
                if 0 <= x <= 1_000_000 and 0 <= y <= 1_000_000:
                    return QPoint(x, y)
            except (ValueError, TypeError, OverflowError):
                pass
        return QPoint(12, 12)

    def _savePosition(self) -> None:
        self._preferredPosition = self.pos()
        self.owner.settingsStore.setValue(self.positionKey, [self.x(), self.y()])

    def setExpanded(self, expanded: bool) -> None:
        self._expanded = bool(expanded)
        self.tabs.setVisible(self._expanded)
        self.closeButton.setVisible(self._expanded)
        self.toggleButton.setToolTip('收起工具区' if expanded else '展开工具区')
        self.toggleButton.setAccessibleName(self.toggleButton.toolTip())
        self._reposition()
        self.raise_()
        if self._expanded:
            self._refreshLibrary()

    def isExpanded(self) -> bool:
        return self._expanded

    def moveWithinCanvas(self, position: QPoint) -> None:
        self._preferredPosition = QPoint(position)
        self._reposition()

    def _reposition(self) -> None:
        if self._positioning:
            return
        self._positioning = True
        try:
            area = self.visibleCanvasRect()
            margin = min(12, max(0, min(area.width(), area.height()) // 8))
            area = area.adjusted(margin, margin, -margin, -margin)
            width = min(308 if self._expanded else 148, max(1, area.width()))
            height = min(560 if self._expanded else 46, max(1, area.height()))
            x = max(area.left(), min(self._preferredPosition.x(), area.right() - width + 1))
            y = max(area.top(), min(self._preferredPosition.y(), area.bottom() - height + 1))
            self.setGeometry(x, y, width, height)
        finally:
            self._positioning = False

    def _refreshLibrary(self, *_args) -> None:
        if self._expanded and self.tabs.currentIndex() == 0:
            self.owner._refreshBubbleOperators()
        elif self._expanded and self.tabs.currentIndex() == 1:
            self.owner.refreshWorkflowDependencyTree()

    def visibleCanvasRect(self) -> QRect:
        """Account for ancestor scroll clipping, without using occlusion regions."""
        view = self.parentWidget()
        area = self.viewport.geometry()
        ancestor = view.parentWidget()
        while ancestor is not None:
            bounds = ancestor.contentsRect()
            origin = view.mapFromGlobal(ancestor.mapToGlobal(bounds.topLeft()))
            area = area.intersected(QRect(origin, bounds.size()))
            ancestor = ancestor.parentWidget()
        return area

    def _watchAncestors(self) -> None:
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
        self._refreshLibrary()
