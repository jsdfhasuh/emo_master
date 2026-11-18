"""Native tab and button page navigation using the project page order."""
from PySide2.QtCore import Qt, Signal
from PySide2.QtWidgets import QHBoxLayout, QPushButton, QTabBar, QWidget

from .appearance import plainLabel


class PageNavigation(QWidget):
    pageRequested = Signal(str)

    def __init__(self, presentation, *, title='', parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        if title:
            layout.addWidget(plainLabel(title))
        self.content = QHBoxLayout()
        self.content.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(self.content)
        layout.addStretch()
        self.setAccessibleName('选择页面')
        self.setPages(presentation)

    def setPages(self, presentation):
        while self.content.count():
            self.content.takeAt(0).widget().deleteLater()
        self.buttons = {}
        self.tabs = None
        if presentation.navigation.mode == 'tabs':
            self.tabs = QTabBar()
            self.tabs.setDrawBase(False)
            self.tabs.setExpanding(False)
            self.tabs.setElideMode(Qt.ElideRight)
            self.content.addWidget(self.tabs)
            for pageId in presentation.pageOrder:
                name = presentation.pages[pageId].name
                index = self.tabs.addTab(name)
                self.tabs.setTabData(index, pageId)
                self.tabs.setTabToolTip(index, name)
            self.tabs.currentChanged.connect(self._tabChanged)
        else:
            for pageId in presentation.pageOrder:
                button = QPushButton(presentation.pages[pageId].name)
                button.setCheckable(True)
                button.clicked.connect(lambda _checked=False, key=pageId: self.pageRequested.emit(key))
                self.content.addWidget(button)
                self.buttons[pageId] = button

    def selectPage(self, pageId):
        if self.tabs is not None:
            blocked = self.tabs.blockSignals(True)
            for index in range(self.tabs.count()):
                if self.tabs.tabData(index) == pageId:
                    self.tabs.setCurrentIndex(index)
                    break
            self.tabs.blockSignals(blocked)
        for key, button in self.buttons.items():
            button.setChecked(key == pageId)

    def _tabChanged(self, index):
        if index >= 0:
            self.pageRequested.emit(self.tabs.tabData(index))
