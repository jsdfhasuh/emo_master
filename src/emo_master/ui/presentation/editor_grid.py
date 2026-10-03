"""Qt 5 grid geometry used only by the embedded native editor."""
from PySide2.QtCore import QRect
from PySide2.QtWidgets import QGridLayout


class EditorGridLayout(QGridLayout):
    def cellRect(self, row, column):
        rect = super().cellRect(row, column)
        parent = self.parentWidget()
        # Qt 5 heightForWidth queries can invalidate row data without changing
        # the layout rectangle. Re-distribute before exposing coordinates to
        # a mouse gesture; no events or project mutations occur in between.
        if rect.height() == 0 and parent is not None and parent.height() > 1:
            geometry = parent.rect()
            self.setGeometry(QRect(0, 0, 1, 1))
            self.setGeometry(geometry)
            rect = super().cellRect(row, column)
        return rect
