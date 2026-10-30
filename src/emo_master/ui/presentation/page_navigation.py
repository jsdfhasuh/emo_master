"""Compact page selection shared by the shell and optional page components."""
from PySide2.QtCore import Qt, Signal
from PySide2.QtWidgets import QComboBox, QHBoxLayout, QLabel, QSizePolicy, QWidget


class PageNavigation(QWidget):
    pageRequested = Signal(str)

    def __init__(self, presentation, *, title='页面', parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        if title:
            label = QLabel(title)
            label.setTextFormat(Qt.PlainText)
            layout.addWidget(label)
        self.picker = QComboBox()
        self.picker.setObjectName('pagePicker')
        self.picker.setAccessibleName('选择页面')
        self.picker.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.picker.setMinimumContentsLength(10)
        self.picker.setMinimumWidth(0)
        self.picker.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        layout.addWidget(self.picker, 1)
        self.setPages(presentation)
        self.picker.activated.connect(self._activated)

    def setPages(self, presentation):
        self.picker.clear()
        for pageId in presentation.pageOrder:
            name = presentation.pages[pageId].name
            self.picker.addItem(name, pageId)
            self.picker.setItemData(self.picker.count() - 1, name, Qt.ToolTipRole)
        self.picker.setEnabled(self.picker.count() > 1)

    def selectPage(self, pageId):
        index = self.picker.findData(pageId)
        self.picker.setCurrentIndex(index)
        self.picker.setToolTip(self.picker.currentText())

    def _activated(self, _index):
        pageId = self.picker.currentData()
        if pageId:
            self.pageRequested.emit(pageId)
