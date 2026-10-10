from __future__ import annotations

from dataclasses import dataclass
from copy import deepcopy
import json
from typing import Any

from emo_master.apps.designer.state.schema_utils import applySchemaDefaults


def parameterTitle(name: str, schema: dict[str, object]) -> str:
    title = schema.get("title")
    return title.strip() if isinstance(title, str) and title.strip() else name


def parameterToolTip(name: str, schema: dict[str, object], hint: str = "") -> str:
    parts = [parameterTitle(name, schema)]
    description = schema.get("description")
    for detail in (description, hint):
        if isinstance(detail, str) and detail.strip() and detail.strip() not in parts:
            parts.append(detail.strip())
    parts.append(f"参数键：{name}")
    return "\n".join(parts)


@dataclass(frozen=True)
class FieldDefinition:
    name: str
    fieldType: str
    required: bool
    defaultValue: object
    schema: dict[str, object]
    enumValues: list[object]
    minimum: int | float | None
    maximum: int | float | None


def getFieldDefinitions(paramSchema: dict[str, object]) -> list[FieldDefinition]:
    properties = paramSchema.get("properties", {})
    requiredList = paramSchema.get("required", [])
    if not isinstance(properties, dict):
        return []
    if not isinstance(requiredList, list):
        requiredList = []

    requiredNames = {name for name in requiredList if isinstance(name, str)}
    definitions: list[FieldDefinition] = []
    for fieldName, fieldValue in properties.items():
        if not isinstance(fieldName, str) or not isinstance(fieldValue, dict):
            continue
        fieldType = fieldValue.get("type")
        enumValues = fieldValue.get("enum", [])
        minimum = fieldValue.get("minimum")
        maximum = fieldValue.get("maximum")
        definitions.append(
            FieldDefinition(
                name=fieldName,
                fieldType=str(fieldType) if isinstance(fieldType, str) else "string",
                required=fieldName in requiredNames,
                defaultValue=fieldValue.get("default"),
                schema=fieldValue,
                enumValues=enumValues if isinstance(enumValues, list) else [],
                minimum=minimum if isinstance(minimum, (int, float)) else None,
                maximum=maximum if isinstance(maximum, (int, float)) else None,
            )
        )
    return definitions


