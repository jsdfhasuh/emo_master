"""Transparent layout occupant; its editing hint never contributes to measurement."""
from PySide2.QtCore import QSize, Qt
from PySide2.QtGui import QColor, QPainter, QPen
from PySide2.QtWidgets import QFrame, QVBoxLayout


class SpacerCard(QFrame):
    def __init__(self, editing=False):
        super().__init__()
        self.editing = editing
        self.setAttribute(Qt.WA_NoSystemBackground)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

    def sizeHint(self):
        return QSize(24, 24)

    def minimumSizeHint(self):
        return QSize(0, 0)

    def paintEvent(self, event):
        if not self.editing:
            return
        painter = QPainter(self)
        painter.setPen(QPen(QColor('#94a3b8'), 1, Qt.DashLine))
        painter.drawRect(self.rect().adjusted(1, 1, -1, -1))
        painter.drawText(self.rect().adjusted(3, 1, -3, -1), Qt.AlignCenter, '空白块')
