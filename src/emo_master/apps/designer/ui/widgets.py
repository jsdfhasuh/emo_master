from __future__ import annotations

from PySide2.QtCore import QSize, Qt
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
        self._addButton = QToolButton(self)
        from emo_master.apps.designer.ui.icon_map import icon
        self._addButton.setIcon(icon("plus"))
        self._addButton.setToolTip("新增工作流")
        self._addButton.setFixedSize(32, 32)
        self._addButton.clicked.connect(lambda: self.tabBarClicked.emit(self.count() - 1))
        self.setCornerWidget(self._addButton, Qt.TopRightCorner)
        self.findChild(QStackedWidget).hide()
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def addTab(self, widget, title):
        index = super().addTab(widget, title)
        self.setTabToolTip(index, title)
        if title == "+":
            self.tabBar().setTabVisible(index, False)
        self.findChild(QStackedWidget).hide()
        return index

    def sizeHint(self):
        return QSize(400, max(38, self.tabBar().sizeHint().height()))

    def minimumSizeHint(self):
        return QSize(120, self.sizeHint().height())
