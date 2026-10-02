"""Native drag/drop and schema property controls over RuntimePages widgets."""
import json

from PySide2.QtCore import Qt, QObject, QEvent, QMimeData, QTimer
from PySide2.QtGui import QDrag
from shiboken2 import isValid
from PySide2.QtWidgets import (
    QWidget, QFormLayout, QVBoxLayout, QTreeWidget,
    QTreeWidgetItem, QPushButton, QLineEdit, QSpinBox, QComboBox, QLabel,
    QScrollArea, QInputDialog, QTableWidget, QTableWidgetItem, QFileDialog, QCheckBox, QToolButton, QMenu,
)

from emo_master.core.presentation.models import Props, Placement
from emo_master.core.presentation.catalog import buildOutputCatalog
from emo_master.core.presentation.validation import validateBindings, pageScopes
from emo_master.apps.designer.state.presentation_store import _component
from .editing import PageCommands, manifestsFromCatalog, outputChoices
from .palette import Palette
from .property_adapters import (decodeIndicatorKey, indicatorStatesFromRows,
                                tableFieldChoices, actionFromFields)

MIME = 'application/x-emo-page-edit'


def mime(payload):
    data = QMimeData()
    data.setData(MIME, json.dumps(payload).encode())
    return data


class Outputs(QTreeWidget):
    def startDrag(self, actions):
        item = self.currentItem()
        if item and item.data(0, Qt.UserRole) is not None:
            drag = QDrag(self)
            drag.setMimeData(mime({'choice': item.data(0, Qt.UserRole)}))
            drag.exec_(Qt.CopyAction)


