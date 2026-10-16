from __future__ import annotations

from collections.abc import Iterable
from copy import deepcopy
from typing import Any

from emo_master.core.contracts.port_types import normalizePortType, validatePortSpec


_TYPE_LABELS = (
    ("图像", "image"),
    ("布尔 / 是非", "boolean"),
    ("整数", "integer"),
    ("数值", "number"),
    ("文本", "string"),
    ("JSON 数据", "json"),
    ("对象", "object"),
    ("任意类型", "any"),
    ("图像列表", "list<image>"),
    ("数值列表", "list<number>"),
    ("文本列表", "list<string>"),
    ("任意类型列表", "list<any>"),
)


def _editedPortSpec(
    original: object, typeName: str, properties: dict[str, object] | None = None
) -> object:
    """Keep descriptor metadata and legacy spellings on a name-only edit."""
    if not typeName:
        raise ValueError("请选择或填写数据类型")
    if normalizePortType(original) == normalizePortType(typeName):
        result = deepcopy(original)
    elif isinstance(original, dict):
        result = {**deepcopy(original), "type": typeName}
    else:
        result = typeName
    if properties is not None:
        if isinstance(result, dict):
            for field in ("required", "nullable", "schemaVersion"):
                result.pop(field, None)
            result.update(properties)
        elif properties:
            result = {"type": result, **properties}
    validatePortSpec(result)
    return result


def _validationMessage(error: ValueError) -> str:
    message = str(error)
    if "schemaVersion is only valid" in message:
        return "结构版本（schemaVersion）仅适用于语义数据；请在“属性…”中取消版本限制，或恢复数据类型。"
    if "schemaVersion must be" in message:
        return "结构版本（schemaVersion）无效，请填写支持的版本，例如 1.x。"
    return message


