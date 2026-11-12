"""Numeric vertex table for the ROI editor."""
from copy import deepcopy

from PySide2.QtCore import Qt, Signal
from PySide2.QtWidgets import QDoubleSpinBox, QHeaderView, QStyledItemDelegate, QTableWidget, QTableWidgetItem


class _CoordinateDelegate(QStyledItemDelegate):
    def displayText(self, value, locale):
        if isinstance(value, (int, float)):
            return f"{value:.3f}".rstrip("0").rstrip(".")
        return str(value)

    def createEditor(self, parent, option, index):
        editor = QDoubleSpinBox(parent)
        editor.setRange(-1_000_000_000., 1_000_000_000.)
        editor.setDecimals(3)
        return editor


class RoiPointEditor(QTableWidget):
    pointsChanged = Signal()

    def __init__(self, parent=None):
        super().__init__(0, 2, parent)
        self._points = []
        self.setHorizontalHeaderLabels(["X", "Y"])
        self.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.setItemDelegate(_CoordinateDelegate(self))
        self.setMinimumHeight(100)
        self.setMaximumHeight(160)
        self.setAccessibleName("多边形顶点坐标")
        self.itemChanged.connect(self._itemChanged)

    def setPoints(self, points):
        self._points = deepcopy(points)
        self.blockSignals(True)
        rows = points if isinstance(points, list) else []
        self.setRowCount(len(rows))
        for row, point in enumerate(rows):
            for column, key in enumerate(("x", "y")):
                item = QTableWidgetItem()
                item.setData(Qt.EditRole, point.get(key, "") if isinstance(point, dict) else "")
                self.setItem(row, column, item)
        self.blockSignals(False)

    def points(self):
        return deepcopy(self._points)

    def _itemChanged(self, item):
        row = item.row()
        if isinstance(self._points[row], dict):
            self._points[row][("x", "y")[item.column()]] = item.data(Qt.EditRole)
            self.pointsChanged.emit()
