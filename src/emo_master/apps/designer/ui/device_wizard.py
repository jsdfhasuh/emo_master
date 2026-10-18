"""Offline device and node drafts with an atomic Finish boundary."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from uuid import uuid4

from PySide2.QtCore import QTimer
from PySide2.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QLabel, QLineEdit,
    QDialogButtonBox, QTabWidget, QScrollArea, QWidget, QComboBox, QPushButton, QMessageBox,
)

from emo_master.apps.designer.state.device_catalog import (DeviceCatalogEditor, changeParameterSource,
    migrateLegacySources, migrateLegacyResourceTargets)
from emo_master.apps.designer.state.schema_utils import applySchemaDefaults
from emo_master.apps.designer.ui.param_form import SchemaParamForm
from emo_master.core.project.devices import DeviceDefinition


def configurationForm(schema, values):
    schema = deepcopy(schema)
    for key, spec in schema.get("properties", {}).items():
        if spec.get("xUnit"):
            spec["title"] = f"{spec.get('title', key)} ({spec['xUnit']})"
        if key not in schema.get("required", []) and "default" not in spec:
            spec["xOptionalPresence"] = True
    form = SchemaParamForm()
    form.setSchema(schema, values)
    for key, spec in schema.get("properties", {}).items():
        if len(spec.get("enum", [])) == 1:
            form._controls[key].setEnabled(False)
    return form


def scrollWidget(widget):
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setWidget(widget)
    return scroll


class DeviceDefinitionDialog(QDialog):
    def __init__(self, declaration, parent=None, *, initial=None):
        super().__init__(parent)
        self.declaration = declaration
        self.value = None
        self.setWindowTitle("新建设备 · " + declaration.displayName)
        self.resize(600, 540)
        layout = QVBoxLayout(self)
        self.name = QLineEdit(declaration.displayName)
        names = QFormLayout()
        names.addRow("设备名称", self.name)
        layout.addLayout(names)
        tabs = QTabWidget()
        self.forms = {}
        for key, title in (("connection", "连接"), ("defaults", "工作参数"), ("policies", "策略")):
            schema = getattr(declaration, key + "Schema")
            self.forms[key] = configurationForm(schema, applySchemaDefaults(schema, (initial or {}).get(key, {})))
            tabs.addTab(scrollWidget(self.forms[key]), title)
        layout.addWidget(tabs)
        self.error = QLabel()
        self.error.setWordWrap(True)
        layout.addWidget(self.error)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("创建")
        buttons.button(QDialogButtonBox.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def accept(self):
        try:
            definition = DeviceDefinition(name=self.name.text().strip(), deviceTypeId=self.declaration.deviceTypeId,
                                          driverVersion=self.declaration.version,
                                          **{key: form.getValues() for key, form in self.forms.items()})
            self.value = self.declaration.validateDefinition(definition).model_dump()
        except ValueError as error:
            self.error.setText(str(error))
            return
        super().accept()


class SourceEditor(QWidget):
    def __init__(self, declaration, definition, node, slot, variables):
        super().__init__()
        self.rows = {}
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
                if kind == spec["type"]:
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

    def apply(self, node, slot):
        for path, (modes, form, variable, inputs) in self.rows.items():
            source = modes.currentData()
            value = (form.getValues()["value"] if source == "constant" else
                     variable.currentData() if source == "variable" else inputs.currentData())
            node = changeParameterSource(node, slot, path, source=source, value=value)
        return node


class DeviceNodeDialog(QDialog):
    def __init__(self, coordinator, node, requirements, parent=None, *, position=None):
        super().__init__(parent)
        self.c, self.node, self.requirements = coordinator, deepcopy(node), requirements
        self.projectSignature = coordinator.session._signature()
        self.workflowId = coordinator.window.activeWorkflowId
        self.runtimeScope = self._runtimeScope()
        self.types = {}
        self.position = position
        self.newDefinitions = {}
        self.slots = {}
        self.sources = {}
        self.setWindowTitle("设备绑定与参数 · " + str(node.get("displayName") or node["operatorId"]))
        self.resize(700, 650)
        layout = QVBoxLayout(self)
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
        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.button(QDialogButtonBox.Ok).setText("完成")
        self.buttons.button(QDialogButtonBox.Cancel).setText("取消")
        self.buttons.button(QDialogButtonBox.Ok).setEnabled(False)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self.reader = ThreadPoolExecutor(max_workers=1, thread_name_prefix="device-wizard")
        reader = getattr(coordinator.window.runtimeClient, "listDeviceTypes", None)
        self.pending = self.reader.submit(reader) if reader else None
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._ready)
        self.timer.start(30)
        self.finished.connect(self._finish)

    def _finish(self, _result):
        self.timer.stop()
        self.reader.shutdown(wait=False, cancel_futures=True)

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
                combo.setCurrentIndex(max(0, combo.findData(deviceId)))
                self.tabs.addTab(scrollWidget(page), slot)
            self.error.setText("")
            self.buttons.button(QDialogButtonBox.Ok).setEnabled(True)
        except Exception as error:
            self.error.setText(str(error))

    def _create(self, slot):
        declaration = self.types[self.requirements[slot]["deviceTypeId"]]
        initial = None
        if self.actionFields is not None and not self.node.get("deviceBindings"):
            legacy = applySchemaDefaults(self.node.get("paramSchema", {}), self.node.get("params", {}))
            if declaration.deviceTypeId == "device.camera.huaray_imv" and legacy.get("waitMode") == "hardware":
                legacy["captureTimeoutMs"] = 0
            initial = {group: {key: value for key, value in legacy.items()
                               if key in getattr(declaration, group + "Schema")["properties"]}
                       for group in ("connection", "defaults")}
        dialog = DeviceDefinitionDialog(declaration, self, initial=initial)
        if dialog.exec_() == dialog.Accepted:
            from emo_master.core.project.device_identity import matchingDevices
            definitions = {**self.c.session.workflows.devices.get('definitions', {}), **self.newDefinitions}
            matches = matchingDevices(dialog.value, definitions, declaration)
            if matches:
                if len(matches) != 1:
                    self.error.setText('多个已有设备使用相同标识，请先处理重复定义：' + ', '.join(matches))
                    return
                key = matches[0]
                label = f"{definitions[key]['name']} ({key})"
                if QMessageBox.question(self, '复用已有设备',
                        '相同设备标识已存在：' + label + '\n复用已有设备？刚输入的默认值和策略不会覆盖已有配置。',
                        QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes:
                    combo = self.slots[slot][0]
                    if combo.findData(key) < 0:
                        combo.addItem(label, key)
                    combo.setCurrentIndex(combo.findData(key))
                return
            key = "device-" + uuid4().hex
            self.newDefinitions[key] = dialog.value
            combo = self.slots[slot][0]
            combo.addItem(f"{dialog.value['name']} ({key})", key)
            combo.setCurrentIndex(combo.count() - 1)

    def _sources(self, slot):
        combo, layout = self.slots[slot]
        old = self.sources.pop(slot, None)
        if old is not None:
            layout.removeWidget(old)
            old.deleteLater()
        key = combo.currentData()
        if key is None:
            return
        definitions = {**self.c.session.workflows.devices.get("definitions", {}), **self.newDefinitions}
        declaration = self.types[definitions[key]["deviceTypeId"]]
        editorNode = self.node
        if declaration.deviceTypeId == "device.camera.huaray_imv" and not self.node.get("deviceBindings"):
            editorNode = deepcopy(self.node)
            legacy = applySchemaDefaults(self.node.get("paramSchema", {}), self.node.get("params", {}))
            if legacy.get("waitMode") == "hardware":
                legacy["captureTimeoutMs"] = 0
            editorNode.setdefault("deviceOverrides", {})[slot] = {
                path: legacy[path] for path in declaration.overridePaths if path in legacy}
            for rule in declaration.parameterRules:
                if rule.whenOverridden and any(legacy.get(key) != value for key, value in rule.requires.items()):
                    editorNode["deviceOverrides"][slot].pop(rule.present, None)
        if self.actionFields is not None and not self.node.get("deviceBindings"):
            try:
                editorNode = migrateLegacySources(editorNode, slot, key, declaration, self.actionFields)
            except ValueError as error:
                self.error.setText(str(error))
        editor = SourceEditor(declaration, definitions[key], editorNode, slot,
                              self.c.session.document().globalVariables)
        self.sources[slot] = editor
        layout.addWidget(editor)

    def accept(self):
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
                    raise ValueError("请选择设备：" + slot)
                draft.setdefault("deviceBindings", {})[slot] = {"deviceId": key}
                declaration = self.types[definitions[key]["deviceTypeId"]]
                if self.actionFields is not None and not self.node.get("deviceBindings"):
                    draft = migrateLegacySources(draft, slot, key, declaration, self.actionFields)
                    if resourcePlan is not None:
                        resourcePlan, changes = migrateLegacyResourceTargets(resourcePlan, self.workflowId,
                            draft["nodeId"], key, declaration, self.actionFields)
                        moved.extend(changes)
                draft = self.sources[slot].apply(draft, slot)
            if (self.actionFields is not None and not self.node.get("deviceBindings")
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
                               position=self.position, resourcePlan=resourcePlan)
            self.c.window.workflowController._renderActive()
            self.c.chrome.update()
        except (ValueError, KeyError) as error:
            self.error.setText(str(error))
            return
        super().accept()
