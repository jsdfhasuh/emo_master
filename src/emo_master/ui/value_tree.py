"""Bounded Qt tree for inspection of nested values."""
from itertools import islice

from PySide2.QtCore import Qt
from PySide2.QtWidgets import (
    QDialog, QHBoxLayout, QPlainTextEdit, QToolButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout,
)


class ValueTree(QTreeWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setHeaderLabels(["字段", "值"])
        self.setColumnWidth(0, 200)
        self.itemExpanded.connect(self.expandValue)
        self.itemDoubleClicked.connect(self.more)

    @staticmethod
    def pageTool(parent, name, title, callback):
        button = QToolButton(parent)
        button.setArrowType(Qt.LeftArrow if name == "panel-left-close" else Qt.RightArrow)
        button.setToolTip(title)
        button.setAccessibleName(title)
        button.clicked.connect(callback)
        return button

    def setValue(self, value):
        self.clear()
        self.appendValue(self.invisibleRootItem(), "root", value)
        self.topLevelItem(0).setExpanded(True)

    def appendValue(self, parent, name, value):
        summary = (f"{type(value).__name__} [{len(value)}]" if isinstance(value, (dict, list)) else str(value))
        item = QTreeWidgetItem(parent, [str(name), summary[:500]])
        item.setToolTip(1, summary[:2000])
        if isinstance(value, (dict, list)) and value:
            item.setData(0, Qt.UserRole, (value, 0))
            QTreeWidgetItem(item, [""])
        elif isinstance(value, str) and len(value) > 500:
            item.setData(1, Qt.UserRole+1, value)
        return item

    def expandValue(self, item):
        pending = item.data(0, Qt.UserRole)
        if pending is None:
            return
        value, offset = pending
        item.setData(0, Qt.UserRole, None)
        item.takeChildren()
        self.page(item, value, offset)

    def page(self, parent, value, offset):
        rows = islice(value.items(), offset, offset+100) if isinstance(value, dict) else enumerate(value[offset:offset+100], offset)
        for key, child in rows:
            self.appendValue(parent, key, child)
        if offset+100 < len(value):
            more = QTreeWidgetItem(parent, ["更多…", f"{len(value)-offset-100}"])
            more.setData(1, Qt.UserRole, (value, offset+100))

    def more(self, item, column):
        pending = item.data(1, Qt.UserRole)
        if pending is not None:
            parent = item.parent()
            parent.removeChild(item)
            self.page(parent, *pending)
        text = item.data(1, Qt.UserRole+1)
        if text is not None:
            dialog = QDialog(self)
            dialog.setWindowTitle(item.text(0))
            dialog.resize(720, 480)
            layout = QVBoxLayout(dialog)
            editor = QPlainTextEdit()
            editor.setReadOnly(True)
            layout.addWidget(editor)
            controls = QHBoxLayout()
            position = [0]
            def page(delta=0):
                position[0] = min(max(0, position[0]+delta), (len(text)-1)//65536)
                editor.setPlainText(text[position[0]*65536:(position[0]+1)*65536])
                dialog.setWindowTitle(f"{item.text(0)} - {position[0]+1}/{(len(text)-1)//65536+1}")
            controls.addWidget(self.pageTool(dialog, "panel-left-close", "上一页", lambda: page(-1)))
            controls.addWidget(self.pageTool(dialog, "panel-left-open", "下一页", lambda: page(1)))
            layout.addLayout(controls)
            page()
            dialog.exec_()