try:
    from PySide2.QtCore import Qt, Signal
    from PySide2.QtWidgets import (
        QAbstractItemView,
        QCheckBox,
        QComboBox,
        QDialog,
        QFormLayout,
        QHBoxLayout,
        QHeaderView,
        QLabel,
        QLineEdit,
        QPushButton,
        QSizePolicy,
        QTableWidget,
        QTabWidget,
        QVBoxLayout,
        QWidget,
    )

    from emo_master.apps.designer.ui.icon_map import icon
    from emo_master.apps.designer.ui.widgets import ElidedLabel, WrapLabel, scrollContent

    class _PortPropertiesDialog(QDialog):
        """Edit metadata explicitly, preserving unspecified flags and legacy types."""

        def __init__(self, side: str, name: str, typeName: str, original: object, parent=None) -> None:
            super().__init__(parent)
            self._original = deepcopy(original)
            self._typeName = typeName
            self.setWindowTitle(f"{side}接口属性")
            self.setModal(True)
            self.resize(520, 360)
            layout = QVBoxLayout(self)
            title = ElidedLabel(f"接口：{name or '（未命名）'}")
            title.setObjectName("panelTitle")
            layout.addWidget(title)
            body = QWidget()
            bodyLayout = QVBoxLayout(body)
            typeLabel = ElidedLabel(f"数据类型：{typeName or '（未选择）'}")
            typeLabel.setObjectName("mutedText")
            bodyLayout.addWidget(typeLabel)
            form = QFormLayout()
            form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
            metadata = original if isinstance(original, dict) else {}
            self._requiredCombo = QComboBox()
            self._requiredCombo.setAccessibleName(f"{side}接口必填要求")
            self._requiredCombo.setToolTip("required：未指定时默认必须提供；可选接口允许不提供此项。")
            for label, value in (("未指定（默认必填）", None), ("必填", True), ("可选", False)):
                self._requiredCombo.addItem(label, value)
            self._requiredCombo.setCurrentIndex(self._requiredCombo.findData(metadata.get("required")))
            form.addRow("必填要求", self._requiredCombo)
            self._nullableCombo = QComboBox()
            self._nullableCombo.setAccessibleName(f"{side}接口空值要求")
            self._nullableCombo.setToolTip("nullable：控制显式空值（null）；与是否可以省略接口是不同的要求。")
            for label, value in (("未指定（按类型判断）", None), ("允许空值", True), ("不允许空值", False)):
                self._nullableCombo.addItem(label, value)
            self._nullableCombo.setCurrentIndex(self._nullableCombo.findData(metadata.get("nullable")))
            form.addRow("空值要求", self._nullableCombo)
            versionRow = QHBoxLayout()
            self._versionCheck = QCheckBox("限制版本")
            self._versionCheck.setAccessibleName(f"{side}接口限制结构版本")
            self._versionCheck.setChecked("schemaVersion" in metadata)
            self._versionEdit = QLineEdit(str(metadata.get("schemaVersion", "")))
            self._versionEdit.setAccessibleName(f"{side}接口结构版本")
            self._versionEdit.setPlaceholderText("例如 1.x 或 1.1")
            self._versionEdit.setToolTip("schemaVersion：只适用于语义数据及其列表；取消勾选会明确移除此限制。")
            self._versionEdit.setEnabled(self._versionCheck.isChecked())
            versionRow.addWidget(self._versionCheck)
            versionRow.addWidget(self._versionEdit, 1)
            form.addRow("结构版本", versionRow)
            bodyLayout.addLayout(form)
            hint = WrapLabel("“未指定”保留原有默认行为，不等同于“否”。结构版本只用于检测结果等语义数据；普通文本、数值无需限制版本。")
            hint.setObjectName("mutedText")
            bodyLayout.addWidget(hint)
            bodyLayout.addStretch(1)
            layout.addWidget(scrollContent(body), 1)
            self._errorLabel = WrapLabel()
            self._errorLabel.setStyleSheet("color: #b91c1c;")
            layout.addWidget(self._errorLabel)
            buttons = QHBoxLayout()
            buttons.addStretch(1)
            self._cancelButton = QPushButton("取消")
            self._cancelButton.setAutoDefault(False)
            self._saveButton = QPushButton("确认属性")
            self._saveButton.setObjectName("primaryButton")
            self._saveButton.setDefault(True)
            buttons.addWidget(self._cancelButton)
            buttons.addWidget(self._saveButton)
            layout.addLayout(buttons)
            self._cancelButton.clicked.connect(self.reject)
            self._saveButton.clicked.connect(self.accept)
            self._requiredCombo.currentIndexChanged.connect(lambda _index: self._validate())
            self._nullableCombo.currentIndexChanged.connect(lambda _index: self._validate())
            self._versionCheck.toggled.connect(self._versionEdit.setEnabled)
            self._versionCheck.toggled.connect(lambda _checked: self._validate())
            self._versionEdit.textChanged.connect(lambda _text: self._validate())
            self._validate()

        def getPortSpec(self) -> object:
            properties = {}
            for field, combo in (("required", self._requiredCombo), ("nullable", self._nullableCombo)):
                value = combo.currentData()
                if value is not None:
                    properties[field] = value
            if self._versionCheck.isChecked():
                properties["schemaVersion"] = self._versionEdit.text().strip()
            return _editedPortSpec(self._original, self._typeName, properties)

        def _validate(self) -> bool:
            try:
                self.getPortSpec()
            except ValueError as error:
                self._errorLabel.setText(_validationMessage(error))
                self._saveButton.setEnabled(False)
                return False
            self._errorLabel.setText("")
            self._saveButton.setEnabled(True)
            return True

        def accept(self) -> None:
            if self._validate():
                super().accept()

        def execProperties(self) -> object | None:
            return self.getPortSpec() if self.exec_() == QDialog.Accepted else None

    class _PortTable(QWidget):
        changed: Any = Signal()

        def __init__(
            self, side: str, ports: dict[str, object], availableTypes: Iterable[str]
        ) -> None:
            super().__init__()
            self.side = side
            self._rows: list[tuple[QLineEdit, QComboBox, object]] = []
            self._typeOptions = [
                (f"{label} ({typeName})", typeName)
                for label, typeName in _TYPE_LABELS
            ]
            knownTypes = {value for _, value in self._typeOptions}
            extraTypes = set(availableTypes) | {normalizePortType(spec) for spec in ports.values()}
            self._typeOptions.extend((value, value) for value in sorted(extraTypes - knownTypes) if value)

            layout = QVBoxLayout(self)
            hint = WrapLabel(
                "工作流从这里接收数据，端口显示在 Workflow Input 节点右侧。"
                if side == "输入"
                else "工作流从这里返回结果，端口显示在 Workflow Output 节点左侧。"
            )
            hint.setObjectName("mutedText")
            layout.addWidget(hint)
            toolbar = QHBoxLayout()
            self._countLabel = QLabel()
            self._countLabel.setObjectName("mutedText")
            toolbar.addWidget(self._countLabel)
            toolbar.addStretch(1)
            self._addButton = QPushButton(f"新增{side}接口")
            self._addButton.setIcon(icon("add"))
            self._addButton.setAutoDefault(False)
            toolbar.addWidget(self._addButton)
            layout.addLayout(toolbar)

            self._table = QTableWidget(0, 4)
            self._table.setObjectName(f"workflow{side}Ports")
            self._table.setHorizontalHeaderLabels(["接口名称", "数据类型", "属性", "操作"])
            self._table.verticalHeader().hide()
            self._table.setSelectionMode(QAbstractItemView.NoSelection)
            self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
            self._table.setShowGrid(False)
            self._table.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Ignored)
            self._table.setMinimumHeight(150)
            header = self._table.horizontalHeader()
            header.setSectionResizeMode(0, QHeaderView.Stretch)
            header.setSectionResizeMode(1, QHeaderView.Stretch)
            header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
            header.setSectionResizeMode(3, QHeaderView.ResizeToContents)
            layout.addWidget(self._table, 1)
            self._emptyLabel = WrapLabel(f"还没有{side}接口\n点击“新增{side}接口”即可添加，无需填写 JSON。")
            self._emptyLabel.setObjectName("mutedText")
            self._emptyLabel.setAlignment(Qt.AlignCenter)
            layout.addWidget(self._emptyLabel, 1)

            for name, spec in ports.items():
                self.addPort(name, spec, focus=False)
            self._refreshCount()
            self._addButton.clicked.connect(lambda: self.addPort())

        def addPort(self, name: str = "", spec: object = "string", *, focus: bool = True) -> None:
            row = len(self._rows)
            self._table.insertRow(row)
            nameEdit = QLineEdit(name)
            nameEdit.setPlaceholderText("例如 hasNext" if self.side == "输入" else "例如 continue")
            nameEdit.setAccessibleName(f"{self.side}接口名称")
            nameEdit.setToolTip("填写用于连接和引用的接口名称；同一方向内不能重名。")
            typeCombo = QComboBox()
            typeCombo.setEditable(True)
            typeCombo.setInsertPolicy(QComboBox.NoInsert)
            typeCombo.setMinimumContentsLength(8)
            typeCombo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
            typeCombo.setAccessibleName(f"{self.side}接口数据类型")
            typeCombo.setToolTip("选择常用类型，也可输入插件的自定义类型，如 list<integer>。")
            for label, typeName in self._typeOptions:
                typeCombo.addItem(label, typeName)
            currentType = normalizePortType(spec)
            index = typeCombo.findData(currentType)
            if index < 0:
                typeCombo.addItem(currentType, currentType)
                index = typeCombo.count() - 1
            typeCombo.setCurrentIndex(index)
            propertiesButton = QPushButton("属性…")
            propertiesButton.setAutoDefault(False)
            propertiesButton.setAccessibleName(f"配置{self.side}接口属性")
            propertiesButton.setToolTip("配置必填要求、空值要求和结构版本；不修改时保留原有描述符。")
            propertiesButton.clicked.connect(lambda: self.editPortProperties(nameEdit))
            removeButton = QPushButton("删除")
            removeButton.setAutoDefault(False)
            removeButton.setAccessibleName(f"删除{self.side}接口")
            removeButton.clicked.connect(lambda: self.removePort(nameEdit))
            self._rows.append((nameEdit, typeCombo, deepcopy(spec)))
            controls = (nameEdit, typeCombo, propertiesButton, removeButton)
            for column, widget in enumerate(controls):
                self._table.setCellWidget(row, column, widget)
            self._table.setRowHeight(row, max(widget.sizeHint().height() for widget in controls) + 10)
            nameEdit.textChanged.connect(lambda _text: self.changed.emit())
            typeCombo.currentTextChanged.connect(lambda _text: self.changed.emit())
            self._refreshCount()
            self.changed.emit()
            if focus:
                self._table.scrollToBottom()
                nameEdit.setFocus()

        @staticmethod
        def _typeName(typeCombo: QComboBox) -> str:
            text = typeCombo.currentText().strip()
            index = typeCombo.findText(text, Qt.MatchExactly)
            return str(typeCombo.itemData(index)) if index >= 0 else text

        def editPortProperties(self, nameEdit: QLineEdit) -> None:
            row = next(index for index, controls in enumerate(self._rows) if controls[0] is nameEdit)
            _, typeCombo, original = self._rows[row]
            dialog = _PortPropertiesDialog(self.side, nameEdit.text(), self._typeName(typeCombo), original, self)
            try:
                spec = dialog.execProperties()
            finally:
                dialog.deleteLater()
            if spec is not None:
                self._rows[row] = (nameEdit, typeCombo, spec)
                self.changed.emit()

        def removePort(self, nameEdit: QLineEdit) -> None:
            row = next(index for index, controls in enumerate(self._rows) if controls[0] is nameEdit)
            self._rows.pop(row)
            self._table.removeRow(row)
            self._refreshCount()
            self.changed.emit()

        def _refreshCount(self) -> None:
            self._countLabel.setText(f"共 {len(self._rows)} 个{self.side}接口")
            self._table.setVisible(bool(self._rows))
            self._emptyLabel.setVisible(not self._rows)

        def getPorts(self) -> dict[str, object]:
            ports: dict[str, object] = {}
            for row, (nameEdit, typeCombo, original) in enumerate(self._rows, 1):
                name = nameEdit.text()
                prefix = f"{self.side}接口第 {row} 行："
                if not name.strip():
                    raise ValueError(prefix + "请填写接口名称")
                if name != name.strip():
                    raise ValueError(prefix + "接口名称首尾不能有空格")
                if name in ports:
                    raise ValueError(prefix + f"接口名称“{name}”重复")
                try:
                    ports[name] = _editedPortSpec(original, self._typeName(typeCombo))
                except ValueError as error:
                    raise ValueError(prefix + _validationMessage(error)) from error
            return ports

    class WorkflowInterfaceDialog(QDialog):
        def __init__(
            self,
            workflowName: str,
            inputs: dict[str, object],
            outputs: dict[str, object],
            *,
            initialTab: str = "inputs",
            availableTypes: Iterable[str] = (),
            parent=None,
        ) -> None:
            super().__init__(parent)
            self.setWindowTitle("工作流接口配置")
            self.setModal(True)
            self.resize(700, 480)
            layout = QVBoxLayout(self)
            title = ElidedLabel(f"工作流：{workflowName}")
            title.setObjectName("panelTitle")
            layout.addWidget(title)
            self._tabs = QTabWidget()
            types = tuple(availableTypes)
            self._inputTable = _PortTable("输入", inputs, types)
            self._outputTable = _PortTable("输出", outputs, types)
            # Keep wrapped help text from forcing a tall top-level minimum size.
            self._tabs.addTab(scrollContent(self._inputTable), "输入接口")
            self._tabs.addTab(scrollContent(self._outputTable), "输出接口")
            self._tabs.setCurrentIndex(1 if initialTab == "outputs" else 0)
            layout.addWidget(self._tabs, 1)
            self._errorLabel = WrapLabel()
            self._errorLabel.setObjectName("workflowInterfaceError")
            self._errorLabel.setStyleSheet("color: #b91c1c;")
            layout.addWidget(self._errorLabel)
            notice = WrapLabel(
                "修改或删除接口可能移除不兼容的连线，引用此工作流的节点会同步更新。"
                "确认后更新项目草稿；保存项目后才会写入文件。"
            )
            notice.setObjectName("mutedText")
            layout.addWidget(notice)
            buttons = QHBoxLayout()
            buttons.addStretch(1)
            self._cancelButton = QPushButton("取消")
            self._cancelButton.setAutoDefault(False)
            self._saveButton = QPushButton("确认配置")
            self._saveButton.setObjectName("primaryButton")
            self._saveButton.setIcon(icon("save", "#ffffff"))
            self._saveButton.setDefault(True)
            buttons.addWidget(self._cancelButton)
            buttons.addWidget(self._saveButton)
            layout.addLayout(buttons)
            self._cancelButton.clicked.connect(self.reject)
            self._saveButton.clicked.connect(self.accept)
            self._inputTable.changed.connect(self._validate)
            self._outputTable.changed.connect(self._validate)
            self._validate()

        def getInterfaces(self) -> tuple[dict[str, object], dict[str, object]]:
            return self._inputTable.getPorts(), self._outputTable.getPorts()

        def _validate(self) -> bool:
            for index, table in enumerate((self._inputTable, self._outputTable)):
                self._tabs.setTabText(index, f"{table.side}接口 ({len(table._rows)})")
            try:
                self.getInterfaces()
            except ValueError as error:
                self._errorLabel.setText(str(error))
                self._saveButton.setEnabled(False)
                return False
            self._errorLabel.setText("")
            self._saveButton.setEnabled(True)
            return True

        def accept(self) -> None:
            if self._validate():
                super().accept()

        def execInterface(self) -> tuple[dict[str, object], dict[str, object]] | None:
            if self.exec_() != QDialog.Accepted:
                return None
            return self.getInterfaces()

except ImportError:  # pragma: no cover
    class WorkflowInterfaceDialog:  # type: ignore[no-redef]
        def __init__(self, *args, **kwargs) -> None:
            pass

        def execInterface(self) -> tuple[dict[str, object], dict[str, object]] | None:
            return None
