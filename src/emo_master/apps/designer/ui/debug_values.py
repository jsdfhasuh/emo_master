"""Typed editing and bounded tree inspection for debug data."""
from copy import deepcopy
from itertools import islice

from PySide2.QtCore import Qt
from PySide2.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QHBoxLayout, QInputDialog,
    QLineEdit, QPlainTextEdit, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget, QToolButton,
)

from emo_master.apps.runtime.operator_debug.contracts import encode, parse
from .icon_map import icon


def tool(parent, name, title, callback):
    button = QToolButton(parent)
    button.setIcon(icon(name))
    button.setToolTip(title)
    button.setAccessibleName(title)
    button.clicked.connect(callback)
    return button


class ValueTree(QTreeWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setHeaderLabels(["字段", "值"])
        self.setColumnWidth(0, 200)
        self.itemExpanded.connect(self.expandValue)
        self.itemDoubleClicked.connect(self.more)

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
            controls.addWidget(tool(dialog, "panel-left-close", "上一页", lambda: page(-1)))
            controls.addWidget(tool(dialog, "panel-left-open", "下一页", lambda: page(1)))
            layout.addLayout(controls)
            page()
            dialog.exec_()


class StructuredValue(QDialog):
    def __init__(self, value, parent=None):
        super().__init__(parent)
        self.setWindowTitle("结构化输入")
        self.resize(620, 480)
        layout = QVBoxLayout(self)
        self.tree = ValueTree(self)
        self.value = deepcopy(value)
        self.tree.setValue(self.value)
        layout.addWidget(self.tree)
        controls = QHBoxLayout()
        for name, title, callback in [("sliders-horizontal", "编辑选中值", self.edit), ("plus", "添加字段或元素", self.add),
                                       ("trash-2", "删除选中字段", self.remove), ("logs", "高级 JSON", self.advanced)]:
            controls.addWidget(tool(self, name, title, callback))
        controls.addStretch()
        layout.addLayout(controls)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def location(self):
        item = self.tree.currentItem() or self.tree.topLevelItem(0)
        if item.data(1, Qt.UserRole) is not None:
            raise ValueError("请选择数据字段")
        keys = []
        while item.parent() is not None:
            keys.insert(0, item.text(0))
            item = item.parent()
        value, parent, key = self.value, None, None
        for key in keys:
            parent = value
            key = int(key) if isinstance(parent, list) else key
            value = parent[key]
        return parent, key, value

    def prompt(self, initial=None):
        names = ["string", "integer", "number", "boolean", "null", "object", "list"]
        kind, ok = QInputDialog.getItem(self, "值类型", "类型", names, 0, False)
        if not ok:
            return False, None
        if kind in {"null", "object", "list"}:
            return True, {"null": None, "object": {}, "list": []}[kind]
        text, ok = QInputDialog.getText(self, "字段值", kind, text="" if initial is None else str(initial))
        if not ok:
            return False, None
        try:
            value = text if kind == "string" else parse(text)
            expected = {"integer": int, "number": (int, float), "boolean": bool, "string": str}[kind]
            if not isinstance(value, expected) or (kind in {"integer", "number"} and isinstance(value, bool)):
                raise ValueError("值与类型不匹配")
            return True, value
        except ValueError as error:
            self.setWindowTitle(str(error))
            return False, None

    def edit(self):
        try:
            parent, key, value = self.location()
            ok, replacement = self.prompt(value)
            if ok:
                if parent is None:
                    self.value = replacement
                else:
                    parent[key] = replacement
                self.tree.setValue(self.value)
        except ValueError as error:
            self.setWindowTitle(str(error))

    def add(self):
        try:
            _, _, value = self.location()
            if not isinstance(value, (dict, list)):
                return
            key, ok = QInputDialog.getText(self, "添加字段", "字段名称") if isinstance(value, dict) else (None, True)
            if not ok or (isinstance(value, dict) and (not key or key in value)):
                return
            ok, child = self.prompt()
            if ok:
                if isinstance(value, dict):
                    value[key] = child
                else:
                    value.append(child)
                self.tree.setValue(self.value)
        except ValueError as error:
            self.setWindowTitle(str(error))

    def remove(self):
        try:
            parent, key, _ = self.location()
            if parent is not None:
                del parent[key]
                self.tree.setValue(self.value)
        except ValueError:
            pass

    def advanced(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("高级 JSON")
        layout = QVBoxLayout(dialog)
        editor = QPlainTextEdit(encode(self.value))
        layout.addWidget(editor)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec_() == QDialog.Accepted:
            try:
                self.value = parse(editor.toPlainText())
                self.tree.setValue(self.value)
            except ValueError as error:
                self.setWindowTitle(str(error))


class InputRow(QWidget):
    def __init__(self, portType, parent=None):
        super().__init__(parent)
        self.portType = portType
        self.reference = None
        self.structured = [] if portType.startswith("list<") else {}
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.mode = QComboBox()
        self.mode.addItems(["未提供", "值", "空值", "完整数据源"])
        layout.addWidget(self.mode)
        self.text = QLineEdit()
        self.boolean = QComboBox()
        self.boolean.addItems(["false", "true"])
        self.structure = tool(self, "git-branch", "编辑结构化输入", self.edit)
        layout.addWidget(self.text, 1)
        layout.addWidget(self.boolean)
        layout.addWidget(self.structure)
        self.mode.currentIndexChanged.connect(self.refresh)
        self.refresh()

    def refresh(self):
        scalar = self.portType in {"integer", "number", "string"}
        value = self.mode.currentIndex() == 1
        self.text.setVisible(scalar or self.mode.currentIndex() == 3 or self.portType == "image")
        self.text.setReadOnly(not value or not scalar)
        self.boolean.setVisible(value and self.portType == "boolean")
        self.structure.setVisible(value and not scalar and self.portType not in {"boolean", "image"})

    def edit(self):
        dialog = StructuredValue(self.structured, self)
        if dialog.exec_() == QDialog.Accepted:
            self.structured = dialog.value

    def setReference(self, value, label):
        self.reference = value
        self.mode.setCurrentIndex(3)
        self.text.setText(label)
        self.text.setToolTip(label)
        self.refresh()

    def wire(self):
        mode = self.mode.currentIndex()
        if mode == 0:
            return None
        if mode == 2:
            return {"inline": None}
        if mode == 3:
            if self.reference is None:
                raise ValueError("尚未选择完整数据源")
            return deepcopy(self.reference)
        if self.portType == "image":
            raise ValueError("请选择 PNG 文件或完整图像来源")
        if self.portType == "string":
            value = self.text.text()
        elif self.portType == "boolean":
            value = self.boolean.currentIndex() == 1
        elif self.portType in {"integer", "number"}:
            value = parse(self.text.text())
            if isinstance(value, bool) or not isinstance(value, int if self.portType == "integer" else (int, float)):
                raise ValueError("数值类型不匹配")
        else:
            value = self.structured
        return {"inline": deepcopy(value)}
