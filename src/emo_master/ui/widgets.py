from PySide2.QtCore import QSize, Qt
from PySide2.QtWidgets import QLabel, QSizePolicy


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
