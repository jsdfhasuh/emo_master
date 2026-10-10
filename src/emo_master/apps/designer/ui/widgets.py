from __future__ import annotations

from PySide2.QtCore import QEvent, QRect, QSize, Qt
from PySide2.QtGui import QColor, QFontMetrics, QPainter, QPixmap
from PySide2.QtWidgets import (
    QLabel, QScrollArea, QSizePolicy, QStackedWidget, QStyle, QTabBar, QTabWidget,
    QToolButton, QWidget,
)

from emo_master.apps.designer.ui.theme import uiFont
from emo_master.ui.workflow_labels import WORKFLOW_RUN_STYLES


class ElidedLabel(QLabel):
    """Keep the original value accessible without imposing its width on a panel."""

    def __init__(self, text="", parent=None, mode=Qt.ElideRight):
        super().__init__(parent)
        self._fullText = str(text)
        self._elideMode = mode
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.setTextFormat(Qt.PlainText)
        self.setText(text)

    def text(self):
        return self._fullText

    def setText(self, text):
        self._fullText = str(text)
        self.setToolTip(self._fullText)
        self._refreshText()
        self.updateGeometry()

    def _refreshText(self):
        available = max(0, self.contentsRect().width() - 8)
        super().setText(self.fontMetrics().elidedText(self._fullText, self._elideMode, available))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._refreshText()

    def sizeHint(self):
        return QSize(min(360, self.fontMetrics().horizontalAdvance(self._fullText) + 8),
                     self.fontMetrics().height() + 8)

    def minimumSizeHint(self):
        return QSize(0, self.fontMetrics().height() + 8)


class WrapLabel(QLabel):
    def __init__(self, text="", parent=None):
        super().__init__(text, parent)
        self.setTextFormat(Qt.PlainText)
        self.setWordWrap(True)
        self.setTextInteractionFlags(Qt.TextSelectableByMouse)
        policy = QSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)

    def minimumSizeHint(self):
        return QSize(0, self.fontMetrics().height())


class OptionalWrapLabel(WrapLabel):
    """An empty diagnostic should not reserve a platform-font-sized row."""
    def __init__(self, text='', parent=None):
        super().__init__(text, parent)
        self.setVisible(bool(text))

    def setText(self, text):
        super().setText(text)
        self.setVisible(bool(text))


class PreviewLabel(QLabel):
    def __init__(self, text="", parent=None):
        super().__init__(text, parent)
        self._sourcePixmap = QPixmap()
        self.setAlignment(Qt.AlignCenter)
        self.setWordWrap(True)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Expanding)
        self.setMinimumSize(120, 90)
        self.setObjectName("previewImage")

    def setPixmap(self, pixmap):
        self._sourcePixmap = QPixmap(pixmap)
        self._rescale()

    def setText(self, text):
        self._sourcePixmap = QPixmap()
        super().setText(text)

    def _rescale(self):
        if not self._sourcePixmap.isNull():
            size = self.contentsRect().size() - QSize(8, 8)
            if size.width() > 0 and size.height() > 0:
                super().setPixmap(self._sourcePixmap.scaled(size, Qt.KeepAspectRatio, Qt.SmoothTransformation))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._rescale()

    def sizeHint(self):
        return QSize(320, 200)

    def minimumSizeHint(self):
        return QSize(120, 90)


def scrollContent(widget: QWidget, *, name="contentScroll") -> QScrollArea:
    scroll = QScrollArea()
    scroll.setObjectName(name)
    scroll.setFrameShape(QScrollArea.NoFrame)
    scroll.setWidgetResizable(True)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    scroll.setWidget(widget)
    scroll.setMinimumSize(0, 0)
    return scroll


