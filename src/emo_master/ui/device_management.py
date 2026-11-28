"""Read-only published configuration and device controls for the operator UI."""
from concurrent.futures import ThreadPoolExecutor
import json

from PySide2.QtCore import QTimer, Qt
from PySide2.QtWidgets import QComboBox, QDialog, QGroupBox, QHBoxLayout, QLabel, QPushButton, QTextBrowser, QVBoxLayout


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
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(10)
        devices = QGroupBox('设备状态与控制')
        deviceLayout = QVBoxLayout(devices)
        deviceLayout.setSpacing(7)
        self.status = QLabel('选择设备后查看当前 Runtime 中的状态与日志')
        self.status.setWordWrap(True)
        self.status.setProperty('operatorRole', 'hint')
        self.selector = QComboBox()
        self.selector.setAccessibleName('已发布设备')
        self.selector.addItem('请选择设备', None)
        self.selector.currentIndexChanged.connect(self.showDevice)
        self.deviceStatus = QLabel()
        self.deviceStatus.setTextFormat(Qt.PlainText)
        self.deviceStatus.setWordWrap(True)
        self.deviceStatus.setProperty('operatorRole', 'section')
        deviceLayout.addWidget(self.selector)
        deviceLayout.addWidget(self.deviceStatus)
        deviceLayout.addWidget(self.status)
        row = QHBoxLayout()
        self.actions = {}
        for action, title in [('connect', '连接'), ('disconnect', '断开'), ('maintenance', '维护'),
                              ('resume', '恢复准入'), ('recover', '重建会话')]:
            button = QPushButton(title)
            button.clicked.connect(lambda _checked=False, action=action: self.control(action))
            row.addWidget(button)
            self.actions[action] = button
        deviceLayout.addLayout(row)
        self.business = QPushButton('业务动作')
        self.business.clicked.connect(self.managementAction)
        row.addWidget(self.business)
        row.addStretch()
        self.actions['recover'].setProperty('operatorRole', 'caution')
        self.detail = QTextBrowser()
        self.detail.setAccessibleName('设备状态数据')
        self.detail.setProperty('operatorRole', 'path')
        from emo_master.ui.communication_panel import TcpChannelStatusTable, DeviceDiagnosticsView
        self.channelStatus = TcpChannelStatusTable()
        self.diagnostics = DeviceDiagnosticsView()
        self.diagnostics.notice.setProperty('operatorRole', 'hint')
        self.diagnostics.records.setColumnWidth(0, 96)
        self.diagnostics.records.setColumnWidth(1, 166)
        self.diagnostics.records.setColumnWidth(2, 110)
        self.diagnostics.records.setAlternatingRowColors(True)
        from emo_master.ui.communication_panel import exportDeviceCache
        self.diagnostics.exportRequested.connect(lambda: exportDeviceCache(self.diagnostics))
        self.diagnostics.runtimeLogsRequested.connect(self.openRuntimeLogs)
        deviceLayout.addWidget(self.detail)
        layout.addWidget(devices, 2)
        self.channelGroup = QGroupBox('TCP 通道')
        QVBoxLayout(self.channelGroup).addWidget(self.channelStatus)
        layout.addWidget(self.channelGroup, 1)
        diagnostics = QGroupBox('近期设备诊断')
        QVBoxLayout(diagnostics).addWidget(self.diagnostics)
        layout.addWidget(diagnostics, 3)
        self.timer = QTimer(self)
        self.timer.setInterval(100)
        self.timer.timeout.connect(self.poll)
        self.showDevice()

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
                    for key, state in self.states.items():
                        name = str(state.get('name') or '').strip()
                        declaration = self.types.get(state.get('deviceTypeId'))
                        typeName = declaration.displayName if declaration else state.get('deviceTypeId', '')
                        suffix = ' · '.join(part for part in (typeName, key[-8:]) if part)
                        label = f'{name}（{suffix}）' if name else key
                        self.selector.addItem(label, key)
                        self.selector.setItemData(self.selector.count()-1, key, Qt.ToolTipRole)
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
        connected = state.get('lifecycleConnected')
        connectionText = {True: '已连接', False: '未连接', None: '连接状态未知'}[connected]
        admission = {'CONTROL_PENDING': '控制处理中', 'PAUSED': '维护中', 'REPLACING': '配置切换中',
                     'READY': '可接受设备请求'}.get(state.get('admissionState'))
        availability = '工程中已禁用' if state.get('enabled') is False else admission
        self.deviceStatus.setText(' · '.join(part for part in (connectionText, availability) if part) if state else '')
        self.deviceStatus.setVisible(bool(state))
        self.detail.setPlainText(json.dumps(state, ensure_ascii=False, indent=2) if state else '请选择设备')
        channels = state.get('driverState', {}).get('channels', [])
        self.channelGroup.setVisible(server)
        self.channelStatus.setChannels(channels)
        page = self.logs.get((state.get('projectLoadGeneration'), key), {})
        self.diagnostics.setPage(page, channels)

    def dispose(self):
        self.hide()
        self.client.close()
        self.reader.shutdown(wait=False, cancel_futures=True)

    def openRuntimeLogs(self):
        host = self.parent()
        if host is not None and hasattr(host, 'showLogs'):
            host.showLogs(deviceId=self.selector.currentData() or '')
            self.hide()
        else:
            self.status.setText('请从 Runtime 本机日志窗口查看所选设备的持久化文件记录。')

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
