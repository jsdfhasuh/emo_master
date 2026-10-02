"""A persistent native title above a container's unchanged logical grid."""
from PySide2.QtCore import Qt
from PySide2.QtWidgets import QLabel, QWidget, QVBoxLayout


class ContainerTitle:
    def __init__(self, card, text, style, *, editing):
        layout = QVBoxLayout(card)
        layout.setContentsMargins(9, 28 if editing else 9, 9, 9)
        layout.setSpacing(8)
        self.label = QLabel(text, card)
        self.label.setObjectName('containerTitle')
        self.label.setTextFormat(Qt.PlainText)
        self.label.setWordWrap(True)
        self.label.setStyleSheet(style)
        self.body = QWidget(card)
        self.body.setProperty('containerGridId', card.property('componentId'))
        # Both the live QWidgetItem and the resize proxy now ask the same native
        # layout for heightForWidth. No Resize callback changes margins later.
        layout.addWidget(self.label)
        layout.addWidget(self.body, 1)
