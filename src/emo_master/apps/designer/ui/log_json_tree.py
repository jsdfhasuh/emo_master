"""Read-only JSON details using the Designer's bounded value tree."""
import json

from PySide2.QtCore import Qt
from PySide2.QtGui import QKeySequence
from PySide2.QtWidgets import QApplication, QHeaderView, QMenu, QTreeWidgetItem

from .debug_values import ValueTree


_VALUE_ROLE = Qt.UserRole + 10


def _json(value):
    return json.dumps(value, ensure_ascii=False, indent=2, default=str)


def _copyText(text):
    QApplication.clipboard().setText(text)


class LogJsonTree(ValueTree):
    """Only expanded containers are materialized; copies always retain full values."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._payload = None
        self.setObjectName("logJsonDetails")
        self.setAccessibleName("日志 JSON 详情")
        self.setHeaderLabels(["字段", "值", "类型"])
        self.setUniformRowHeights(True)
        self.setColumnWidth(0, 230)
        self.header().setStretchLastSection(False)
        self.header().setSectionResizeMode(0, QHeaderView.Interactive)
        self.header().setSectionResizeMode(1, QHeaderView.Stretch)
        self.header().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.setToolTip("展开对象或数组查看字段；双击长文本查看完整内容；右键可复制值或完整 JSON。")
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._contextMenu)

    def setPayload(self, payload):
        self.clear()
        self._payload = payload
        root = self.appendValue(self.invisibleRootItem(), "日志详情", payload)
        root.setExpanded(True)
        self.setCurrentItem(root)

    def clear(self):
        super().clear()
        self._payload = None

    def toPlainText(self):
        return "" if self._payload is None else _json(self._payload)

    def appendValue(self, parent, name, value):
        container = isinstance(value, (dict, list))
        if isinstance(value, dict):
            kind, summary = "object", f"{{ {len(value)} 个字段 }}"
        elif isinstance(value, list):
            kind, summary = "array", f"[ {len(value)} 项 ]"
        else:
            kind = ("null" if value is None else "boolean" if isinstance(value, bool)
                    else "number" if isinstance(value, (int, float)) else "string")
            summary = json.dumps(value, ensure_ascii=False, default=str)
        item = QTreeWidgetItem(parent, [str(name), summary[:500], kind])
        item.setToolTip(0, str(name))
        item.setToolTip(1, summary[:2000])
        # A wrapper also distinguishes a real JSON null from the pagination row.
        item.setData(0, _VALUE_ROLE, (value,))
        if container and value:
            item.setData(0, Qt.UserRole, (value, 0))
            QTreeWidgetItem(item, [""])
        elif isinstance(value, str) and len(value) > 500:
            item.setData(1, Qt.UserRole + 1, value)
        return item

    def copySelectedValue(self):
        item = self.currentItem()
        value = item.data(0, _VALUE_ROLE) if item is not None else None
        if value is not None:
            _copyText(_json(value[0]))

    def copyJson(self):
        if self._payload is not None:
            _copyText(self.toPlainText())

    def keyPressEvent(self, event):
        if event.matches(QKeySequence.Copy):
            self.copySelectedValue()
            event.accept()
            return
        super().keyPressEvent(event)

    def _contextMenu(self, position):
        item = self.itemAt(position)
        if item is not None:
            self.setCurrentItem(item)
        menu = QMenu(self)
        selected = menu.addAction("复制选中值", self.copySelectedValue)
        selected.setEnabled(item is not None and item.data(0, _VALUE_ROLE) is not None)
        complete = menu.addAction("复制完整 JSON", self.copyJson)
        complete.setEnabled(self._payload is not None)
        menu.exec_(self.viewport().mapToGlobal(position))
