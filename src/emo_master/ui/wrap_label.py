"""Labels that wrap without imposing a minimum width."""
from PySide2.QtCore import QSize, Qt
from PySide2.QtWidgets import QLabel, QSizePolicy


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
