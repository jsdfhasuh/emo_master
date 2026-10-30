"""A local wall clock that only ticks while its page is visible."""
from PySide2.QtCore import QDateTime, Qt, QTimer
from PySide2.QtWidgets import QLabel


class ClockView(QLabel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setTextFormat(Qt.PlainText)
        self.setWordWrap(True)
        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self.refresh)
        self.refresh()

    def refresh(self):
        self.setText(QDateTime.currentDateTime().toString('yyyy-MM-dd HH:mm:ss'))

    def showEvent(self, event):
        self.refresh()
        self.timer.start()
        super().showEvent(event)

    def hideEvent(self, event):
        self.timer.stop()
        super().hideEvent(event)
