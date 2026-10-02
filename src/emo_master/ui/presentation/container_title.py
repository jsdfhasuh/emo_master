"""A persistent native title above a container's unchanged logical grid."""
from PySide2.QtCore import Qt, QEvent, QObject
from PySide2.QtWidgets import QLabel


class ContainerTitle(QObject):
    def __init__(self, card, text, style, *, editing):
        super().__init__(card)
        self.card = card
        self.label = QLabel(text, card)
        self.label.setObjectName('containerTitle')
        self.label.setTextFormat(Qt.PlainText)
        self.label.setWordWrap(True)
        self.label.setStyleSheet(style)
        self.top = 28 if editing else 9
        card.installEventFilter(self)
        self.arrange()

    def arrange(self):
        width = max(1, self.card.width() - 18)
        height = max(self.label.fontMetrics().height(), self.label.heightForWidth(width))
        self.label.setGeometry(9, self.top, width, height)
        layout = self.card.layout()
        margin = layout.contentsMargins()
        top = self.top + height + 8
        if margin.top() != top:
            layout.setContentsMargins(margin.left(), top, margin.right(), margin.bottom())

    def eventFilter(self, obj, event):
        if event.type() in (QEvent.Resize, QEvent.Show):
            self.arrange()
        return False
