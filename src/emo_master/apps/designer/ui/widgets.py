from __future__ import annotations

from PySide2.QtCore import QEvent, QObject, QPointF, QSize, Qt
from PySide2.QtGui import QPixmap, QWheelEvent
from PySide2.QtWidgets import (
    QApplication, QLabel, QScrollArea, QSizePolicy, QToolButton, QVBoxLayout, QWidget,
)

from emo_master.apps.designer.ui.workflow_navigation import WorkflowTabs as WorkflowTabs


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


class CollapsibleSection(QWidget):
    def __init__(self, title, content, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.toggle = QToolButton(self)
        self.toggle.setText(title)
        self.toggle.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.toggle.setArrowType(Qt.RightArrow)
        self.toggle.setCheckable(True)
        self.toggle.setAccessibleName(title)
        self.toggle.setToolTip("展开或收起" + title)
        self.content = content
        layout.addWidget(self.toggle)
        layout.addWidget(content)
        content.hide()
        self.toggle.toggled.connect(self.setExpanded)

    def setExpanded(self, expanded):
        self.toggle.setChecked(expanded)
        self.content.setVisible(expanded)
        self.toggle.setArrowType(Qt.DownArrow if expanded else Qt.RightArrow)


class FormWheelGuard(QObject):
    """Scroll a form without accidentally editing its numeric/combo controls."""
    def eventFilter(self, watched, event):
        if event.type() != QEvent.Wheel:
            return False
        viewport = self.parent().viewport()
        forwarded = QWheelEvent(QPointF(viewport.mapFromGlobal(event.globalPos())),
            event.globalPosition(), event.pixelDelta(), event.angleDelta(),
            event.buttons(), event.modifiers(), event.phase(), event.inverted(), event.source())
        QApplication.sendEvent(viewport, forwarded)
        event.accept()
        return True
