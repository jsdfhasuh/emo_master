"""Foreground, non-blocking feedback without changing a debug owner's state."""
from PySide2.QtCore import Qt
from PySide2.QtWidgets import QDialog, QHBoxLayout, QPushButton, QVBoxLayout

from emo_master.apps.designer.ui.widgets import WrapLabel


class ProjectReplacementNotice(QDialog):
    def __init__(self, message: str, parent) -> None:
        super().__init__(parent)
        self.setObjectName('projectReplacementNotice')
        self.setWindowTitle('无法切换项目')
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setWindowModality(Qt.WindowModal)
        layout = QVBoxLayout(self)
        self.messageLabel = WrapLabel(message.replace('；', '；\n'))
        self.messageLabel.setMinimumWidth(360)
        layout.addWidget(self.messageLabel)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        acknowledge = QPushButton('知道了')
        acknowledge.setDefault(True)
        acknowledge.clicked.connect(self.accept)
        buttons.addWidget(acknowledge)
        layout.addLayout(buttons)
        self.resize(440, self.sizeHint().height())
