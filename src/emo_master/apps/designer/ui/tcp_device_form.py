"""TCP connection form using the shared channel editor and normal form contract."""
from copy import deepcopy

from PySide2.QtWidgets import QVBoxLayout, QWidget

from emo_master.apps.designer.ui.param_form import SchemaParamForm
from emo_master.ui.communication_panel import TcpChannelEditor


class TcpConnectionForm(QWidget):
    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.fields = SchemaParamForm()
        self.channels = TcpChannelEditor()
        layout.addWidget(self.fields)
        layout.addWidget(self.channels)

    def setSchema(self, schema, values):
        self.schema = deepcopy(schema)
        base = deepcopy(schema)
        base.get('properties', {}).pop('channels', None)
        base['required'] = [key for key in base.get('required', []) if key != 'channels']
        self.fields.setSchema(base, {key: value for key, value in values.items() if key != 'channels'})
        self.channels.setValue(values.get('channels', []))

    def getValues(self):
        return dict(self.fields.getValues(), channels=self.channels.value())

    def validationMessage(self):
        message = self.fields.validationMessage()
        if message:
            return message
        try:
            from emo_master.core.project.communication import TcpServerSettings
            TcpServerSettings(**self.getValues())
        except ValueError as error:
            self.focusField('channels')
            return str(error)
        return ''

    def focusField(self, name):
        if name == 'channels':
            self.channels.table.setFocus()
        else:
            self.fields.focusField(name)

    def setFieldEnabled(self, name, enabled):
        if name == 'channels':
            self.channels.setEnabled(enabled)
        else:
            self.fields.setFieldEnabled(name, enabled)
