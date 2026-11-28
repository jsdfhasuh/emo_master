from __future__ import annotations

from PySide2.QtCore import QEvent, QObject, QPointF, QSize, Qt
from PySide2.QtGui import QPixmap, QWheelEvent
from PySide2.QtWidgets import (
    QApplication, QLabel, QScrollArea, QSizePolicy, QToolButton, QVBoxLayout, QWidget,
)

from emo_master.apps.designer.ui.workflow_navigation import WorkflowTabs as WorkflowTabs
from emo_master.ui.widgets import ElidedLabel as ElidedLabel
from emo_master.ui.wrap_label import OptionalWrapLabel as OptionalWrapLabel, WrapLabel as WrapLabel


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
