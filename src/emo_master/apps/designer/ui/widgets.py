from __future__ import annotations

from PySide2.QtCore import QEvent, QSize, Qt
from PySide2.QtGui import QFontMetrics, QPixmap
from PySide2.QtWidgets import (
    QLabel, QScrollArea, QSizePolicy, QStackedWidget, QTabBar, QTabWidget,
    QToolButton, QWidget,
)

from emo_master.apps.designer.ui.theme import uiFont


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

    def tabSizeHint(self, index):
        size = super().tabSizeHint(index)
        metrics = QFontMetrics(uiFont(bold=True))
        size.setWidth(min(280, max(100, metrics.horizontalAdvance(self.tabText(index)) + 36)))
        size.setHeight(max(36, self.fontMetrics().height() + 16))
        return size


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