class _WorkflowTabBar(QTabBar):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFont(uiFont())
        self.setExpanding(False)
        self.setUsesScrollButtons(True)
        self.setElideMode(Qt.ElideRight)
        for button in self.findChildren(QToolButton):
            # The global toolbar padding otherwise leaves no room for the
            # native arrow inside Qt's narrow tab-scroll buttons.
            button.setStyleSheet('QToolButton { padding: 0px; background: #f5f6f8; '
                                'border: 1px solid #d8dce3; border-radius: 2px; }')

    def tabSizeHint(self, index):
        size = super().tabSizeHint(index)
        metrics = QFontMetrics(uiFont(bold=True))
        markers = [self.tabButton(index, side) for side in (QTabBar.LeftSide, QTabBar.RightSide)]
        markerWidth = sum(marker.sizeHint().width() + 8 for marker in markers if marker is not None)
        limit = 340 if markers[1] is not None else 280
        parent = self.parentWidget()
        if parent is not None:
            arrows = 2 * self.style().pixelMetric(QStyle.PM_TabBarScrollButtonWidth)
            limit = min(limit, max(100, parent.width() - 38 - arrows - 8))
        size.setWidth(min(limit, max(100, metrics.horizontalAdvance(self.tabText(index)) + 36 + markerWidth)))
        size.setHeight(max(36, self.fontMetrics().height() + 16))
        for marker in markers:
            if marker is not None:
                size.setHeight(max(size.height(), marker.sizeHint().height() + 12))
        return size

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        try:
            for index in range(self.count()):
                badge = self.tabButton(index, QTabBar.RightSide)
                color = badge.property('runtimeColor') if badge is not None else None
                if color and self.isTabVisible(index):
                    rect = self.tabRect(index).adjusted(1, 1, -1, -1)
                    painter.fillRect(QRect(rect.x(), rect.y(), rect.width(), 3), QColor(color))
        finally:
            painter.end()

    def minimumTabSizeHint(self, index):
        # Scroll the strip instead of squeezing names and badges into slivers.
        # Long names are still elided inside tabSizeHint's bounded width.
        return self.tabSizeHint(index)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if not self.count():
            return
        first = self.tabRect(0)
        gap = (first.right() < self.width() - 1 if self.isRightToLeft() else first.left() > 0)
        if gap and self.sizeHint().width() > self.width():
            # Qt 5 can retain a negative scroll offset after an overflowed
            # strip grows, leaving a blank leading area and disabled arrows.
            # Relayout without changing selection or emitting currentChanged.
            self.setExpanding(True)
            self.setExpanding(False)


