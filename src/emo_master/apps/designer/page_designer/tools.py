"""Native drag/drop and schema property controls over RuntimePages widgets."""
import json

from PySide2.QtCore import Qt, QObject, QEvent, QMimeData, QTimer
from PySide2.QtGui import QDrag
from shiboken2 import isValid
from PySide2.QtWidgets import (
    QWidget, QFormLayout, QVBoxLayout, QHBoxLayout, QTreeWidget,
    QTreeWidgetItem, QPushButton, QLineEdit, QSpinBox, QComboBox, QLabel,
    QScrollArea, QInputDialog, QTableWidget, QTableWidgetItem, QToolButton, QMenu, QHeaderView,
    QSizePolicy, QLayout,
)

from emo_master.core.presentation.models import Props, Placement
from emo_master.core.presentation.catalog import buildOutputCatalog
from emo_master.core.presentation.validation import validateBindings, pageScopes
from emo_master.apps.designer.state.presentation_store import _component
from .editing import PageCommands, manifestsFromCatalog, outputChoices
from .palette import Palette
from emo_master.apps.designer.ui.widgets import ElidedLabel
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
            try:
                drag.exec_(Qt.CopyAction)
            finally:
                drag.deleteLater()


class EditingTools(QObject):
    def __init__(self, workspace, sidebar):
        super().__init__(workspace)
        self.w = workspace
        self.selected = None
        self.choices = []
        self.dragStart = None
        self.pendingRefresh = False
        self.refreshTimer = QTimer(self)
        self.refreshTimer.setSingleShot(True)
        self.refreshTimer.timeout.connect(self.finishRefresh)
        self.selection = None
        self.retainedSelection = None
        self.gridPreview = None
        self.hoverTarget = None
        self.bindingMarks = []
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
        self.outputs.setHeaderLabels(['流程结果 · 拖到组件'])
        self.outputs.setDragEnabled(True)
        self.outputs.setMinimumWidth(0)
        workspace.libraryTabs.addTab(self.outputs, '流程结果')
        panel = QWidget()
        self.form = QFormLayout(panel)
        self.form.setRowWrapPolicy(QFormLayout.WrapAllRows)
        self.form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        self.title = ElidedLabel('页面属性')
        self.form.addRow(self.title)
        self.propertyError = QLabel()
        self.propertyError.setStyleSheet('color:#b91c1c')
        self.propertyError.hide()
        self.form.addRow(self.propertyError)
        self.pageName = QLineEdit()
        self.form.addRow('页面名称', self.pageName)
        self.fields = {}
        for name, label in [('title', '标题'), ('text', '文字'), ('emptyText', '空值文字'), ('unit', '单位')]:
            field = QLineEdit()
            self.fields[name] = field
            self.form.addRow(label, field)
        for name, label, maximum, minimum in [('row', '行', 4095, 0), ('column', '列', 23, 0),
                ('rowSpan', '占用行数', 128, 1), ('columnSpan', '占用列数', 24, 1),
                ('decimals', '小数位', 12, 0), ('pageSize', '每页行数', 100, 1),
                ('columns', '页面/容器列数', 24, 1)]:
            field = QSpinBox()
            field.setRange(minimum, maximum)
            self.fields[name] = field
            self.form.addRow(label, field)
        self.destination = QComboBox()
        self.form.addRow('目标页面', self.destination)
        self.actionMode = QComboBox()
        for label, value in [('未配置', 'none'), ('跳转页面（实时）', 'navigate'),
                ('查看已显示结果详情', 'detail'), ('固定当前结果', 'freeze'), ('继续更新', 'resume_live')]:
            self.actionMode.addItem(label, value)
        self.form.addRow('点击后', self.actionMode)
        self.actionScope = QComboBox()
        self.form.addRow('结果所属流程', self.actionScope)
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
        self.form.addRow('数据来源', self.binding)
        self.bindingButtons = []
        self.operationButtons = []
        for label, fn in [('应用修改', self.apply), ('使用此来源', self.bindSelected),
                ('取消关联', self.clearBinding), ('复制控件', self.copy), ('删除控件', self.delete),
                ('定位流程节点', self.locate)]:
            button = QPushButton(label)
            button.clicked.connect(lambda _checked=False, command=fn: self.w.run(command))
            self.form.addRow(button)
            if label == '应用修改':
                self.applyButton = button
            if label in ('使用此来源', '取消关联', '定位流程节点'):
                self.bindingButtons.append(button)
            if label in ('复制控件', '删除控件'):
                self.operationButtons.append(button)
        self.extra = QTableWidget(0, 4)
        self.extra.setHorizontalHeaderLabels(['类型/列标题', '值/字段', '显示文字', '颜色'])
        self.extra.setMaximumHeight(170)
        self.extra.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.extra.horizontalHeader().setMinimumSectionSize(40)
        self.extra.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
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
        self.captureNotice = QLabel()
        self.captureNotice.setWordWrap(True)
        workspace.detailsLayout.addWidget(self.captureNotice)
        from .property_panel import PropertyGroups
        self.propertyGroups = PropertyGroups(self, panel)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(panel)
        scroll.setMinimumWidth(240)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        panel.setMinimumWidth(0)
        panel.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.form.setSizeConstraint(QLayout.SetNoConstraint)
        self.w.propertyScroll = scroll
        propertyPanel = QWidget()
        propertyPanel.setObjectName('pagePropertyPanel')
        outer = QVBoxLayout(propertyPanel)
        outer.setContentsMargins(8, 0, 8, 0)
        header = QHBoxLayout()
        self.title.setObjectName('panelTitle')
        self.title.setWordWrap(False)
        header.addWidget(self.title, 1)
        self.componentMenu = QMenu(propertyPanel)
        for button in self.operationButtons:
            action = self.componentMenu.addAction(button.text())
            action.triggered.connect(button.click)
        self.componentMore = QToolButton()
        self.componentMore.setText('操作')
        self.componentMore.setPopupMode(QToolButton.InstantPopup)
        self.componentMore.setMenu(self.componentMenu)
        header.addWidget(self.componentMore)
        outer.addLayout(header)
        outer.addWidget(self.propertyError)
        outer.addWidget(scroll, 1)
        self.applyButton.setObjectName('primaryButton')
        outer.addWidget(self.applyButton)
        self.w.splitter.addWidget(propertyPanel)
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
        for page in self.w.renderer.pages.values():
            # Reserve the editing scrollbar lane so resizing a nested component
            # cannot change its parent grid width midway through the gesture.
            page.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        for widget in [self.w.renderer, *self.w.renderer.findChildren(QWidget)]:
            if widget.property('editorDecoration'):
                continue
            widget.installEventFilter(self)
            widget.setAcceptDrops(True)
        if self.w.pageId in self.w.renderer.pages:
            body = self.w.renderer.pages[self.w.pageId].widget()
            grids = [(body, self.w.store.snapshot().pages[self.w.pageId].components)]
            for widget in body.findChildren(QWidget):
                key = widget.property('componentId')
                if key:
                    item = _component(self.w.store.snapshot(), self.w.pageId, key)
                    if item.type == 'container':
                        content = widget.containerTitle.body if hasattr(widget, 'containerTitle') else widget
                        grids.append((content, item.children))
            for widget, children in grids:
                row = max((c.layout.row + c.layout.rowSpan for c in children), default=0)
                widget.layout().setRowMinimumHeight(row, 64)
                widget.layout().setGeometry(widget.rect())
                widget.setToolTip('编辑模式下底部空白行为组件拖入区域')
        self.updateSelection()

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
        return (values, self.pageName.text(), self.destination.currentData(), self.actionMode.currentData(), self.actionScope.currentData(),
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
        self.componentMore.setEnabled(key is not None)
        self.applyButton.setEnabled(self.w.pageId is not None)
        self.updateSelection()
        try:
            component = _component(self.w.store.snapshot(), self.w.pageId, key)
        except KeyError:
            self.selected = None
            if self.w.pageId:
                self.fields['columns'].setValue(self.w.store.snapshot().pages[self.w.pageId].layout.columns)
                self.pageName.setText(self.w.store.snapshot().pages[self.w.pageId].name)
            self.title.setText('页面属性')
            self.propertyGroups.select(None)
            self._loadedPage = self.w.pageId
            self._loadedFields = self._fieldState()
            return
        from .palette import TITLES
        self.propertyGroups.select(component.type)
        self.title.setText(component.props.title or TITLES[component.type])
        self.title.setToolTip(self.title.text() + '\n组件编号：' + component.componentId)
        source = self.w.store.snapshot().dataSources.get(next(iter(component.bindings.values()), ''))
        if source:
            comparable = source.model_dump(exclude={'resultScopeId'})
            index = next((i for i, choice in enumerate(self.choices)
                          if choice.source.model_dump(exclude={'resultScopeId'}) == comparable), -1)
            self.binding.setCurrentIndex(index)
            self.title.setToolTip(self.title.toolTip() + '\n数据来源：' + str(source.nodeId or source.workflowId) + '.' + str(source.port))
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
        self.extra.setHorizontalHeaderLabels(['列标题', '选择字段', '', ''] if component.type == 'table' else ['类型', '值', '显示文字', '颜色'])
        self.extra.setColumnHidden(2, component.type == 'table')
        self.extra.setColumnHidden(3, component.type == 'table')
        self.extra.setToolTip('布尔值填写 true/false；文字值保持原文')
        data = [(c.title, tuple(c.fieldPath)) for c in component.props.columns] if component.type == 'table' else [
            (*decodeIndicatorKey(key), style.text, style.color) for key, style in component.props.indicatorStates.items()]
        for row in data:
            self.addExtraRow(values=row)
        self._loadedPage = self.w.pageId
        self._loadedFields = self._fieldState()

    def apply(self):
        try:
            self.applyFields()
            if not self.propertyError.isHidden():
                self.w.message.setText('修改已应用')
                self.w.coordinator.window.statusBar().clearMessage()
            self.propertyError.hide()
        except ValueError as error:
            from .property_panel import editorMessage
            message = str(error)
            self.propertyError.setText(editorMessage(message))
            self.propertyError.setToolTip(message)
            self.w.coordinator.window.appendRuntimeLog('ERROR', message)
            self.propertyError.show()
            field = next((widget for name, widget in self.fields.items() if name in message), self.extra
                         if any(word in message for word in ('boolean', 'duplicate', '字段')) else self.fields['columnSpan'])
            if not self.selected and 'name' in message:
                field = self.pageName
            self.w.propertyScroll.ensureWidgetVisible(field)
            field.setFocus()
            raise

    def applyFields(self):
        if not self.w.pageId:
            return
        columns = self.fields['columns'].value()
        if not self.selected:
            def editPage(p):
                p.pages[self.w.pageId].layout.columns = columns
                p.pages[self.w.pageId].name = self.pageName.text()
            self.w.session.editPresentation(editPage)
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

    def clearGridPreview(self):
        if self.gridPreview is not None and isValid(self.gridPreview):
            self.gridPreview.hide()
            self.gridPreview.deleteLater()
        self.gridPreview = None

    def showGridPreview(self, grid, columns, placement, message, valid, *, box=None):
        from .canvas_tools import GridPreview
        if self.gridPreview is not None and isValid(self.gridPreview) and self.gridPreview.parentWidget() is grid:
            self.gridPreview.columns, self.gridPreview.placement = columns, placement
            self.gridPreview.message, self.gridPreview.valid = message, valid
            self.gridPreview.box = box
            self.gridPreview.setGeometry(grid.rect())
            self.gridPreview.update()
            self.w.message.setText(message)
            return
        self.clearGridPreview()
        self.gridPreview = GridPreview(grid, columns, placement, message, valid, box)
        self.w.message.setText(message)

    def clearSelection(self):
        if self.selection and self.selection is not self.retainedSelection:
            self.selection.dispose()
            self.selection = None
        self.clearGridPreview()
        self.clearBindingMarks()
        self.hoverTarget = None
        self.dragStart = None

    def clearBindingMarks(self):
        for mark in self.bindingMarks:
            if isValid(mark):
                mark.hide()
                mark.deleteLater()
        self.bindingMarks.clear()

    def highlightBindings(self, index):
        if self.bindingMarks or self.w.pageId not in self.w.renderer.pages:
            return
        from .canvas_tools import Outline
        from .editing import ACCEPTED
        choice = self.choices[index]
        for card in self.w.renderer.pages[self.w.pageId].widget().findChildren(QWidget):
            key = card.property('componentId')
            if key:
                item = _component(self.w.store.snapshot(), self.w.pageId, key)
                valid = choice.source.expectedType in ACCEPTED.get(item.type, set())
                text = '类型兼容 · 松开校验绑定' if valid else '不兼容: ' + choice.source.expectedType + ' → ' + item.type
                mark = Outline(card, text, valid)
                mark.setGeometry(card.rect())
                mark.show()
                mark.raise_()
                self.bindingMarks.append(mark)

    def updateSelection(self):
        if self.retainedSelection is not None:
            return
        self.clearSelection()
        if self.selected and self.w.renderer.editing and self.w.pageId in self.w.renderer.pages:
            from .canvas_tools import Selection
            body = self.w.renderer.pages[self.w.pageId].widget()
            for card in body.findChildren(QWidget):
                if card.property('componentId') == self.selected:
                    self.selection = Selection(self, card, self.selected)
                    break

    def shutdown(self):
        self.refreshTimer.stop()
        self.pendingRefresh = False
        self.clearSelection()

    def drop(self, payload, widget, point, *, preview=False):
        if not self.w.renderer.editing or not self.w.pageId:
            raise ValueError('先创建页面并进入编辑模式')
        key, card = self.componentAt(widget)
        commands = self.commands().preview() if preview else self.commands()
        if 'choice' in payload:
            if not key:
                raise ValueError('将输出拖到兼容控件上')
            index = int(payload['choice'])
            choice = self.choices[index]
            if preview:
                commands.bind(self.w.pageId, key, choice)
                return
            name, ok = QInputDialog.getItem(self.w, '确认绑定', choice.title + '\n' + choice.hint,
                                           ['绑定到所选控件的值/图像/行'], 0, False)
            if ok:
                self.bindChoice(index, key)
            return
        parentId = None
        gridWidget = widget
        while gridWidget and not gridWidget.property('pageGrid'):
            if gridWidget.property('containerGridId'):
                parentId = gridWidget.property('containerGridId')
                break
            candidate = gridWidget.property('componentId')
            if candidate and _component(self.w.store.snapshot(), self.w.pageId, candidate).type == 'container':
                parentId = candidate
                if hasattr(gridWidget, 'containerTitle'):
                    gridWidget = gridWidget.containerTitle.body
                break
            gridWidget = gridWidget.parentWidget()
        if gridWidget is None:
            raise ValueError('请拖入页面网格')
        from .canvas_tools import cellAt
        position = gridWidget.mapFromGlobal(widget.mapToGlobal(point))
        columnCount = (_component(self.w.store.snapshot(), self.w.pageId, parentId).grid.columns if parentId
                       else self.w.store.snapshot().pages[self.w.pageId].layout.columns)
        row, col = cellAt(gridWidget, columnCount, position)
        if key and _component(self.w.store.snapshot(), self.w.pageId, key).type != 'container':
            occupied = _component(self.w.store.snapshot(), self.w.pageId, key).layout
            row, col = occupied.row, occupied.column
        if not preview and self.hoverTarget is not None:
            oldWidget, oldPoint, oldPayload, target = self.hoverTarget
            if oldWidget is widget and oldPoint == point and oldPayload == payload:
                gridWidget, columnCount, row, col, parentId = target
        if preview:
            self.hoverTarget = (widget, point, dict(payload), (gridWidget, columnCount, row, col, parentId))
        if 'move' in payload:
            commands.move(self.w.pageId, payload['move'], row, col, parentId)
            selected = payload['move']
        else:
            selected = commands.add(self.w.pageId, payload['kind'], row, col, parentId)
        if not preview:
            self.selected = selected
        return gridWidget, columnCount, row, col

    def laterRefresh(self):
        if not self.pendingRefresh:
            self.pendingRefresh = True
            self.refreshTimer.start(0)

    def finishRefresh(self):
        self.pendingRefresh = False
        if not self.w.closed:
            self.w.refresh()

    def eventFilter(self, obj, event):
        if self.w.closed or not isValid(self.w.renderer):
            return False
        if obj.property('editorDecoration'):
            return False
        kind = event.type()
        if kind in (QEvent.DragEnter, QEvent.DragMove) and event.mimeData().hasFormat(MIME):
            payload = json.loads(bytes(event.mimeData().data(MIME)))
            self.hoverTarget = None
            try:
                if 'choice' in payload:
                    self.highlightBindings(int(payload['choice']))
                target = self.drop(payload, obj, event.pos(), preview=True)
                if target:
                    grid, columns, row, column = target
                    placement = (_component(self.w.store.snapshot(), self.w.pageId, payload['move']).layout.model_copy()
                                 if 'move' in payload else Placement())
                    placement.row, placement.column = row, column
                    self.showGridPreview(grid, columns, placement, '合法位置 · 松开应用', True)
                else:
                    key, card = self.componentAt(obj)
                    self.showGridPreview(card, 1, Placement(), '类型兼容 · 松开确认绑定', True)
                event.acceptProposedAction()
            except (ValueError, KeyError, IndexError) as error:
                from .canvas_tools import describeError
                key, card = self.componentAt(obj)
                message = '不可放置: ' + describeError(error)
                self.w.message.setText(message)
                if card:
                    self.showGridPreview(card, 1, Placement(), message, False)
                elif self.hoverTarget:
                    grid, columns, row, column, _parent = self.hoverTarget[3]
                    self.showGridPreview(grid, columns, Placement(row=max(0, min(4095, row)),
                        column=max(0, min(23, column))), message, False)
                else:
                    self.clearGridPreview()
                # Keep receiving moves so another cell can become valid; drop
                # still runs the same authoritative validation before mutation.
                event.acceptProposedAction()
            return True
        if kind == QEvent.DragLeave:
            self.hoverTarget = None
            self.clearGridPreview()
            self.clearBindingMarks()
        if kind == QEvent.Drop and event.mimeData().hasFormat(MIME):
            self.clearGridPreview()
            self.clearBindingMarks()
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
            finally:
                self.hoverTarget = None
            return True
        key, _card = self.componentAt(obj)
        if kind == QEvent.ContextMenu and key:
            try:
                self.select(key)
                self.componentMenu.exec_(event.globalPos())
            except ValueError as error:
                self.w.message.setText(str(error))
            return True
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
                try:
                    drag.exec_(Qt.MoveAction)
                finally:
                    drag.deleteLater()
                    self.hoverTarget = None
                    self.clearGridPreview()
            return True
        if kind == QEvent.MouseButtonRelease and key:
            self.dragStart = None
            return True
        return False
