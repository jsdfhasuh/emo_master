"""Project-owned device editor. Metadata reads never discover or open hardware."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
from time import monotonic

from PySide2.QtCore import Qt, QTimer
from PySide2.QtWidgets import (
    QWidget, QVBoxLayout, QSplitter, QTreeWidget, QTreeWidgetItem,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView, QLabel,
    QLineEdit, QCheckBox, QFormLayout, QTabWidget, QScrollArea, QToolButton,
    QMessageBox, QDialog, QDialogButtonBox, QTextBrowser, QFileDialog,
    QApplication, QFrame, QHBoxLayout, QMenu, QAction, QSizePolicy,
)

from emo_master.apps.designer.state.device_catalog import DeviceCatalogEditor
from emo_master.apps.designer.ui.icon_map import icon, deviceIcon
from emo_master.apps.designer.ui.param_form import SchemaParamForm


def _activeOwnersText(state):
    owners = state.get('activeOwners')
    if owners is None:
        owners = [state['activeOwner']] if state.get('activeOwner') else []
    text = ', '.join(str(owner)[:80] for owner in owners[:4]) or '-'
    return text + (f' 等 {len(owners)} 个任务' if len(owners) > 4 else '')


class DeviceWorkspace(QDialog):
    def __init__(self, coordinator):
        super().__init__(coordinator.window)
        self.setWindowTitle("设备管理")
        self.setWindowModality(Qt.WindowModal)
        self.setWindowFlag(Qt.WindowContextHelpButtonHint, False)
        self.setSizeGripEnabled(True)
        available = coordinator.window.screen().availableGeometry()
        self.resize(min(1200, available.width() - 40), min(760, available.height() - 60))
        self.c = coordinator
        self.deviceTypes = {}
        self.requirements = {}
        self.selectedId = None
        self.original = None
        self.formBaseline = None
        self.inputErrorControl = None
        self.inputErrorMessage = ''
        self.pendingTest = None
        self.lastSearch = ''
        self.loading = False
        self.reader = ThreadPoolExecutor(max_workers=1, thread_name_prefix="device-metadata")
        self.pending = None
        self._networkOpenRequest = None
        self._tcpCategoryIntent = None
        self.pendingKind = "metadata"
        self.pendingScope = None
        self.states = {}
        self.stateRuntimeId = ""
        self.stateScope = None
        self.stateReadAt = 0.0
        self.stateError = ''
        self.stateNotice = ''
        self.discoveredInfo = {}
        self.diagnosticPages = {}
        self.stateTimer = QTimer(self)
        self.stateTimer.setInterval(2000)
        self.stateTimer.timeout.connect(self._refreshKnownState)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._metadataReady)
        outerLayout = QVBoxLayout(self)
        outerLayout.setSpacing(4)
        content = QWidget()
        content.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 0, 0)
        outerLayout.addWidget(content, 1)
        commands = QHBoxLayout()
        deviceCommands = QHBoxLayout()
        propertyCommands = QHBoxLayout()
        self.listMore = QMenu(self)
        self.diagnosticMenu = QMenu(self)
        self.deviceMore = QMenu(self)
        self.buttons = {}
        shortLabels = dict(new='新建设备', delete='删除设备', apply='立即应用到草稿', revert='恢复当前输入',
            import_='导入设备', export='导出设备', refresh='刷新',
            network='设备发现', migration='旧节点迁移',
            diagnostics='设备诊断', test='测试连接', connect='连接设备', disconnect='断开连接',
            maintenance='暂停设备', resume='恢复准入', recover='恢复会话')
        shortLabels['import'] = shortLabels.pop('import_')
        shortLabels['business'] = '业务动作'
        for key, title, glyph, callback in (
            ("new", "新建设备", "plus", self.createDevice),
            ("delete", "删除设备", "trash-2", self.deleteDevice),
            ("apply", "应用到工程草稿", "save", self.apply),
            ("revert", "放弃属性修改", "x", self.revert),
            ("import", "导入设备配置包", "upload", self.importDevices),
            ("export", "导出所选设备配置包", "download", self.exportDevices),
            ("refresh", "刷新设备与在线状态", "refresh-cw", self.loadMetadata),
            ("network", "Runtime 设备发现 / 实物 IP", "search", self.deviceNetwork),
            ("migration", "旧设备节点迁移预览", "list", self.migrationPreview),
            ("diagnostics", "设备诊断", "sliders-horizontal", self.diagnostics),
            ("test", "测试当前连接草稿", "shield-check", self.testConnection),
            ("connect", "连接并恢复使用", "plug", lambda: self.control("connect")),
            ("disconnect", "断开并暂停连接", "unplug", lambda: self.control("disconnect")),
            ("maintenance", "暂停所选设备并取消其请求", "pause", lambda: self.control("maintenance")),
            ("resume", "恢复所选设备的业务准入", "play", lambda: self.control("resume")),
            ("recover", "恢复设备会话", "refresh-cw", self.recover),
            ("business", "执行业务动作", "play", self.managementAction),
        ):
            menu = (self.listMore if key in {'delete', 'migration'} else
                    self.diagnosticMenu if key == 'diagnostics' else
                    self.deviceMore if key in {'network', 'maintenance', 'resume', 'recover', 'business'} else None)
            if menu is not None:
                button = QAction(icon(glyph), shortLabels[key], self)
                button.triggered.connect(lambda _checked=False, command=callback, action=button:
                                         command() if action.isEnabled() else None)
                menu.addAction(button)
            else:
                button = QToolButton()
                button.setMinimumHeight(32)
                button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
                button.setText(shortLabels[key])
                button.setIcon(icon(glyph))
                button.setAccessibleName(title)
                button.clicked.connect(callback)
                if key in {'apply', 'revert'}:
                    propertyCommands.addWidget(button)
                elif key in {'test', 'connect', 'disconnect'}:
                    deviceCommands.addWidget(button)
                elif key != 'refresh':
                    commands.addWidget(button)
            button.setToolTip(title)
            self.buttons[key] = button
        self.commandTitles = {key: command.toolTip() for key, command in self.buttons.items()}
        self.readParametersButton = self.diagnosticMenu.addAction('读取实际参数')
        self.readParametersButton.triggered.connect(lambda _checked=False:
            self.diagnostics(readParameters=True) if self.readParametersButton.isEnabled() else None)
        for row, menu, title in ((commands, self.listMore, '设备列表操作'),
                                  (deviceCommands, self.diagnosticMenu, '诊断'),
                                  (deviceCommands, self.deviceMore, '设备维护')):
            tool = QToolButton()
            tool.setText(title)
            tool.setAccessibleName(title)
            tool.setMinimumHeight(32)
            tool.setMenu(menu)
            tool.setPopupMode(QToolButton.InstantPopup)
            row.addWidget(tool)
        commands.addStretch(1)
        commands.addWidget(self.buttons['refresh'])
        deviceCommands.addStretch(1)
        propertyCommands.addStretch(1)
        self.status = QLabel("未读取 Runtime 设备类型")
        self.status.setWordWrap(True)
        layout.addLayout(commands)
        layout.addWidget(self.status)
        self.search = QLineEdit()
        self.search.setPlaceholderText("搜索设备名称")
        self.search.textChanged.connect(lambda: self._filter(self.tree.currentItem(), self.tree.currentItem()))
        layout.addWidget(self.search)
        self.difference = QLabel()
        self.difference.setWordWrap(True)
        layout.addWidget(self.difference)
        self.splitter = QSplitter()
        # A viewport bounds the splitter's height-for-width hints. The trees,
        # parameter tabs and information views retain their own scrollbars.
        body = QScrollArea()
        body.setFrameShape(QFrame.NoFrame)
        body.setWidgetResizable(True)
        body.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        body.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        body.setWidget(self.splitter)
        layout.addWidget(body, 1)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabel("设备类型 / 实例")
        self.tree.setMinimumWidth(130)
        self.tree.currentItemChanged.connect(self._filter)
        self.splitter.addWidget(self.tree)
        self.table = QTableWidget(0, 8)
        self.table.setHorizontalHeaderLabels(["设备名称", "类型", "引用", "状态", "连接策略", "当前占用 / 排队", "最近错误", "操作"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Interactive)
        self.table.setColumnWidth(0, 150)
        self.table.verticalHeader().hide()
        self.table.currentCellChanged.connect(self._select)
        self.splitter.addWidget(self.table)
        self.details = QWidget()
        self.details.setMinimumWidth(280)
        detailLayout = QVBoxLayout(self.details)
        detailLayout.setContentsMargins(6, 6, 6, 6)
        detailLayout.setSpacing(4)
        self.detailTitle = QLabel('未选择设备')
        self.detailTitle.setWordWrap(True)
        titleFont = self.detailTitle.font()
        titleFont.setBold(True)
        self.detailTitle.setFont(titleFont)
        self.typeInfo = QLabel()
        self.typeInfo.setWordWrap(True)
        self.onlineIndicator = QLabel()
        self.onlineIndicator.setWordWrap(True)
        for label in (self.detailTitle, self.typeInfo, self.onlineIndicator):
            label.setTextFormat(Qt.PlainText)
        for label in (self.detailTitle, self.onlineIndicator):
            detailLayout.addWidget(label)
        detailLayout.addLayout(deviceCommands)
        basic = QWidget()
        basicLayout = QVBoxLayout(basic)
        form = QFormLayout()
        self.basicForm = form
        self.name = QLineEdit()
        self.description = QLineEdit()
        self.enabled = QCheckBox("启用")
        self.identity = QLabel()
        self.identity.setWordWrap(True)
        self.identity.setTextInteractionFlags(Qt.TextSelectableByMouse)
        form.addRow("名称", self.name)
        form.addRow("说明", self.description)
        form.addRow("", self.enabled)
        # Long type/driver metadata belongs in the scrollable basic page, not
        # in a permanent header which steals editing space in small windows.
        form.addRow("类型 / 驱动", self.typeInfo)
        form.addRow("设备 ID", self.identity)
        self.hardwareFields = {}
        for key, title in [('modelName', '型号'), ('serialNumber', '序列号'), ('macAddress', 'MAC')]:
            label = QLabel('未获取')
            label.setTextFormat(Qt.PlainText)
            label.setWordWrap(True)
            label.setTextInteractionFlags(Qt.TextSelectableByMouse)
            self.hardwareFields[key] = label
            form.addRow(title, label)
        self.hardwareSource = QLabel()
        self.hardwareSource.setWordWrap(True)
        form.addRow('信息来源', self.hardwareSource)
        basicLayout.addLayout(form)
        basicLayout.addStretch(1)
        self.tabs = QTabWidget()
        basicScroll = QScrollArea()
        basicScroll.setWidgetResizable(True)
        basicScroll.setWidget(basic)
        self.tabs.addTab(basicScroll, '基本信息')
        self.forms = {}
        self.formScrolls = {}
        for key, title in (("connection", "连接"), ("defaults", "工作参数"), ("policies", "策略")):
            editor = SchemaParamForm(deviceMode=True)
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setWidget(editor)
            self.tabs.addTab(scroll, title)
            self.forms[key] = editor
            self.formScrolls[key] = scroll
        self.infoTabs = QTabWidget()
        self.references = QTextBrowser()
        self.references.setOpenLinks(False)
        self.references.anchorClicked.connect(self.navigateReference)
        self.infoTabs.addTab(self.references, "节点引用")
        from emo_master.ui.communication_panel import TcpChannelStatusTable, DeviceDiagnosticsView
        self.onlineState = QTextBrowser()
        self.channelStatus = TcpChannelStatusTable()
        online = QSplitter(Qt.Vertical)
        online.addWidget(self.onlineState)
        online.addWidget(self.channelStatus)
        self.infoTabs.addTab(online, "在线状态")
        self.logs = DeviceDiagnosticsView()
        self.infoTabs.addTab(self.logs, "诊断日志")
        self.infoTabs.currentChanged.connect(lambda _index: self.showDiagnostics())
        self.detailSplitter = QSplitter(Qt.Vertical)
        propertyArea = QWidget()
        propertyLayout = QVBoxLayout(propertyArea)
        propertyLayout.setContentsMargins(0, 0, 0, 0)
        propertyLayout.addWidget(self.tabs)
        self.detailSplitter.addWidget(propertyArea)
        self.detailSplitter.addWidget(self.infoTabs)
        propertyArea.setMinimumSize(0, 0)
        propertyArea.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Expanding)
        self.infoTabs.setMinimumSize(0, 0)
        self.infoTabs.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
        self.tabs.setMinimumSize(0, 0)
        self.tabs.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Ignored)
        # Give editing most of the available space, without imposing a fixed
        # form height or preventing the user from resizing/collapsing either pane.
        self.detailSplitter.setStretchFactor(0, 4)
        self.detailSplitter.setStretchFactor(1, 1)
        self.detailSplitter.setSizes([4, 1])
        self.details.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
        detailLayout.addWidget(self.detailSplitter, 1)
        # Draft actions remain reachable even when a pane is collapsed.
        detailLayout.addLayout(propertyCommands)
        self.splitter.addWidget(self.details)
        self.splitter.setSizes([180, 430, 400])
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        publish = buttons.addButton('查看并写入 Runtime…', QDialogButtonBox.ActionRole)
        publish.clicked.connect(self.c.configureFromDevices)
        publish.setToolTip('比较、选择并显式写入；不自动保存工程文件')
        self.buttons['publish'] = publish
        self.closeButton = buttons.button(QDialogButtonBox.Close)
        self.closeButton.setText("保留草稿并关闭")
        self.draftHelp = QLabel('切换设备或关闭窗口时，有效修改会保留到工程草稿。保存工程文件和写入 Runtime 需分别执行。')
        self.draftHelp.setWordWrap(True)
        self.draftHelp.setToolTip(self.draftHelp.text())
        outerLayout.addWidget(self.draftHelp)
        history = QHBoxLayout()
        for key, title, callback in (('undo', '撤销工程修改', lambda: self.c.history()),
                                     ('redo', '重做工程修改', lambda: self.c.history(True))):
            button = QToolButton()
            button.setText(title)
            button.setToolTip(title + '；作用于统一工程历史')
            button.setAccessibleName(title)
            button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
            button.setMinimumHeight(32)
            button.clicked.connect(callback)
            history.addWidget(button)
            self.buttons[key] = button
        self.inputState = QLabel()
        self.inputState.setWordWrap(True)
        history.addWidget(self.inputState, 1)
        outerLayout.addLayout(history)
        buttons.rejected.connect(self.reject)
        outerLayout.addWidget(buttons)
        for signal in (self.name.textChanged, self.description.textChanged, self.enabled.toggled):
            signal.connect(self._inputStatus)
        from .device_wizard import clearNameFeedback
        self.name.textChanged.connect(lambda *_: clearNameFeedback(self.name, self.status))
        self.refresh()

    def _inputStatus(self, *_args):
        if getattr(self, '_populatingDetails', False):
            return
        if self.inputErrorControl is not None and not self.inputErrorControl.accessibleDescription():
            if self.status.text() == self.inputErrorMessage:
                self.status.clear()
                self.status.setToolTip('')
            self.inputErrorControl = None
        try:
            changed = bool(self.original and self._formValue() != self.formBaseline)
            text = ('输入有误' if self.inputErrorControl is not None or
                    self.original and (not self.name.text().strip() or len(self.name.text().strip()) > 160)
                    else '有待应用输入' if changed else '当前输入未修改')
        except ValueError:
            text = '输入有误'
        self.inputState.setText(text + ' · 工程文件' + ('有未保存修改' if self.c.session.dirty else '已保存'))
        if self.selectedId:
            from emo_master.core.project.snapshots import revisionOf
            state = self.states.get(self.selectedId)
            fresh = self.stateScope == self._scope() and monotonic() - self.stateReadAt < 6 and not self.stateError
            comparison = '在线信息未获取或已失效'
            if fresh and self.stateRuntimeId:
                if state is None:
                    comparison = '尚未写入 Runtime'
                else:
                    try:
                        definition = self.c.session.document().devices.definitions[self.selectedId]
                        declaration = self.deviceTypes[definition.deviceTypeId]
                        same = revisionOf(declaration.validateDefinition(definition).model_dump()) == state['definitionRevision']
                        comparison = '与草稿一致' if same else '有配置差异'
                    except (ValueError, KeyError):
                        comparison = '草稿待校验'
            self.difference.setText('当前设备与 Runtime：' + comparison)
        else:
            self.difference.setText('选择设备以查看它与 Runtime 的配置关系')

    def showEvent(self, event):
        super().showEvent(event)
        self.stateTimer.start()
        if self.pending is None:
            self._refreshKnownState()

    def hideEvent(self, event):
        self.stateTimer.stop()
        super().hideEvent(event)

    def _refreshKnownState(self):
        if (self.isVisible() and self.pending is None and QApplication.activeModalWidget() in {None, self}
                and callable(getattr(self.c.window.runtimeClient, 'getDeviceStates', None))):
            self.readState(background=True)

    def done(self, result):
        try:
            self.commitPending()
        except ValueError as error:
            self._showInputError(error)
            return
        # Keep the reusable metadata reader, but abandon this opening intent.
        # Reopening the workspace must not revive a response from before close.
        self._networkOpenRequest = None
        self._tcpCategoryIntent = None
        super().done(result)
        self.c.chrome.update()

    def catalog(self):
        return DeviceCatalogEditor(self.c.session, self.deviceTypes, self.requirements)

    def selectTcpCategory(self):
        if not self.deviceTypes:
            self._tcpCategoryIntent = self._scope()
            return
        self._tcpCategoryIntent = None
        definition = self.c.session.workflows.devices.get('definitions', {}).get(self.selectedId, {})
        if definition.get('deviceTypeId', '').startswith('device.tcp.'):
            return
        categories = {value.category for key, value in self.deviceTypes.items() if key.startswith('device.tcp.')}
        for index in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(index)
            if item.text(0) in categories:
                self.tree.setCurrentItem(item)
                self.table.clearSelection()
                self.table.setCurrentCell(-1, -1)
                return

    def exportDevices(self):
        if not self.selectedId or not self.c._canSync():
            return
        path, _ = QFileDialog.getSaveFileName(self, '导出设备配置包', '', 'Device package (*.emodev)')
        if not path:
            return
        try:
            from pathlib import Path
            from emo_master.core.project.delivery_store import atomicJson
            from emo_master.apps.designer.state.device_package import exportDevices
            package = exportDevices(self.c.session, [self.selectedId], self.c.directory)
            atomicJson(Path(path), package.model_dump(mode='json'))
            self.status.setText('设备配置已导出；已清空声明的现场字段')
        except (ValueError, OSError) as error:
            self.status.setText(str(error))

    def importDevices(self):
        if not self.c._canSync():
            return
        path, _ = QFileDialog.getOpenFileName(self, '导入设备配置包', '', 'Device package (*.emodev)')
        if not path:
            return
        try:
            from emo_master.apps.designer.state.device_package import importDevices, readDevicePackage
            package = readDevicePackage(path)
            if QMessageBox.question(self, '导入设备配置', f'新建 {len(package.devices)} 个禁用设备，不覆盖已有设备。继续？',
                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
                return
            ids = importDevices(self.c.session, package, self.deviceTypes, self.c.directory)
            self.selectedId = next(iter(ids.values()))
            self.refresh()
            self.c.chrome.update()
            names = self.c.session.workflows.devices['definitions']
            fields = []
            for binding in package.resources.siteBindings:
                target = binding.target
                value = names[ids[target.deviceId]]
                schema = getattr(self.deviceTypes[value['deviceTypeId']], target.section + 'Schema')
                for part in target.parameterPath:
                    schema = schema.get('properties', {}).get(part, {})
                fields.append(value['name'] + ' / ' + schema.get('title', '.'.join(target.parameterPath)))
            self.status.setText('已导入工程草稿：' + '、'.join(names[key]['name'] for key in ids.values())
                + '；请填写现场字段后显式启用设备'
                + ('；待填写：' + '、'.join(fields) if fields else ''))
        except (ValueError, OSError) as error:
            self.status.setText(str(error))

    def navigateReference(self, url):
        from urllib.parse import unquote
        parts = url.toString().split("/")
        if len(parts) == 2 and self.c._canSync():
            self.accept()
            self.c.showFlow()
            self.c.window.navigateToWorkflow(*(unquote(part) for part in parts))

    def showDiagnostics(self):
        from html import escape
        if self.stateScope is not None and self.stateScope != self._scope():
            self.states, self.stateRuntimeId, self.discoveredInfo = {}, '', {}
            self.stateError = 'Runtime 连接已变化，等待刷新'
        state = self.states.get(self.selectedId) or {}
        freshness = (self.stateNotice or '读取失败：' + self.stateError if self.stateError or self.stateNotice else
                     f'更新于 {max(0, int(monotonic() - self.stateReadAt))} 秒前' if self.stateReadAt else '未获取在线状态')
        values = [('连接', {'CONNECTED': '已连接', 'DISCONNECTED': '未连接', 'FAULTED': '连接故障'}.get(state.get('driverState', {}).get('connectionState', 'CONNECTED' if state.get('lifecycleConnected') else 'DISCONNECTED'), '未知')),
                  ('执行状态', {'IDLE': '空闲', 'BUSY': '执行中', 'QUARANTINED': '已隔离', 'CLOSED': '已关闭'}.get(state.get('state'), '未知')),
                  ('当前任务', _activeOwnersText(state)), ('排队请求', state.get('queued', '-')),
                  ('连接准入', {'PAUSED': '手动暂停', 'CONTROL_PENDING': '维护处理中', 'REPLACING': '配置替换中'}.get(state.get('admissionState'), '正常' if state else '未知')),
                  ('会话代次', state.get('generation', '-')), ('设备恢复', state.get('recovery', {}).get('state', '-')),
                  ('最近错误', state.get('lifecycleError') or state.get('lastError') or '-'), ('状态更新时间', freshness)]
        self.onlineState.setHtml('<table cellpadding="4">' + ''.join(
            f'<tr><th align="left">{escape(title)}</th><td>{escape(str(value))}</td></tr>' for title, value in values) + '</table>')
        self.onlineIndicator.setText(('在线状态 · ' if state else '') + freshness)
        definition = self.c.session.workflows.devices.get('definitions', {}).get(self.selectedId, {})
        hardware = state.get('hardwareInfo') or self.discoveredInfo.get(definition.get('connection', {}).get('cameraKey'), {})
        for key, label in self.hardwareFields.items():
            label.setText(str(hardware.get(key) or '未获取'))
        self.hardwareSource.setText('Runtime 最近一次显式发现（非实时回读）' if hardware else '未发现 / 类型未提供；不会自动访问设备')
        driver = state.get('driverState', {})
        self.channelStatus.setVisible(definition.get('deviceTypeId') == 'device.tcp.server')
        self.channelStatus.setChannels(driver.get('channels', []))
        if driver:
            self.onlineState.append('<pre>' + escape(json.dumps(driver, ensure_ascii=False, indent=2)) + '</pre>')
        camera = definition.get('deviceTypeId', '').startswith('device.camera.')
        for field in (*self.hardwareFields.values(), self.hardwareSource):
            field.setVisible(camera)
            self.basicForm.labelForField(field).setVisible(camera)
        scope = tuple(state.get(key, '') for key in ('runtimeInstanceId', 'projectId', 'projectLoadGeneration'))
        page = self.diagnosticPages.get((*scope, self.selectedId), {})
        self.logs.setPage(page, driver.get('channels', []))

    def readDiagnostics(self):
        state = self.states.get(self.selectedId)
        reader = getattr(self.c.window.runtimeClient, 'getDeviceDiagnostics', None)
        if self.pending is not None or not state or not callable(reader) or not self.isVisible():
            return
        scope = {key: state[key] for key in ('runtimeInstanceId', 'projectId', 'projectLoadGeneration')}
        key = (*scope.values(), self.selectedId)
        self.pendingKind, self.pendingScope, self.pendingDevice = 'deviceDiagnostics', self._scope(), self.selectedId
        self.pending = self.reader.submit(reader, scope, self.selectedId, self.diagnosticPages.get(key, {}).get('cursor', 0))
        self.timer.start(40)

    def deviceNetwork(self):
        if not self.selectedId or self.pending is not None:
            self.status.setText('请选择设备并等待当前请求完成')
            return
        try:
            self.commitPending()
        except ValueError as error:
            self.status.setText(str(error))
            return
        definition = self.c.session.document().devices.definitions[self.selectedId]
        if definition.deviceTypeId != 'device.camera.huaray_imv':
            self.status.setText('当前网络发现入口仅支持华睿 IMV')
            return
        if not self.stateRuntimeId:
            # Discovery/adoption is also a legitimate offline draft operation.
            # Obtain the Runtime identity without loading this project; the
            # discovery-only dialog never exposes a ForceIP control.
            self.pendingKind, self.pendingScope, self.pendingDevice = 'networkInfo', self._scope(), self.selectedId
            self.pending = self.reader.submit(self.c.window.runtimeClient.deviceNetworkCommand, '', '', 'info')
            self._networkOpenRequest = self.pending
            self.timer.start(40)
            self._updateOnlineActions()
            self.status.setText('正在读取 Runtime 发现能力；尚未发现或修改设备')
            return
        self._openDeviceNetwork(self._scope()[-1], self.stateRuntimeId)

    def _openDeviceNetwork(self, projectId, runtimeId, *, discoveryOnly=False):
        from .device_network import DeviceNetworkDialog
        definition = self.c.session.document().devices.definitions[self.selectedId]
        before, scope = definition.model_dump(), self._scope()
        dialog = DeviceNetworkDialog(self.c.window.runtimeClient, projectId, runtimeId,
            self.selectedId, self.states.get(self.selectedId, {}), self, discoveryOnly=discoveryOnly)
        dialog.exec_()
        if dialog.discovery and scope == self._scope():
            self.discoveredInfo = {row['cameraKey']: row for row in dialog.discovery['devices']}
            self.showDiagnostics()
        if dialog.selectedCameraKey:
            current = self.c.session.document().devices.definitions.get(self.selectedId)
            if scope != self._scope() or current is None or current.model_dump() != before:
                self.status.setText('工程或设备已改变，未覆盖当前草稿')
                return
            value = deepcopy(before)
            value['connection']['cameraKey'] = dialog.selectedCameraKey
            try:
                self.catalog().put(value, deviceId=self.selectedId)
            except ValueError as error:
                self.status.setText(str(error))
                return
            self.refresh()
            self.status.setText('设备标识已写入草稿；未连接设备，未发布配置')

    def diagnostics(self, readParameters=False):
        if not self.selectedId or not self.c._canSync():
            return
        try:
            self.commitPending()
            definition = self.c.session.document().devices.definitions[self.selectedId]
            definition = self.deviceTypes[definition.deviceTypeId].validateDefinition(definition)
            if definition.deviceTypeId == "device.camera.huaray_imv":
                from emo_master.apps.designer.ui.managed_camera_debug import ManagedCameraDebugDialog as DiagnosticDialog
            elif definition.deviceTypeId == "device.plc.mitsubishi_slmp":
                from emo_master.apps.designer.ui.managed_plc_debug import ManagedPlcDebugDialog as DiagnosticDialog
            else:
                raise ValueError("所选设备不支持设备诊断")
            dialog = DiagnosticDialog(self.c.window.runtimeClient, self.c.session.document().project.projectId,
                                          self.selectedId, definition, self)
            if readParameters and definition.deviceTypeId == 'device.camera.huaray_imv':
                dialog.readOnReady = True
            dialog.exec_()
        except (ValueError, KeyError) as error:
            self.status.setText(str(error))

    def testConnection(self):
        if self.pending is not None or not self.selectedId:
            return
        try:
            from .device_wizard import validateDeviceDefinition
            base = dict(self.original, description=self.description.text(), enabled=self.enabled.isChecked())
            definition = validateDeviceDefinition(self.deviceTypes[base['deviceTypeId']],
                self.name, self.forms, self.tabs, base=base)
            candidate = (self.selectedId, deepcopy(self._formValue()))
        except (ValueError, KeyError) as error:
            self._showInputError(error)
            return
        from .device_wizard import connectionTestPrompt
        if QMessageBox.question(self, '测试连接', connectionTestPrompt(definition['deviceTypeId']),
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        client = self.c.window.runtimeClient
        def test():
            info = client.deviceNetworkCommand('', '', 'info')
            if info.get('deviceSetupVersion', 0) < 1:
                raise ValueError('Runtime 不支持连接测试，请升级 Runtime')
            return client.deviceNetworkCommand(info['projectId'], info['runtimeInstanceId'], 'test_connection',
                payload=dict(confirmed=True, definition=definition))
        self.pendingKind, self.pendingScope = 'test', self._scope()
        self.pendingTest = candidate
        self.pending = self.reader.submit(test)
        self.status.setText('正在测试当前连接草稿')
        self.timer.start(40)

    def recover(self):
        if self.pending is not None or not self.selectedId:
            return
        state = self.states.get(self.selectedId)
        if state is None or not self.stateRuntimeId:
            self.status.setText("请先读取在线设备状态")
            return
        recover = getattr(self.c.window.runtimeClient, "recoverDevice", None)
        if recover is None:
            self.status.setText("Runtime 不支持设备恢复")
            return
        if QMessageBox.question(self, "恢复设备会话", "恢复所选设备会话？相关任务及诊断窗口必须先退出。\n"
                "相机须先停止外部触发并确认现场采集已同步。此次操作不连接设备、不重发失败请求。",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        self.pendingKind = "recover"
        self.pendingScope = self._scope()
        definition = self.c.session.document().devices.definitions[self.selectedId]
        self.pending = self.reader.submit(recover, self.c.session.document().project.projectId,
                                          self.stateRuntimeId, self.selectedId, deepcopy(state),
                                          hardwareSynchronized=definition.deviceTypeId == "device.camera.huaray_imv")
        self.timer.start(40)

    def control(self, action):
        if self.pending is not None or not self.selectedId:
            return
        state = self.states.get(self.selectedId)
        if state is None or not self.stateRuntimeId:
            self.status.setText("请先读取在线设备状态")
            return
        control = getattr(self.c.window.runtimeClient, "controlDevice", None)
        if not callable(control):
            self.status.setText("Runtime 不支持设备连接控制")
            return
        messages = {
            "connect": "连接所选在线设备并恢复使用？不拍照、不写 PLC、不改变工程草稿。",
            "disconnect": "断开所选设备并暂停自动连接？使用中的设备将拒绝此操作。",
            "resume": "恢复所选设备的业务准入？之后按原连接策略使用，不立即连接或执行业务。",
            "maintenance": "暂停所选设备，取消它的排队及活动请求，等待实际退出后断开？\n"
                           "无关设备继续运行；已发生的触发或 PLC 写入不会撤销。",
        }
        name = self.c.session.workflows.devices["definitions"][self.selectedId]["name"]
        if QMessageBox.question(self, "设备连接控制", f"{name} ({self.selectedId})\n{messages[action]}", QMessageBox.Yes | QMessageBox.No,
                                QMessageBox.No) != QMessageBox.Yes:
            return
        self.pendingKind = "control"
        self.pendingDevice = self.selectedId
        self.pendingScope = self._scope()
        self.pending = self.reader.submit(control, self.c.session.document().project.projectId,
            self.stateRuntimeId, self.selectedId, deepcopy(state), action, confirmed=True)
        self.timer.start(40)

    def managementAction(self):
        state = self.states.get(self.selectedId, {})
        declaration = self.deviceTypes.get(state.get('deviceTypeId'))
        if self.pending is not None or declaration is None or not state.get('availableManagementActions'):
            return
        from emo_master.ui.device_actions import DeviceActionDialog
        dialog = DeviceActionDialog(declaration, state['availableManagementActions'], self)
        if dialog.exec_() != QDialog.Accepted:
            return
        self.pendingKind, self.pendingScope = 'managementAction', self._scope()
        self.pending = self.reader.submit(self.c.window.runtimeClient.executeDeviceAction,
            self.c.session.document().project.projectId, self.stateRuntimeId, self.selectedId, deepcopy(state),
            dialog.action.currentData(), dialog.arguments)
        self.timer.start(40)

    def migrationPreview(self):
        if not self.c._canSync():
            return
        from emo_master.apps.designer.ui.device_migration import DeviceMigrationDialog
        dialog = DeviceMigrationDialog(self.c, self.requirements, self)
        dialog.exec_()
        self.refresh()

    def loadMetadata(self):
        if self.pending is not None:
            return
        reader = getattr(self.c.window.runtimeClient, "listDeviceTypes", None)
        if reader is None:
            self.status.setText("Runtime 不支持设备类型读取；已有配置保留")
            return
        self.status.setText("正在读取设备类型…")
        self.buttons["refresh"].setEnabled(False)
        self.pendingKind = "metadata"
        self.pendingScope = self._scope()
        self.pending = self.reader.submit(reader)
        self.timer.start(40)

    def _scope(self):
        client = self.c.window.runtimeClient
        return (getattr(client, "runtimeScope", None), id(getattr(client, "runtimeService", None)),
                id(getattr(client, "stub", None)), self.c.session.document().project.projectId)

    def readState(self, *, background=False):
        if self.pending is not None:
            return
        reader = getattr(self.c.window.runtimeClient, "getDeviceStates", None)
        if reader is None:
            self.status.setText("Runtime 不支持设备状态读取")
            return
        self.pendingKind = "state"
        self.buttons['refresh'].setEnabled(False)
        self.backgroundState = background
        self.pendingScope = self._scope()
        self.pending = self.reader.submit(reader, self.c.session.document().project.projectId)
        self.timer.start(40)

    def _metadataReady(self):
        if self.pending is None or not self.pending.done():
            return
        self.timer.stop()
        future, self.pending = self.pending, None
        self.buttons["refresh"].setEnabled(True)
        self._updateOnlineActions()
        self._inputStatus()
        try:
            if self.pendingScope != self._scope():
                self._tcpCategoryIntent = None
                raise ValueError("Runtime 连接已变化，请重新读取")
            if self.pendingKind == 'deviceDiagnostics':
                page = future.result()
                key = tuple(page[field] for field in ('runtimeInstanceId', 'projectId', 'projectLoadGeneration', 'deviceId'))
                previous = self.diagnosticPages.get(key, {}).get('records', [])
                self.diagnosticPages[key] = dict(page, records=(previous + page['records'])[-512:])
                self.showDiagnostics()
                return
            if self.pendingKind == 'test':
                # This request owns a device/candidate, unlike project-level reads.
                # Check before consuming success OR failure, without validating or
                # committing the current controls as a side effect of completion.
                try:
                    current = (self.selectedId, self._formValue())
                except ValueError:
                    current = None
                if current != self.pendingTest:
                    if self.status.text() == '正在测试当前连接草稿':
                        self.status.setText('设备或输入已变化；此前测试结果不用于当前输入')
                    return
                result = future.result()
                self.status.setText(result.get('message', '端点测试通过') + '；工程草稿和在线配置未改变')
                return
            if self.pendingKind == 'managementAction':
                self.status.setText('业务动作完成：' + json.dumps(future.result(), ensure_ascii=False))
                self.readState()
                return
            if self.pendingKind == 'networkInfo':
                if self._networkOpenRequest is not future:
                    return
                self._networkOpenRequest = None
                info = future.result()
                if self.pendingDevice != self.selectedId:
                    self.status.setText('已切换设备；请重新打开设备发现')
                    return
                if info.get('deviceSetupVersion', 0) < 1:
                    raise ValueError('Runtime 不支持离线设备发现，请升级 Runtime')
                self._openDeviceNetwork(info.get('projectId', ''), info['runtimeInstanceId'], discoveryOnly=True)
                return
            if self.pendingKind in {"state", "recover", "control"}:
                runtimeId, self.states = future.result()
                recoveredStateError = bool(self.stateError and
                    self.status.text() == "设备信息读取失败：" + self.stateError)
                self.stateRuntimeId = runtimeId
                self.stateScope, self.stateReadAt, self.stateError = self._scope(), monotonic(), ''
                self.stateNotice = ''
                self._showStates()
                keepInput = self.pendingKind == 'state' and getattr(self, 'backgroundState', False) and self.inputErrorControl is not None
                if not keepInput and (self.pendingKind != 'state' or not getattr(self, 'backgroundState', False) or recoveredStateError):
                    self.status.setText("会话已恢复；未连接设备、未重发读写" if self.pendingKind == "recover"
                                    else "设备控制完成；工程草稿未改变" if self.pendingKind == "control"
                                    else "在线状态已刷新")
                if not keepInput:
                    self.status.setToolTip("Runtime: " + runtimeId)
                self.readDiagnostics()
                return
            types, requirements, rejected = future.result()
            self.commitPending()
            self.deviceTypes, self.requirements = types, requirements
            self.refresh()
            intent, self._tcpCategoryIntent = self._tcpCategoryIntent, None
            if intent == self._scope() and self.isVisible():
                self.selectTcpCategory()
            self.status.setText(f"{len(types)} 个设备类型；{len(rejected)} 个声明被拒绝；工程草稿")
            self.status.setToolTip(str(rejected) if rejected else "")
            if callable(getattr(self.c.window.runtimeClient, 'getDeviceStates', None)):
                self.readState()
        except Exception as error:
            notice = ''
            if self.pendingKind == 'test':
                from .device_wizard import deviceErrorMessage
                self.status.setText(deviceErrorMessage(error))
                self.status.setToolTip(f"{getattr(error, 'code', '')}：{error}")
                return
            if self.pendingKind in {"state", "recover", "control"}:
                self.states = {}
                self.stateRuntimeId = ""
                self.stateNotice = ('当前工程未下发；请查看并写入 Runtime'
                    if getattr(error, 'code', '') == 'E_CONFIG_NOT_LOADED' else
                    'Runtime 当前加载的是其他工程；本地草稿仍可编辑'
                    if getattr(error, 'code', '') in {'E_CONFIG_PROJECT', 'E_PROJECT_NOT_LOADED'} else '')
                self.stateError = '' if self.stateNotice else str(error)
                self._showStates()
                notice = self.stateNotice
                if self.pendingKind == 'state' and getattr(self, 'backgroundState', False) and self.inputErrorControl is not None:
                    return
            self.status.setText(notice or "设备信息读取失败：" + str(error))

    def _showStates(self):
        from emo_master.core.project.snapshots import revisionOf
        from emo_master.core.project.devices import DeviceDefinition
        definitions = self.c.session.workflows.devices.get("definitions", {})
        labels = {"IDLE": "空闲", "BUSY": "执行中", "QUARANTINED": "已隔离", "CLOSED": "已关闭"}
        for row in range(self.table.rowCount()):
            key = self.table.item(row, 0).data(Qt.UserRole)
            state = self.states.get(key)
            item = self.table.item(row, 3)
            if state is None:
                draft = self.stateScope == self._scope() and self.stateRuntimeId and not self.stateError
                item.setText(self.stateNotice.split('；', 1)[0] if self.stateNotice else
                             '尚未写入 Runtime' if draft else '未读取在线状态')
                item.setToolTip("")
                for column in (5, 6):
                    self.table.item(row, column).setText("未获取")
                continue
            definition = definitions[key]
            declaration = self.deviceTypes.get(definition["deviceTypeId"])
            try:
                revision = revisionOf(declaration.validateDefinition(DeviceDefinition.model_validate(definition)).model_dump())
            except (AttributeError, ValueError):
                revision = None
            label = labels.get(state["state"], state["state"])
            if not definition.get('enabled', True) and definition['deviceTypeId'] == 'device.tcp.server' and not definition['connection'].get('channels'):
                label += ' · 未启用；待配置客户端通道'
            if state.get("admissionState") == "PAUSED":
                label += " · 手动暂停连接"
            elif state.get("admissionState") == "CONTROL_PENDING":
                label += " · 维护处理中"
            driver = state.get('driverState', {})
            if declaration and declaration.connectionLifetime == 'per_operation':
                label += ' · ' + ('事务执行中' if state['state'] == 'BUSY' else '事务就绪')
            elif definition['deviceTypeId'] == 'device.tcp.server':
                peers = sum(bool(channel.get('connected')) for channel in driver.get('channels', []))
                label += ' · ' + ((f'监听中，已连接 {peers} 个客户端' if peers else '监听中，等待客户端')
                                  if driver.get('ready') else '未监听')
            else:
                label += ' · ' + ('已连接' if state.get('lifecycleConnected') else '未连接')
            item.setText(label if revision == state["definitionRevision"] else label + " · 配置不同")
            owners = _activeOwnersText(state)
            self.table.item(row, 5).setText(f"{owners} / {state['queued']}")
            self.table.item(row, 6).setText(str(state.get('lifecycleError') or state.get('lastError') or '-'))
            item.setToolTip(f"排队：{state['queued']}\n操作 owner：{owners}\n代次：{state['generation']}"
                            f"\n建连恢复：{state.get('recovery', {}).get('state', '-')}"
                            f" · 尝试 {state.get('recovery', {}).get('attempt', 0)}"
                            f"\n显式保活：{state.get('explicitKeepAlive', 0)}"
                            f"\n触发流保活：{state.get('streamKeepAlive', False)}"
                            f"\n错误：{state.get('lifecycleError') or state.get('lastError') or '-'}")
        for key, item in self.instanceItems.items():
            row = next((r for r in range(self.table.rowCount()) if self.table.item(r, 0).data(Qt.UserRole) == key), None)
            if row is not None:
                item.setText(0, definitions[key]['name'] + ' · ' + self.table.item(row, 3).text())
        self._inputStatus()
        self._updateOnlineActions()
        self.showDiagnostics()

        status = getattr(self.c.window, 'communicationStatus', None)
        if status is not None:
            from emo_master.ui.communication_panel import tcpDeviceSummary
            status.setText(tcpDeviceSummary(definitions, self.states))

    def _updateOnlineActions(self):
        definition = self.c.session.workflows.devices.get('definitions', {}).get(self.selectedId, {})
        declaration = self.deviceTypes.get(definition.get('deviceTypeId'))
        capabilities = set(declaration.controlCapabilities) if declaration else set()
        state = self.states.get(self.selectedId)
        current = (self.stateScope is None or self.stateScope == self._scope()) and not self.stateError
        fresh = current and (not self.stateReadAt or monotonic() - self.stateReadAt < 6)
        online = bool(state and self.stateRuntimeId and self.pending is None and fresh)
        if 'publish' in self.buttons:
            self.buttons['publish'].setEnabled(self.pending is None and not self.c._devicePublishPending)
        self.buttons['business'].setVisible(bool(declaration and declaration.managementActions))
        self.buttons['business'].setEnabled(online and bool(state.get('availableManagementActions')))
        connected = bool(state and state.get('lifecycleConnected'))
        for key in ('connect', 'disconnect', 'maintenance', 'resume', 'recover'):
            self.buttons[key].setVisible(key in capabilities and (key not in {'connect', 'disconnect'} or (key == 'disconnect') == connected))
            self.buttons[key].setEnabled(online and key in state.get('availableControls', []))
        server = definition.get('deviceTypeId') == 'device.tcp.server'
        self.buttons['connect'].setText('启动服务' if server else '连接设备')
        self.buttons['disconnect'].setText('停止服务' if server else '断开连接')
        reason = ('设备未启用；请先配置启用状态' if definition and not definition.get('enabled', True) else
                  '尚未写入 Runtime；请查看并写入 Runtime' if not state and self.stateRuntimeId and fresh else
                  '在线信息未获取或已失效，请刷新' if not online else '')
        for key in ('connect', 'disconnect'):
            self.buttons[key].setToolTip(self.commandTitles[key] + ('；' + reason if reason else ''))
        self.buttons['network'].setEnabled(self.pending is None
            and definition.get('deviceTypeId') == 'device.camera.huaray_imv'
            and callable(getattr(self.c.window.runtimeClient, 'deviceNetworkCommand', None)))

    def refresh(self):
        self.loading = True
        try:
            self.tree.clear()
            allTypes = QTreeWidgetItem(["全部设备"])
            self.tree.addTopLevelItem(allTypes)
            categories = {}
            typeItems = {}
            self.instanceItems = {}
            for key, declaration in sorted(self.deviceTypes.items()):
                category = categories.get(declaration.category)
                if category is None:
                    category = QTreeWidgetItem([declaration.category])
                    self.tree.addTopLevelItem(category)
                    categories[declaration.category] = category
                child = QTreeWidgetItem([declaration.displayName])
                child.setIcon(0, deviceIcon(declaration.iconKey))
                child.setData(0, Qt.UserRole, key)
                category.addChild(child)
                typeItems[key] = child
            self.tree.expandAll()
            definitions = self.c.session.workflows.devices.get("definitions", {})
            self.table.setRowCount(len(definitions))
            selected = -1
            for row, (key, definition) in enumerate(definitions.items()):
                declaration = self.deviceTypes.get(definition["deviceTypeId"])
                state = "未读取在线状态" if declaration else "类型不可用"
                if not definition.get("enabled", True):
                    state = ("未启用；待配置客户端通道" if definition['deviceTypeId'] == 'device.tcp.server'
                             and not definition.get('connection', {}).get('channels') else "工程中禁用")
                values = (definition["name"], declaration.displayName if declaration else definition["deviceTypeId"],
                          str(len(self.catalog().references(key))), state,
                          {'on_first_use': '首次使用时连接', 'on_project_open': '打开工程时连接',
                           'on_project_run': '运行工程时连接', 'manual': '手动连接'}.get(
                              definition.get('policies', {}).get('connect', 'on_first_use'), '未声明'),
                          "未获取", "未获取", "")
                instance = QTreeWidgetItem([definition['name'] + ' · ' + state])
                instance.setIcon(0, deviceIcon(declaration.iconKey if declaration else 'default'))
                instance.setData(0, Qt.UserRole + 1, key)
                parent = typeItems.get(definition['deviceTypeId'], allTypes)
                parent.addChild(instance)
                self.instanceItems[key] = instance
                for col, value in enumerate(values):
                    item = QTableWidgetItem(value)
                    item.setData(Qt.UserRole, key)
                    if col == 0:
                        item.setIcon(deviceIcon(declaration.iconKey if declaration else 'default'))
                    self.table.setItem(row, col, item)
                action = QToolButton()
                action.setIcon(icon('sliders-horizontal'))
                action.setToolTip('查看设备详情')
                action.setAccessibleName('查看设备详情')
                action.clicked.connect(lambda _checked=False, row=row: self.table.setCurrentCell(row, 0))
                self.table.setCellWidget(row, 7, action)
                if key == self.selectedId:
                    selected = row
            if selected >= 0:
                self.table.selectRow(selected)
            else:
                self.selectedId = None
            self._showDetails()
            if self.states or self.stateRuntimeId or self.stateError:
                self._showStates()
        finally:
            self.loading = False
        self._filter(None, None)

    def _filter(self, item, previous):
        if self.loading:
            return
        try:
            self.commitPending()
        except ValueError as error:
            self.status.setText(str(error))
            self.tree.blockSignals(True)
            self.tree.setCurrentItem(previous)
            self.tree.blockSignals(False)
            self.search.blockSignals(True)
            self.search.setText(self.lastSearch)
            self.search.blockSignals(False)
            return
        self.lastSearch = self.search.text()
        typeId = item.data(0, Qt.UserRole) if item else None
        instanceId = item.data(0, Qt.UserRole + 1) if item else None
        category = item.text(0) if item and item.parent() is None and item.text(0) != "全部设备" else None
        definitions = self.c.session.workflows.devices.get("definitions", {})
        visible = []
        self.loading = True
        for row in range(self.table.rowCount()):
            key = self.table.item(row, 0).data(Qt.UserRole)
            definition = definitions[key]
            declaration = self.deviceTypes.get(definition["deviceTypeId"])
            hidden = bool(typeId and typeId != definition["deviceTypeId"])
            if category:
                hidden = not declaration or declaration.category != category
            hidden |= self.search.text().strip().casefold() not in definition['name'].casefold()
            self.table.setRowHidden(row, hidden)
            self.instanceItems[key].setHidden(self.search.text().strip().casefold() not in definition['name'].casefold())
            if not hidden:
                visible.append((row, key))
        if self.selectedId not in {key for _row, key in visible}:
            self.selectedId = None
            self.table.setCurrentCell(-1, -1)
            self.table.clearSelection()
        instanceRow = next((row for row, key in visible if key == instanceId), None)
        if instanceRow is not None:
            self.selectedId = instanceId
            self.table.setCurrentCell(instanceRow, 0)
        self.loading = False
        self._showDetails()

    def _select(self, row, _col, oldRow, _oldCol):
        if self.loading:
            return
        try:
            self.commitPending()
        except ValueError as error:
            self._showInputError(error)
            self.loading = True
            self.table.setCurrentCell(oldRow, 0)
            self.loading = False
            return
        item = self.table.item(row, 0)
        self.selectedId = item.data(Qt.UserRole) if item else None
        if self.selectedId in self.instanceItems:
            self.tree.blockSignals(True)
            self.tree.setCurrentItem(self.instanceItems[self.selectedId])
            self.tree.blockSignals(False)
        self._showDetails()

    def _showDetails(self):
        self._populatingDetails = True
        if self.inputErrorControl is not None and self.status.text() == self.inputErrorMessage:
            self.status.clear()
            self.status.setToolTip('')
        self.inputErrorControl = None
        self.original = deepcopy(self.c.session.workflows.devices.get("definitions", {}).get(self.selectedId))
        declaration = self.deviceTypes.get(self.original["deviceTypeId"]) if self.original else None
        self.details.setEnabled(bool(declaration))
        self.buttons["new"].setEnabled(bool(self.deviceTypes))
        self.buttons["delete"].setEnabled(self.original is not None)
        self.buttons['export'].setEnabled(self.original is not None)
        capabilities = set(declaration.capabilities) if declaration else set()
        controls = set(declaration.controlCapabilities) if declaration else set()
        supported = dict(connect='connect' in controls, disconnect='disconnect' in controls,
            maintenance='maintenance' in controls, resume='resume' in controls,
            recover='recover' in controls, test='test_connection' in controls and callable(getattr(self.c.window.runtimeClient, 'deviceNetworkCommand', None)),
            diagnostics=bool(declaration and (declaration.deviceTypeId == 'device.camera.huaray_imv' and 'capture' in capabilities
                or declaration.deviceTypeId == 'device.plc.mitsubishi_slmp' and 'read' in capabilities)),
            network=bool(declaration and declaration.deviceTypeId == 'device.camera.huaray_imv'
                         and callable(getattr(self.c.window.runtimeClient, 'deviceNetworkCommand', None))))
        for key, available in supported.items():
            self.buttons[key].setEnabled(available)
            self.buttons[key].setToolTip(self.commandTitles[key] + ('' if available else '（所选类型不支持或 Runtime 不提供）'))
        self.readParametersButton.setEnabled(bool(declaration and declaration.deviceTypeId == 'device.camera.huaray_imv' and 'capture' in capabilities))
        self.detailTitle.setText(self.original['name'] if self.original else '未选择设备')
        self.detailSplitter.setVisible(self.original is not None)
        self._updateOnlineActions()
        self.typeInfo.setText(f'{declaration.displayName} · {declaration.deviceTypeId} · 驱动 {declaration.version}' if declaration else '')
        for key in ("apply", "revert"):
            self.buttons[key].setEnabled(bool(declaration))
        self.name.setText(self.original["name"] if self.original else "")
        self.description.setText(self.original.get("description", "") if self.original else "")
        self.enabled.setChecked(bool(self.original and self.original.get("enabled", True)))
        self.identity.setText(self.selectedId or "")
        if declaration:
            for key, editor in self.forms.items():
                from .device_wizard import configurationSchema
                schema = configurationSchema(getattr(declaration, key + "Schema"))
                from .device_wizard import configurationForm
                from .tcp_device_form import TcpConnectionForm
                if isinstance(editor, TcpConnectionForm) != (declaration.deviceTypeId == 'device.tcp.server' and key == 'connection'):
                    newEditor = configurationForm(schema, self.original.get(key, {}),
                        deviceTypeId=declaration.deviceTypeId, section=key)
                    self.formScrolls[key].takeWidget()
                    editor.deleteLater()
                    self.formScrolls[key].setWidget(newEditor)
                    self.forms[key] = editor = newEditor
                else:
                    editor.setSchema(schema, self.original.get(key, {}))
                for name, spec in schema["properties"].items():
                    if len(spec.get("enum", [])) == 1:
                        editor.setFieldEnabled(name, False)
                editor.valuesChanged.connect(self._inputStatus, Qt.UniqueConnection)
        else:
            for editor in self.forms.values():
                editor.setSchema({"type": "object", "properties": {}}, {})
        references = self.catalog().references(self.selectedId) if self.selectedId else []
        from html import escape
        from urllib.parse import quote
        rows = []
        for workflow, nodeId, slot in references:
            workflowValue = self.c.session.workflows.get(workflow)
            node = next(n for n in workflowValue.nodes if n['nodeId'] == nodeId)
            sources = [escape('channelId · ' + str(node.get('deviceBindings', {}).get(slot, {}).get('selectors', {}).get('channelId')))] if node.get('deviceBindings', {}).get(slot, {}).get('selectors') else []
            if declaration:
                for path in declaration.overridePaths:
                    dynamic = node.get('deviceValueBindings', {}).get(slot, {}).get(path)
                    value = node.get('deviceOverrides', {}).get(slot, {})
                    missing = object()
                    for part in path.split('.'):
                        value = value.get(part, missing) if isinstance(value, dict) else missing
                    if dynamic:
                        source = '动态绑定：' + str(dynamic.get('variableId') or dynamic.get('inputName'))
                    elif value is not missing:
                        source = '本节点覆写：' + str(value)
                    else:
                        value = self.original.get('defaults', {})
                        for part in path.split('.'):
                            value = value.get(part) if isinstance(value, dict) else None
                        source = '继承设备：' + str(value)
                    sources.append(escape(path + ' · ' + source))
            rows.append(f'<tr><td><a href="{quote(workflow, safe="")}/{quote(nodeId, safe="")}">'
                f'{escape(workflowValue.name)} / {escape(node.get("displayName") or nodeId)}</a></td>'
                f'<td>{escape(slot)}</td><td>{"<br>".join(sources)}</td></tr>')
        self.references.setHtml('<table cellpadding="4"><tr><th>流程 / 节点</th><th>槽位</th><th>参数来源</th></tr>'
            + ''.join(rows) + '</table>' if rows else '无节点引用')
        self.showDiagnostics()
        try:
            self.formBaseline = self._formValue() if declaration else None
        except ValueError:
            self.formBaseline = None
        self._populatingDetails = False
        self._inputStatus()

    def _formValue(self):
        value = deepcopy(self.original)
        value.update(name=self.name.text().strip(), description=self.description.text(), enabled=self.enabled.isChecked())
        for key, editor in self.forms.items():
            value[key] = editor.getValues()
        return value

    def commitPending(self):
        if not self.original or self.original["deviceTypeId"] not in self.deviceTypes:
            return
        from .device_wizard import validateDeviceDefinition
        declaration = self.deviceTypes[self.original["deviceTypeId"]]
        try:
            value = self._formValue()
        except ValueError:
            # Parsing can fail before the baseline comparison; reveal the invalid field.
            validateDeviceDefinition(declaration, self.name, self.forms, self.tabs, base=self.original)
            raise
        if value != self.formBaseline:
            value = validateDeviceDefinition(declaration, self.name, self.forms, self.tabs, base=value)
            self.catalog().put(value, deviceId=self.selectedId)
            self.original = deepcopy(self.c.session.workflows.devices["definitions"][self.selectedId])
            self.formBaseline = deepcopy(value)
            self.detailTitle.setText(value['name'])
            if self.selectedId in self.instanceItems:
                self.instanceItems[self.selectedId].setText(0, value['name'])
            for row in range(self.table.rowCount()):
                if self.table.item(row, 0).data(Qt.UserRole) == self.selectedId:
                    self.table.item(row, 0).setText(value['name'])

    def apply(self):
        try:
            self.c.sync()
            self.refresh()
            self.status.setText("已应用到工程草稿；在线配置未改变")
        except ValueError as error:
            self._showInputError(error)

    def _showInputError(self, error):
        from .device_wizard import deviceValidationMessage, deviceErrorDetails, deviceErrorControl
        self.inputErrorMessage = deviceValidationMessage(error)
        self.status.setText(self.inputErrorMessage)
        self.status.setToolTip(deviceErrorDetails(error))
        self.inputErrorControl = deviceErrorControl(self.name, self.forms, str(error))
        self._inputStatus()

    def revert(self):
        self._showDetails()
        self.name.setStyleSheet('')
        self.name.setAccessibleDescription('')
        self.status.setText("已放弃尚未应用的属性修改；已应用的工程草稿保持不变")

    def _acceptNewDevice(self, value, parent):
        from emo_master.core.project.device_identity import matchingDevices
        definitions = self.c.session.workflows.devices.get('definitions', {})
        matches = matchingDevices(value, definitions, self.deviceTypes[value['deviceTypeId']])
        if len(matches) > 1:
            raise ValueError('存在多个相同标识的设备，请取消后从设备列表检查并选择')
        if matches:
            key = matches[0]
            if QMessageBox.question(parent, '复用已有设备',
                    f"此设备标识已用于「{definitions[key]['name']}」\n设备 ID：{key}\n"
                    "复用会选择已有设备，不覆盖其名称或参数。选择“否”可继续修改当前表单。",
                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
                return False
            self.selectedId = key
        else:
            self.selectedId = self.catalog().put(value)
        return True

    def createDevice(self):
        try:
            self.c.sync()
            if not self.deviceTypes:
                return
            from .device_wizard import DeviceTypeDialog
            chooser = DeviceTypeDialog(self.deviceTypes, self)
            if chooser.exec_() != chooser.Accepted:
                return
            declaration = self.deviceTypes[chooser.selection.currentData()]
            from .device_wizard import DeviceDefinitionDialog
            dialog = DeviceDefinitionDialog(declaration, self, client=self.c.window.runtimeClient,
                beforeAccept=lambda value: self._acceptNewDevice(value, dialog))
            if dialog.exec_() != dialog.Accepted:
                return
            self.refresh()
            self.c.chrome.update()
            self.status.setText('已加入工程草稿，尚未写入 Runtime' + ('；设备暂不启用'
                if not self.c.session.workflows.devices['definitions'][self.selectedId].get('enabled', True) else ''))
        except ValueError as error:
            self.status.setText(str(error))

    def deleteDevice(self):
        if not self.selectedId:
            return
        definition = self.c.session.workflows.devices['definitions'][self.selectedId]
        if QMessageBox.question(self, "删除设备",
                                f"从工程草稿删除「{definition['name']}」？\n设备 ID：{self.selectedId}\n"
                                "在线设备不会改变；仍被节点引用的设备不能删除。",
                                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        try:
            self.catalog().remove(self.selectedId)
            self.selectedId = None
            self.refresh()
            self.c.chrome.update()
        except ValueError as error:
            self.status.setText(str(error))

    def shutdown(self):
        # Project replacement retires the old editor without applying it to the
        # newly loaded project. Ordinary dialog close keeps the reader reusable.
        self.hide()
        self.timer.stop()
        self.stateTimer.stop()
        self.reader.shutdown(wait=False, cancel_futures=True)
