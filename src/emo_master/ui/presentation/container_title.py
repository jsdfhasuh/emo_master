"""A persistent native title above a container's unchanged logical grid."""
from PySide2.QtCore import Qt
from PySide2.QtWidgets import QLabel, QToolButton, QWidget, QVBoxLayout


class ContainerTitle:
    def __init__(self, card, text, style, *, margins, collapse=None):
        layout = QVBoxLayout(card)
        layout.setContentsMargins(*margins)
        layout.setSpacing(6)
        self.label = QLabel(text, card) if collapse is None else QToolButton(card)
        self.label.setObjectName('containerTitle')
        self.label.setStyleSheet(style)
        self.body = QWidget(card)
        self.body.setProperty('containerGridId', card.property('componentId'))
        # Both the live QWidgetItem and the resize proxy now ask the same native
        # layout for heightForWidth. No Resize callback changes margins later.
        layout.addWidget(self.label)
        layout.addWidget(self.body, 1)
        if collapse is None:
            self.label.setTextFormat(Qt.PlainText)
            self.label.setWordWrap(True)
        else:
            self.label.setText(text or '详情')
            self.label.setCheckable(True)
            self.label.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
            self.label.toggled.connect(self.setExpanded)
            self.label.setChecked(not collapse.initiallyCollapsed)
            self.setExpanded(self.label.isChecked())

    def setExpanded(self, expanded):
        self.body.setVisible(expanded)
        self.label.setArrowType(Qt.DownArrow if expanded else Qt.RightArrow)
