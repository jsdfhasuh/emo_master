"""Existing PLC diagnostic controls bound to one explicit managed device."""
from copy import deepcopy

from PySide2.QtWidgets import QDialog, QVBoxLayout, QTabWidget, QWidget, QMessageBox

from emo_master.core.project.snapshots import revisionOf
from emo_master.plugins.builtins._communication_operators import PLC_READ_PARAM_SCHEMA
from emo_master.plugins.builtins._plc_editor import PlcEditorController


class _Context:
    operatorId = "communication.plc.slmp_read"
    paramSchema = PLC_READ_PARAM_SCHEMA
    workflowOptions: list = []

    def __init__(self, client, projectId, deviceId, definition):
        self.client, self.projectId, self.deviceId = client, projectId, deviceId
        self.selector = dict(deviceId=deviceId, definitionRevision=revisionOf(definition.model_dump()))

    def openPlcDebugSession(self, params, requestId, cancellation=None):
        return self.client.openPlcDebugSession(self.projectId, "devices", self.deviceId, self.operatorId,
                                              deepcopy(self.selector), requestId, cancellation=cancellation)

    def executePlcDebugCommand(self, *args, **kwargs):
        return self.client.executePlcDebugCommand(*args, **kwargs)

    def closePlcDebugSession(self, *args, **kwargs):
        return self.client.closePlcDebugSession(*args, **kwargs)

    def plcDebugLocation(self):
        return "Runtime / " + self.deviceId

    def log(self, level, message):
        # The controller already renders the actionable status in its own UI.
        pass


class _Controller(PlcEditorController):
    def _refreshControls(self):
        super()._refreshControls()
        for control in self._endpointControls.values():
            control.setEnabled(False)
        self.reloadButton.setEnabled(False)

    def _readParams(self):
        return dict(super()._readParams(), confirmed=True)

    def write(self):
        if not self._canWrite():
            return
        if QMessageBox.question(self.root, "确认本次 PLC 写入", "写入将改变所选 PLC 的数据。执行本次写入？",
                                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes:
            super().write()


class ManagedPlcDebugDialog(QDialog):
    def __init__(self, client, projectId, deviceId, definition, parent=None):
        super().__init__(parent)
        self.setWindowTitle("PLC 诊断 · " + definition.name)
        self.resize(960, 720)
        layout = QVBoxLayout(self)
        root = QWidget()
        rootLayout = QVBoxLayout(root)
        tabs = QTabWidget()
        tabs.setObjectName("plcTabs")
        for name in ("paramsPage", "runtimePage"):
            page = QWidget()
            page.setObjectName(name)
            QVBoxLayout(page)
            tabs.addTab(page, name)
        rootLayout.addWidget(tabs)
        layout.addWidget(root)
        self.controller = _Controller()
        self.controller.bind(root, _Context(client, projectId, deviceId, definition))
        self.controller.loadParams(dict(definition.connection, startAddress=0))
        tabs.setCurrentIndex(1)
        tabs.tabBar().hide()
        self.controller.onOpen()
        self.finished.connect(lambda result: self.controller.dispose())
