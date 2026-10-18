"""Project-owned device editor. Metadata reads never discover or open hardware."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy

from PySide2.QtCore import Qt, QTimer
from PySide2.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QSplitter, QTreeWidget, QTreeWidgetItem,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView, QLabel,
    QLineEdit, QCheckBox, QFormLayout, QTabWidget, QScrollArea, QToolButton,
    QInputDialog, QMessageBox,
)

from emo_master.apps.designer.state.device_catalog import DeviceCatalogEditor
from emo_master.apps.designer.ui.icon_map import icon
from emo_master.apps.designer.ui.param_form import SchemaParamForm


class DeviceWorkspace(QWidget):
    def __init__(self, coordinator):
        super().__init__()
        self.c = coordinator
        self.deviceTypes = {}
        self.requirements = {}
        self.selectedId = None
        self.original = None
        self.formBaseline = None
        self.loading = False
        self.reader = ThreadPoolExecutor(max_workers=1, thread_name_prefix="device-metadata")
        self.pending = None
        self.pendingKind = "metadata"
        self.pendingScope = None
        self.states = {}
        self.stateRuntimeId = ""
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._metadataReady)
        layout = QVBoxLayout(self)
        commands = QHBoxLayout()
        commands.setSpacing(2)
        self.buttons = {}
        for key, title, glyph, callback in (
            ("new", "新建设备", "plus", self.createDevice),
            ("delete", "删除设备", "trash-2", self.deleteDevice),
            ("apply", "应用到工程草稿", "save", self.apply),
            ("revert", "放弃属性修改", "x", self.revert),
            ("refresh", "刷新设备类型", "refresh-cw", self.loadMetadata),
            ("state", "读取在线设备状态", "logs", self.readState),
            ("network", "Runtime 设备发现 / 实物 IP", "search", self.deviceNetwork),
            ("compare", "与 Runtime 比较", "git-compare", self.compareOnline),
            ("bindings", "工程资源 / 现场绑定", "link", self.resourceBindings),
            ("migration", "旧设备节点迁移预览", "list", self.migrationPreview),
            ("diagnostics", "设备诊断", "sliders-horizontal", self.diagnostics),
            ("connect", "连接并恢复使用", "plug", lambda: self.control("connect")),
            ("disconnect", "断开并暂停连接", "unplug", lambda: self.control("disconnect")),
            ("maintenance", "暂停所选设备并取消其请求", "pause", lambda: self.control("maintenance")),
            ("recover", "恢复设备会话", "refresh-cw", self.recover),
        ):
            button = QToolButton()
            button.setFixedSize(26, 26)
            button.setIcon(icon(glyph))
            button.setToolTip(title)
            button.setAccessibleName(title)
            button.clicked.connect(callback)
            self.buttons[key] = button
            commands.addWidget(button)
        self.status = QLabel("未读取 Runtime 设备类型")
        self.status.setWordWrap(True)
        commands.addWidget(self.status, 1)
        layout.addLayout(commands)
        self.splitter = QSplitter()
        layout.addWidget(self.splitter, 1)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabel("设备类型")
        self.tree.setMinimumWidth(130)
        self.tree.currentItemChanged.connect(self._filter)
        self.splitter.addWidget(self.tree)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["设备名称", "类型", "引用", "状态"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.verticalHeader().hide()
        self.table.currentCellChanged.connect(self._select)
        self.splitter.addWidget(self.table)
        self.details = QWidget()
        detailLayout = QVBoxLayout(self.details)
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
        detailLayout.addLayout(form)
        self.tabs = QTabWidget()
        self.forms = {}
        for key, title in (("connection", "连接"), ("defaults", "工作参数"), ("policies", "策略")):
            editor = SchemaParamForm()
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setWidget(editor)
            self.tabs.addTab(scroll, title)
            self.forms[key] = editor
        self.references = QLabel()
        self.references.setWordWrap(True)
        self.references.setAlignment(Qt.AlignTop)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.references)
        self.tabs.addTab(scroll, "节点引用")
        detailLayout.addWidget(self.tabs)
        self.splitter.addWidget(self.details)
        self.splitter.setSizes([180, 430, 400])
        self.refresh()

    def catalog(self):
        return DeviceCatalogEditor(self.c.session, self.deviceTypes, self.requirements)

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

    def diagnostics(self):
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
            dialog.exec_()
        except (ValueError, KeyError) as error:
            self.status.setText(str(error))

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
        if QMessageBox.question(self, "设备连接控制", messages[action], QMessageBox.Yes | QMessageBox.No,
                                QMessageBox.No) != QMessageBox.Yes:
            return
        self.pendingKind = "control"
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

    def readState(self):
        if self.pending is not None:
            return
        reader = getattr(self.c.window.runtimeClient, "getDeviceStates", None)
        if reader is None:
            self.status.setText("Runtime 不支持设备状态读取")
            return
        self.pendingKind = "state"
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
            if self.pendingKind in {"state", "recover", "control"}:
                runtimeId, self.states = future.result()
                self.stateRuntimeId = runtimeId
                self._showStates()
                self.status.setText("会话已恢复；未连接设备、未重发读写" if self.pendingKind == "recover"
                                    else "设备控制完成；工程草稿未改变" if self.pendingKind == "control"
                                    else "在线状态已读取；工程草稿未改变")
                self.status.setToolTip("Runtime: " + runtimeId)
                return
            types, requirements, rejected = future.result()
            self.commitPending()
            self.deviceTypes, self.requirements = types, requirements
            self.refresh()
            self.status.setText(f"{len(types)} 个设备类型；{len(rejected)} 个声明被拒绝；工程草稿")
            self.status.setToolTip(str(rejected) if rejected else "")
        except Exception as error:
            if self.pendingKind in {"state", "recover", "control"}:
                self.states = {}
                self.stateRuntimeId = ""
                self._showStates()
            self.status.setText("设备信息读取失败：" + str(error))

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
                item.setText("未读取在线状态")
                item.setToolTip("")
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
            item.setToolTip(f"排队：{state['queued']}\n占用 Job：{state['activeOwner'] or '-'}\n代次：{state['generation']}"
                            f"\n建连恢复：{state.get('recovery', {}).get('state', '-')}"
                            f" · 尝试 {state.get('recovery', {}).get('attempt', 0)}"
                            f"\n显式保活：{state.get('explicitKeepAlive', 0)}"
                            f"\n触发流保活：{state.get('streamKeepAlive', False)}"
                            f"\n错误：{state.get('lifecycleError') or state.get('lastError') or '-'}")

    def refresh(self):
        self.loading = True
        try:
            self.tree.clear()
            allTypes = QTreeWidgetItem(["全部设备"])
            self.tree.addTopLevelItem(allTypes)
            categories = {}
            for key, declaration in sorted(self.deviceTypes.items()):
                category = categories.get(declaration.category)
                if category is None:
                    category = QTreeWidgetItem([declaration.category])
                    self.tree.addTopLevelItem(category)
                    categories[declaration.category] = category
                child = QTreeWidgetItem([declaration.displayName])
                child.setData(0, Qt.UserRole, key)
                category.addChild(child)
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
                          str(len(self.catalog().references(key))), state)
                for col, value in enumerate(values):
                    item = QTableWidgetItem(value)
                    item.setData(Qt.UserRole, key)
                    self.table.setItem(row, col, item)
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

    def _filter(self, item, previous):
        if self.loading:
            return
        typeId = item.data(0, Qt.UserRole) if item else None
        category = item.text(0) if item and item.childCount() else None
        definitions = self.c.session.workflows.devices.get("definitions", {})
        for row in range(self.table.rowCount()):
            definition = definitions[self.table.item(row, 0).data(Qt.UserRole)]
            declaration = self.deviceTypes.get(definition["deviceTypeId"])
            hidden = bool(typeId and typeId != definition["deviceTypeId"])
            if category:
                hidden = not declaration or declaration.category != category
            self.table.setRowHidden(row, hidden)

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
        self._showDetails()

    def _showDetails(self):
        self.original = deepcopy(self.c.session.workflows.devices.get("definitions", {}).get(self.selectedId))
        declaration = self.deviceTypes.get(self.original["deviceTypeId"]) if self.original else None
        self.details.setEnabled(bool(declaration))
        self.buttons["new"].setEnabled(bool(self.deviceTypes))
        self.buttons["delete"].setEnabled(self.original is not None)
        for key in ("apply", "revert"):
            self.buttons[key].setEnabled(bool(declaration))
        self.name.setText(self.original["name"] if self.original else "")
        self.description.setText(self.original.get("description", "") if self.original else "")
        self.enabled.setChecked(bool(self.original and self.original.get("enabled", True)))
        self.identity.setText(self.selectedId or "")
        if declaration:
            for key, editor in self.forms.items():
                schema = deepcopy(getattr(declaration, key + "Schema"))
                # Preserve absence for optional keep/auto settings instead of inventing a value.
                for name, spec in schema["properties"].items():
                    if spec.get("xUnit"):
                        spec["title"] = f"{spec.get('title', name)} ({spec['xUnit']})"
                    if name not in schema.get("required", []) and "default" not in spec:
                        spec["xOptionalPresence"] = True
                editor.setSchema(schema, self.original.get(key, {}))
                for name, spec in schema["properties"].items():
                    if len(spec.get("enum", [])) == 1:
                        editor._controls[name].setEnabled(False)
        else:
            for editor in self.forms.values():
                editor.setSchema({"type": "object", "properties": {}}, {})
        references = self.catalog().references(self.selectedId) if self.selectedId else []
        self.references.setText("\n".join(f"{workflow} / {node} / {slot}" for workflow, node, slot in references)
                                or "无节点引用")
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
        value = self._formValue()
        if value != self.formBaseline:
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
            dialog = DeviceDefinitionDialog(declaration, self)
            if dialog.exec_() != dialog.Accepted:
                return
            self.selectedId = self.catalog().put(dialog.value)
            self.refresh()
            self.c.chrome.update()
        except ValueError as error:
            self.status.setText(str(error))

    def deleteDevice(self):
        if not self.selectedId:
            return
        if QMessageBox.question(self, "删除设备", "从工程草稿删除所选设备？",
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
        self.timer.stop()
        self.reader.shutdown(wait=False, cancel_futures=True)
