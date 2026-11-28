"""Read-only published configuration and device controls for the operator UI."""
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import json
from time import monotonic

from PySide2.QtCore import QPoint, QSignalBlocker, QTimer, Qt
from PySide2.QtGui import QColor
from PySide2.QtWidgets import (
    QAbstractItemView, QApplication, QDialog, QFormLayout, QGridLayout, QGroupBox, QHeaderView, QHBoxLayout,
    QLineEdit, QMessageBox, QPlainTextEdit, QPushButton, QScrollArea, QSplitter, QToolButton,
    QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from emo_master.apps.operator_runtime.log_reader import safeText
from emo_master.ui.communication_panel import (
    DeviceDiagnosticsView, TcpChannelStatusTable, exportDeviceCache, label,
)


_TCP_SERVER = 'device.tcp.server'
_HEALTH = {
    'normal': ('正常', '#18754c'),
    'inactive': ('非运行', '#535d6d'),
    'maintenance': ('维护', '#875701'),
    'fault': ('异常', '#b32930'),
    'unknown': ('未知', '#535d6d'),
}


def _deviceHealth(state):
    """Present device/driver lifecycle fields. Executor IDLE is not a connection state."""
    driver = state.get('driverState', {})
    connection, current = driver.get('connectionState'), driver.get('state')
    executor = state.get('state')
    if executor == 'QUARANTINED' or connection == 'QUARANTINED':
        return 'fault', '已隔离'
    if connection == 'FAULTED' or current == 'FAILED':
        return 'fault', '故障'
    if state.get('admissionState') == 'PAUSED':
        return 'maintenance', '维护中'
    if state.get('enabled') is False:
        return 'inactive', '未启用'
    if executor == 'CLOSED':
        return 'inactive', '已关闭'
    if state.get('deviceTypeId') == _TCP_SERVER:
        return {
            'LISTENING': ('normal', '监听中'),
            'STOPPED': ('inactive', '未监听'),
            'STARTING': ('inactive', '启动中'),
            'STOPPING': ('inactive', '停止中'),
        }.get(current, ('unknown', '未知'))
    if current == 'STOPPING':
        return 'inactive', '停止中'
    if current == 'STREAMING':
        return 'normal', '采集中'
    if connection == 'CONNECTED':
        return 'normal', '已连接'
    if connection == 'DISCONNECTED':
        return 'inactive', '未连接'
    if state.get('connectionLifetime') == 'per_operation':
        return {
            'BUSY': ('normal', '事务执行中'),
            'TRANSACTION_READY': ('normal', '事务就绪'),
        }.get(current, ('unknown', '未知'))
    if state.get('lifecycleConnected') is True:
        return 'normal', '已连接'
    if state.get('lifecycleConnected') is False:
        return 'inactive', '未连接'
    if driver.get('ready') is True:
        return 'normal', '已就绪'
    return 'unknown', '未知'


class DeviceManagementDialog(QDialog):
    def __init__(self, client, projectId, parent=None):
        super().__init__(parent)
        self.client, self.projectId = client, projectId
        self.setWindowTitle('已发布设备')
        available = (parent.screen() if parent is not None else self.screen()).availableGeometry()
        self.resize(min(960, available.width() - 32), min(650, available.height() - 48))
        self.reader = ThreadPoolExecutor(max_workers=1, thread_name_prefix='operator-devices')
        self.pending = None
        self.states, self.types, self.logs = {}, {}, {}
        self.deviceItems, self.expandedDetails = {}, set()
        self.kind = ''
        self.scope = None
        self.deviceScope = None
        self.stateReadFailed = False
        self.nextState = 0
        self.shownDeviceId = None
        self.shownChannels, self.shownDiagnostics = [], None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(10)
        self.status = label('正在读取设备状态…')
        self.status.setProperty('operatorRole', 'hint')
        layout.addWidget(self.status)
        split = QSplitter(Qt.Horizontal)
        split.setChildrenCollapsible(False)
        layout.addWidget(split, 1)

        fleet = QWidget()
        fleetLayout = QVBoxLayout(fleet)
        fleetLayout.setContentsMargins(0, 0, 0, 0)
        self.overview = label()
        fleetLayout.addWidget(self.overview)
        self.deviceList = QTreeWidget()
        self.deviceList.setAccessibleName('已发布设备列表')
        self.deviceList.setHeaderLabels(['设备', '状态'])
        self.deviceList.setRootIsDecorated(False)
        self.deviceList.setAlternatingRowColors(True)
        self.deviceList.setUniformRowHeights(True)
        self.deviceList.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.deviceList.header().setStretchLastSection(False)
        self.deviceList.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self.deviceList.header().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.deviceList.currentItemChanged.connect(self.showDevice)
        fleetLayout.addWidget(self.deviceList, 1)
        split.addWidget(fleet)

        details = QWidget()
        detailsLayout = QVBoxLayout(details)
        detailsLayout.setContentsMargins(6, 0, 6, 0)
        self.selectionHint = label('选择设备后查看状态与诊断')
        detailsLayout.addWidget(self.selectionHint)
        self.selectedDetail = QWidget()
        selectedLayout = QVBoxLayout(self.selectedDetail)
        selectedLayout.setContentsMargins(0, 0, 0, 0)
        detailsLayout.addWidget(self.selectedDetail)
        detailsLayout.addStretch()
        summary = QFormLayout()
        summary.setRowWrapPolicy(QFormLayout.WrapLongRows)
        self.deviceName, self.deviceType, self.deviceHealth, self.deviceError = [label() for _ in range(4)]
        self.deviceHealth.setProperty('operatorRole', 'section')
        self.deviceIdField = QLineEdit()
        self.deviceIdField.setReadOnly(True)
        copyId = QPushButton('复制 ID')
        copyId.clicked.connect(lambda: QApplication.clipboard().setText(self.selectedDeviceId() or ''))
        identity = QHBoxLayout()
        identity.addWidget(self.deviceIdField, 1)
        identity.addWidget(copyId)
        summary.addRow('名称', self.deviceName)
        summary.addRow('类型', self.deviceType)
        summary.addRow('完整 ID', identity)
        summary.addRow('运行状态', self.deviceHealth)
        summary.addRow('最近错误', self.deviceError)
        self.deviceErrorLabel = summary.labelForField(self.deviceError)
        selectedLayout.addLayout(summary)

        controls = QGridLayout()
        self.actions = {}
        for index, (action, title) in enumerate([
                ('connect', '连接'), ('disconnect', '断开'), ('maintenance', '维护'),
                ('resume', '恢复准入'), ('recover', '重建会话')]):
            button = QPushButton(title)
            button.clicked.connect(lambda _checked=False, action=action: self.control(action))
            row, column = divmod(index, 3)
            controls.addWidget(button, row, column)
            self.actions[action] = button
        self.actions['recover'].setProperty('operatorRole', 'caution')
        self.business = QPushButton('业务动作')
        self.business.clicked.connect(self.managementAction)
        controls.addWidget(self.business, 1, 2)
        controls.setColumnStretch(3, 1)
        selectedLayout.addLayout(controls)

        self.rawToggle = QToolButton()
        self.rawToggle.setText('原始状态 JSON')
        self.rawToggle.setCheckable(True)
        self.rawToggle.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.rawToggle.toggled.connect(self._toggleRawState)
        selectedLayout.addWidget(self.rawToggle, 0, Qt.AlignLeft)
        self.rawState = QPlainTextEdit()
        self.rawState.setAccessibleName('设备状态数据')
        self.rawState.setProperty('operatorRole', 'path')
        self.rawState.setReadOnly(True)
        selectedLayout.addWidget(self.rawState)

        self.channelStatus = TcpChannelStatusTable()
        self.channelStatus.setMinimumHeight(130)
        self.channelGroup = QGroupBox('TCP 通道')
        QVBoxLayout(self.channelGroup).addWidget(self.channelStatus)
        selectedLayout.addWidget(self.channelGroup)
        self.diagnostics = DeviceDiagnosticsView(compact=True)
        self.diagnostics.setMinimumHeight(260)
        self.diagnostics.notice.setProperty('operatorRole', 'hint')
        self.diagnostics.records.setColumnWidth(0, 96)
        self.diagnostics.records.setColumnWidth(1, 166)
        self.diagnostics.records.setColumnWidth(2, 110)
        self.diagnostics.records.setAlternatingRowColors(True)
        self.diagnostics.exportRequested.connect(lambda: exportDeviceCache(self.diagnostics))
        self.diagnostics.runtimeLogsRequested.connect(self.openRuntimeLogs)
        diagnosticsGroup = QGroupBox('近期设备诊断')
        QVBoxLayout(diagnosticsGroup).addWidget(self.diagnostics)
        selectedLayout.addWidget(diagnosticsGroup)

        detailScroll = QScrollArea()
        detailScroll.setWidgetResizable(True)
        detailScroll.setFrameShape(QScrollArea.NoFrame)
        detailScroll.setWidget(details)
        split.addWidget(detailScroll)
        split.setSizes([280, 650])
        split.setStretchFactor(1, 1)
        self._updateDeviceList()
        self.showDevice()

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

    def _setStatus(self, text):
        self.status.setText(text)
        self.status.setVisible(bool(text))

    def submit(self, kind, callback, *args, **kwargs):
        if self.pending is None:
            self.kind, self.scope = kind, self.projectId()
            self.pending = self.reader.submit(callback, *args, **kwargs)
            self.showDevice()

    def _readState(self, project):
        types, _requirements, _rejected = self.client.listDeviceTypes()
        runtimeId, states = self.client.getDeviceStates(project)
        return types, runtimeId, states

    def _clearScope(self):
        self.deviceScope = None
        self.states, self.logs = {}, {}
        self.expandedDetails.clear()
        self.deviceItems.clear()
        blocker = QSignalBlocker(self.deviceList)
        self.deviceList.clear()
        del blocker

    def poll(self):
        project = self.projectId()
        if self.deviceScope is not None and self.deviceScope[1] != project:
            self._clearScope()
            self.nextState = 0
            self._setStatus('正在读取设备状态…')
            self._updateDeviceList()
            self.showDevice()
        if self.pending is None:
            if monotonic() >= self.nextState:
                self.submit('state', self._readState, project)
            return
        if not self.pending.done():
            return
        future, kind, scope = self.pending, self.kind, self.scope
        self.pending = None
        if scope != project:
            self.nextState = 0
            return
        try:
            result = future.result()
        except Exception as error:
            self._setStatus(('设备状态读取失败：' if kind == 'state' else '') + safeText(error))
            self.nextState = monotonic() + 2
            if kind == 'state':
                self.stateReadFailed = True
                self._updateDeviceList()
        else:
            if kind == 'state':
                self._receiveState(project, result)
                self.requestLogs()
            elif kind == 'logs':
                key = (result['projectLoadGeneration'], result['deviceId'])
                recent = self.logs.get(key, {}).get('records', [])
                self.logs[key] = dict(result, records=(recent + result['records'])[-512:])
            else:
                self._setStatus('业务动作完成：' + json.dumps(result, ensure_ascii=False) if kind == 'managementAction'
                                else '设备控制完成；工程配置未改变')
                self.nextState = 0
        self.showDevice()

    def _receiveState(self, project, result):
        types, runtimeId, states = result
        generation = next(iter(states.values()), {}).get('projectLoadGeneration')
        deviceScope = (runtimeId, project, generation)
        changed = self.deviceScope != deviceScope
        if changed:
            self._clearScope()
        if changed or self.stateReadFailed:
            self._setStatus('' if states else '当前工程没有已发布设备。')
        self.deviceScope = deviceScope
        self.stateReadFailed = False
        self.types, self.states = types, states
        self.nextState = monotonic() + 2
        self._updateDeviceList()

    def _deviceTypeName(self, state):
        typeId = state.get('deviceTypeId')
        declaration = self.types.get(typeId)
        return declaration.displayName if declaration else typeId or '未知类型'

    def _updateDeviceList(self):
        selected = self.selectedDeviceId()
        anchor = self.deviceList.itemAt(QPoint(1, 0))
        offset = self.deviceList.visualItemRect(anchor).top() if anchor else 0
        removed = self.deviceItems.keys() - self.states.keys()
        added = self.states.keys() - self.deviceItems.keys()
        counts = Counter()
        blocker = QSignalBlocker(self.deviceList)
        for key in removed:
            item = self.deviceItems.pop(key)
            self.deviceList.takeTopLevelItem(self.deviceList.indexOfTopLevelItem(item))
            self.expandedDetails.discard(key)
            self.logs.pop((self.deviceScope[2], key), None)
        for key, state in self.states.items():
            item = self.deviceItems.get(key)
            if item is None:
                item = self.deviceItems[key] = QTreeWidgetItem(self.deviceList)
                item.setData(0, Qt.UserRole, key)
                item.setToolTip(0, key)
                item.setToolTip(1, key)
            name = str(state.get('name') or '').strip()
            typeName = self._deviceTypeName(state)
            detail = f'{typeName} · {key[-8:]}' if name else typeName
            item.setText(0, f'{name or key}\n{detail}')
            health, current = _deviceHealth({} if self.stateReadFailed else state)
            item.setText(1, current)
            item.setForeground(1, QColor(_HEALTH[health][1]))
            counts[health] += 1
        if removed or added:
            self.deviceList.setCurrentItem(self.deviceItems.get(selected))
            if anchor is not None and anchor in self.deviceItems.values():
                self.deviceList.scrollToItem(anchor, QAbstractItemView.PositionAtTop)
                bar = self.deviceList.verticalScrollBar()
                bar.setValue(bar.value() - offset)
        del blocker
        breakdown = [f'{title} {counts[key]}' for key, (title, _color) in _HEALTH.items()]
        self.overview.setText(f'{len(self.states)} 台设备\n' + '  ·  '.join(breakdown))

    def selectedDeviceId(self):
        current = self.deviceList.currentItem()
        return current.data(0, Qt.UserRole) if current is not None else None

    def _toggleRawState(self, expanded):
        key = self.selectedDeviceId()
        if expanded and key is not None:
            self.expandedDetails.add(key)
        else:
            self.expandedDetails.discard(key)
        self.showDevice()

    def showDevice(self):
        key = self.selectedDeviceId()
        listedState = self.states.get(key, {})
        state = {} if self.stateReadFailed else listedState
        declaration = self.types.get(listedState.get('deviceTypeId'))
        enabled = self.pending is None and self.deviceScope is not None and self.deviceScope[1] == self.projectId()
        self.selectionHint.setVisible(not listedState)
        self.selectedDetail.setVisible(bool(listedState))
        self.business.setVisible(bool(declaration and declaration.managementActions))
        self.business.setEnabled(enabled and bool(state.get('availableManagementActions')))
        for action, button in self.actions.items():
            button.setVisible(bool(declaration and action in declaration.controlCapabilities))
            button.setEnabled(enabled and action in state.get('availableControls', []))
        server = listedState.get('deviceTypeId') == _TCP_SERVER
        self.actions['connect'].setText('启动服务' if server else '连接')
        self.actions['disconnect'].setText('停止服务' if server else '断开')

        driver = state.get('driverState', {})
        channels = driver.get('channels', [])
        health, current = _deviceHealth(state)
        display = current
        if current == '监听中':
            count = sum(bool(channel.get('connected')) for channel in channels)
            display += f' · 已连接 {count} 个客户端'
        admission = {'CONTROL_PENDING': '控制处理中', 'REPLACING': '配置切换中',
                     'READY': '可接受设备请求'}.get(state.get('admissionState'))
        if admission:
            display += f' · {admission}'
        error = (driver.get('error') or driver.get('lastError') or state.get('lifecycleError') or
                 state.get('lastError') or state.get('recovery', {}).get('lastError'))
        self.deviceName.setText(str(listedState.get('name') or '').strip() or key or '')
        self.deviceType.setText(self._deviceTypeName(listedState))
        if self.deviceIdField.text() != (key or ''):
            self.deviceIdField.setText(key or '')
            self.deviceIdField.setToolTip(key or '')
        self.deviceHealth.setText(display)
        self.deviceHealth.setStyleSheet(f'color: {_HEALTH[health][1]}')
        self.deviceError.setText(safeText(error) if error else '')
        self.deviceErrorLabel.setVisible(bool(error))
        self.deviceError.setVisible(bool(error))

        expanded = bool(state) and key in self.expandedDetails
        blocker = QSignalBlocker(self.rawToggle)
        self.rawToggle.setChecked(expanded)
        del blocker
        self.rawToggle.setArrowType(Qt.DownArrow if expanded else Qt.RightArrow)
        self.rawToggle.setEnabled(bool(state))
        self.rawState.setVisible(expanded)
        if expanded:
            raw = json.dumps(state, ensure_ascii=False, indent=2)
            if self.rawState.toPlainText() != raw:
                scroll = self.rawState.verticalScrollBar()
                previous = scroll.value() if key == self.shownDeviceId else 0
                self.rawState.setPlainText(raw)
                scroll.setValue(previous)
        elif self.shownDeviceId != key or not state:
            self.rawState.clear()

        self.channelGroup.setVisible(server)
        if channels != self.shownChannels:
            self.channelStatus.setChannels(channels)
        page = self.logs.get((state.get('projectLoadGeneration'), key), {})
        if key != self.shownDeviceId:
            self.diagnostics.filter.setCurrentIndex(0)
            self.diagnostics.records.setCurrentItem(None)
        if key != self.shownDeviceId or page != self.shownDiagnostics or channels != self.shownChannels:
            self.diagnostics.setPage(page, channels)
        self.shownDeviceId = key
        self.shownChannels, self.shownDiagnostics = channels, page

    def dispose(self):
        self.hide()
        self.client.close()
        self.reader.shutdown(wait=False, cancel_futures=True)

    def openRuntimeLogs(self):
        key = self.selectedDeviceId()
        host = self.parent()
        showLogs = getattr(host, 'showLogs', None)
        if not callable(showLogs):
            self._setStatus('请从 Runtime 本机日志窗口查看所选设备的持久化文件记录。')
            return
        if not self._isCurrentDevice(key, self.deviceScope):
            return
        name = str(self.states[key].get('name') or '').strip()
        showLogs(deviceId=key, deviceName=name)
        self.hide()

    def _isCurrentDevice(self, key, scope):
        return (scope is not None and scope == self.deviceScope and scope[1] == self.projectId()
                and key in self.states and key == self.selectedDeviceId())

    def managementAction(self):
        key, scope = self.selectedDeviceId(), self.deviceScope
        if not self._isCurrentDevice(key, scope) or not self.business.isEnabled():
            return
        state = self.states[key]
        declaration = self.types.get(state.get('deviceTypeId'))
        from emo_master.ui.device_actions import DeviceActionDialog
        dialog = DeviceActionDialog(declaration, state['availableManagementActions'], self)
        if (dialog.exec_() == QDialog.Accepted and self._isCurrentDevice(key, scope)
                and self.business.isEnabled()):
            self.submit('managementAction', self.client.executeDeviceAction,
                        scope[1], scope[0], key, state, dialog.action.currentData(), dialog.arguments)

    def requestLogs(self):
        key = self.selectedDeviceId()
        state = self.states.get(key)
        if state:
            scope = {field: state[field] for field in ('runtimeInstanceId', 'projectId', 'projectLoadGeneration')}
            cursor = self.logs.get((scope['projectLoadGeneration'], key), {}).get('cursor', 0)
            self.submit('logs', self.client.getDeviceDiagnostics, scope, key, cursor)

    def control(self, action):
        key, scope = self.selectedDeviceId(), self.deviceScope
        if not self._isCurrentDevice(key, scope) or not self.actions[action].isEnabled():
            return
        state = self.states[key]
        if action == 'recover':
            if QMessageBox.question(self, '重建设备会话', '确认设备外部触发已停止，并重建所选设备会话？') != QMessageBox.Yes:
                return
            if self._isCurrentDevice(key, scope) and self.actions[action].isEnabled():
                self.submit('control', self.client.recoverDevice, scope[1], scope[0], key, state,
                            hardwareSynchronized=True)
        else:
            self.submit('control', self.client.controlDevice, scope[1], scope[0], key, state, action,
                        confirmed=True)
