"""TCP connection form using the shared channel editor and normal form contract."""
from copy import deepcopy

from pydantic import ValidationError
from PySide2.QtCore import Signal
from PySide2.QtWidgets import QVBoxLayout, QWidget

from emo_master.apps.designer.ui.param_form import SchemaParamForm
from emo_master.ui.communication_panel import TcpChannelEditor


class TcpConnectionForm(QWidget):
    valuesChanged = Signal()

    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.fields = SchemaParamForm(deviceMode=True)
        self.channels = TcpChannelEditor()
        self.channelErrorCell = None
        self.fields.valuesChanged.connect(self.valuesChanged)
        self.channels.table.cellChanged.connect(self._channelEdited)
        self.channels.table.model().rowsRemoved.connect(self._channelsRemoved)
        layout.addWidget(self.fields)
        layout.addWidget(self.channels)

    def setSchema(self, schema, values):
        self.channels.table.setAccessibleDescription('')
        self.channelErrorCell = None
        self.schema = deepcopy(schema)
        base = deepcopy(schema)
        base.get('properties', {}).pop('channels', None)
        base['required'] = [key for key in base.get('required', []) if key != 'channels']
        self.fields.setSchema(base, {key: value for key, value in values.items() if key != 'channels'})
        self.channels.setValue(values.get('channels', []))

    def getValues(self):
        return dict(self.fields.getValues(), channels=self.channels.value())

    def _channelEdited(self, row, column):
        # A row is filled by three successive setItem calls; only expose the
        # completed row to the enclosing form's value reader.
        if any(self.channels.table.item(row, col) is None for col in range(3)):
            return
        if self.channels.table.accessibleDescription():
            corrected = self.channelErrorCell == (row, column)
            if self.channelErrorCell is None:
                # Model-wide route errors have no cell location. Do not clear
                # them for an unrelated name edit while duplicate IPs remain.
                from emo_master.core.project.communication import TcpServerSettings
                try:
                    TcpServerSettings(**self.getValues())
                    corrected = True
                except ValueError:
                    pass
            if corrected:
                self.channels.table.setAccessibleDescription('')
                self.channelErrorCell = None
        self.valuesChanged.emit()

    def _channelsRemoved(self, _parent, first, last):
        if self.channelErrorCell is not None:
            row, column = self.channelErrorCell
            if first <= row <= last:
                self.channels.table.setAccessibleDescription('')
                self.channelErrorCell = None
            elif row > last:
                self.channelErrorCell = (row - (last - first + 1), column)
        elif self.channels.table.accessibleDescription():
            from emo_master.core.project.communication import TcpServerSettings
            try:
                TcpServerSettings(**self.getValues())
                self.channels.table.setAccessibleDescription('')
            except ValueError:
                pass
        self.valuesChanged.emit()

    def showChannelError(self, message, cell=None):
        self.channelErrorCell = cell
        self.channels.table.setAccessibleDescription(message)
        if cell is not None:
            self.channels.table.setCurrentCell(*cell)
        self.focusField('channels')
        return message

    def validationMessage(self):
        message = self.fields.validationMessage()
        if message:
            return message
        try:
            from emo_master.core.project.communication import TcpServerSettings
            TcpServerSettings(**self.getValues())
        except ValidationError as error:
            issue = error.errors()[0]
            location = issue['loc']
            if location and location[0] == 'channels' and len(location) >= 3:
                row, key = int(location[1]), str(location[2])
                cell = (row, {'channelId': 0, 'name': 1, 'allowedIps': 2}.get(key, 2))
                if key == 'allowedIps' and not self.getValues()['channels'][row]['allowedIps']:
                    message = f'通道 {row + 1}：请填写至少一个允许接入的客户端 IPv4'
                else:
                    message = f'通道 {row + 1}：请检查' + {'channelId': '通道 ID', 'name': '显示名称', 'allowedIps': '客户端 IPv4'}.get(key, key) + '；' + issue['msg']
                return self.showChannelError(message, cell)
            name = str(location[0]) if location else 'channels'
            return (self.showChannelError(str(error)) if name == 'channels'
                    else self.fields._showFieldError(name, str(error)))
        except ValueError as error:
            return self.showChannelError(str(error))
        self.channels.table.setAccessibleDescription('')
        self.channelErrorCell = None
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