class WorkflowTabs(QTabWidget):
    """A tab strip retaining the existing workflow-index API, without empty pages."""

    def __init__(self):
        super().__init__()
        self.setTabBar(_WorkflowTabBar(self))
        self.setDocumentMode(True)
        self._placingAddButton = False
        self._addPage = None
        self._addButton = QToolButton(self)
        from emo_master.apps.designer.ui.icon_map import icon
        self._addButton.setIcon(icon("plus"))
        self._addButton.setToolTip("新增工作流")
        self._addButton.setAccessibleName("新增工作流")
        self._addButton.setFixedSize(32, 32)
        self._addButton.clicked.connect(self._requestAddWorkflow)
        self.tabBar().installEventFilter(self)
        self.findChild(QStackedWidget).hide()
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def addTab(self, widget, title):
        index = super().addTab(widget, title)
        self.setTabToolTip(index, title)
        if title == "+":
            self._addPage = widget
            self.tabBar().setTabVisible(index, False)
        self.findChild(QStackedWidget).hide()
        self._placeAddButton()
        return index

    def _requestAddWorkflow(self):
        # Retain the hidden plus page's index used by MainWindow. Clearing or
        # removing it must not emit -1 or accidentally activate a real workflow.
        index = self.indexOf(self._addPage) if self._addPage is not None else -1
        if index >= 0:
            self.tabBarClicked.emit(index)

    def setEntryMarker(self, index, role, tooltip):
        """Keep the role visible even when Qt elides a long workflow name."""
        bar = self.tabBar()
        old = bar.tabButton(index, QTabBar.LeftSide)
        if old is not None:
            bar.setTabButton(index, QTabBar.LeftSide, None)
            old.deleteLater()
        if role:
            badge = QLabel(role, bar)
            badge.setObjectName("workflowEntryBadge")
            badge.setTextFormat(Qt.PlainText)
            badge.setFont(uiFont(bold=True))
            badge.setAttribute(Qt.WA_TransparentForMouseEvents)
            badge.setContentsMargins(6, 2, 6, 2)
            color, background = (("#1d4ed8", "#dbeafe") if role == "默认入口"
                                 else ("#047857", "#d1fae5"))
            badge.setStyleSheet(f"QLabel {{ color: {color}; background: {background}; border-radius: 4px; }}")
            badge.setAccessibleName(role)
            badge.adjustSize()
            bar.setTabButton(index, QTabBar.LeftSide, badge)
        self.widget(index).setProperty('entryTooltip', tooltip)
        self._updateTabDescription(index)
        self._placeAddButton()

    def setRuntimeStatus(self, index, status, details=''):
        bar = self.tabBar()
        old = bar.tabButton(index, QTabBar.RightSide)
        previous = old.property('runtimeStatus') if old is not None else ''
        if previous != status:
            if old is not None:
                bar.setTabButton(index, QTabBar.RightSide, None)
                old.deleteLater()
            if status in WORKFLOW_RUN_STYLES:
                text, color, background = WORKFLOW_RUN_STYLES[status]
                badge = QLabel(text, bar)
                badge.setObjectName('workflowRuntimeBadge')
                badge.setFont(uiFont(bold=True))
                badge.setAttribute(Qt.WA_TransparentForMouseEvents)
                badge.setContentsMargins(6, 2, 6, 2)
                badge.setStyleSheet(f'QLabel {{ color: {color}; background: {background}; border-radius: 4px; }}')
                badge.setProperty('runtimeStatus', status)
                badge.setProperty('runtimeColor', color)
                badge.adjustSize()
                bar.setTabButton(index, QTabBar.RightSide, badge)
            bar.update()
            self._placeAddButton()
        self.widget(index).setProperty('runtimeDetails', details)
        self._updateTabDescription(index)

    def _updateTabDescription(self, index):
        page, bar = self.widget(index), self.tabBar()
        entry = page.property('entryTooltip') or self.tabText(index)
        details = page.property('runtimeDetails') or ''
        self.setTabToolTip(index, entry + ('\n\n' + details if details else ''))
        markers = [bar.tabButton(index, side) for side in (QTabBar.LeftSide, QTabBar.RightSide)]
        labels = [marker.text() for marker in markers if marker is not None]
        bar.setAccessibleTabName(index, ' '.join([*labels, self.tabText(index)]))

    def _placeAddButton(self):
        if not hasattr(self, '_addButton') or self._placingAddButton:
            return
        self._placingAddButton = True
        try:
            bar, button, gap = self.tabBar(), self._addButton, 6
            available = max(0, self.width() - button.width() - gap)
            width = min(available, bar.sizeHint().width())
            if bar.maximumWidth() != width:
                bar.setMaximumWidth(width)
            # A width constraint reserves room for add/scroll controls. When
            # tabs fit, Qt keeps its natural width and the button follows them.
            if self.layoutDirection() == Qt.RightToLeft:
                bar.move(self.width() - bar.width(), bar.y())
                x = bar.x() - gap - button.width()
            else:
                x = bar.geometry().right() + 1 + gap
            x = max(0, min(x, self.width() - button.width()))
            y = max(0, bar.y() + (bar.height() - button.height()) // 2)
            button.move(x, y)
            button.raise_()
        finally:
            self._placingAddButton = False

    def event(self, event):
        result = super().event(event)
        if event.type() in (QEvent.Resize, QEvent.Show, QEvent.LayoutRequest,
                            QEvent.StyleChange, QEvent.FontChange, QEvent.LayoutDirectionChange):
            self._placeAddButton()
        return result

    def eventFilter(self, obj, event):
        if obj is self.tabBar() and event.type() in (QEvent.Resize, QEvent.Move, QEvent.LayoutRequest):
            self._placeAddButton()
        return super().eventFilter(obj, event)

    def sizeHint(self):
        return QSize(400, max(38, self.tabBar().sizeHint().height()))

    def minimumSizeHint(self):
        return QSize(120, self.sizeHint().height())
