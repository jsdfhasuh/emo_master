"""Offline device and node drafts with an atomic Finish boundary."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from uuid import uuid4

from PySide2.QtCore import QTimer, Qt
from PySide2.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QLabel, QLineEdit,
    QDialogButtonBox, QTabWidget, QScrollArea, QWidget, QComboBox, QPushButton, QMessageBox, QCheckBox,
)

from emo_master.apps.designer.state.device_catalog import (DeviceCatalogEditor, changeParameterSource,
    migrateLegacySources, migrateLegacyResourceTargets)
from emo_master.apps.designer.state.schema_utils import applySchemaDefaults
from emo_master.apps.designer.ui.param_form import SchemaParamForm
from emo_master.core.project.devices import DeviceDefinition


def configurationSchema(schema):
    """Add form-only hints without changing the device declaration or enum data."""
    schema = deepcopy(schema)
    policyLabels = {
        'connect': {'on_first_use': '首次使用时连接', 'on_project_open': '打开工程时连接',
                    'on_project_run': '运行工程时连接', 'manual': '手动连接'},
        'release': {'on_project_close': '关闭工程时释放', 'when_unused': '不再使用时释放',
                    'idle_timeout': '空闲超时后释放', 'manual': '手动释放'},
    }
    for key, spec in schema.get("properties", {}).items():
        if spec.get("xUnit"):
            spec["title"] = f"{spec.get('title', key)} ({spec['xUnit']})"
        if key not in schema.get("required", []) and "default" not in spec:
            spec["xOptionalPresence"] = True
        if key in policyLabels:
            labels = {value: label for value, label in policyLabels[key].items() if value in spec.get('enum', [])}
            spec['xOptionLabels'] = {**labels, **spec.get('xOptionLabels', {})}
    return schema


def configurationForm(schema, values, *, deviceTypeId=None, section=None):
    schema = configurationSchema(schema)
    if deviceTypeId == 'device.tcp.server' and section == 'connection':
        from .tcp_device_form import TcpConnectionForm
        form = TcpConnectionForm()
    else:
        form = SchemaParamForm(deviceMode=deviceTypeId is not None)
    form.setSchema(schema, values)
    for key, spec in schema.get("properties", {}).items():
        if len(spec.get("enum", [])) == 1:
            form.setFieldEnabled(key, False)
    return form


def scrollWidget(widget):
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setWidget(widget)
    return scroll


def _showControlTab(tabs, control):
    if tabs is not None:
        for index in range(tabs.count()):
            page = tabs.widget(index)
            if page is control or page.isAncestorOf(control):
                tabs.setCurrentIndex(index)
                break


def validateDeviceDefinition(declaration, nameControl, forms, tabs=None, *, base=None):
    """Validate editor fields before core validation, retaining existing metadata."""
    name = nameControl.text().strip()
    message = '设备名称不能为空' if not name else '设备名称不能超过 160 个字符' if len(name) > 160 else ''
    nameControl.setAccessibleDescription(message)
    nameControl.setStyleSheet('border: 1px solid #d93025;' if message else '')
    if message:
        _showControlTab(tabs, nameControl)
        nameControl.setFocus(Qt.OtherFocusReason)
        raise ValueError(message)
    for form in forms.values():
        message = form.validationMessage()
        if message:
            _showControlTab(tabs, form)
            form.validationMessage()  # Focus the invalid control after revealing its tab.
            raise ValueError(message)
    payload = deepcopy(base) if base is not None else dict(
        deviceTypeId=declaration.deviceTypeId, driverVersion=declaration.version)
    payload.update(name=name, **{key: form.getValues() for key, form in forms.items()})
    if (declaration.deviceTypeId == 'device.tcp.server' and payload.get('enabled', True)
            and not payload['connection'].get('channels')):
        form = forms['connection']
        _showControlTab(tabs, form)
        form.showChannelError('请添加至少一个客户端通道')
        raise ValueError('请添加至少一个客户端通道')
    try:
        return declaration.validateDefinition(DeviceDefinition.model_validate(payload)).model_dump()
    except ValueError as error:
        if str(error).startswith('when_unused requires on_first_use'):
            message = '释放策略：选择“不再使用时释放”时，连接策略必须为“首次使用时连接”'
            form = forms['policies']
            _showControlTab(tabs, form)
            form._showFieldError('release', message)
            raise ValueError(message) from error
        from emo_master.core.workflow.errors import locatedValidationError
        raise locatedValidationError(error, 'E_DEVICE_DEFINITION') from error


def connectionTestPrompt(deviceTypeId):
    if deviceTypeId in {'device.tcp.server', 'device.tcp.receive_once'}:
        return '检查本地监听端点；不验证外部客户端，不应用草稿通道规则，不保存或发布配置。继续？'
    return '检查设备连接；不拍照、不发送业务报文、不写 PLC，不保存或发布配置。继续？'


def deviceErrorMessage(error):
    if isinstance(error, ConnectionRefusedError) or getattr(error, 'code', '') in {'ECONNREFUSED', 'WSAECONNREFUSED', 'E_DEVICE_CONNECTION_REFUSED'}:
        return '无法连接到目标地址和端口，请检查设备端服务与端口设置'
    return '设备操作未完成，请查看详情'


def deviceValidationMessage(error):
    from emo_master.core.workflow.errors import WorkflowCompileError
    return ('设备配置校验未通过，请查看详情' if isinstance(error, WorkflowCompileError)
            and not any(issue.fieldPath for issue in error.issues) else str(error))


def deviceErrorDetails(error):
    codes = ', '.join(dict.fromkeys(issue.code for issue in getattr(error, 'issues', ())))
    return (codes or getattr(error, 'code', '')) + '：' + str(error)


def clearNameFeedback(control, feedback):
    previous = control.accessibleDescription()
    control.setAccessibleDescription('')
    control.setStyleSheet('')
    if previous and previous in feedback.text().splitlines():
        feedback.setText('\n'.join(line for line in feedback.text().splitlines() if line != previous))
        if not feedback.text():
            feedback.setToolTip('')


def deviceErrorControl(name, forms, message):
    controls = [name]
    for form in forms.values():
        controls.extend(getattr(getattr(form, 'fields', form), '_controls', {}).values())
        if hasattr(form, 'channels'):
            controls.append(form.channels.table)
    return next((control for control in controls if control.accessibleDescription() == message), None)


class DeviceTypeDialog(QDialog):
    def __init__(self, deviceTypes, parent=None):
        super().__init__(parent)
        self.setWindowTitle('新建设备')
        layout = QVBoxLayout(self)
        self.selection = QComboBox()
        names = [declaration.displayName for declaration in deviceTypes.values()]
        for key, declaration in deviceTypes.items():
            label = declaration.displayName
            if names.count(label) > 1:
                label += f' · {declaration.version} ({key})'
            self.selection.addItem(label, key)
            self.selection.setItemData(self.selection.count()-1, key, Qt.ToolTipRole)
        layout.addWidget(self.selection)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


class DeviceDefinitionDialog(QDialog):
    """Keep the draft open when beforeAccept returns False or a commit fails."""

    def __init__(self, declaration, parent=None, *, initial=None, client=None, beforeAccept=None):
        super().__init__(parent)
        self.declaration = declaration
        self.initial = deepcopy(initial or {})
        self.value = None
        self.client = client
        self.beforeAccept = beforeAccept
        self.setupPool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='device-setup')
        self.setupPending = None
        self.setupScope = None
        self.setupCandidate = None
        self.inputErrorControl = None
        self.inputErrorMessage = ''
        self.setupTimer = QTimer(self)
        self.setupTimer.timeout.connect(self.setupReady)
        self.setWindowTitle("新建设备 · " + declaration.displayName)
        self.resize(600, 540)
        layout = QVBoxLayout(self)
        self.name = QLineEdit(self.initial.get('name', declaration.displayName))
        self.enabled = QCheckBox('启用设备')
        self.enabled.setChecked(self.initial.get('enabled', True))
        names = QFormLayout()
        names.addRow("设备名称", self.name)
        names.addRow('', self.enabled)
        layout.addLayout(names)
        if declaration.deviceTypeId == 'device.tcp.server':
            tip = QLabel('暂不启用时，可以先保存设备，稍后配置客户端通道')
            tip.setWordWrap(True)
            layout.addWidget(tip)
        self.tabs = QTabWidget()
        self.forms = {}
        for key, title in (("connection", "连接"), ("defaults", "工作参数"), ("policies", "策略")):
            schema = getattr(declaration, key + "Schema")
            self.forms[key] = configurationForm(schema, applySchemaDefaults(schema, (initial or {}).get(key, {})),
                deviceTypeId=declaration.deviceTypeId, section=key)
            self.tabs.addTab(scrollWidget(self.forms[key]), title)
        layout.addWidget(self.tabs)
        self.error = QLabel()
        self.name.textChanged.connect(lambda *_: clearNameFeedback(self.name, self.error))
        self.name.textChanged.connect(self._fieldEdited)
        for form in self.forms.values():
            form.valuesChanged.connect(self._fieldEdited)
        self.error.setWordWrap(True)
        layout.addWidget(self.error)
        actions = QHBoxLayout()
        self.discoverButton = QPushButton('发现设备')
        self.discoverButton.clicked.connect(self.discover)
        self.discoverButton.setEnabled(client is not None and declaration.deviceTypeId == 'device.camera.huaray_imv')
        self.testButton = QPushButton('测试连接')
        self.testButton.clicked.connect(self.testConnection)
        self.testButton.setEnabled(client is not None and "test_connection" in declaration.controlCapabilities)
        actions.addWidget(self.discoverButton)
        actions.addWidget(self.testButton)
        layout.addLayout(actions)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("创建")
        buttons.button(QDialogButtonBox.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def definition(self):
        base = dict(self.initial, deviceTypeId=self.declaration.deviceTypeId,
                    driverVersion=self.initial.get('driverVersion', self.declaration.version), enabled=self.enabled.isChecked())
        return validateDeviceDefinition(self.declaration, self.name, self.forms, self.tabs, base=base)

    def _fieldEdited(self):
        if self.inputErrorControl is not None and not self.inputErrorControl.accessibleDescription():
            if self.error.text() == self.inputErrorMessage:
                self.error.clear()
                self.error.setToolTip('')
            self.inputErrorControl = None

    def _testCandidate(self):
        return (self.name.text(), self.enabled.isChecked(),
                {key: form.getValues() for key, form in self.forms.items()})

    def _setupScope(self):
        parent = self.parentWidget()
        coordinator = getattr(parent, 'c', None)
        return (id(self.client), getattr(self.client, 'runtimeScope', None),
                id(getattr(self.client, 'runtimeService', None)), id(getattr(self.client, 'stub', None)),
                coordinator.session._signature() if coordinator else None)

    def discover(self):
        if self.setupPending is None:
            self.setupScope = self._setupScope()
            self.setupKind = 'discover'
            self.setupPending = self.setupPool.submit(self.client.deviceNetworkCommand, '', '', 'info')
            self.setupTimer.start(40)

    def testConnection(self):
        if self.setupPending is not None:
            return
        try:
            definition = self.definition()
            candidate = deepcopy(self._testCandidate())
        except ValueError as error:
            self.inputErrorMessage = deviceValidationMessage(error)
            self.error.setText(self.inputErrorMessage)
            self.error.setToolTip(deviceErrorDetails(error))
            self.inputErrorControl = deviceErrorControl(self.name, self.forms, str(error))
            return
        if QMessageBox.question(self, '测试连接', connectionTestPrompt(self.declaration.deviceTypeId),
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        def test():
            info = self.client.deviceNetworkCommand('', '', 'info')
            if info.get('deviceSetupVersion', 0) < 1:
                raise ValueError('Runtime 不支持连接测试，请升级 Runtime')
            return self.client.deviceNetworkCommand(info['projectId'], info['runtimeInstanceId'], 'test_connection',
                payload=dict(confirmed=True, definition=definition))
        self.setupKind = 'test'
        self.setupScope = self._setupScope()
        self.setupCandidate = candidate
        self.setupPending = self.setupPool.submit(test)
        self.setupTimer.start(40)
        self.error.setText('正在测试连接')

    def setupReady(self):
        if self.setupPending is None or not self.setupPending.done():
            return
        future, self.setupPending = self.setupPending, None
        self.setupTimer.stop()
        try:
            if self.setupScope is not None and self.setupScope != self._setupScope():
                raise ValueError('工程或 Runtime 连接已变化，请重新测试')
            if self.setupKind == 'test':
                try:
                    current = self._testCandidate()
                except ValueError:
                    current = None
                if current != self.setupCandidate:
                    if self.error.text() == '正在测试连接':
                        self.error.setText('输入已变化；此前测试结果不用于当前输入')
                    return
            result = future.result()
            if self.setupKind == 'discover':
                from .device_network import DeviceNetworkDialog
                dialog = DeviceNetworkDialog(self.client, result['projectId'], result['runtimeInstanceId'], '', {}, self,
                                             discoveryOnly=True)
                dialog.exec_()
                if dialog.selectedCameraKey:
                    values = self.forms['connection'].getValues()
                    values['cameraKey'] = dialog.selectedCameraKey
                    self.forms['connection'].setSchema(self.declaration.connectionSchema, values)
            else:
                self.error.setText(result.get('message') or '连接检查通过；未验证采集或业务读写；未保存或发布配置')
        except Exception as error:
            self.error.setText(deviceErrorMessage(error))
            self.error.setToolTip(str(error))

    def done(self, result):
        if self.setupPending is not None:
            self.error.setText('等待连接测试或发现请求返回后可关闭')
            return
        self.setupTimer.stop()
        self.setupPool.shutdown(wait=False, cancel_futures=True)
        super().done(result)

    def accept(self):
        if self.setupPending is not None:
            self.error.setText('等待连接测试或发现请求返回后可创建')
            return
        self.error.clear()
        try:
            value = self.definition()
            if self.beforeAccept is not None and self.beforeAccept(value) is False:
                return
        except (ValueError, KeyError, OSError) as error:
            self.inputErrorMessage = deviceValidationMessage(error)
            self.error.setText(self.inputErrorMessage)
            self.error.setToolTip(deviceErrorDetails(error))
            self.inputErrorControl = deviceErrorControl(self.name, self.forms, str(error))
            return
        self.value = value
        super().accept()


class SourceEditor(QWidget):
    def __init__(self, declaration, definition, node, slot, variables):
        super().__init__()
        self.rows = {}
        self.onlineLabels = {}
        self.declaration, self.definition, self.node, self.slot = declaration, deepcopy(definition), deepcopy(node), slot
        self.online = None
        layout = QFormLayout(self)
        layout.setRowWrapPolicy(QFormLayout.WrapLongRows)
        for path in declaration.overridePaths:
            spec = declaration.parameterSpec(path)
            modes = QComboBox()
            modes.addItem("继承设备", "inherit")
            modes.addItem("常量覆写", "constant")
            if path in declaration.dynamicPaths:
                modes.addItem("全局变量", "variable")
                modes.addItem("节点输入", "input")
            variable = QComboBox()
            from emo_master.core.project.global_variables import compatible
            for key, value in variables.items():
                if compatible(value.type, spec["type"]):
                    variable.addItem(f"{getattr(value, 'name', key)} ({key})", key)
            inputs = QComboBox()
            for key, kind in node.get("inputPorts", {}).items():
                from emo_master.core.contracts.port_types import normalizePortType
                if compatible(normalizePortType(kind), spec["type"]):
                    inputs.addItem(key, key)
            value = definition.get("defaults", {})
            override = node.get("deviceOverrides", {}).get(slot, {})
            missing = object()
            for part in path.split("."):
                value = value.get(part, missing) if isinstance(value, dict) else missing
                override = override.get(part, missing) if isinstance(override, dict) else missing
            constant = override if override is not missing else value
            form = configurationForm({"type": "object", "properties": {"value": dict(spec, title="值")},
                                      "required": ["value"]}, {} if constant is missing else {"value": constant})
            label = QLabel("设备默认：" + ("未设置" if value is missing else str(value) + " " + spec.get("xUnit", "")))
            label.setWordWrap(True)
            row = QWidget()
            rowLayout = QVBoxLayout(row)
            rowLayout.setContentsMargins(0, 0, 0, 0)
            for widget in (modes, label, form, variable, inputs):
                rowLayout.addWidget(widget)
            onlineLabel = QLabel('在线配置未读取')
            onlineLabel.setWordWrap(True)
            rowLayout.addWidget(onlineLabel)
            self.onlineLabels[path] = onlineLabel
            def update(_index=0, modes=modes, label=label, form=form, variable=variable, inputs=inputs):
                source = modes.currentData()
                label.setVisible(source == "inherit")
                form.setVisible(source == "constant")
                variable.setVisible(source == "variable")
                inputs.setVisible(source == "input")
            modes.currentIndexChanged.connect(update)
            binding = node.get("deviceValueBindings", {}).get(slot, {}).get(path)
            source = binding["source"] if binding else "constant" if override is not missing else "inherit"
            modes.setCurrentIndex(modes.findData(source))
            if binding:
                selector = variable if source == "variable" else inputs
                selected = binding.get("variableId") or binding.get("inputName")
                if selector.findData(selected) < 0:
                    selector.addItem("不可用：" + selected, selected)
                selector.setCurrentIndex(selector.findData(selected))
            update()
            title = QLabel(spec.get("title", path))
            title.setWordWrap(True)
            title.setFixedWidth(180)
            layout.addRow(title, row)
            self.rows[path] = (modes, form, variable, inputs)
            for control in (modes, variable, inputs, *form._controls.values()):
                for signal in ('textChanged', 'currentIndexChanged', 'toggled'):
                    if hasattr(control, signal):
                        getattr(control, signal).connect(lambda *_args, path=path: self._compareField(path))
                        break

    def setOnline(self, definition, node, *, revision, deviceId):
        self.online = (definition, node, revision, deviceId)
        for path in self.rows:
            self._compareField(path)

    def onlineUnavailable(self, message):
        self.online = None
        for label in self.onlineLabels.values():
            label.setText(message)

    @staticmethod
    def _field(definition, node, slot, path):
        binding = node.get('deviceValueBindings', {}).get(slot, {}).get(path)
        if binding:
            return binding['source'], binding.get('variableId') or binding.get('inputName')
        override = node.get('deviceOverrides', {}).get(slot, {})
        for part in path.split('.'):
            if not isinstance(override, dict) or part not in override:
                break
            override = override[part]
        else:
            return 'constant', override
        value = definition.get('defaults', {})
        for part in path.split('.'):
            if not isinstance(value, dict) or part not in value:
                return 'inherit', None
            value = value[part]
        return 'inherit', value

    def _compareField(self, path):
        if self.online is None:
            return
        definition, node, revision, deviceId = self.online
        label = self.onlineLabels[path]
        if definition is None or node is None or self.slot not in node.get('deviceBindings', {}):
            label.setText(f'在线 R{revision} · 此设备 / 节点绑定尚未配置 · 草稿未应用')
            return
        onlineDevice = node['deviceBindings'][self.slot]['deviceId']
        source, value = self._field(definition, node, self.slot, path)
        sourceName = {'inherit': '继承设备', 'constant': '常量覆写', 'variable': '全局变量', 'input': '节点输入'}[source]
        text = str(value) if value is not None else '未设置 / 保留设备值'
        if source in {'input', 'variable'}:
            text += '（运行时取值）'
        elif value is not None:
            text += ' ' + self.declaration.parameterSpec(path).get('xUnit', '')
        try:
            draft = self.apply(deepcopy(self.node), self.slot)
            same = onlineDevice == deviceId and (source, value) == self._field(self.definition, draft, self.slot, path)
            difference = '与在线一致' if same else '草稿未应用'
        except ValueError:
            difference = '草稿值无效，未应用'
        label.setText(f'在线 R{revision} · {onlineDevice} · {sourceName}：{text} · {difference}')

    def apply(self, node, slot):
        for path, (modes, form, variable, inputs) in self.rows.items():
            source = modes.currentData()
            value = (form.getValues()["value"] if source == "constant" else
                     variable.currentData() if source == "variable" else inputs.currentData())
            node = changeParameterSource(node, slot, path, source=source, value=value)
        return node


class DeviceNodeDialog(QDialog):
    def __init__(self, coordinator, node, requirements, parent=None, *, position=None, migrateLegacy=False):
        super().__init__(parent)
        self.c, self.node, self.requirements = coordinator, deepcopy(node), requirements
        self.migrateLegacy = migrateLegacy and not node.get("deviceBindings")
        self.projectSignature = coordinator.session._signature()
        self.workflowId = coordinator.window.activeWorkflowId
        self.runtimeScope = self._runtimeScope()
        self.types = {}
        self.position = position
        self.newDefinitions = {}
        self.slots = {}
        self.sources = {}
        self.selectors = {}
        self.onlineSnapshot = None
        self.onlineMessage = '在线配置未读取'
        self.onlinePending = None
        self.setWindowTitle("设备绑定与参数 · " + str(node.get("displayName") or node["operatorId"]))
        self.resize(700, 650)
        layout = QVBoxLayout(self)
        from .widgets import OptionalWrapLabel
        definition = coordinator.window._operatorDefinition(node["operatorId"])
        operatorName = str(definition.get("displayName") or "")
        self.operatorIdentityLabel = OptionalWrapLabel(
            f"原始算子：{operatorName}\n算子 ID：{node['operatorId']}" if operatorName else ""
        )
        self.operatorIdentityLabel.setObjectName("operatorEditorIdentity")
        layout.addWidget(self.operatorIdentityLabel)
        if operatorName and operatorName != node.get("displayName"):
            self.setWindowTitle(self.windowTitle() + f" · {operatorName}")
        self.tabs = QTabWidget()
        schema = deepcopy(node.get("paramSchema", {}))
        self.actionFields = schema.get("xDeviceActionFields")
        values = node.get("params", {})
        if self.actionFields is not None:
            schema["properties"] = {key: spec for key, spec in schema.get("properties", {}).items()
                                    if key in self.actionFields}
            schema["required"] = [key for key in schema.get("required", []) if key in self.actionFields]
            values = {key: value for key, value in values.items() if key in self.actionFields}
        self.params = configurationForm(schema, values)
        if schema.get("properties"):
            self.tabs.addTab(scrollWidget(self.params), "节点参数")
        else:
            self.params.setParent(self)
            self.params.hide()
        layout.addWidget(self.tabs)
        self.error = QLabel("正在读取 Runtime 设备类型…")
        self.error.setWordWrap(True)
        layout.addWidget(self.error)
        self.onlineButton = QPushButton('刷新在线字段对比')
        from .icon_map import icon
        self.onlineButton.setIcon(icon('refresh-cw'))
        self.onlineButton.clicked.connect(self.refreshOnline)
        layout.addWidget(self.onlineButton)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.button(QDialogButtonBox.Ok).setText("完成")
        self.buttons.button(QDialogButtonBox.Cancel).setText("取消")
        self.buttons.button(QDialogButtonBox.Ok).setEnabled(False)
        self.pendingButton = self.buttons.addButton('保存待绑定节点', QDialogButtonBox.ActionRole)
        self.pendingButton.setEnabled(False)
        self.pendingButton.clicked.connect(self.savePending)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self.reader = ThreadPoolExecutor(max_workers=1, thread_name_prefix="device-wizard")
        reader = getattr(coordinator.window.runtimeClient, "listDeviceTypes", None)
        self.pending = self.reader.submit(reader) if reader else None
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._ready)
        self.timer.start(30)
        self.onlineTimer = QTimer(self)
        self.onlineTimer.timeout.connect(self.onlineReady)
        self.refreshOnline()
        self.finished.connect(self._finish)

    def _finish(self, _result):
        self.timer.stop()
        self.onlineTimer.stop()
        self.reader.shutdown(wait=False, cancel_futures=True)

    def refreshOnline(self):
        reader = getattr(self.c.window.runtimeClient, 'getOnlineProjectSnapshot', None)
        if self.onlinePending is not None:
            return
        if not callable(reader):
            self.onlineMessage = 'Runtime 不支持在线字段对比，请升级 Runtime'
            self.onlineButton.setEnabled(False)
            return
        self.onlineButton.setEnabled(False)
        self.onlinePending = self.reader.submit(reader, self.c.session.document().project.projectId)
        self.onlineTimer.start(40)

    def onlineReady(self):
        if self.onlinePending is None or not self.onlinePending.done():
            return
        future, self.onlinePending = self.onlinePending, None
        self.onlineTimer.stop()
        self.onlineButton.setEnabled(True)
        try:
            self._checkContext()
            snapshot = future.result()
            if snapshot['payload']['project']['projectId'] != self.c.session.document().project.projectId:
                raise ValueError('Runtime 在线工程不同')
            self.onlineSnapshot = snapshot
        except Exception as error:
            self.onlineSnapshot = None
            self.onlineMessage = '在线字段未获取：' + str(error)
        for slot, editor in self.sources.items():
            self._compareSource(slot, editor)

    def _compareSource(self, slot, editor):
        if self.onlineSnapshot is None:
            editor.onlineUnavailable(self.onlineMessage)
            return
        payload = self.onlineSnapshot['payload']
        node = next((n for n in payload.get('workflows', {}).get(self.workflowId, {}).get('nodes', [])
                     if n['nodeId'] == self.node['nodeId']), None)
        onlineId = (node or {}).get('deviceBindings', {}).get(slot, {}).get('deviceId')
        definition = payload.get('devices', {}).get('definitions', {}).get(onlineId)
        editor.setOnline(definition, node, revision=self.onlineSnapshot['documentRevision'],
                         deviceId=self.slots[slot][0].currentData())

    def _runtimeScope(self):
        client = self.c.window.runtimeClient
        return (id(client), getattr(client, "runtimeScope", None), id(getattr(client, "runtimeService", None)),
                id(getattr(client, "stub", None)))

    def _checkContext(self):
        if (self.c.session._signature() != self.projectSignature or self.c.window.activeWorkflowId != self.workflowId
                or self._runtimeScope() != self.runtimeScope):
            raise ValueError("工程或 Runtime 连接已变化，请重新打开设备向导")

    def _ready(self):
        if self.pending is not None and not self.pending.done():
            return
        self.timer.stop()
        try:
            self._checkContext()
            if self.pending is None:
                raise ValueError("Runtime 不支持设备元数据读取")
            self.types, requirements, _rejected = self.pending.result()
            actual = requirements.get(self.node["operatorId"], {})
            actual = {key: value.model_dump() if hasattr(value, "model_dump") else value for key, value in actual.items()}
            if actual != self.requirements:
                raise ValueError("算子设备声明已变化，请刷新算子后重试")
            for slot, requirement in self.requirements.items():
                typeId = requirement["deviceTypeId"]
                if typeId not in self.types:
                    raise ValueError("设备类型不可用：" + typeId)
                page = QWidget()
                pageLayout = QVBoxLayout(page)
                line = QHBoxLayout()
                combo = QComboBox()
                combo.addItem("请选择设备", None)
                for key, definition in self.c.session.workflows.devices.get("definitions", {}).items():
                    if definition["deviceTypeId"] == typeId:
                        combo.addItem(f"{definition['name']} ({key})", key)
                create = QPushButton("新建设备")
                create.clicked.connect(lambda _checked=False, slot=slot: self._create(slot))
                line.addWidget(combo, 1)
                line.addWidget(create)
                pageLayout.addLayout(line)
                self.slots[slot] = (combo, pageLayout)
                combo.currentIndexChanged.connect(lambda _index, slot=slot: self._sources(slot))
                deviceId = self.node.get("deviceBindings", {}).get(slot, {}).get("deviceId")
                if deviceId is not None and combo.findData(deviceId) < 0:
                    combo.addItem('不可用：' + deviceId, deviceId)
                combo.setCurrentIndex(max(0, combo.findData(deviceId)))
                if slot not in self.sources:
                    self._sources(slot)
                self.tabs.addTab(scrollWidget(page), slot)
            self.error.setText("")
            self.buttons.button(QDialogButtonBox.Ok).setEnabled(True)
            self.pendingButton.setEnabled(True)
        except Exception as error:
            self.error.setText(str(error))

    def _create(self, slot):
        declaration = self.types[self.requirements[slot]["deviceTypeId"]]
        initial = None
        if self.actionFields is not None and self.migrateLegacy:
            legacy = applySchemaDefaults(self.node.get("paramSchema", {}), self.node.get("params", {}))
            if declaration.deviceTypeId == "device.camera.huaray_imv" and legacy.get("waitMode") == "hardware":
                legacy["captureTimeoutMs"] = 0
            initial = {group: {key: value for key, value in legacy.items()
                               if key in getattr(declaration, group + "Schema")["properties"]}
                       for group in ("connection", "defaults")}
        selected = None

        def chooseDefinition(value):
            nonlocal selected
            self._checkContext()
            from emo_master.core.project.device_identity import matchingDevices
            definitions = {**self.c.session.workflows.devices.get('definitions', {}), **self.newDefinitions}
            matches = matchingDevices(value, definitions, declaration)
            if matches:
                if len(matches) != 1:
                    raise ValueError('多个已有设备使用相同标识，请先处理重复定义：' + ', '.join(matches))
                key = matches[0]
                label = f"{definitions[key]['name']} ({key})"
                if QMessageBox.question(dialog, '复用已有设备',
                        '相同设备标识已存在：' + label + '\n复用已有设备？刚输入的默认值和策略不会覆盖已有配置。',
                        QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
                    return False
                selected = (key, definitions[key], False)
            else:
                selected = ("device-" + uuid4().hex, value, True)
            return True

        dialog = DeviceDefinitionDialog(declaration, self, initial=initial, client=self.c.window.runtimeClient,
                                        beforeAccept=chooseDefinition)
        if dialog.exec_() == dialog.Accepted:
            key, value, isNew = selected
            if isNew:
                self.newDefinitions[key] = value
            combo = self.slots[slot][0]
            if combo.findData(key) < 0:
                combo.addItem(f"{value['name']} ({key})", key)
            combo.setCurrentIndex(combo.findData(key))

    def _sources(self, slot):
        combo, layout = self.slots[slot]
        old = self.sources.pop(slot, None)
        if old is not None:
            layout.removeWidget(old)
            old.deleteLater()
        oldSelectors = self.selectors.pop(slot, {})
        for oldControl in oldSelectors.values():
            clearNameFeedback(oldControl, self.error)
            oldControl.deleteLater()
        hint = getattr(self, '_sourceHints', {}).pop(slot, None)
        if hint is not None:
            layout.removeWidget(hint)
            hint.deleteLater()
        key = combo.currentData()
        definitions = {**self.c.session.workflows.devices.get("definitions", {}), **self.newDefinitions}
        if key is None or key not in definitions:
            if not hasattr(self, '_sourceHints'):
                self._sourceHints = {}
            hint = QLabel('选择设备后可配置继承与覆盖' if key is None else '设备引用不存在：' + key)
            hint.setWordWrap(True)
            layout.addWidget(hint)
            self._sourceHints[slot] = hint
            return
        clearNameFeedback(combo, self.error)
        declaration = self.types[definitions[key]["deviceTypeId"]]
        editorNode = self.node
        if declaration.deviceTypeId == "device.camera.huaray_imv" and getattr(self, "migrateLegacy", False):
            editorNode = deepcopy(self.node)
            legacy = applySchemaDefaults(self.node.get("paramSchema", {}), self.node.get("params", {}))
            if legacy.get("waitMode") == "hardware":
                legacy["captureTimeoutMs"] = 0
            editorNode.setdefault("deviceOverrides", {})[slot] = {
                path: legacy[path] for path in declaration.overridePaths if path in legacy}
            for rule in declaration.parameterRules:
                if rule.whenOverridden and any(legacy.get(key) != value for key, value in rule.requires.items()):
                    editorNode["deviceOverrides"][slot].pop(rule.present, None)
        if self.actionFields is not None and getattr(self, "migrateLegacy", False):
            try:
                editorNode = migrateLegacySources(editorNode, slot, key, declaration, self.actionFields)
            except ValueError as error:
                self.error.setText(str(error))
        editor = SourceEditor(declaration, definitions[key], editorNode, slot,
                              self.c.session.document().globalVariables)
        from emo_master.core.plugin.devices import DeviceRequirement, selectorCandidates
        requirement = DeviceRequirement.model_validate(self.requirements[slot]) if isinstance(self.requirements[slot], dict) else self.requirements[slot]
        self.selectors[slot] = {}
        for name in requirement.selectorSchema['properties']:
            select = QComboBox(editor)
            select.setObjectName('selector-' + slot + '-' + name)
            select.addItem('请选择通道' if name == 'channelId' else '请选择 ' + name, None)
            candidates = selectorCandidates(declaration, definitions[key], name)
            for value, label in candidates:
                select.addItem(f'{label} ({value})', value)
            prior = self.node.get('deviceBindings', {}).get(slot, {}).get('selectors', {}).get(name)
            if prior is not None:
                if select.findData(prior) < 0:
                    select.addItem('不可用：' + str(prior), prior)
                select.setCurrentIndex(select.findData(prior))
            editor.layout().addRow('通道' if name == 'channelId' else name, select)
            self.selectors[slot][name] = select
            select.currentIndexChanged.connect(lambda _index, control=select, valid={value for value, _ in candidates}:
                clearNameFeedback(control, self.error) if control.currentData() in valid else None)
        self.sources[slot] = editor
        self._compareSource(slot, editor)
        layout.addWidget(editor)

    def accept(self):
        self._commit(allowIncomplete=False)

    def savePending(self):
        self._commit(allowIncomplete=True)

    def _commit(self, *, allowIncomplete):
        try:
            self._checkContext()
            if not self.types or set(self.slots) != set(self.requirements):
                raise ValueError("设备类型尚未就绪")
            draft = deepcopy(self.node)
            draft["params"] = self.params.getValues()
            resources = self.c.session.document().resources
            resourcePlan = resources.model_dump() if resources else None
            moved = []
            definitions = {**self.c.session.workflows.devices.get("definitions", {}), **self.newDefinitions}
            for slot, (combo, _layout) in self.slots.items():
                key = combo.currentData()
                if key is None:
                    if not allowIncomplete:
                        message = '请选择设备：' + slot
                        combo.setAccessibleDescription(message)
                        _showControlTab(self.tabs, combo)
                        combo.setFocus()
                        raise ValueError(message)
                    hadSources = bool(draft.get('deviceOverrides', {}).get(slot) or draft.get('deviceValueBindings', {}).get(slot))
                    if hadSources and QMessageBox.question(self, '解除设备绑定',
                            f'解除 {slot} 并移除该槽的参数覆写与动态来源？其他槽保持原值。',
                            QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
                        return
                    for group in ('deviceBindings', 'deviceOverrides', 'deviceValueBindings'):
                        draft.get(group, {}).pop(slot, None)
                    continue
                if key not in definitions:
                    message = '槽位 ' + slot + '：设备引用不存在：' + key
                    combo.setAccessibleDescription(message)
                    raise ValueError(message)
                draft.setdefault("deviceBindings", {})[slot] = {"deviceId": key, "selectors": {
                    name: control.currentData() for name, control in self.selectors.get(slot, {}).items()
                    if control.currentData() is not None}}
                declaration = self.types[definitions[key]["deviceTypeId"]]
                if self.actionFields is not None and self.migrateLegacy:
                    draft = migrateLegacySources(draft, slot, key, declaration, self.actionFields)
                    if resourcePlan is not None:
                        resourcePlan, changes = migrateLegacyResourceTargets(resourcePlan, self.workflowId,
                            draft["nodeId"], key, declaration, self.actionFields)
                        moved.extend(changes)
                draft = self.sources[slot].apply(draft, slot)
            if (self.actionFields is not None and self.migrateLegacy
                    and (self.node.get("params") or self.node.get("globalVariableBindings") or moved)):
                old = self.node.get("params", {})
                firstSlot = next(iter(self.requirements))
                selected = definitions[draft["deviceBindings"][firstSlot]["deviceId"]]
                connection = self.types[selected["deviceTypeId"]].validateDefinition(
                    DeviceDefinition.model_validate(selected)).connection
                lines = ["节点 ID 与输出端口保持不变。", "连接参数移入所选设备，自动重试取消。", ""]
                lines += [f"{key}: {old.get(key, '(默认)')} -> {value}" for key, value in connection.items()]
                if moved:
                    lines += ["", "以下资源/现场绑定将移到共享设备属性，影响所有使用该设备的节点：", *moved]
                if selected["deviceTypeId"] == "device.camera.huaray_imv":
                    lines += ["", "旧 keep 表示保留设备当前值，不等于固定默认值。共享设备可能受其他节点覆写影响。",
                              "请核对设备工作参数与节点覆写。硬件触发参数变化需要停触发并同步后恢复。"]
                    if old.get("waitMode") == "hardware":
                        lines += ["无限硬件等待迁移为取图超时 0；停止操作可取消等待，不重发触发。"]
                lines += ["", "转换仅修改本地草稿。工程中其他旧设备节点也需绑定后才能运行。"]
                if QMessageBox.question(self, "确认设备迁移", "\n".join(lines),
                                        QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
                    return
            catalog = DeviceCatalogEditor(self.c.session, self.types, {draft["operatorId"]: self.requirements})
            used = {binding["deviceId"] for binding in draft.get("deviceBindings", {}).values()}
            catalog.commitNode(self.workflowId, draft,
                               newDevices={key: value for key, value in self.newDefinitions.items() if key in used},
                               position=self.position, resourcePlan=resourcePlan, allowIncomplete=allowIncomplete)
            self.c.window.workflowController._renderActive()
            self.c.chrome.update()
        except (ValueError, KeyError) as error:
            from emo_master.core.workflow.errors import WorkflowCompileError
            if isinstance(error, WorkflowCompileError):
                self.error.setText('\n'.join(f'{issue.fieldPath}：{issue.message}' for issue in error.issues))
                first = None
                for issue in error.issues:
                    parts = issue.fieldPath.split('.')
                    if len(parts) >= 4 and parts[0] == 'deviceBindings' and parts[2] == 'selectors':
                        control = self.selectors.get(parts[1], {}).get(parts[3])
                        if control is not None:
                            control.setAccessibleDescription(f'{issue.fieldPath}：{issue.message}')
                            if first is None:
                                first = control
                if first is not None:
                    _showControlTab(self.tabs, first)
                    first.setFocus(Qt.OtherFocusReason)
            else:
                self.error.setText(str(error))
            return
        super().accept()
