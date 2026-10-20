"""Project-owned device editor. Metadata reads never discover or open hardware."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from time import monotonic

from PySide2.QtCore import Qt, QTimer
from PySide2.QtWidgets import (
    QWidget, QVBoxLayout, QSplitter, QTreeWidget, QTreeWidgetItem,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView, QLabel,
    QLineEdit, QCheckBox, QFormLayout, QTabWidget, QScrollArea, QToolButton,
    QInputDialog, QMessageBox, QDialog, QDialogButtonBox, QTextBrowser, QFileDialog,
    QApplication, QFrame, QGridLayout,
)

from emo_master.apps.designer.state.device_catalog import DeviceCatalogEditor
from emo_master.apps.designer.ui.icon_map import icon, deviceIcon
from emo_master.apps.designer.ui.param_form import SchemaParamForm


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
        self.lastSearch = ''
        self.loading = False
        self.reader = ThreadPoolExecutor(max_workers=1, thread_name_prefix="device-metadata")
        self.pending = None
        self.pendingKind = "metadata"
        self.pendingScope = None
        self.states = {}
        self.stateRuntimeId = ""
        self.stateScope = None
        self.stateReadAt = 0.0
        self.stateError = ''
        self.discoveredInfo = {}
        self.stateTimer = QTimer(self)
        self.stateTimer.setInterval(2000)
        self.stateTimer.timeout.connect(self._refreshKnownState)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._metadataReady)
        outerLayout = QVBoxLayout(self)
        # The detail splitters have useful minimum sizes, but must not force the
        # whole dialog off a small/high-DPI screen. Keep the action footer fixed
        # and allow the workspace body to scroll when those minima cannot fit.
        self.contentScroll = QScrollArea()
        self.contentScroll.setWidgetResizable(True)
        self.contentScroll.setFrameShape(QFrame.NoFrame)
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 0, 0)
        self.contentScroll.setWidget(content)
        outerLayout.addWidget(self.contentScroll, 1)
        commands = QGridLayout()
        commands.setSpacing(2)
        deviceCommands = QGridLayout()
        deviceCommands.setSpacing(2)
        self.buttons = {}
        shortLabels = dict(new='新建设备', delete='删除设备', apply='应用到草稿', revert='放弃当前修改',
            save='保存工程', import_='导入设备', export='导出设备', refresh='刷新列表', state='读取状态',
            network='设备发现', compare='在线对比', bindings='资源与绑定', migration='旧节点迁移',
            diagnostics='设备诊断', test='测试连接', connect='连接设备', disconnect='断开连接',
            maintenance='暂停设备', recover='恢复会话')
        shortLabels['import'] = shortLabels.pop('import_')
        commandCount = deviceCommandCount = 0
        for key, title, glyph, callback in (
            ("new", "新建设备", "plus", self.createDevice),
            ("delete", "删除设备", "trash-2", self.deleteDevice),
            ("apply", "应用到工程草稿", "save", self.apply),
            ("revert", "放弃属性修改", "x", self.revert),
            ("save", "保存工程", "save", self.saveProject),
            ("import", "导入设备配置包", "upload", self.importDevices),
            ("export", "导出所选设备配置包", "download", self.exportDevices),
            ("refresh", "刷新设备与在线状态", "refresh-cw", self.loadMetadata),
            ("state", "读取在线设备状态", "logs", self.readState),
            ("network", "Runtime 设备发现 / 实物 IP", "search", self.deviceNetwork),
            ("compare", "与 Runtime 比较", "git-compare", self.compareOnline),
            ("bindings", "工程资源 / 现场绑定", "link", self.resourceBindings),
            ("migration", "旧设备节点迁移预览", "list", self.migrationPreview),
            ("diagnostics", "设备诊断", "sliders-horizontal", self.diagnostics),
            ("test", "测试当前连接草稿", "shield-check", self.testConnection),
            ("connect", "连接并恢复使用", "plug", lambda: self.control("connect")),
            ("disconnect", "断开并暂停连接", "unplug", lambda: self.control("disconnect")),
            ("maintenance", "暂停所选设备并取消其请求", "pause", lambda: self.control("maintenance")),
            ("recover", "恢复设备会话", "refresh-cw", self.recover),
        ):
            button = QToolButton()
            button.setMinimumHeight(32)
            button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
            button.setText(shortLabels[key])
            button.setIcon(icon(glyph))
            button.setToolTip(title)
            button.setAccessibleName(title)
            button.clicked.connect(callback)
            self.buttons[key] = button
            if key in {'network', 'diagnostics', 'test', 'connect', 'disconnect', 'maintenance', 'recover'}:
                deviceCommands.addWidget(button, deviceCommandCount // 2, deviceCommandCount % 2)
                deviceCommandCount += 1
            else:
                commands.addWidget(button, commandCount // 4, commandCount % 4)
                commandCount += 1
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
        self.difference.linkActivated.connect(lambda _link: self.compareOnline())
        layout.addWidget(self.difference)
        self.splitter = QSplitter()
        layout.addWidget(self.splitter, 1)
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
        detailLayout = QVBoxLayout(self.details)
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
            detailLayout.addWidget(label)
        detailLayout.addLayout(deviceCommands)
        basic = QWidget()
        basicLayout = QVBoxLayout(basic)
        form = QFormLayout()
        self.name = QLineEdit()
        self.description = QLineEdit()
        self.enabled = QCheckBox("启用")
        self.identity = QLabel()
        self.identity.setWordWrap(True)
        self.identity.setTextInteractionFlags(Qt.TextSelectableByMouse)
        form.addRow("名称", self.name)
        form.addRow("说明", self.description)
        form.addRow("", self.enabled)
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
        for key, title in (("connection", "连接"), ("defaults", "工作参数"), ("policies", "策略")):
            editor = SchemaParamForm()
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setWidget(editor)
            self.tabs.addTab(scroll, title)
            self.forms[key] = editor
        self.infoTabs = QTabWidget()
        self.references = QTextBrowser()
        self.references.setOpenLinks(False)
        self.references.anchorClicked.connect(self.navigateReference)
        self.infoTabs.addTab(self.references, "节点引用")
        self.onlineState = QTextBrowser()
        self.infoTabs.addTab(self.onlineState, "在线状态")
        self.logs = QTextBrowser()
        self.infoTabs.addTab(self.logs, "诊断日志")
        self.infoTabs.currentChanged.connect(lambda _index: self.showDiagnostics())
        self.detailSplitter = QSplitter(Qt.Vertical)
        self.detailSplitter.addWidget(self.tabs)
        self.detailSplitter.addWidget(self.infoTabs)
        self.detailSplitter.setSizes([260, 180])
        detailLayout.addWidget(self.detailSplitter)
        self.splitter.addWidget(self.details)
        self.splitter.setSizes([180, 430, 400])
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        for title, callback in [('读取实际参数', lambda: self.diagnostics(readParameters=True)),
                                ('保存工程', self.saveProject), ('写入 Runtime', self.compareOnline)]:
            button = buttons.addButton(title, QDialogButtonBox.ActionRole)
            button.clicked.connect(callback)
            if title == '读取实际参数':
                self.readParametersButton = button
        self.closeButton = buttons.button(QDialogButtonBox.Close)
        self.closeButton.setText("保留草稿并关闭")
        self.draftHelp = QLabel("修改先进入工程草稿；保存写入本地文件，在线配置需单独写入 Runtime。"
            "切换设备或关闭会保留有效修改；放弃当前修改仅撤回尚未应用的属性。")
        self.draftHelp.setWordWrap(True)
        outerLayout.addWidget(self.draftHelp)
        buttons.rejected.connect(self.reject)
        outerLayout.addWidget(buttons)
        self.refresh()

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
            self.status.setText(str(error))
            return
        super().done(result)
        self.c.chrome.update()

    def catalog(self):
        return DeviceCatalogEditor(self.c.session, self.deviceTypes, self.requirements)

    def saveProject(self):
        if self.c._canSync():
            self.c.window.saveProjectAction()

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
            self.status.setText('已导入工程草稿；请填写现场字段后显式启用设备')
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
        freshness = ('读取失败：' + self.stateError if self.stateError else
                     f'更新于 {max(0, int(monotonic() - self.stateReadAt))} 秒前' if self.stateReadAt else '未获取在线状态')
        values = [('连接', {'CONNECTED': '已连接', 'DISCONNECTED': '未连接', 'FAULTED': '连接故障'}.get(state.get('connectionState'), '未知')),
                  ('执行状态', {'IDLE': '空闲', 'BUSY': '执行中', 'QUARANTINED': '已隔离', 'CLOSED': '已关闭'}.get(state.get('state'), '未知')),
                  ('当前任务', state.get('activeOwner') or '-'), ('排队请求', state.get('queued', '-')),
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
        refs = {(w, n) for w, n, _s in self.catalog().references(self.selectedId)}
        entries = getattr(self.c.window, "logEntries", [])
        projectId = self.c.session.document().project.projectId
        token = getattr(self.c.window, '_runtimeLogProjectToken', '')
        self.logs.setPlainText("\n".join(entry.displayLine() for entry in entries
            if entry.projectId == projectId and entry.projectInstanceToken == token
            and ((entry.workflowId, entry.nodeId) in refs or entry.payload.get('deviceId') == self.selectedId)) or "无可用诊断日志")

    def recordDiagnostic(self, message, deviceId):
        from time import time
        from .log_dialog import StructuredLogEntry
        entry = StructuredLogEntry(timestampMs=int(time() * 1000), level='INFO', message=message, source='device',
            projectId=self.c.session.document().project.projectId,
            projectInstanceToken=self.c.window._runtimeLogProjectToken, payload=dict(deviceId=deviceId))
        self.c.window._storeRuntimeLogEntry(entry, entry.displayLine())

    def deviceNetwork(self):
        if not self.selectedId or not self.stateRuntimeId or self.pending is not None:
            self.status.setText('请选择设备并先读取在线状态')
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
        from .device_network import DeviceNetworkDialog
        before, scope = definition.model_dump(), self._scope()
        dialog = DeviceNetworkDialog(self.c.window.runtimeClient, scope[-1], self.stateRuntimeId,
            self.selectedId, self.states.get(self.selectedId, {}), self)
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
            from emo_master.core.project.devices import DeviceDefinition
            definition = self.deviceTypes[self.original['deviceTypeId']].validateDefinition(
                DeviceDefinition.model_validate(self._formValue())).model_dump()
        except (ValueError, KeyError) as error:
            self.status.setText(str(error))
            return
        if QMessageBox.question(self, '测试连接', '测试当前连接草稿？将实际连接设备，不拍照、不写 PLC，不保存或发布草稿。',
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

    def compareOnline(self):
        if not self.c._canSync():
            return
        from emo_master.apps.designer.ui.project_configuration import ProjectConfigurationDialog
        dialog = ProjectConfigurationDialog(self.c, self)
        dialog.exec_()

    def resourceBindings(self):
        if not self.c._canSync():
            return
        from emo_master.apps.designer.ui.resource_bindings import ResourceBindingsDialog
        operators = {item["operatorId"]: item for item in self.c.window.operatorCatalogController.getCatalog()}
        dialog = ResourceBindingsDialog(self.c, self.deviceTypes, operators, self)
        dialog.exec_()
        self.refresh()

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
        try:
            if self.pendingScope != self._scope():
                raise ValueError("Runtime 连接已变化，请重新读取")
            if self.pendingKind == 'test':
                future.result()
                self.status.setText('连接测试通过；工程草稿和在线配置未改变，原有连接保持不变')
                return
            if self.pendingKind in {"state", "recover", "control"}:
                runtimeId, self.states = future.result()
                recoveredStateError = bool(self.stateError and
                    self.status.text() == "设备信息读取失败：" + self.stateError)
                self.stateRuntimeId = runtimeId
                self.stateScope, self.stateReadAt, self.stateError = self._scope(), monotonic(), ''
                self._showStates()
                if self.pendingKind != 'state' or not getattr(self, 'backgroundState', False) or recoveredStateError:
                    self.status.setText("会话已恢复；未连接设备、未重发读写" if self.pendingKind == "recover"
                                    else "设备控制完成；工程草稿未改变" if self.pendingKind == "control"
                                    else "在线状态已读取；工程草稿未改变")
                self.status.setToolTip("Runtime: " + runtimeId)
                if self.pendingKind == 'control':
                    self.recordDiagnostic(self.status.text(), self.pendingDevice)
                    self.showDiagnostics()
                return
            types, requirements, rejected = future.result()
            self.commitPending()
            self.deviceTypes, self.requirements = types, requirements
            self.refresh()
            self.status.setText(f"{len(types)} 个设备类型；{len(rejected)} 个声明被拒绝；工程草稿")
            self.status.setToolTip(str(rejected) if rejected else "")
            if callable(getattr(self.c.window.runtimeClient, 'getDeviceStates', None)):
                self.readState()
        except Exception as error:
            if self.pendingKind in {"state", "recover", "control"}:
                self.states = {}
                self.stateRuntimeId = ""
                self.stateError = str(error)
                self._showStates()
            self.status.setText("设备信息读取失败：" + str(error))

    def _showStates(self):
        from emo_master.core.project.snapshots import revisionOf
        from emo_master.core.project.devices import DeviceDefinition
        definitions = self.c.session.workflows.devices.get("definitions", {})
        labels = {"IDLE": "空闲", "BUSY": "执行中", "QUARANTINED": "已隔离", "CLOSED": "已关闭"}
        different = False
        for row in range(self.table.rowCount()):
            key = self.table.item(row, 0).data(Qt.UserRole)
            state = self.states.get(key)
            item = self.table.item(row, 3)
            if state is None:
                item.setText("未读取在线状态")
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
            if state.get("admissionState") == "PAUSED":
                label += " · 手动暂停连接"
            elif state.get("admissionState") == "CONTROL_PENDING":
                label += " · 维护处理中"
            if state.get("connectionState"):
                label += " · " + {"CONNECTED": "已连接", "DISCONNECTED": "未连接", "FAULTED": "连接故障"}.get(
                    state["connectionState"], state["connectionState"])
            item.setText(label if revision == state["definitionRevision"] else label + " · 配置不同")
            different |= revision != state["definitionRevision"]
            self.table.item(row, 5).setText(f"{state['activeOwner'] or '-'} / {state['queued']}")
            self.table.item(row, 6).setText(str(state.get('lifecycleError') or state.get('lastError') or '-'))
            item.setToolTip(f"排队：{state['queued']}\n占用 Job：{state['activeOwner'] or '-'}\n代次：{state['generation']}"
                            f"\n建连恢复：{state.get('recovery', {}).get('state', '-')}"
                            f" · 尝试 {state.get('recovery', {}).get('attempt', 0)}"
                            f"\n显式保活：{state.get('explicitKeepAlive', 0)}"
                            f"\n触发流保活：{state.get('streamKeepAlive', False)}"
                            f"\n错误：{state.get('lifecycleError') or state.get('lastError') or '-'}")
        for key, item in self.instanceItems.items():
            row = next((r for r in range(self.table.rowCount()) if self.table.item(r, 0).data(Qt.UserRole) == key), None)
            if row is not None:
                item.setText(0, definitions[key]['name'] + ' · ' + self.table.item(row, 3).text())
        self.difference.setText('设备工程与在线配置有差异 · <a href="compare">查看对比</a>' if different
            else ('设备状态已读取 · <a href="compare">比较完整工程</a>' if self.stateRuntimeId else '未获取在线状态'))
        self.showDiagnostics()

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
                    state = "工程中禁用"
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
            if self.states:
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
            self.status.setText(str(error))
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
        self.original = deepcopy(self.c.session.workflows.devices.get("definitions", {}).get(self.selectedId))
        declaration = self.deviceTypes.get(self.original["deviceTypeId"]) if self.original else None
        self.details.setEnabled(bool(declaration))
        self.buttons["new"].setEnabled(bool(self.deviceTypes))
        self.buttons["delete"].setEnabled(self.original is not None)
        self.buttons['export'].setEnabled(self.original is not None)
        capabilities = set(declaration.capabilities) if declaration else set()
        supported = dict(connect='connect' in capabilities, disconnect='disconnect' in capabilities,
            maintenance=bool(capabilities & {'connect', 'disconnect', 'capture', 'read', 'write'}),
            recover=bool(capabilities), test='connect' in capabilities and callable(getattr(self.c.window.runtimeClient, 'deviceNetworkCommand', None)),
            diagnostics=bool(declaration and (declaration.deviceTypeId == 'device.camera.huaray_imv' and 'capture' in capabilities
                or declaration.deviceTypeId == 'device.plc.mitsubishi_slmp' and 'read' in capabilities)),
            network=bool(declaration and declaration.deviceTypeId == 'device.camera.huaray_imv' and 'discover' in capabilities))
        for key, available in supported.items():
            self.buttons[key].setEnabled(available)
            self.buttons[key].setToolTip(self.buttons[key].accessibleName() + ('' if available else '（所选类型不支持或 Runtime 不提供）'))
        self.readParametersButton.setEnabled(bool(declaration and declaration.deviceTypeId == 'device.camera.huaray_imv' and 'capture' in capabilities))
        self.detailTitle.setText(self.original['name'] if self.original else '未选择设备')
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
                editor.setSchema(schema, self.original.get(key, {}))
                for name, spec in schema["properties"].items():
                    if len(spec.get("enum", [])) == 1:
                        editor._controls[name].setEnabled(False)
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
            sources = []
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

    def apply(self):
        try:
            self.c.sync()
            self.refresh()
            self.status.setText("已应用到工程草稿；在线配置未改变")
        except ValueError as error:
            self.status.setText(str(error))

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
            keys = list(self.deviceTypes)
            labels = [f"{self.deviceTypes[key].displayName} ({key})" for key in keys]
            if not labels:
                return
            label, ok = QInputDialog.getItem(self, "新建设备", "设备类型", labels, 0, False)
            if not ok:
                return
            declaration = self.deviceTypes[keys[labels.index(label)]]
            from .device_wizard import DeviceDefinitionDialog
            dialog = DeviceDefinitionDialog(declaration, self, client=self.c.window.runtimeClient,
                beforeAccept=lambda value: self._acceptNewDevice(value, dialog))
            if dialog.exec_() != dialog.Accepted:
                return
            self.refresh()
            self.c.chrome.update()
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
