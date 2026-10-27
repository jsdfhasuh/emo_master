"""Workflow navigation only: real tabs, independent commands, no hidden pages."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from PySide2.QtCore import QEvent, QPoint, QSignalBlocker, QSize, Qt, QTimer, Signal
from PySide2.QtGui import QColor, QFontMetrics
from PySide2.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QSizePolicy, QStyle, QStyleOptionTab, QStylePainter, QTabBar, QToolButton, QVBoxLayout, QWidget,
)

from emo_master.apps.designer.ui.icon_map import icon
from emo_master.apps.designer.ui.theme import BORDER, MUTED, TEXT, uiFont
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
        self._fitting = False
        self._fitTimer = QTimer(self)
        self._fitTimer.setSingleShot(True)
        self._fitTimer.timeout.connect(self._fitTabs)
        self.setFont(uiFont())
        self.setExpanding(False)
        self.setDocumentMode(True)
        self.setDrawBase(False)
        self.setUsesScrollButtons(False)
        self.setElideMode(Qt.ElideRight)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.currentChanged.connect(self._fitTabs)

    def _fitTabs(self):
        current = self.currentIndex()
        if self._fitting or current < 0:
            return
        self._fitting = True
        try:
            widths = [self.tabSizeHint(i).width() for i in range(self.count())]
            available = self.width()
            first = next((i for i in range(self.count()) if self.isTabVisible(i)), current)
            first = min(first, current)
            used = sum(widths[first:current + 1])
            while first < current and used > available:
                used -= widths[first]
                first += 1
            last = current + 1
            while last < self.count() and used + widths[last] <= available:
                used += widths[last]
                last += 1
            while first > 0 and used + widths[first - 1] <= available:
                first -= 1
                used += widths[first]
            # Keep real indices and IDs, and never hide the selected tab.
            # Overflow remains available through the fixed workflow picker.
            self.setTabVisible(current, True)
            for index in range(self.count()):
                visible = first <= index < last
                if self.isTabVisible(index) != visible:
                    self.setTabVisible(index, visible)
        finally:
            self._fitting = False

    def setCurrentIndex(self, index):
        super().setCurrentIndex(index)
        self._fitTabs()

    def tabLayoutChange(self):
        if not self._fitting:
            self._fitTimer.start(0)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fitTabs()

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Left, Qt.Key_Right):
            step = 1 if event.key() == Qt.Key_Right else -1
            if self.layoutDirection() == Qt.RightToLeft:
                step = -step
            index = self.currentIndex() + step
            if 0 <= index < self.count():
                self.setCurrentIndex(index)
            event.accept()
            return
        super().keyPressEvent(event)

    def tabSizeHint(self, index):
        size = super().tabSizeHint(index)
        metrics = QFontMetrics(uiFont(bold=True))
        markers = [self.tabButton(index, side) for side in (QTabBar.LeftSide, QTabBar.RightSide)]
        markerWidth = sum(marker.sizeHint().expandedTo(marker.minimumSize()).width() + 8
                          for marker in markers if marker is not None)
        hasStatusText = markers[1] is not None and markers[1].property('runtimeStatus') != 'COMPLETED'
        limit = min(340 if hasStatusText else 280, max(1, self.width() - 2))
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

    def initStyleOption(self, option, index):
        super().initStyleOption(option, index)
        option.fontMetrics = QFontMetrics(uiFont(bold=index == self.currentIndex()))
        # Qt's default elision does not reserve stylesheet padding or the
        # selected tab's heavier font when a custom width cap is used.
        option.text = option.fontMetrics.elidedText(self.tabText(index), Qt.ElideRight,
                                                   max(0, self._textRect(option).width()))

    def _textRect(self, option):
        rect = self.style().subElementRect(QStyle.SE_TabBarTabText, option, self)
        padding = self.style().sizeFromContents(QStyle.CT_TabBarTab, option, QSize(0, 0), self).width()
        return rect.adjusted(padding // 2, 0, -(padding - padding // 2), 0)

    def paintEvent(self, event):
        painter = QStylePainter(self)
        try:
            current = self.currentIndex()
            indices = [i for i in range(self.count()) if i != current]
            if current >= 0:
                indices.append(current)
            for index in indices:
                if not self.isTabVisible(index):
                    continue
                option = QStyleOptionTab()
                self.initStyleOption(option, index)
                painter.setFont(uiFont(bold=index == current))
                painter.drawControl(QStyle.CE_TabBarTabShape, option)
                painter.setPen(QColor(TEXT if index == current else MUTED))
                painter.drawText(self._textRect(option), Qt.AlignCenter, option.text)
        finally:
            painter.end()


class _WorkflowPicker(QFrame):
    workflowRequested: Any = Signal(str)

    def __init__(self, parent, noun='工作流'):
        super().__init__(parent, Qt.Popup)
        self.setObjectName('workflowPicker')
        self.setFrameShape(QFrame.StyledPanel)
        layout = QVBoxLayout(self)
        self.search = QLineEdit(self)
        self.search.setPlaceholderText(f'搜索{noun}名称或 ID…')
        self.search.setAccessibleName(f'搜索{noun}')
        self.search.setClearButtonEnabled(True)
        self.search.installEventFilter(self)
        self.list = QListWidget(self)
        self.list.setAccessibleName(f'全部{noun}')
        self.empty = QLabel(f'没有匹配的{noun}', self)
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

    def __init__(self, *, noun='工作流'):
        super().__init__()
        self._labels: dict[str, _WorkflowLabel] = {}
        self._bar = _WorkflowTabBar(self)
        self._bar.currentChanged.connect(self.currentChanged.emit)
        self._bar.tabBarClicked.connect(self.tabBarClicked.emit)
        self._allButton = QToolButton(self)
        self._allButton.setIcon(icon('list'))
        self._allButton.setToolButtonStyle(Qt.ToolButtonIconOnly)
        self._allButton.setToolTip(f'全部{noun}（可搜索）')
        self._allButton.setAccessibleName(f'全部{noun}')
        self._allButton.clicked.connect(self._showPicker)
        self._addButton = QToolButton(self)
        self._addButton.setIcon(icon('plus'))
        self._addButton.setToolTip(f'新增{noun}')
        self._addButton.setAccessibleName(f'新增{noun}')
        self._addButton.clicked.connect(lambda: self.addRequested.emit())
        for button in (self._allButton, self._addButton):
            button.setFixedSize(32, 32)
            button.setIconSize(QSize(18, 18))
        separator = QFrame(self)
        separator.setFixedSize(1, 20)
        separator.setStyleSheet(f'QFrame {{ background: {BORDER}; border: none; }}')
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(self._bar, 1)
        layout.addWidget(separator)
        layout.addWidget(self._allButton)
        layout.addWidget(self._addButton)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._picker = _WorkflowPicker(self, noun)
        self._picker.workflowRequested.connect(self.selectWorkflow)
        self._picker.workflowRequested.connect(lambda _: self._bar.setFocus())

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
                if status == 'COMPLETED':
                    badge = QLabel(self._bar)
                    badge.setPixmap(icon('check', color).pixmap(QSize(16, 16)))
                    badge.setAlignment(Qt.AlignCenter)
                    badge.setFixedSize(20, 20)
                    badge.setStyleSheet('QLabel { background: transparent; border: none; }')
                    badge.setAccessibleName(text)
                    badge.setAttribute(Qt.WA_TransparentForMouseEvents)
                else:
                    badge = self._badge(text, color, background)
                badge.setToolTip(text)
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
        statusText = WORKFLOW_RUN_STYLES.get(label.status, ('',))[0]
        if statusText:
            tooltip += '\n状态：' + statusText
        if label.runtimeDetails:
            tooltip += '\n\n' + label.runtimeDetails
        self._bar.setTabToolTip(index, tooltip)
        markers = [self._bar.tabButton(index, side) for side in (QTabBar.LeftSide, QTabBar.RightSide)]
        names = [marker.accessibleName() for marker in markers if marker is not None]
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
        point = self._allButton.mapToGlobal(QPoint(self._allButton.width(), self._allButton.height() + 4))
        point.setX(point.x() - self._picker.width())
        x = min(max(screen.left(), point.x()), screen.right() - self._picker.width() + 1)
        y = min(max(screen.top(), point.y()), screen.bottom() - self._picker.height() + 1)
        self._picker.move(x, y)
        if self._picker.list.currentItem() is not None:
            self._picker.list.scrollToItem(self._picker.list.currentItem())
        self._picker.search.setFocus()

    def sizeHint(self):
        return QSize(400, max(38, self._bar.sizeHint().height()))

    def minimumSizeHint(self):
        return QSize(160, self.sizeHint().height())