class EditingTools(QObject):
    def __init__(self, workspace, sidebar):
        super().__init__(workspace)
        self.w = workspace
        self.selected = None
        self.choices = []
        self.dragStart = None
        self.pendingRefresh = False
        self._loadedFields = None
        self._loadedPage = None
        self.palette = Palette()
        library = QWidget()
        libraryLayout = QVBoxLayout(library)
        libraryLayout.setContentsMargins(0, 0, 0, 0)
        self.search = QLineEdit()
        self.search.setPlaceholderText('搜索组件')
        self.search.textChanged.connect(self.palette.search)
        libraryLayout.addWidget(self.search)
        libraryLayout.addWidget(self.palette)
        workspace.libraryTabs.addTab(library, '组件')
        self.outputs = Outputs()
        self.outputs.setHeaderLabels(['流程数据 · 拖到兼容组件'])
        self.outputs.setDragEnabled(True)
        self.outputs.setMinimumWidth(0)
        workspace.libraryTabs.addTab(self.outputs, '流程数据')
        panel = QWidget()
        self.form = QFormLayout(panel)
        self.form.setRowWrapPolicy(QFormLayout.WrapAllRows)
        self.form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        self.title = QLabel('选择控件编辑')
        self.form.addRow(self.title)
        self.fields = {}
        for name, label in [('title', '标题'), ('text', '文字'), ('emptyText', '空值文字'), ('unit', '单位')]:
            field = QLineEdit()
            self.fields[name] = field
            self.form.addRow(label, field)
        for name, label, maximum, minimum in [('row', '行', 4095, 0), ('column', '列', 23, 0),
                ('rowSpan', '行跨度', 128, 1), ('columnSpan', '列跨度', 24, 1),
                ('decimals', '小数位', 12, 0), ('pageSize', '每页行数', 100, 1),
                ('columns', '页面/容器列数', 24, 1)]:
            field = QSpinBox()
            field.setRange(minimum, maximum)
            self.fields[name] = field
            self.form.addRow(label, field)
        self.destination = QComboBox()
        self.form.addRow('导航目标（稳定 ID）', self.destination)
        self.actionMode = QComboBox()
        for label, value in [('未配置', 'none'), ('跳转页面（实时）', 'navigate'),
                ('查看已显示结果详情', 'detail'), ('冻结已显示结果', 'freeze'), ('恢复实时', 'resume_live')]:
            self.actionMode.addItem(label, value)
        self.form.addRow('只读按钮动作', self.actionMode)
        self.actionScope = QComboBox()
        self.form.addRow('已显示结果作用域', self.actionScope)
        self.appearance = {}
        for name, label, choices in [
            ('fontFamily', '字体', [('系统字体', 'system'), ('无衬线', 'sans'), ('衬线', 'serif'), ('等宽', 'monospace')]),
            ('fontWeight', '字重', [('常规', 'normal'), ('粗体', 'bold')]),
            ('textColor', '文字颜色', [('默认', 'default'), ('中性', 'neutral'), ('绿', 'green'), ('红', 'red'), ('琥珀', 'amber')]),
            ('cardStyle', '卡片样式', [('白底', 'plain'), ('浅色', 'soft'), ('轮廓', 'outlined')])]:
            combo = QComboBox()
            for title, value in choices:
                combo.addItem(title, value)
            self.appearance[name] = combo
            self.form.addRow(label, combo)
        self.fields['fontSize'] = QSpinBox()
        self.fields['fontSize'].setRange(0, 48)
        self.fields['fontSize'].setSpecialValueText('自动')
        self.form.addRow('字号（自动或8—48像素）', self.fields['fontSize'])
        self.binding = QComboBox()
        self.binding.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.binding.setMinimumContentsLength(10)
        self.form.addRow('输出绑定', self.binding)
        self.bindingButtons = []
        for label, fn in [('应用属性 / 布局', self.apply), ('绑定所选输出', self.bindSelected),
                ('清除绑定', self.clearBinding), ('复制控件', self.copy), ('删除控件', self.delete),
                ('从此来源定位流程节点', self.locate)]:
            button = QPushButton(label)
            button.clicked.connect(lambda _checked=False, command=fn: self.w.run(command))
            self.form.addRow(button)
            if label == '应用属性 / 布局':
                self.applyButton = button
            if label in ('绑定所选输出', '清除绑定', '从此来源定位流程节点'):
                self.bindingButtons.append(button)
        self.extra = QTableWidget(0, 4)
        self.extra.setHorizontalHeaderLabels(['类型/列标题', '值/字段', '显示文字', '颜色'])
        self.extra.setMaximumHeight(170)
        self.form.addRow('表格列 / 判定映射（最多16）', self.extra)
        add = self.extraAdd = QPushButton('增加列 / 映射')
        add.clicked.connect(self.addExtraRow)
        self.form.addRow(add)
        remove = self.extraRemove = QPushButton('删除所选列 / 映射')
        remove.clicked.connect(lambda: self.extra.removeRow(self.extra.currentRow()) if self.extra.currentRow() >= 0 else None)
        self.form.addRow(remove)
        self.fieldHint = QLabel('已知 Blob / Detection 字段可选择；未知结构不自动推断')
        self.fieldHint.setWordWrap(True)
        self.form.addRow(self.fieldHint)
        self.preview = QPushButton('交互预览')
        self.preview.setCheckable(True)
        self.preview.toggled.connect(self.previewMode)
        workspace.toolbar.addWidget(self.preview)
        self.simulation = QComboBox()
        for label, value in [('布局（无示例值）', None), ('模拟 OK', 'OK'), ('模拟 NG', 'NG'),
                             ('模拟等待', 'WAITING'), ('模拟错误', 'ERROR')]:
            self.simulation.addItem(label, value)
        self.simulation.currentIndexChanged.connect(lambda _index: self.changeSimulation())
        self.simulation.setToolTip('离线状态示例，不读写 Runtime')
        workspace.toolbar.addWidget(self.simulation)
        self.examples = QCheckBox('设计示例')
        self.examples.setChecked(True)
        self.examples.toggled.connect(self.changeExamples)
        workspace.renderer.designExamplesChanged.connect(self.syncExamples)
        workspace.toolbar.addWidget(self.examples)
        tasks = QToolButton()
        tasks.setText('任务观察 ▾')
        tasks.setPopupMode(QToolButton.InstantPopup)
        taskMenu = QMenu(tasks)
        tasks.setMenu(taskMenu)
        workspace.toolbar.addWidget(tasks)
        self.captureNotice = QLabel()
        self.captureNotice.setWordWrap(True)
        workspace.detailsLayout.addWidget(self.captureNotice)
        for text, command in [('登记本地输入图片', self.importInput),
                ('明确开始隔离草稿调试', workspace.coordinator.preview.startDebug),
                ('观看当前工程任务', workspace.coordinator.preview.watchCurrent),
                ('只读连接已有 Job', self.connectJob),
                ('停止自有调试 / 断开观察', workspace.coordinator.preview.closeAsync)]:
            button = QPushButton(text)
            button.clicked.connect(lambda _checked=False, fn=command: self.w.run(fn))
            action = taskMenu.addAction(text)
            action.triggered.connect(button.click)
            button.setParent(workspace)
            button.hide()
            if text == '观看当前工程任务':
                self.observer = QPushButton('弹出只读观察窗口')
                self.observer.setToolTip('复用已选择任务；包括内嵌页面最多两个共享窗口，关闭弹窗不会断开观察')
                self.observer.clicked.connect(lambda _checked=False: self.w.openObserver())
                action = taskMenu.addAction('弹出只读观察窗口')
                action.triggered.connect(self.observer.click)
                self.observer.setParent(workspace)
                self.observer.hide()
        from .property_panel import PropertyGroups
        self.propertyGroups = PropertyGroups(self, panel)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(panel)
        scroll.setMinimumWidth(240)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        panel.setMinimumWidth(0)
        self.w.propertyScroll = scroll
        self.w.splitter.addWidget(scroll)
        self.refreshCatalog()
        workspace.renderer.setDesignExamples(True)

    def commands(self):
        return PageCommands(self.w.session, manifestsFromCatalog(self.w.coordinator.window.operatorCatalog))

    def refreshCatalog(self):
        manifests = self.commands().manifests
        unsupported = []
        self.choices = outputChoices(self.w.session.document(), manifests,
            onUnsupported=lambda title, reason: unsupported.append((title, reason)))
        self.outputs.clear()
        self.binding.clear()
        groups = {}
        nodes = {}
        document = self.w.session.document()
        def friendly(node):
            manifest = manifests.get(node.operatorId)
            fallback = manifest.displayName if manifest else {'subflow': '子流程调用', 'loop': '循环调用'}.get(node.kind, '节点')
            return node.displayName or fallback + ' [' + node.nodeId[:8] + ']'
        for index, choice in enumerate(self.choices):
            address = (choice.source.workflowId, tuple((step.nodeId, step.relation) for step in choice.source.callPath))
            if address not in groups:
                names = []
                current = document.entryWorkflowId
                for step in choice.source.callPath:
                    caller = next((node for node in document.workflows[current].nodes if node.nodeId == step.nodeId), None)
                    names.append((friendly(caller) if caller else step.nodeId) + ' [' + step.nodeId[:8] + '] · ' + step.relation)
                    if caller:
                        current = caller.targetWorkflowId if step.relation == 'subflow' else caller.loop.get(
                            'bodyWorkflowId' if step.relation == 'loop_body' else 'conditionWorkflowId')
                path = ' / '.join(names) or '入口'
                group = QTreeWidgetItem([document.workflows[choice.source.workflowId].name + ' · ' + path])
                group.setToolTip(0, '/'.join(step.nodeId + ':' + step.relation for step in choice.source.callPath) or document.entryWorkflowId)
                group.setFlags(group.flags() & ~Qt.ItemIsDragEnabled)
                self.outputs.addTopLevelItem(group)
                groups[address] = group
            nodeAddress = (address, choice.source.nodeId)
            if nodeAddress not in nodes:
                instance = next((node for node in document.workflows[choice.source.workflowId].nodes
                                 if node.nodeId == choice.source.nodeId), None)
                name = friendly(instance) if instance else '工作流出口'
                node = QTreeWidgetItem([name])
                node.setToolTip(0, choice.source.nodeId or choice.source.workflowId)
                node.setFlags(node.flags() & ~Qt.ItemIsDragEnabled)
                groups[address].addChild(node)
                nodes[nodeAddress] = node
            item = QTreeWidgetItem([choice.source.port + ('.' + '.'.join(choice.source.fieldPath) if choice.source.fieldPath else '')
                                    + ' · ' + choice.source.expectedType])
            item.setData(0, Qt.UserRole, index)
            item.setToolTip(0, choice.title + '\n' + choice.hint)
            nodes[nodeAddress].addChild(item)
            readable = nodes[nodeAddress].text(0)[:24] + ' / ' + item.text(0)
            self.binding.addItem(readable, index)
            self.binding.setItemData(index, choice.title + '\n' + choice.hint, Qt.ToolTipRole)
        if unsupported:
            group = QTreeWidgetItem(['暂不支持的输出'])
            group.setFlags(group.flags() & ~Qt.ItemIsDragEnabled)
            self.outputs.addTopLevelItem(group)
            for title, reason in unsupported:
                item = QTreeWidgetItem([title + ' · ' + reason])
                item.setToolTip(0, reason)
                item.setFlags(item.flags() & ~Qt.ItemIsDragEnabled & ~Qt.ItemIsEnabled)
                group.addChild(item)
        self.outputs.expandAll()
        from emo_master.core.presentation.capture_limits import normalCaptureLimits
        try:
            limits = normalCaptureLimits(self.w.store.snapshot())
            lanes = len(set(limits['imageLaneBySource'].values()))
            self.captureNotice.setText(('双图：两个来源各限4 MiB（原单图额度8 MiB将同时降为4 MiB）' if lanes == 2 else
                '单图来源上限8 MiB' if lanes else '尚无图像来源') +
                '；不隐式缩放，超额明确不可用。实际来源尺寸/现场屏幕尚待确认；新来源仅下一次明确运行生效。')
        except (KeyError, ValueError) as error:
            self.captureNotice.setText('采集配置不可用：' + str(error))
        problems = validateBindings(self.w.session.document(), manifests)
        if problems:
            self.w.message.setText('\n'.join(f'{p.path}: {p.message}' for p in problems[:8]))

    def refresh(self):
        self.refreshCatalog()
        self.install()
        self.destination.clear()
        self.destination.addItem('未配置', None)
        for key in self.w.store.snapshot().pageOrder:
            self.destination.addItem(self.w.store.snapshot().pages[key].name, key)
        self.select(self.selected)

    def install(self):
        for widget in [self.w.renderer, *self.w.renderer.findChildren(QWidget)]:
            widget.installEventFilter(self)
            widget.setAcceptDrops(not self.preview.isChecked())
        if self.w.pageId in self.w.renderer.pages:
            body = self.w.renderer.pages[self.w.pageId].widget()
            grids = [(body, self.w.store.snapshot().pages[self.w.pageId].components)]
            for widget in body.findChildren(QWidget):
                key = widget.property('componentId')
                if key:
                    item = _component(self.w.store.snapshot(), self.w.pageId, key)
                    if item.type == 'container':
                        grids.append((widget, item.children))
            for widget, children in grids:
                row = max((c.layout.row + c.layout.rowSpan for c in children), default=0)
                widget.layout().setRowMinimumHeight(row, 0 if self.preview.isChecked() else 64)
                widget.setToolTip('编辑模式下底部空白行为组件拖入区域')

    def previewMode(self, preview):
        try:
            self.commitPending()
        except ValueError as error:
            self.w.message.setText(str(error))
            self.preview.blockSignals(True)
            self.preview.setChecked(not preview)
            self.preview.blockSignals(False)
            return
        self.w.renderer.editing = not preview
        self.preview.setText('返回编辑' if preview else '交互预览')
        if self.w.renderer.config != self.w.store.snapshot():
            self.w.refresh()
        self.changeSimulation()
        self.install()

    def changeSimulation(self):
        renderer = self.w.renderer
        if renderer.hub or renderer.captureCoverage is not None:
            if self.simulation.currentData() is not None:
                self.w.message.setText('请先断开任务观察，再使用离线模拟')
            self.simulation.blockSignals(True)
            self.simulation.setCurrentIndex(0)
            self.simulation.blockSignals(False)
            return
        wanted = self.examples.isChecked()
        renderer.setSimulationState(self.simulation.currentData() if self.preview.isChecked() else None)
        if self.simulation.currentData() is None or not self.preview.isChecked():
            if wanted:
                renderer.setDesignExamples(True)
            else:
                renderer.banner.setText('编辑模式 · 无设计示例 · 不运行设备')

    def syncExamples(self, enabled):
        self.examples.blockSignals(True)
        self.examples.setChecked(enabled)
        self.examples.blockSignals(False)

    def changeExamples(self, enabled):
        try:
            self.w.renderer.setDesignExamples(enabled)
            if enabled:
                self.simulation.blockSignals(True)
                self.simulation.setCurrentIndex(0)
                self.simulation.blockSignals(False)
        except ValueError as error:
            self.w.message.setText(str(error))
            self.examples.blockSignals(True)
            self.examples.setChecked(False)
            self.examples.blockSignals(False)

    def _cellValue(self, row, column):
        widget = self.extra.cellWidget(row, column)
        if isinstance(widget, QComboBox):
            return widget.currentData()
        item = self.extra.item(row, column)
        return item.text() if item else ''

    def _tableChoices(self):
        item = _component(self.w.store.snapshot(), self.w.pageId, self.selected)
        source = self.w.store.snapshot().dataSources.get(item.bindings.get('rows'))
        if source is None:
            raise ValueError('先绑定已知集合输出，再选择表格字段')
        return tableFieldChoices(source, buildOutputCatalog(self.w.session.document(), self.commands().manifests))

    def addExtraRow(self, _checked=False, *, values=None):
        if self.extra.rowCount() >= 16 or not self.selected:
            return
        item = _component(self.w.store.snapshot(), self.w.pageId, self.selected)
        row = self.extra.rowCount()
        if item.type == 'indicator':
            kind, value, label, color = values or ('boolean', 'true', 'OK', 'green')
            self.extra.insertRow(row)
            types = QComboBox()
            types.addItem('布尔', 'boolean')
            types.addItem('文字', 'string')
            types.setCurrentIndex(types.findData(kind))
            self.extra.setCellWidget(row, 0, types)
            self.extra.setItem(row, 1, QTableWidgetItem(str(value).lower() if type(value) is bool else value))
            self.extra.setItem(row, 2, QTableWidgetItem(label))
            colors = QComboBox()
            for title, key in [('中性', 'neutral'), ('绿', 'green'), ('红', 'red'), ('琥珀', 'amber')]:
                colors.addItem(title, key)
            colors.setCurrentIndex(colors.findData(color))
            self.extra.setCellWidget(row, 3, colors)
        elif item.type == 'table':
            try:
                choices = self._tableChoices()
                self.fieldHint.setText('字段来自受信任输出契约；集合投影不自动取第一项')
            except ValueError as error:
                self.fieldHint.setText(str(error))
                if values is None:
                    return
                choices = []
            title, path = values or (choices[0].title, tuple(choices[0].fieldPath))
            self.extra.insertRow(row)
            self.extra.setItem(row, 0, QTableWidgetItem(title))
            fields = QComboBox()
            for choice in choices:
                fields.addItem(choice.title, tuple(choice.fieldPath))
            # Qt's QVariant matching does not compare Python tuple payloads.
            index = next((i for i in range(fields.count()) if fields.itemData(i) == tuple(path)), -1)
            if index < 0:
                fields.addItem('已有字段（保留）: ' + '.'.join(path), tuple(path))
                index = fields.count() - 1
            fields.setCurrentIndex(index)
            self.extra.setCellWidget(row, 1, fields)

    def _fieldState(self):
        values = tuple((name, field.text() if isinstance(field, QLineEdit) else field.value())
                       for name, field in self.fields.items())
        rows = tuple(tuple(self._cellValue(r, c) for c in range(4)) for r in range(self.extra.rowCount()))
        return (values, self.destination.currentData(), self.actionMode.currentData(), self.actionScope.currentData(),
                tuple((name, field.currentData()) for name, field in self.appearance.items()), rows)

    def commitPending(self):
        """Save the visible form first; invalid input stays visible and blocks leaving."""
        if self._loadedFields is None or self._fieldState() == self._loadedFields:
            return
        if self._loadedPage != self.w.pageId:
            raise ValueError('属性输入仍属于上一页面，请先应用或修正后再切页')
        self.apply()

    def select(self, key):
        if key != self.selected:
            self.commitPending()
        self.selected = key
        try:
            component = _component(self.w.store.snapshot(), self.w.pageId, key)
        except KeyError:
            self.selected = None
            if self.w.pageId:
                self.fields['columns'].setValue(self.w.store.snapshot().pages[self.w.pageId].layout.columns)
            self.title.setText('页面属性 · 可调整页面列数')
            self.propertyGroups.select(None)
            self._loadedPage = self.w.pageId
            self._loadedFields = self._fieldState()
            return
        from .palette import TITLES
        self.propertyGroups.select(component.type)
        self.title.setText(f'{TITLES[component.type]} · {component.componentId[:8]}')
        source = self.w.store.snapshot().dataSources.get(next(iter(component.bindings.values()), ''))
        if source:
            comparable = source.model_dump(exclude={'resultScopeId'})
            index = next((i for i, choice in enumerate(self.choices)
                          if choice.source.model_dump(exclude={'resultScopeId'}) == comparable), -1)
            self.binding.setCurrentIndex(index)
            self.title.setText(self.title.text() + '\n已绑定: ' + str(source.nodeId or source.workflowId)[:16] + '.' + str(source.port))
            self.title.setToolTip(str(source.nodeId or source.workflowId) + '.' + str(source.port))
            self.title.setWordWrap(True)
        for name in ['title', 'text', 'emptyText', 'unit']:
            self.fields[name].setText(getattr(component.props, name))
        for name in ['row', 'column', 'rowSpan', 'columnSpan']:
            self.fields[name].setValue(getattr(component.layout, name))
        for name in ['decimals', 'pageSize', 'fontSize']:
            self.fields[name].setValue(getattr(component.props, name))
        for name, field in self.appearance.items():
            field.setCurrentIndex(field.findData(getattr(component.props, name)))
        self.fields['columns'].setValue(component.grid.columns if component.type == 'container'
                                       else self.w.store.snapshot().pages[self.w.pageId].layout.columns)
        action = component.actions.get('clicked')
        self.destination.setCurrentIndex(max(0, self.destination.findData(action.pageId if action else None)))
        self.destination.setEnabled(component.type == 'navigation_button')
        self.actionMode.setEnabled(component.type == 'navigation_button')
        mode = 'none' if action is None else 'detail' if action.type == 'navigate' and action.context == 'displayed_result' else action.type
        self.actionMode.setCurrentIndex(self.actionMode.findData(mode))
        self.actionScope.clear()
        self.actionScope.addItem('未选择', None)
        p = self.w.store.snapshot()
        for key in sorted(pageScopes(p, self.w.pageId)):
            scope = p.resultScopes.get(key)
            if scope is None:
                self.actionScope.addItem('来源作用域缺失 · ' + key, key)
                continue
            workflow = self.w.session.document().workflows.get(scope.scopeWorkflowId)
            self.actionScope.addItem((workflow.name if workflow else scope.scopeWorkflowId) + ' · ' + key, key)
        self.actionScope.setCurrentIndex(max(0, self.actionScope.findData(action.resultScopeId if action else None)))
        self.actionScope.setEnabled(component.type == 'navigation_button')
        self.extra.setRowCount(0)
        self.extra.setHorizontalHeaderLabels(['列标题', '选择字段', '', ''] if component.type == 'table' else ['类型', '值（布尔填true/false）', '显示文字', '颜色'])
        data = [(c.title, tuple(c.fieldPath)) for c in component.props.columns] if component.type == 'table' else [
            (*decodeIndicatorKey(key), style.text, style.color) for key, style in component.props.indicatorStates.items()]
        for row in data:
            self.addExtraRow(values=row)
        self._loadedPage = self.w.pageId
        self._loadedFields = self._fieldState()

    def apply(self):
        if not self.w.pageId:
            return
        columns = self.fields['columns'].value()
        if not self.selected:
            self.w.session.editPresentation(lambda p: setattr(p.pages[self.w.pageId].layout, 'columns', columns))
            self._loadedFields = self._fieldState()
            return
        item = _component(self.w.store.snapshot(), self.w.pageId, self.selected)
        props = item.props.model_dump()
        from .property_panel import CONTENT
        applicable = CONTENT[item.type] | {'fontSize'}
        props.update({key: self.fields[key].text() for key in ['title', 'text', 'emptyText', 'unit'] if key in applicable})
        props.update({key: self.fields[key].value() for key in ['decimals', 'pageSize', 'fontSize'] if key in applicable})
        props.update({key: field.currentData() for key, field in self.appearance.items()})
        rows = [[self._cellValue(r, c) for c in range(4)]
                for r in range(self.extra.rowCount())]
        if item.type == 'table':
            if any(not isinstance(path, (list, tuple)) for _, path, _, _ in rows):
                raise ValueError('请选择已知表格字段；未知结构不能自动推断')
            props['columns'] = [{'title': title, 'fieldPath': list(path)} for title, path, _, _ in rows]
        if item.type == 'indicator':
            props['indicatorStates'] = indicatorStatesFromRows(rows)
        layout = Placement(**{key: self.fields[key].value() for key in ['row', 'column', 'rowSpan', 'columnSpan']})
        actions = item.actions
        if item.type == 'navigation_button':
            mode = self.actionMode.currentData()
            action = actionFromFields(self.w.store.snapshot(), self.w.pageId, mode,
                targetPageId=self.destination.currentData() if mode in ('navigate', 'detail') else None,
                resultScopeId=self.actionScope.currentData() if mode in ('detail', 'freeze') else None)
            actions = {'clicked': action} if action else {}
        self.commands().update(self.w.pageId, self.selected, props=Props(**props), layout=layout,
            actions=actions, columns=columns if item.type == 'container' else None)
        self._loadedFields = self._fieldState()

    def bindChoice(self, index, key):
        choice = self.choices[index]
        limits = self.commands().bind(self.w.pageId, key, choice)
        dual = len(set(limits['imageLaneBySource'].values())) == 2
        notice = '双图来源各限4 MiB；原8 MiB单图额度将降为4 MiB，不缩放，超额不可用。' if dual else ''
        self.w.message.setText((choice.hint + '\n' if choice.hint else '') + notice + '绑定已保存；新来源在下一次明确运行生效；隔离调试保留其专用限制')

    def bindSelected(self):
        if self.selected and self.binding.currentIndex() >= 0:
            self.bindChoice(self.binding.currentData(), self.selected)

    def clearBinding(self):
        if self.selected:
            def edit(p):
                _component(p, self.w.pageId, self.selected).bindings.clear()
            self.w.session.editPresentation(edit)

    def delete(self):
        if self.selected:
            self.commands().delete(self.w.pageId, self.selected)
            self.selected = None

    def copy(self):
        if self.selected:
            self.selected = self.commands().copy(self.w.pageId, self.selected)

    def locate(self):
        if self.selected:
            item = _component(self.w.store.snapshot(), self.w.pageId, self.selected)
            source = self.w.store.snapshot().dataSources.get(next(iter(item.bindings.values()), ''))
            if source and source.nodeId:
                self.w.coordinator.window.activateWorkflow(source.workflowId)
                self.w.coordinator.showFlow()
                self.w.coordinator.window.navigateToNodeFromSidebar(source.nodeId)

    def componentAt(self, widget):
        while widget and widget is not self.w.renderer:
            key = widget.property('componentId')
            if key:
                return key, widget
            widget = widget.parentWidget()
        return None, None

    def drop(self, payload, widget, point):
        if self.preview.isChecked() or not self.w.pageId:
            raise ValueError('先创建页面并进入编辑模式')
        key, card = self.componentAt(widget)
        if 'choice' in payload:
            if not key:
                raise ValueError('将输出拖到兼容控件上')
            index = int(payload['choice'])
            choice = self.choices[index]
            name, ok = QInputDialog.getItem(self.w, '确认绑定', choice.title + '\n' + choice.hint,
                                           ['绑定到所选控件的值/图像/行'], 0, False)
            if ok:
                self.bindChoice(index, key)
            return
        parentId = None
        gridWidget = widget
        while gridWidget and not gridWidget.property('pageGrid'):
            candidate = gridWidget.property('componentId')
            if candidate and _component(self.w.store.snapshot(), self.w.pageId, candidate).type == 'container':
                parentId = candidate
                break
            gridWidget = gridWidget.parentWidget()
        if gridWidget is None:
            raise ValueError('请拖入页面网格')
        position = gridWidget.mapFromGlobal(widget.mapToGlobal(point))
        layout = gridWidget.layout()
        layout.activate()
        columnCount = (_component(self.w.store.snapshot(), self.w.pageId, parentId).grid.columns if parentId
                       else self.w.store.snapshot().pages[self.w.pageId].layout.columns)
        col = min(columnCount-1, max(0, position.x() * columnCount // max(1, gridWidget.width())))
        row = 0
        for r in range(layout.rowCount()):
            rect = layout.cellRect(r, col)
            if position.y() <= rect.bottom():
                row = r
                break
        else:
            row = layout.rowCount() if layout.count() else 0
        if key and _component(self.w.store.snapshot(), self.w.pageId, key).type != 'container':
            occupied = _component(self.w.store.snapshot(), self.w.pageId, key).layout
            row, col = occupied.row, occupied.column
        if 'move' in payload:
            self.commands().move(self.w.pageId, payload['move'], row, col, parentId)
            self.selected = payload['move']
        else:
            self.selected = self.commands().add(self.w.pageId, payload['kind'], row, col, parentId)

    def laterRefresh(self):
        if not self.pendingRefresh:
            self.pendingRefresh = True
            QTimer.singleShot(0, self.finishRefresh)

    def finishRefresh(self):
        self.pendingRefresh = False
        if not self.w.closed:
            self.w.refresh()

    def connectJob(self):
        address, ok = QInputDialog.getText(self.w, '只读连接', 'loopback 地址（如 127.0.0.1:50051）')
        if not ok:
            return
        job, ok = QInputDialog.getText(self.w, '明确选择任务', '已有展示 Job ID（不启动检测）')
        if ok:
            self.w.coordinator.preview.connect(address, job)

    def importInput(self):
        from .resources import registerImage
        document = self.w.session.document()
        nodes = [(workflowId, node.nodeId, f'{workflow.name}/{node.displayName or node.nodeId} [{node.nodeId}]')
                 for workflowId, workflow in document.workflows.items() for node in workflow.nodes
                 if node.operatorId == 'vision.io.image_loader']
        if not nodes:
            raise ValueError('请先在流程设计中添加 ImageLoader 节点')
        label, ok = QInputDialog.getItem(self.w, '输入资源目标', '明确选择 ImageLoader 实例',
            [n[2] for n in nodes], 0, False)
        if not ok:
            return
        workflow, node, _ = next(n for n in nodes if n[2] == label)
        path, _ = QFileDialog.getOpenFileName(self.w, '选择本地测试图片', '', 'Images (*.png *.jpg *.jpeg *.bmp)')
        if path:
            registerImage(self.w.session, self.w.coordinator.directory, workflow, node, path)
            self.w.message.setText('图片已复制为声明资源；下一次明确调试使用新快照')

    def eventFilter(self, obj, event):
        if self.w.closed or not isValid(self.preview) or self.preview.isChecked():
            return False
        kind = event.type()
        if kind in (QEvent.DragEnter, QEvent.DragMove) and event.mimeData().hasFormat(MIME):
            event.acceptProposedAction()
            return True
        if kind == QEvent.Drop and event.mimeData().hasFormat(MIME):
            try:
                # Match button commands: validate/commit the form before a drop
                # mutates the draft and its deferred refresh reloads the inputs.
                self.w.coordinator.sync()
                self.drop(json.loads(bytes(event.mimeData().data(MIME))), obj, event.pos())
                event.acceptProposedAction()
                self.laterRefresh()
            except (ValueError, KeyError, IndexError) as error:
                self.w.message.setText(str(error))
                event.ignore()
            return True
        key, _card = self.componentAt(obj)
        if kind == QEvent.MouseButtonPress and event.button() == Qt.LeftButton and not key:
            try:
                self.select(None)
            except ValueError as error:
                self.w.message.setText(str(error))
                return True
        if kind == QEvent.MouseButtonPress and event.button() == Qt.LeftButton and key:
            try:
                # Starting a move of the selected component must retain its
                # pending form until the drop synchronizes it.
                if key != self.selected:
                    self.select(key)
            except ValueError as error:
                self.w.message.setText(str(error))
                return True
            self.dragStart = (key, event.globalPos())
            return True
        if kind == QEvent.MouseMove and self.dragStart and event.buttons() & Qt.LeftButton:
            key, start = self.dragStart
            if (event.globalPos() - start).manhattanLength() >= 10:
                self.dragStart = None
                drag = QDrag(self.w)
                drag.setMimeData(mime({'move': key}))
                drag.exec_(Qt.MoveAction)
            return True
        if kind == QEvent.MouseButtonRelease and key:
            self.dragStart = None
            return True
        return False
