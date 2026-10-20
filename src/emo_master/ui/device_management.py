"""Read-only published configuration and device controls for the operator UI."""
from concurrent.futures import ThreadPoolExecutor
import json

from PySide2.QtCore import QTimer
from PySide2.QtWidgets import QComboBox, QDialog, QHBoxLayout, QLabel, QPushButton, QTextBrowser, QVBoxLayout


class DeviceManagementDialog(QDialog):
    def __init__(self, client, projectId, parent=None):
        super().__init__(parent)
        self.client, self.projectId = client, projectId
        self.setWindowTitle('已发布设备')
        self.resize(920, 620)
        self.reader = ThreadPoolExecutor(max_workers=1, thread_name_prefix='operator-devices')
        self.pending = None
        self.states, self.types, self.logs = {}, {}, {}
        self.runtimeId = ''
        self.kind = ''
        self.scope = None
        self.nextState = 0
        layout = QVBoxLayout(self)
        self.status = QLabel('选择设备后查看当前 Runtime 中的状态与日志')
        self.status.setWordWrap(True)
        self.selector = QComboBox()
        self.selector.addItem('请选择设备', None)
        self.selector.currentIndexChanged.connect(self.showDevice)
        layout.addWidget(self.status)
        layout.addWidget(self.selector)
        row = QHBoxLayout()
        self.actions = {}
        for action, title in [('connect', '连接'), ('disconnect', '断开'), ('maintenance', '维护'),
                              ('resume', '恢复准入'), ('recover', '重建会话')]:
            button = QPushButton(title)
            button.clicked.connect(lambda _checked=False, action=action: self.control(action))
            row.addWidget(button)
            self.actions[action] = button
        layout.addLayout(row)
        self.business = QPushButton('业务动作')
        self.business.clicked.connect(self.managementAction)
        row.addWidget(self.business)
        self.detail = QTextBrowser()
        from emo_master.ui.communication_panel import TcpChannelStatusTable, DeviceDiagnosticsView
        self.channelStatus = TcpChannelStatusTable()
        self.diagnostics = DeviceDiagnosticsView()
        layout.addWidget(self.detail)
        layout.addWidget(self.channelStatus)
        layout.addWidget(self.diagnostics)
        self.timer = QTimer(self)
        self.timer.setInterval(100)
        self.timer.timeout.connect(self.poll)

    def showEvent(self, event):
        super().showEvent(event)
        self.nextState = 0
        self.timer.start()

    def hideEvent(self, event):
        self.timer.stop()
        super().hideEvent(event)

    def submit(self, kind, callback, *args, **kwargs):
        if self.pending is None:
            self.kind, self.scope = kind, self.projectId()
            self.pending = self.reader.submit(callback, *args, **kwargs)
            self.showDevice()

    def poll(self):
        from time import monotonic
        if self.pending is not None:
            if not self.pending.done():
                return
            future, kind, scope = self.pending, self.kind, self.scope
            self.pending = None
            if scope != self.projectId():
                self.states, self.logs = {}, {}
                self.nextState = 0
                return
            try:
                if kind == 'state':
                    self.types, self.runtimeId, self.states = future.result()
                    selected = self.selector.currentData()
                    self.selector.blockSignals(True)
                    self.selector.clear()
                    self.selector.addItem('请选择设备', None)
                    for key in self.states:
                        self.selector.addItem(key, key)
                    self.selector.setCurrentIndex(max(0, self.selector.findData(selected)))
                    self.selector.blockSignals(False)
                    self.nextState = monotonic()+2
                    self.requestLogs()
                elif kind == 'logs':
                    page = future.result()
                    key = (page['projectLoadGeneration'], page['deviceId'])
                    self.logs[key] = dict(page, records=(self.logs.get(key, {}).get('records', []) + page['records'])[-512:])
                else:
                    result = future.result()
                    self.status.setText('业务动作完成：' + json.dumps(result, ensure_ascii=False) if kind == 'managementAction'
                        else '设备控制完成；工程配置未改变')
                    self.nextState = 0
                self.showDevice()
            except Exception as error:
                self.status.setText(str(error))
                self.nextState = monotonic()+2
        elif monotonic() >= self.nextState:
            def read():
                types, _requirements, _rejected = self.client.listDeviceTypes()
                runtimeId, states = self.client.getDeviceStates(self.projectId())
                return types, runtimeId, states
            self.submit('state', read)

    def showDevice(self):
        key = self.selector.currentData()
        state = self.states.get(key, {})
        declaration = self.types.get(state.get('deviceTypeId'))
        self.business.setVisible(bool(declaration and declaration.managementActions))
        self.business.setEnabled(self.pending is None and bool(state.get('availableManagementActions')))
        for action, button in self.actions.items():
            button.setVisible(bool(declaration and action in declaration.controlCapabilities))
            button.setEnabled(self.pending is None and action in state.get('availableControls', []))
        server = state.get('deviceTypeId') == 'device.tcp.server'
        self.actions['connect'].setText('启动服务' if server else '连接')
        self.actions['disconnect'].setText('停止服务' if server else '断开')
        self.detail.setPlainText(json.dumps(state, ensure_ascii=False, indent=2) if state else '请选择设备')
        channels = state.get('driverState', {}).get('channels', [])
        self.channelStatus.setVisible(server)
        self.channelStatus.setChannels(channels)
        page = self.logs.get((state.get('projectLoadGeneration'), key), {})
        self.diagnostics.setPage(page, channels)

    def dispose(self):
        self.hide()
        self.client.close()
        self.reader.shutdown(wait=False, cancel_futures=True)

    def managementAction(self):
        key = self.selector.currentData()
        state = self.states.get(key, {})
        declaration = self.types.get(state.get('deviceTypeId'))
        if self.pending is not None or declaration is None or not state.get('availableManagementActions'):
            return
        from emo_master.ui.device_actions import DeviceActionDialog
        dialog = DeviceActionDialog(declaration, state['availableManagementActions'], self)
        if dialog.exec_() == QDialog.Accepted:
            self.submit('managementAction', self.client.executeDeviceAction,
                self.projectId(), self.runtimeId, key, state, dialog.action.currentData(), dialog.arguments)

    def requestLogs(self):
        key = self.selector.currentData()
        state = self.states.get(key)
        if state:
            scope = {field: state[field] for field in ('runtimeInstanceId', 'projectId', 'projectLoadGeneration')}
            cursor = self.logs.get((scope['projectLoadGeneration'], key), {}).get('cursor', 0)
            self.submit('logs', self.client.getDeviceDiagnostics, scope, key, cursor)

    def control(self, action):
        key = self.selector.currentData()
        state = self.states.get(key)
        if not state or self.pending is not None:
            return
        if action == 'recover':
            from PySide2.QtWidgets import QMessageBox
            if QMessageBox.question(self, '重建设备会话', '确认设备外部触发已停止，并重建所选设备会话？') != QMessageBox.Yes:
                return
            self.submit('control', self.client.recoverDevice, self.projectId(), self.runtimeId, key, state,
                        hardwareSynchronized=True)
        else:
            self.submit('control', self.client.controlDevice, self.projectId(), self.runtimeId, key, state, action,
                        confirmed=True)
