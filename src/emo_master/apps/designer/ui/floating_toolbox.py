"""A canvas-owned toolbox; its widgets retain the existing command/model owners."""
from __future__ import annotations

from PySide2.QtCore import QSize, Qt
from PySide2.QtGui import QColor, QKeySequence
from PySide2.QtWidgets import (
    QGraphicsDropShadowEffect, QHBoxLayout, QLayout, QShortcut,
    QSizePolicy, QTabWidget, QToolButton, QVBoxLayout,
)

from .canvas_overlay import CanvasOverlay, DragHandle
from .icon_map import icon
from .widgets import scrollContent


class _ToolboxTabs(QTabWidget):
    def minimumSizeHint(self):
        # Each tab has its own scroll surface; its content must not force a
        # larger tab body than the visible canvas on short screens.
        return QSize(0, 0)


class FloatingToolbox(CanvasOverlay):
    """Overlay on the graphics viewport, without taking space from the canvas."""

    positionKey = 'ui/floating_toolbox_position'

    def __init__(self, window) -> None:
        self.owner = window
        # QGraphicsView scrolls viewport children along with the scene. Own the
        # overlay on the view itself and constrain it to the viewport rectangle.
        super().__init__(window.flowView, window.flowView.viewport(), window.settingsStore, self.positionKey)
        self.setObjectName('floatingToolbox')
        self.setAccessibleName('流程工具区')
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
        self.dragHandle = DragHandle(self, self.moveWithinCanvas, self._savePosition)
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
        self.setExpanded(False)

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

    def _refreshLibrary(self, *_args) -> None:
        if self._expanded and self.tabs.currentIndex() == 0:
            self.owner._refreshBubbleOperators()
        elif self._expanded and self.tabs.currentIndex() == 1:
            self.owner.refreshWorkflowDependencyTree()

    def showEvent(self, event):
        super().showEvent(event)
        self._refreshLibrary()
