"""Small shared schema form for explicitly declared device-page actions."""
import json

from PySide2.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QLabel,
    QLineEdit, QVBoxLayout, QWidget,
)


class DeviceActionDialog(QDialog):
    def __init__(self, declaration, actions, parent=None):
        super().__init__(parent)
        self.declaration = declaration
        self.arguments = None
        self.setWindowTitle('设备业务动作')
        self.resize(480, 260)
        layout = QVBoxLayout(self)
        self.action = QComboBox()
        for name in actions:
            self.action.addItem(declaration.actionSchemas[name].get('title', name), name)
        layout.addWidget(self.action)
        self.body = QVBoxLayout()
        layout.addLayout(self.body)
        self.error = QLabel()
        self.error.setWordWrap(True)
        layout.addWidget(self.error)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText('执行一次')
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.formWidget = None
        self.action.currentIndexChanged.connect(self.rebuild)
        self.rebuild()

    def rebuild(self):
        if self.formWidget is not None:
            self.body.removeWidget(self.formWidget)
            self.formWidget.deleteLater()
        self.formWidget = QWidget()
        form = QFormLayout(self.formWidget)
        self.body.addWidget(self.formWidget)
        schema = self.declaration.actionSchemas[self.action.currentData()]
        self.fields = {}
        for key, spec in schema.get('properties', {}).items():
            default = spec.get('default')
            if 'enum' in spec:
                widget = QComboBox()
                for value in spec['enum']:
                    widget.addItem(str(value), value)
                widget.setCurrentIndex(max(0, widget.findData(default)))
            elif spec.get('type') == 'boolean':
                widget = QCheckBox()
                widget.setChecked(bool(default))
            else:
                widget = QLineEdit('' if default is None else str(default) if spec.get('type') == 'string'
                                   else json.dumps(default))
            widget.setToolTip(spec.get('description', ''))
            form.addRow(spec.get('title', key), widget)
            self.fields[key] = (widget, spec)
        self.error.clear()

    def accept(self):
        try:
            arguments = {}
            schema = self.declaration.actionSchemas[self.action.currentData()]
            for key, (widget, spec) in self.fields.items():
                if isinstance(widget, QComboBox):
                    value = widget.currentData()
                elif isinstance(widget, QCheckBox):
                    value = widget.isChecked()
                else:
                    value = widget.text()
                    if not value and key not in schema.get('required', []):
                        continue
                    if spec.get('type') != 'string':
                        try:
                            value = json.loads(value)
                        except ValueError as error:
                            raise ValueError(f"{spec.get('title', key)}：请输入有效的 {spec.get('type', 'JSON')} 值") from error
                arguments[key] = value
            from emo_master.core.project.snapshots import _parameters
            self.arguments = _parameters(arguments, schema, '动作参数')
        except (ValueError, TypeError) as error:
            self.error.setText(str(error))
            return
        super().accept()
