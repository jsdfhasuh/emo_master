from __future__ import annotations

from dataclasses import dataclass
import json

from emo_master.apps.designer.state.schema_utils import applySchemaDefaults


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
    from PySide2.QtCore import Qt
    from PySide2.QtWidgets import (
        QCheckBox,
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
        QTextEdit,
        QWidget,
    )

    class _FilePickerControl(QWidget):
        def __init__(self, fileMode: str, filterText: str, initialPath: str) -> None:
            super().__init__()
            self._fileMode = fileMode if fileMode in ("open", "save") else "open"
            self._filterText = filterText if filterText != "" else "所有文件 (*.*)"
            self._lineEdit = QLineEdit()
            self._lineEdit.setText(initialPath)
            self._browseButton = QPushButton("浏览...")
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
            if self._fileMode == "save":
                selectedPath, _ = QFileDialog.getSaveFileName(
                    self, "选择输出文件", currentPath, self._filterText
                )
            else:
                selectedPath, _ = QFileDialog.getOpenFileName(
                    self, "选择输入文件", currentPath, self._filterText
                )
            if selectedPath != "":
                self._lineEdit.setText(selectedPath)

    class SchemaParamForm(QWidget):
        def __init__(self) -> None:
            super().__init__()
            self._layout = QFormLayout()
            self.setLayout(self._layout)
            self._controls: dict[str, QWidget] = {}
            self._fieldsByName: dict[str, FieldDefinition] = {}
            self._nestedFormsByControlId: dict[int, SchemaParamForm] = {}
            self._rawSchema: dict[str, object] = {}
            self._workflowOptions: list[str] = []

        def setWorkflowOptions(self, options: list[str]) -> None:
            self._workflowOptions = [option for option in options if isinstance(option, str)]
            if self._rawSchema:
                self.setSchema(self._rawSchema, self.getValues())

        def setSchema(
            self, paramSchema: dict[str, object], values: dict[str, object]
        ) -> None:
            self._rawSchema = dict(paramSchema)
            self._controls = {}
            self._fieldsByName = {}
            self._nestedFormsByControlId = {}
            while self._layout.rowCount() > 0:
                self._layout.removeRow(0)

            valuesWithDefaultsRaw = applySchemaDefaults(paramSchema, values)
            valuesWithDefaults = (
                valuesWithDefaultsRaw if isinstance(valuesWithDefaultsRaw, dict) else {}
            )

            for field in getFieldDefinitions(paramSchema):
                labelText = field.name + (" *" if field.required else "")
                control = self._createControl(field, valuesWithDefaults)
                self._controls[field.name] = control
                self._fieldsByName[field.name] = field
                self._layout.addRow(QLabel(labelText), control)

            if len(self._controls) == 0:
                tip = QLabel("No schema fields")
                tip.setAlignment(Qt.AlignLeft)
                self._layout.addRow(tip)

        def getValues(self) -> dict[str, object]:
            values: dict[str, object] = {}
            for fieldName, control in self._controls.items():
                field = self._fieldsByName.get(fieldName)
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
                    combo = QComboBox()
                    rawOptions = field.schema.get("xOptions", self._workflowOptions)
                    options = (
                        [option for option in rawOptions if isinstance(option, str)]
                        if isinstance(rawOptions, list)
                        else list(self._workflowOptions)
                    )
                    if isinstance(selectedValue, str) and selectedValue not in options:
                        options.append(selectedValue)
                    for option in options:
                        combo.addItem(option, option)
                    self._setComboValue(combo, selectedValue)
                    return combo

            if len(field.enumValues) > 0:
                combo = QComboBox()
                for enumOption in field.enumValues:
                    combo.addItem(str(enumOption), enumOption)
                self._setComboValue(combo, selectedValue)
                return combo

            if field.fieldType == "boolean":
                checkbox = QCheckBox()
                checkbox.setChecked(bool(selectedValue))
                return checkbox

            if field.fieldType == "integer":
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
            if isinstance(control, QCheckBox):
                return control.isChecked()
            if isinstance(control, QSpinBox):
                return int(control.value())
            if isinstance(control, QDoubleSpinBox):
                return float(control.value())
            if isinstance(control, QComboBox):
                return control.currentData()
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
