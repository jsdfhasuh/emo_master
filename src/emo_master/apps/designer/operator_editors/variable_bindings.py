from copy import deepcopy

from PySide2.QtWidgets import QComboBox, QFormLayout, QGroupBox, QVBoxLayout, QWidget

from emo_master.core.project.global_variables import scalarFields, compatible, definitions, validateBindings, isFileParameter


class VariableBindingsPanel(QGroupBox):
    """Shared by generic and specialized editors; references are never params."""
    def __init__(self, context, parent=None):
        super().__init__("参数来源（固定值保留；引用在每次执行前读取）", parent)
        self.context = context
        self.fields = {}
        layout = QFormLayout(self)
        layout.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        self._populate(context.variableBindings)

    def needsRefresh(self, schema):
        return self._schema != schema or self._variables != self.context.variableDefinitions()

    def refresh(self):
        # Read without validating: removed variables/fields must remain visible
        # and removable, and un-applied bindings must survive a definition edit.
        selected = self.rawBindings()
        layout = self.layout()
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.hide()
                widget.deleteLater()
        self.fields = {}
        self._populate(selected)

    def _populate(self, bindings):
        context = self.context
        layout = self.layout()
        self._schema = deepcopy(context.paramSchema)
        self._variables = deepcopy(context.variableDefinitions())
        selected = {tuple(item["parameterPath"]): item["variableId"] for item in bindings}
        for path, spec in scalarFields(context.paramSchema):
            if spec.get("xGlobalVariableBindingDisabled") or isFileParameter(spec):
                continue
            if context.operatorId in {"vision.state.variable_read", "vision.state.variable_write"} and path == ("variableId",):
                continue
            combo = QComboBox()
            combo.setMinimumWidth(140)
            combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
            combo.addItem("固定值", "")
            for key, value in self._variables.items():
                if compatible(value["type"], spec["type"]):
                    combo.addItem(f'{value["name"]} ({value["type"]})', key)
            variable = selected.pop(path, "")
            if variable and combo.findData(variable) < 0:
                combo.addItem(f"失效引用：{variable}", variable)
            combo.setCurrentIndex(combo.findData(variable))
            combo.setToolTip(combo.currentText())
            combo.currentTextChanged.connect(combo.setToolTip)
            combo.currentIndexChanged.connect(lambda _index: context.markDirty())
            layout.addRow(str(spec.get("title") or ".".join(path)), combo)
            self.fields[path] = combo
        # A removed schema field must remain visible, not silently discard its binding.
        for path, variable in selected.items():
            combo = QComboBox()
            combo.addItem(f"失效参数 / 引用：{variable}", variable)
            combo.addItem("固定值（移除此绑定）", "")
            combo.setToolTip(combo.currentText())
            combo.currentTextChanged.connect(combo.setToolTip)
            combo.currentIndexChanged.connect(lambda _index: context.markDirty())
            layout.addRow(".".join(path), combo)
            self.fields[path] = combo

    def rawBindings(self):
        return [{"parameterPath": list(path), "variableId": combo.currentData()}
                for path, combo in self.fields.items() if combo.currentData()]

    def bindings(self):
        result = self.rawBindings()
        validateBindings(result, definitions(self.context.variableDefinitions()), self.context.paramSchema)
        return result

    def installInline(self, form):
        """Mirror selectors beside generic fields; keep the shared model alive on schema refresh."""
        for path, spec in scalarFields(self.context.paramSchema):
            if not isFileParameter(spec):
                continue
            target = form
            for part in path[:-1]:
                target = target._nestedFormsByControlId.get(id(target._controls.get(part)))
                if target is None:
                    break
            if target is not None and path[-1] in target._controls:
                target._controls[path[-1]].setToolTip(
                    "文件／目录路径暂不支持全局变量引用，请使用固定路径、资源绑定或现场参数配置。")
        if not self.context.variableDefinitions() and not self.rawBindings():
            return True
        import shiboken2
        installed = 0
        targetForms = set()
        for path, source in self.fields.items():
            target = form
            for part in path[:-1]:
                target = target._nestedFormsByControlId.get(id(target._controls.get(part)))
                if target is None:
                    break
            if target is None or path[-1] not in target._controls:
                continue
            control = target._controls[path[-1]]
            proxy = QComboBox()
            proxy.setMinimumWidth(130)
            proxy.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
            for index in range(source.count()):
                proxy.addItem(source.itemText(index), source.itemData(index))
            proxy.setCurrentIndex(source.currentIndex())
            hint = "参数来源：固定值 / 全局变量。引用在每次执行前读取；下方固定值保留但不参与执行。"
            proxy.setToolTip(proxy.currentText() + "\n" + hint)
            proxy.currentTextChanged.connect(lambda text, combo=proxy, tip=hint: combo.setToolTip(text + "\n" + tip))
            box = QWidget()
            layout = QVBoxLayout(box)
            layout.setContentsMargins(0, 0, 0, 0)
            target._layout.replaceWidget(control, box)
            layout.addWidget(proxy)
            layout.addWidget(control)
            target._fieldContainers[path[-1]] = box
            target._variableSources[path[-1]] = proxy
            targetForms.add(target)
            def selected(index, original=source, targetForm=target):
                original.setCurrentIndex(index)
                targetForm._updateDependentFields()
            proxy.currentIndexChanged.connect(selected)
            def synchronize(index, combo=proxy):
                if shiboken2.isValid(combo):
                    combo.setCurrentIndex(index)
            source.currentIndexChanged.connect(synchronize)
            installed += 1
        for targetForm in targetForms:
            targetForm._updateDependentFields()
        return installed == len(self.fields)