try:
    from PySide2.QtCore import Qt, QTimer, Signal, QRegularExpression
    from PySide2.QtGui import QRegularExpressionValidator
    from PySide2.QtWidgets import (
        QCheckBox,
        QApplication,
        QComboBox,
        QDoubleSpinBox,
        QFileDialog,
        QFormLayout,
        QGroupBox,
        QHBoxLayout,
        QLabel,
        QLineEdit,
        QPushButton,
        QSpinBox,
        QStyle,
        QStyleOptionComboBox,
        QTextEdit,
        QWidget,
    )

    class _IntegerValueControl(QLineEdit):
        """Exact integer input for schemas wider than Qt's signed int32 spin box.

        Keep incomplete/invalid text intact so validation can report it instead
        of silently coercing, rounding or replacing the user's draft with zero.
        """

        def __init__(self, value: object) -> None:
            super().__init__("" if value is None else str(value))
            self.setValidator(QRegularExpressionValidator(QRegularExpression("-?[0-9]+"), self))

        def value(self) -> object:
            value = self.text()
            try:
                return int(value) if value and value.lstrip("-").isdigit() and value.count("-") <= 1 else value
            except ValueError:
                return value

    class _WorkflowComboBox(QComboBox):
        workflowOpenRequested: Any = Signal(str)

        def __init__(self):
            super().__init__()
            self._popupTimer = QTimer(self)
            self._popupTimer.setSingleShot(True)
            self._popupTimer.timeout.connect(self.showPopup)

        def _overName(self, event):
            option = QStyleOptionComboBox()
            self.initStyleOption(option)
            return self.style().subControlRect(
                QStyle.CC_ComboBox, option, QStyle.SC_ComboBoxEditField, self,
            ).contains(event.pos())

        def mousePressEvent(self, event):
            self._popupTimer.stop()
            if event.button() == Qt.LeftButton and self._overName(event):
                self.setFocus(Qt.MouseFocusReason)
                # Let a second click reach the name before a popup grabs the mouse.
                self._popupTimer.start(QApplication.doubleClickInterval())
                event.accept()
                return
            super().mousePressEvent(event)

        def mouseReleaseEvent(self, event):
            if event.button() == Qt.LeftButton and self._overName(event):
                event.accept()
                return
            super().mouseReleaseEvent(event)

        def mouseDoubleClickEvent(self, event):
            self._popupTimer.stop()
            if event.button() == Qt.LeftButton and self._overName(event):
                workflowId = self.currentData()
                if isinstance(workflowId, str) and workflowId:
                    self.workflowOpenRequested.emit(workflowId)
                event.accept()
                return
            super().mouseDoubleClickEvent(event)

        def showPopup(self):
            self._popupTimer.stop()
            super().showPopup()

        def hideEvent(self, event):
            self._popupTimer.stop()
            super().hideEvent(event)

        def focusOutEvent(self, event):
            self._popupTimer.stop()
            super().focusOutEvent(event)

    def applyParameterLabel(
        label: QLabel, control: QWidget, name: str,
        paramSchema: dict[str, object], hint: str = "",
    ) -> None:
        properties = paramSchema.get("properties", {})
        rawField = properties.get(name, {}) if isinstance(properties, dict) else {}
        fieldSchema = rawField if isinstance(rawField, dict) else {}
        required = paramSchema.get("required", [])
        suffix = " *" if isinstance(required, list) and name in required else ""
        label.setTextFormat(Qt.PlainText)
        label.setText(parameterTitle(name, fieldSchema) + suffix)
        label.setWordWrap(True)
        tip = parameterToolTip(name, fieldSchema, hint)
        label.setToolTip(tip)
        control.setToolTip(tip)


    class _FilePickerControl(QWidget):
        def __init__(self, fileMode: str, filterText: str, initialPath: str) -> None:
            super().__init__()
            self._fileMode = fileMode if fileMode in ("open", "save", "directory") else "open"
            self._filterText = filterText if filterText != "" else "所有文件 (*.*)"
            self._lineEdit = QLineEdit()
            self._lineEdit.setText(initialPath)
            from emo_master.apps.designer.ui.icon_map import icon
            self._browseButton = QPushButton()
            self._browseButton.setIcon(icon("folder-open"))
            self._browseButton.setToolTip("选择文件夹" if self._fileMode == "directory" else "选择文件")
            self._browseButton.setFixedWidth(34)
            self._browseButton.clicked.connect(self._onBrowseClicked)

            layout = QHBoxLayout()
            layout.setContentsMargins(0, 0, 0, 0)
            layout.addWidget(self._lineEdit)
            layout.addWidget(self._browseButton)
            self.setLayout(layout)

        def text(self) -> str:
            return self._lineEdit.text()

        def _onBrowseClicked(self) -> None:
            currentPath = self._lineEdit.text().strip()
            if self._fileMode == "directory":
                selectedPath = QFileDialog.getExistingDirectory(
                    self, "选择图片文件夹", currentPath
                )
            elif self._fileMode == "save":
                selectedPath, _ = QFileDialog.getSaveFileName(
                    self, "选择输出文件", currentPath, self._filterText
                )
            else:
                selectedPath, _ = QFileDialog.getOpenFileName(
                    self, "选择输入文件", currentPath, self._filterText
                )
            if selectedPath != "":
                self._lineEdit.setText(selectedPath)

    class _OptionalFieldControl(QWidget):
        """Keep an absent parameter distinct from a present empty value."""

        def __init__(self, control: QWidget, present: bool) -> None:
            super().__init__()
            self.valueControl = control
            self.enabledCheckBox = QCheckBox("启用")
            self.enabledCheckBox.setToolTip("未启用时不参与匹配；启用且匹配值为空时，匹配空字符串")
            self.enabledCheckBox.setChecked(present)
            control.setEnabled(present)
            self.enabledCheckBox.toggled.connect(control.setEnabled)
            layout = QHBoxLayout()
            layout.setContentsMargins(0, 0, 0, 0)
            layout.addWidget(self.enabledCheckBox)
            layout.addWidget(control, 1)
            self.setLayout(layout)

    class SchemaParamForm(QWidget):
        workflowOpenRequested: Any = Signal(str)

        def __init__(self) -> None:
            super().__init__()
            self._layout = QFormLayout()
            self._layout.setRowWrapPolicy(QFormLayout.WrapLongRows)
            self._layout.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
            self._layout.setContentsMargins(8, 8, 8, 8)
            self._layout.setHorizontalSpacing(12)
            self._layout.setVerticalSpacing(10)
            self.setLayout(self._layout)
            self._controls: dict[str, QWidget] = {}
            self._fieldContainers: dict[str, QWidget] = {}
            self._variableSources: dict[str, QComboBox] = {}
            self._fieldsByName: dict[str, FieldDefinition] = {}
            self._nestedFormsByControlId: dict[int, SchemaParamForm] = {}
            self._rawSchema: dict[str, object] = {}
            self._suppliedFields: set[str] = set()
            self._workflowOptions: list[str] = []

        def setWorkflowOptions(self, options: list[str]) -> None:
            self._workflowOptions = [option for option in options if isinstance(option, str)]
            if self._rawSchema:
                self.setSchema(self._rawSchema, self.getValues())

        def setSchema(
            self, paramSchema: dict[str, object], values: dict[str, object]
        ) -> None:
            self._rawSchema = deepcopy(paramSchema)
            self._suppliedFields = set(values)
            self._controls = {}
            self._fieldContainers = {}
            self._variableSources = {}
            self._fieldsByName = {}
            self._nestedFormsByControlId = {}
            while self._layout.rowCount() > 0:
                self._layout.removeRow(0)

            valuesWithDefaultsRaw = applySchemaDefaults(paramSchema, values)
            valuesWithDefaults = (
                valuesWithDefaultsRaw if isinstance(valuesWithDefaultsRaw, dict) else {}
            )

            for field in getFieldDefinitions(paramSchema):
                control = self._createControl(field, valuesWithDefaults)
                if field.schema.get("xOptionalPresence") and not field.required:
                    control = _OptionalFieldControl(control, field.name in values)
                self._controls[field.name] = control
                self._fieldsByName[field.name] = field
                from emo_master.apps.designer.ui.widgets import WrapLabel
                label = WrapLabel()
                applyParameterLabel(label, control, field.name, paramSchema)
                if isinstance(control, _WorkflowComboBox):
                    control.setToolTip(control.toolTip() + "\n双击名称打开工作流；单击选择工作流")
                self._layout.addRow(label, control)
                if field.name in {f"case{i}Value" for i in range(4)} and field.schema.get("xOptionalPresence"):
                    valueControl = control.valueControl if isinstance(control, _OptionalFieldControl) else control
                    if isinstance(valueControl, QLineEdit):
                        for text in ("True", "False"):
                            button = QPushButton(text)
                            button.setToolTip("布尔输入的 Python 文本；不是 JSON 小写，也不需要输入引号")
                            def choose(_checked=False, text=text, valueControl=valueControl, control=control):
                                if isinstance(control, _OptionalFieldControl):
                                    control.enabledCheckBox.setChecked(True)
                                valueControl.setText(text)
                            button.clicked.connect(choose)
                            control.layout().addWidget(button)
                if field.schema.get("xHidden"):
                    label.hide()
                    control.hide()

            if len(self._controls) == 0:
                tip = QLabel("No schema fields")
                tip.setAlignment(Qt.AlignLeft)
                self._layout.addRow(tip)
            if "xForEachInputs" in self._rawSchema or "xWhileBooleanPorts" in self._rawSchema or any(
                    "xEnabledWhen" in field.schema or "xVisibleWhen" in field.schema or "xRequiredWhen" in field.schema
                    for field in self._fieldsByName.values()):
                for control in self._controls.values():
                    if isinstance(control, QComboBox):
                        control.currentIndexChanged.connect(self._updateDependentFields)
            self._updateDependentFields()
            if all(f"case{i}Value" in self._controls for i in range(4)):
                self._buildSwitchPreview()

        def _buildSwitchPreview(self):
            from .widgets import WrapLabel
            self._layout.addRow(WrapLabel('按 Python str(value) 逐项文本匹配，先匹配分支 0。布尔 true/false 转成 True/False；字符串 true 保持小写。匹配值不填引号，未启用不参与。'))
            panel = QWidget()
            layout = QHBoxLayout(panel)
            layout.setContentsMargins(0, 0, 0, 0)
            kind, sample = QComboBox(), QLineEdit('true')
            kind.addItems(['boolean', 'string', 'integer'])
            layout.addWidget(kind)
            layout.addWidget(sample, 1)
            self._layout.addRow('输入匹配预览', panel)
            preview = WrapLabel()
            preview.setObjectName('switchMatchPreview')
            self._layout.addRow(preview)
            def update(*_args):
                try:
                    text = sample.text()
                    if kind.currentText() == 'boolean':
                        if text not in {'true', 'false', 'True', 'False'}:
                            raise ValueError('请输入 true 或 false')
                        value = text.lower() == 'true'
                    elif kind.currentText() == 'integer':
                        value = int(text)
                    else:
                        value = text
                    converted = str(value)
                    values = self.getValues()
                    target = next((f'case{i}' for i in range(4) if values.get(f'case{i}Value') == converted), 'default')
                    preview.setText(f'转换文本：{converted!r} → {target}（固定匹配值预览；动态绑定以运行时为准）')
                except ValueError as error:
                    preview.setText(str(error))
            kind.currentIndexChanged.connect(update)
            sample.textChanged.connect(update)
            for control in self._controls.values():
                if isinstance(control, _OptionalFieldControl):
                    control.enabledCheckBox.toggled.connect(update)
                    if isinstance(control.valueControl, QLineEdit):
                        control.valueControl.textChanged.connect(update)
            update()

        def _updateDependentFields(self, *_args) -> None:
            values = self.getValues()
            for name, field in self._fieldsByName.items():
                requirement = field.schema.get("xRequiredWhen")
                if isinstance(requirement, dict):
                    required = all(values.get(key) in allowed for key, allowed in requirement.items())
                    requiredLabel = self._layout.labelForField(self._fieldContainers.get(name, self._controls[name]))
                    if isinstance(requiredLabel, QLabel):
                        requiredLabel.setText(parameterTitle(name, field.schema) + (" *" if required else ""))
                visibility = field.schema.get("xVisibleWhen")
                if isinstance(visibility, dict) and not field.schema.get("xHidden"):
                    visible = all(values.get(key) in allowed for key, allowed in visibility.items())
                    control = self._controls[name]
                    self._fieldContainers.get(name, control).setVisible(visible)
                    rowLabel = self._layout.labelForField(self._fieldContainers.get(name, control))
                    if rowLabel is not None:
                        rowLabel.setVisible(visible)
                condition = field.schema.get("xEnabledWhen")
                if isinstance(condition, dict):
                    enabled = all(values.get(key) in allowed for key, allowed in condition.items())
                    source = self._variableSources.get(name)
                    if source is not None:
                        source.setEnabled(enabled)
                    self._controls[name].setEnabled(enabled and not (source and source.currentData()))
            portsByWorkflow = self._rawSchema.get("xWhileBooleanPorts")
            combo = self._controls.get("conditionPort")
            if isinstance(portsByWorkflow, dict) and isinstance(combo, QComboBox):
                options = portsByWorkflow.get(values.get("bodyWorkflowId"), [])
                selected = values.get("conditionPort")
                combo.blockSignals(True)
                combo.clear()
                for port in options:
                    combo.addItem(f"{port} : boolean", port)
                if selected not in options:
                    combo.addItem(f"端口已失效：{selected}" if selected else "请选择布尔端口", selected)
                self._setComboValue(combo, selected)
                combo.blockSignals(False)
            inputsByWorkflow = self._rawSchema.get("xForEachInputs")
            if not isinstance(inputsByWorkflow, dict):
                return
            from emo_master.core.contracts.port_types import normalizePortType
            inputs = inputsByWorkflow.get(values.get("bodyWorkflowId"), {})
            for name in ("itemInputPort", "indexInputPort"):
                combo = self._controls.get(name)
                if not isinstance(combo, QComboBox):
                    continue
                options = list(inputs) if name == "itemInputPort" else [
                    "", *[port for port, spec in inputs.items() if normalizePortType(spec) == "integer"]]
                selected = values.get(name)
                combo.blockSignals(True)
                combo.clear()
                for port in options:
                    combo.addItem(port or "不传入索引", port)
                if selected not in options:
                    label = (f"端口已失效：{selected}" if selected else
                             "无输入端口" if not inputs and name == "itemInputPort" else "请选择端口")
                    combo.addItem(label, selected)
                self._setComboValue(combo, selected)
                combo.blockSignals(False)

        def validationMessage(self) -> str:
            for name, control in self._controls.items():
                field = self._fieldsByName[name]
                if not control.isEnabled():
                    continue
                valueControl = control.valueControl if isinstance(control, _OptionalFieldControl) else control
                if isinstance(valueControl, _IntegerValueControl):
                    value = valueControl.value()
                    title = parameterTitle(name, field.schema)
                    if not isinstance(value, int) or isinstance(value, bool):
                        return f"{title}：请输入完整整数"
                    if field.minimum is not None and value < field.minimum:
                        return f"{title}：不能小于 {field.minimum}"
                    if field.maximum is not None and value > field.maximum:
                        return f"{title}：不能大于 {field.maximum}"
                nested = self._nestedFormsByControlId.get(id(valueControl))
                if nested is not None:
                    message = nested.validationMessage()
                    if message:
                        return message
            portsByWorkflow = self._rawSchema.get("xWhileBooleanPorts")
            values = self.getValues()
            if isinstance(portsByWorkflow, dict) and values.get("conditionMode") == "boolean":
                if values.get("bodyWorkflowId") not in portsByWorkflow:
                    return "循环体工作流不存在，请重新选择"
                if values.get("conditionPort") not in portsByWorkflow[values.get("bodyWorkflowId")]:
                    return "继续条件端口已失效，请选择循环体同名输入/输出的布尔端口"
            inputsByWorkflow = self._rawSchema.get("xForEachInputs")
            if not isinstance(inputsByWorkflow, dict):
                return ""
            from emo_master.core.contracts.port_types import normalizePortType
            values = self.getValues()
            if values.get("bodyWorkflowId") not in inputsByWorkflow:
                return "循环体工作流不存在，请重新选择"
            inputs = inputsByWorkflow.get(values.get("bodyWorkflowId"), {})
            item = values.get("itemInputPort")
            index = values.get("indexInputPort")
            if (inputs or item) and item not in inputs:
                return "元素输入端口已失效，请选择当前循环体的输入端口"
            if index and (index not in inputs or normalizePortType(inputs[index]) != "integer"):
                return "索引输入端口已失效，请选择整数端口或不传入索引"
            if index and index == item:
                return "元素输入端口和索引输入端口不能相同，请选择不同端口或不传入索引"
            return ""

        def getValues(self) -> dict[str, object]:
            values: dict[str, object] = {}
            for fieldName, control in self._controls.items():
                field = self._fieldsByName.get(fieldName)
                if field and field.schema.get("xHidden") and fieldName not in self._suppliedFields:
                    continue
                if isinstance(control, _OptionalFieldControl) and not control.enabledCheckBox.isChecked():
                    continue
                values[fieldName] = self._readControlValue(control, field)
            return values

        def _createControl(
            self, field: FieldDefinition, values: dict[str, object]
        ) -> QWidget:
            selectedValue = values.get(field.name, field.defaultValue)

            if field.fieldType == "string":
                widgetType = str(field.schema.get("xWidget", "")).strip().lower()
                if widgetType == "file":
                    fileMode = (
                        str(field.schema.get("xFileMode", "open")).strip().lower()
                    )
                    filterText = str(field.schema.get("xFilter", "所有文件 (*.*)"))
                    initialPath = "" if selectedValue is None else str(selectedValue)
                    return _FilePickerControl(
                        fileMode=fileMode,
                        filterText=filterText,
                        initialPath=initialPath,
                    )
                if widgetType in {"workflow-select", "workflow_select"}:
                    combo = _WorkflowComboBox()
                    combo.workflowOpenRequested.connect(self.workflowOpenRequested.emit)
                    rawOptions = field.schema.get("xOptions", self._workflowOptions)
                    options = (
                        [option for option in rawOptions if isinstance(option, str)]
                        if isinstance(rawOptions, list)
                        else list(self._workflowOptions)
                    )
                    if isinstance(selectedValue, str) and selectedValue not in options:
                        options.append(selectedValue)
                    labels = field.schema.get("xOptionLabels")
                    for option in options:
                        label = labels.get(option) if isinstance(labels, dict) else option
                        if not isinstance(label, str) or not label.strip():
                            label = f"未找到工作流 ({option})" if option else "未设置"
                        combo.addItem(label, option)
                        combo.setItemData(combo.count() - 1, f"{label}\n工作流 ID：{option}", Qt.ToolTipRole)
                    self._setComboValue(combo, selectedValue)
                    return combo

            if len(field.enumValues) > 0 or field.schema.get("xWidget") == "port-select":
                combo = QComboBox()
                labels = field.schema.get("xOptionLabels", {})
                labels = labels if isinstance(labels, dict) else {}
                for enumOption in field.enumValues:
                    combo.addItem(str(labels.get(str(enumOption), enumOption)), enumOption)
                if field.schema.get("xWidget") == "port-select" and selectedValue not in field.enumValues:
                    combo.addItem(str(selectedValue or "请选择端口"), selectedValue)
                self._setComboValue(combo, selectedValue)
                return combo

            if field.fieldType == "boolean":
                checkbox = QCheckBox()
                checkbox.setChecked(bool(selectedValue))
                return checkbox

            if field.fieldType == "integer":
                if (any(bound is not None and not -2147483648 <= bound <= 2147483647
                        for bound in (field.minimum, field.maximum)) or
                        isinstance(selectedValue, int) and not -2147483648 <= selectedValue <= 2147483647):
                    return _IntegerValueControl(selectedValue)
                spin = QSpinBox()
                spin.setRange(-2147483648, 2147483647)
                if isinstance(field.minimum, (int, float)):
                    spin.setMinimum(int(field.minimum))
                if isinstance(field.maximum, (int, float)):
                    spin.setMaximum(int(field.maximum))
                if isinstance(selectedValue, (int, float)):
                    spin.setValue(int(selectedValue))
                return spin

            if field.fieldType == "number":
                spin = QDoubleSpinBox()
                spin.setRange(-1000000000.0, 1000000000.0)
                if isinstance(field.minimum, (int, float)):
                    spin.setMinimum(float(field.minimum))
                if isinstance(field.maximum, (int, float)):
                    spin.setMaximum(float(field.maximum))
                if isinstance(selectedValue, (int, float)):
                    spin.setValue(float(selectedValue))
                return spin

            if field.fieldType == "object":
                rawProperties = field.schema.get("properties", {})
                if isinstance(rawProperties, dict) and len(rawProperties) > 0:
                    groupBox = QGroupBox()
                    nestedForm = SchemaParamForm()
                    nestedForm.workflowOpenRequested.connect(self.workflowOpenRequested.emit)
                    nestedValue = (
                        selectedValue if isinstance(selectedValue, dict) else {}
                    )
                    nestedForm.setSchema(field.schema, nestedValue)
                    layout = QFormLayout()
                    layout.addRow(nestedForm)
                    groupBox.setLayout(layout)
                    self._nestedFormsByControlId[id(groupBox)] = nestedForm
                    return groupBox

                textEditor = QTextEdit()
                objectValue = selectedValue if isinstance(selectedValue, dict) else {}
                textEditor.setPlainText(
                    json.dumps(objectValue, ensure_ascii=True, indent=2)
                )
                return textEditor

            if field.fieldType == "array":
                textEditor = QTextEdit()
                arrayValue = selectedValue if isinstance(selectedValue, list) else []
                textEditor.setPlainText(
                    json.dumps(arrayValue, ensure_ascii=True, indent=2)
                )
                return textEditor

            lineEdit = QLineEdit()
            if selectedValue is not None:
                lineEdit.setText(str(selectedValue))
            return lineEdit

        def _setComboValue(self, combo: QComboBox, selectedValue: object) -> None:
            for index in range(combo.count()):
                if combo.itemData(index) == selectedValue:
                    combo.setCurrentIndex(index)
                    return

        def _readControlValue(
            self, control: QWidget, field: FieldDefinition | None
        ) -> object:
            _ = field
            if isinstance(control, _OptionalFieldControl):
                return self._readControlValue(control.valueControl, field)
            if isinstance(control, QCheckBox):
                return control.isChecked()
            if isinstance(control, QSpinBox):
                return int(control.value())
            if isinstance(control, QDoubleSpinBox):
                return float(control.value())
            if isinstance(control, QComboBox):
                return control.currentData()
            if isinstance(control, _IntegerValueControl):
                return control.value()
            if isinstance(control, QLineEdit):
                return control.text()
            if isinstance(control, _FilePickerControl):
                return control.text()
            if isinstance(control, QGroupBox):
                nestedForm = self._nestedFormsByControlId.get(id(control))
                if isinstance(nestedForm, SchemaParamForm):
                    return nestedForm.getValues()
                return {}
            if isinstance(control, QTextEdit):
                textValue = control.toPlainText().strip()
                if textValue == "":
                    if field is not None and field.fieldType == "array":
                        return []
                    if field is not None and field.fieldType == "object":
                        return {}
                    return ""
                try:
                    return json.loads(textValue)
                except json.JSONDecodeError:
                    if field is not None and field.fieldType == "array":
                        return []
                    if field is not None and field.fieldType == "object":
                        return {}
                    return textValue
            return None

except Exception:  # pragma: no cover

    class SchemaParamForm:  # type: ignore[no-redef]
        def __init__(self) -> None:
            self._schema: dict[str, object] = {}
            self._values: dict[str, object] = {}
            self._workflowOptions: list[str] = []

        def setWorkflowOptions(self, options: list[str]) -> None:
            self._workflowOptions = [option for option in options if isinstance(option, str)]

        def setSchema(
            self, paramSchema: dict[str, object], values: dict[str, object]
        ) -> None:
            self._schema = dict(paramSchema)
            parsedValues = applySchemaDefaults(paramSchema, values)
            self._values = parsedValues if isinstance(parsedValues, dict) else {}

        def getValues(self) -> dict[str, object]:
            return dict(self._values)
