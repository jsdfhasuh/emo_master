"""Workflow navigation only: real tabs, independent commands, no hidden pages."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from PySide2.QtCore import QEvent, QPoint, QRect, QSignalBlocker, QSize, Qt, Signal
from PySide2.QtGui import QColor, QFontMetrics, QPainter
from PySide2.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QSizePolicy, QStyle, QTabBar, QToolButton, QVBoxLayout, QWidget,
)

from emo_master.apps.designer.ui.icon_map import icon
from emo_master.apps.designer.ui.theme import uiFont
from emo_master.ui.workflow_labels import WORKFLOW_RUN_STYLES


@dataclass
class _WorkflowLabel:
    title: str
    role: str = ''
    entryTooltip: str = ''
    status: str = ''
    runtimeDetails: str = ''


class _WorkflowTabBar(QTabBar):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFont(uiFont())
        self.setExpanding(False)
        self.setDocumentMode(True)
        self.setDrawBase(False)
        self.setUsesScrollButtons(True)
        self.setElideMode(Qt.ElideRight)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        for button in self.findChildren(QToolButton):
            button.setStyleSheet('QToolButton { padding: 0px; background: #f5f6f8; '
                                'border: 1px solid #d8dce3; border-radius: 2px; }')
            label = '向左浏览工作流' if button.arrowType() == Qt.LeftArrow else '向右浏览工作流'
            button.setToolTip(label)
            button.setAccessibleName(label)

    def tabSizeHint(self, index):
        size = super().tabSizeHint(index)
        metrics = QFontMetrics(uiFont(bold=True))
        markers = [self.tabButton(index, side) for side in (QTabBar.LeftSide, QTabBar.RightSide)]
        markerWidth = sum(marker.sizeHint().width() + 8 for marker in markers if marker is not None)
        limit = 340 if markers[1] is not None else 280
        arrows = 2 * self.style().pixelMetric(QStyle.PM_TabBarScrollButtonWidth)
        limit = min(limit, max(100, self.width() - arrows - 8))
        size.setWidth(min(limit, max(100, metrics.horizontalAdvance(self.tabText(index)) + 36 + markerWidth)))
        size.setHeight(max(36, self.fontMetrics().height() + 16))
        for marker in markers:
            if marker is not None:
                size.setHeight(max(size.height(), marker.sizeHint().height() + 12))
        return size

    def minimumTabSizeHint(self, index):
        return self.tabSizeHint(index)

    def minimumSizeHint(self):
        # The surrounding layout, not the longest name, owns the available width.
        return QSize(0, self.sizeHint().height())

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        try:
            for index in range(self.count()):
                badge = self.tabButton(index, QTabBar.RightSide)
                color = badge.property('runtimeColor') if badge is not None else None
                if color:
                    rect = self.tabRect(index).adjusted(1, 1, -1, -1)
                    painter.fillRect(QRect(rect.x(), rect.y(), rect.width(), 3), QColor(color))
        finally:
            painter.end()


class _WorkflowPicker(QFrame):
    workflowRequested: Any = Signal(str)

    def __init__(self, parent):
        super().__init__(parent, Qt.Popup)
        self.setObjectName('workflowPicker')
        self.setFrameShape(QFrame.StyledPanel)
        layout = QVBoxLayout(self)
        self.search = QLineEdit(self)
        self.search.setPlaceholderText('搜索工作流名称或 ID…')
        self.search.setAccessibleName('搜索工作流')
        self.search.setClearButtonEnabled(True)
        self.search.installEventFilter(self)
        self.list = QListWidget(self)
        self.list.setAccessibleName('全部工作流')
        self.empty = QLabel('没有匹配的工作流', self)
        layout.addWidget(self.search)
        layout.addWidget(self.list)
        layout.addWidget(self.empty)
        self.search.textChanged.connect(self._filter)
        self.search.returnPressed.connect(self._chooseCurrent)
        self.list.itemActivated.connect(self._choose)
        self.list.itemClicked.connect(self._choose)
        self._rows = None

    def setWorkflows(self, rows, currentId):
        if self._rows == rows:
            return
        selected = self.list.currentItem()
        selectedId = selected.data(Qt.UserRole) if selected is not None else currentId
        self._rows = rows
        self.list.clear()
        for workflowId, title, role, status, tooltip in rows:
            labels = [workflowId, role, WORKFLOW_RUN_STYLES.get(status, ('',))[0]]
            item = QListWidgetItem(title + '\n' + ' · '.join(label for label in labels if label))
            item.setData(Qt.UserRole, workflowId)
            item.setToolTip(tooltip)
            self.list.addItem(item)
            if workflowId == selectedId:
                self.list.setCurrentItem(item)
        self._filter(self.search.text())

    def _filter(self, text):
        query = text.strip().casefold()
        visible = []
        for i in range(self.list.count()):
            item = self.list.item(i)
            item.setHidden(query not in item.text().casefold())
            if not item.isHidden():
                visible.append(item)
        current = self.list.currentItem()
        if current is None or current.isHidden():
            self.list.setCurrentItem(visible[0] if visible else None)
        self.empty.setVisible(not visible)

    def _choose(self, item):
        if item is None or item.isHidden():
            return
        workflowId = item.data(Qt.UserRole)
        self.hide()
        self.workflowRequested.emit(workflowId)

    def _chooseCurrent(self):
        self._choose(self.list.currentItem())

    def eventFilter(self, obj, event):
        if obj is self.search and event.type() == QEvent.KeyPress and event.key() == Qt.Key_Down:
            self.list.setFocus()
            return True
        return super().eventFilter(obj, event)


class WorkflowTabs(QWidget):
    """An ID-keyed projection of workflows; never owns graph or execution state."""
    currentChanged: Any = Signal(int)
    tabBarClicked: Any = Signal(int)
    addRequested: Any = Signal()

    def __init__(self):
        super().__init__()
        self._labels: dict[str, _WorkflowLabel] = {}
        self._bar = _WorkflowTabBar(self)
        self._bar.currentChanged.connect(self.currentChanged.emit)
        self._bar.tabBarClicked.connect(self.tabBarClicked.emit)
        self._allButton = QToolButton(self)
        self._allButton.setText('全部')
        self._allButton.setToolTip('全部工作流（可搜索）')
        self._allButton.setAccessibleName('全部工作流')
        self._allButton.setFixedSize(52, 32)
        self._allButton.clicked.connect(self._showPicker)
        self._addButton = QToolButton(self)
        self._addButton.setIcon(icon('plus'))
        self._addButton.setToolTip('新增工作流')
        self._addButton.setAccessibleName('新增工作流')
        self._addButton.setFixedSize(32, 32)
        self._addButton.clicked.connect(lambda: self.addRequested.emit())
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(self._bar, 1)
        layout.addWidget(self._allButton)
        layout.addWidget(self._addButton)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._picker = _WorkflowPicker(self)
        self._picker.workflowRequested.connect(self.selectWorkflow)

    def tabBar(self):
        return self._bar

    def count(self):
        return self._bar.count()

    def currentIndex(self):
        return self._bar.currentIndex()

    def setCurrentIndex(self, index):
        if index != self.currentIndex():
            self._bar.setCurrentIndex(index)

    def tabText(self, index):
        return self._bar.tabText(index)

    def tabToolTip(self, index):
        return self._bar.tabToolTip(index)

    def indexForWorkflow(self, workflowId):
        return next((i for i in range(self.count()) if self._bar.tabData(i) == workflowId), -1)

    def selectWorkflow(self, workflowId):
        index = self.indexForWorkflow(workflowId)
        if index >= 0:
            self.setCurrentIndex(index)

    def addWorkflow(self, workflowId, title):
        if workflowId in self._labels:
            raise ValueError(f'Duplicate workflow ID: {workflowId}')
        self._labels[workflowId] = _WorkflowLabel(title)
        index = self._bar.addTab(title)
        self._bar.setTabData(index, workflowId)
        self._updateTabDescription(index)
        return index

    def removeTab(self, index):
        self._labels.pop(self._bar.tabData(index), None)
        self._bar.removeTab(index)
        self._refreshPicker()

    def clear(self):
        while self.count():
            self.removeTab(self.count() - 1)

    def setTabText(self, index, title):
        label = self._labels[self._bar.tabData(index)]
        if label.title != title:
            label.title = title
            self._bar.setTabText(index, title)
            self._updateTabDescription(index)

    def syncWorkflows(self, workflows, activeId):
        """Reconcile by stable ID, preserving surviving tabs and their badges."""
        blocker = QSignalBlocker(self)
        try:
            wanted = {workflowId for workflowId, _title in workflows}
            for index in reversed(range(self.count())):
                if self._bar.tabData(index) not in wanted:
                    self.removeTab(index)
            for target, (workflowId, title) in enumerate(workflows):
                index = self.indexForWorkflow(workflowId)
                if index < 0:
                    index = self.addWorkflow(workflowId, title)
                if index != target:
                    self._bar.moveTab(index, target)
                self.setTabText(target, title)
            self.selectWorkflow(activeId)
        finally:
            blocker.unblock()
        self._refreshPicker()

    def setEntryMarker(self, index, role, tooltip):
        label = self._labels[self._bar.tabData(index)]
        if label.role != role:
            label.role = role
            old = self._bar.tabButton(index, QTabBar.LeftSide)
            if old is not None:
                self._bar.setTabButton(index, QTabBar.LeftSide, None)
                old.deleteLater()
            if role:
                color, background = (('#1d4ed8', '#dbeafe') if role == '默认入口' else ('#047857', '#d1fae5'))
                self._bar.setTabButton(index, QTabBar.LeftSide, self._badge(role, color, background))
        label.entryTooltip = tooltip
        self._updateTabDescription(index)

    def setRuntimeStatus(self, index, status, details=''):
        label = self._labels[self._bar.tabData(index)]
        if label.status != status:
            label.status = status
            old = self._bar.tabButton(index, QTabBar.RightSide)
            if old is not None:
                self._bar.setTabButton(index, QTabBar.RightSide, None)
                old.deleteLater()
            if status in WORKFLOW_RUN_STYLES:
                text, color, background = WORKFLOW_RUN_STYLES[status]
                badge = self._badge(text, color, background)
                badge.setObjectName('workflowRuntimeBadge')
                badge.setProperty('runtimeStatus', status)
                badge.setProperty('runtimeColor', color)
                self._bar.setTabButton(index, QTabBar.RightSide, badge)
            self._bar.update()
        label.runtimeDetails = details
        self._updateTabDescription(index)

    def _badge(self, text, color, background):
        badge = QLabel(text, self._bar)
        badge.setObjectName('workflowEntryBadge')
        badge.setTextFormat(Qt.PlainText)
        badge.setFont(uiFont(bold=True))
        badge.setAttribute(Qt.WA_TransparentForMouseEvents)
        badge.setContentsMargins(6, 2, 6, 2)
        badge.setStyleSheet(f'QLabel {{ color: {color}; background: {background}; border-radius: 4px; }}')
        badge.setAccessibleName(text)
        badge.adjustSize()
        return badge

    def _updateTabDescription(self, index):
        label = self._labels[self._bar.tabData(index)]
        tooltip = label.entryTooltip or label.title
        if label.runtimeDetails:
            tooltip += '\n\n' + label.runtimeDetails
        self._bar.setTabToolTip(index, tooltip)
        markers = [self._bar.tabButton(index, side) for side in (QTabBar.LeftSide, QTabBar.RightSide)]
        names = [marker.text() for marker in markers if marker is not None]
        self._bar.setAccessibleTabName(index, ' '.join([*names, label.title]))
        self._refreshPicker()

    def _refreshPicker(self):
        if not self._picker.isVisible():
            return
        rows = []
        for index in range(self.count()):
            key = self._bar.tabData(index)
            label = self._labels[key]
            rows.append((key, label.title, label.role, label.status, self.tabToolTip(index)))
        self._picker.setWorkflows(rows, self._bar.tabData(self.currentIndex()))

    def _showPicker(self):
        self._picker.search.clear()
        # Reopening follows the currently viewed workflow, not the last search
        # selection. Live updates while open still preserve the user's row.
        self._picker.list.setCurrentItem(None)
        self._picker._rows = None
        self._picker.show()
        self._refreshPicker()
        screen = self.screen().availableGeometry()
        self._picker.resize(min(460, screen.width()), min(480, screen.height()))
        point = self._allButton.mapToGlobal(QPoint(0, self._allButton.height()))
        x = min(max(screen.left(), point.x()), screen.right() - self._picker.width() + 1)
        y = min(max(screen.top(), point.y()), screen.bottom() - self._picker.height() + 1)
        self._picker.move(x, y)
        self._picker.search.setFocus()

    def sizeHint(self):
        return QSize(400, max(38, self._bar.sizeHint().height()))

    def minimumSizeHint(self):
        return QSize(160, self.sizeHint().height())
