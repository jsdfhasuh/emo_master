"""Native drag/drop and schema property controls over RuntimePages widgets."""
import json

from PySide2.QtCore import Qt, QObject, QEvent, QMimeData, QTimer
from PySide2.QtGui import QDrag
from PySide2.QtWidgets import (
    QWidget, QFormLayout, QListWidget, QListWidgetItem, QTreeWidget,
    QTreeWidgetItem, QPushButton, QLineEdit, QSpinBox, QComboBox, QLabel,
    QScrollArea, QInputDialog, QTableWidget, QTableWidgetItem, QFileDialog,
)

from emo_master.core.presentation.models import Props, Placement, Action
from emo_master.core.presentation.validation import validateBindings
from emo_master.apps.designer.state.presentation_store import _component
from .editing import PageCommands, manifestsFromCatalog, outputChoices

MIME = 'application/x-emo-page-edit'


def mime(payload):
    data = QMimeData()
    data.setData(MIME, json.dumps(payload).encode())
    return data


class Palette(QListWidget):
    def startDrag(self, actions):
        item = self.currentItem()
        if item:
            drag = QDrag(self)
            drag.setMimeData(mime({'kind': item.data(Qt.UserRole)}))
            drag.exec_(Qt.CopyAction)


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
        self.palette.setDragEnabled(True)
        self.palette.setMaximumHeight(145)
        for kind, title in [('image', '图像'), ('number', '数值'), ('text', '文字'),
                ('indicator', '判定指示'), ('table', '集合表格'), ('navigation_button', '导航按钮'),
                ('container', '容器'), ('runtime_status', '客户端连接状态（无业务绑定）')]:
            item = QListWidgetItem(title)
            item.setData(Qt.UserRole, kind)
            self.palette.addItem(item)
        sidebar.insertWidget(0, QLabel('组件 · 拖入网格'))
        sidebar.insertWidget(1, self.palette)
        self.outputs = Outputs()
        self.outputs.setHeaderLabels(['流程数据 · 静态目录，不运行算子'])
        self.outputs.setDragEnabled(True)
        self.outputs.setMinimumWidth(240)
        sidebar.insertWidget(2, self.outputs)
        panel = QWidget()
        self.form = QFormLayout(panel)
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
        self.binding = QComboBox()
        self.binding.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.binding.setMinimumContentsLength(10)
        self.form.addRow('输出绑定', self.binding)
        for label, fn in [('应用属性 / 布局', self.apply), ('绑定所选输出', self.bindSelected),
                ('清除绑定', self.clearBinding), ('复制控件', self.copy), ('删除控件', self.delete),
                ('从此来源定位流程节点', self.locate)]:
            button = QPushButton(label)
            button.clicked.connect(lambda _checked=False, command=fn: self.w.run(command))
            self.form.addRow(button)
        self.extra = QTableWidget(0, 3)
        self.extra.setHorizontalHeaderLabels(['值/列标题', '文字/字段路径', '颜色'])
        self.extra.setMaximumHeight(170)
        self.form.addRow('表格列 / 判定映射（最多16）', self.extra)
        add = QPushButton('增加列 / 映射')
        add.clicked.connect(lambda: self.extra.insertRow(self.extra.rowCount()) if self.extra.rowCount() < 16 else None)
        self.form.addRow(add)
        self.preview = QPushButton('切换为模拟预览')
        self.preview.setCheckable(True)
        self.preview.toggled.connect(self.previewMode)
        self.form.addRow(self.preview)
        for text, command in [('登记本地输入图片', self.importInput),
                ('明确开始隔离草稿调试', workspace.coordinator.preview.startDebug),
                ('观看当前工程任务', workspace.coordinator.preview.watchCurrent),
                ('只读连接已有 Job', self.connectJob),
                ('停止自有调试 / 断开观察', workspace.coordinator.preview.closeAsync)]:
            button = QPushButton(text)
            button.clicked.connect(lambda _checked=False, fn=command: self.w.run(fn))
            self.form.addRow(button)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(panel)
        scroll.setMinimumWidth(275)
        scroll.setMaximumWidth(370)
        self.w.root.addWidget(scroll)
        self.refreshCatalog()

    def commands(self):
        return PageCommands(self.w.session, manifestsFromCatalog(self.w.coordinator.window.operatorCatalog))

    def refreshCatalog(self):
        manifests = self.commands().manifests
        self.choices = outputChoices(self.w.session.document(), manifests)
        self.outputs.clear()
        self.binding.clear()
        groups = {}
        nodes = {}
        for index, choice in enumerate(self.choices):
            address = (choice.source.workflowId, tuple((step.nodeId, step.relation) for step in choice.source.callPath))
            if address not in groups:
                path = '/'.join(step.nodeId + ':' + step.relation for step in choice.source.callPath) or '入口'
                group = QTreeWidgetItem([self.w.session.document().workflows[choice.source.workflowId].name + ' · ' + path])
                group.setFlags(group.flags() & ~Qt.ItemIsDragEnabled)
                self.outputs.addTopLevelItem(group)
                groups[address] = group
            nodeAddress = (address, choice.source.nodeId)
            if nodeAddress not in nodes:
                document = self.w.session.document()
                instance = next((node for node in document.workflows[choice.source.workflowId].nodes
                                 if node.nodeId == choice.source.nodeId), None)
                name = (instance.displayName or instance.nodeId) if instance else '工作流出口'
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
            self.binding.addItem(choice.title, index)
            self.binding.setItemData(index, choice.title + '\n' + choice.hint, Qt.ToolTipRole)
        self.outputs.expandAll()
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
        self.w.renderer.editing = not preview
        self.preview.setText('返回编辑模式' if preview else '切换为模拟预览')
        self.w.renderer.banner.setText('实时只读预览' if self.w.renderer.hub else '模拟布局预览 · 无模拟业务值 · 不运行设备')
        self.install()

    def _fieldState(self):
        values = tuple((name, field.text() if isinstance(field, QLineEdit) else field.value())
                       for name, field in self.fields.items())
        rows = tuple(tuple(self.extra.item(r, c).text() if self.extra.item(r, c) else ''
                           for c in range(3)) for r in range(self.extra.rowCount()))
        return values, self.destination.currentData(), rows

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
            self.title.setText('未选择控件 · 可调整页面列数')
            self._loadedPage = self.w.pageId
            self._loadedFields = self._fieldState()
            return
        self.title.setText(f'{component.type} · {component.componentId[:8]}')
        source = self.w.store.snapshot().dataSources.get(next(iter(component.bindings.values()), ''))
        if source:
            comparable = source.model_dump(exclude={'resultScopeId'})
            index = next((i for i, choice in enumerate(self.choices)
                          if choice.source.model_dump(exclude={'resultScopeId'}) == comparable), -1)
            self.binding.setCurrentIndex(index)
            self.title.setText(self.title.text() + '\n已绑定: ' + str(source.nodeId or source.workflowId) + '.' + str(source.port))
            self.title.setWordWrap(True)
        for name in ['title', 'text', 'emptyText', 'unit']:
            self.fields[name].setText(getattr(component.props, name))
        for name in ['row', 'column', 'rowSpan', 'columnSpan']:
            self.fields[name].setValue(getattr(component.layout, name))
        for name in ['decimals', 'pageSize']:
            self.fields[name].setValue(getattr(component.props, name))
        self.fields['columns'].setValue(component.grid.columns if component.type == 'container'
                                       else self.w.store.snapshot().pages[self.w.pageId].layout.columns)
        action = component.actions.get('clicked')
        self.destination.setCurrentIndex(max(0, self.destination.findData(action.pageId if action else None)))
        self.destination.setEnabled(component.type == 'navigation_button' and (action is None or action.type == 'navigate'))
        self.extra.setRowCount(0)
        data = [(c.title, '.'.join(c.fieldPath), '') for c in component.props.columns] if component.type == 'table' else [
            (key, style.text, style.color) for key, style in component.props.indicatorStates.items()]
        for row in data:
            index = self.extra.rowCount()
            self.extra.insertRow(index)
            for col, value in enumerate(row):
                self.extra.setItem(index, col, QTableWidgetItem(value))
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
        props.update({key: self.fields[key].text() for key in ['title', 'text', 'emptyText', 'unit']})
        props.update({key: self.fields[key].value() for key in ['decimals', 'pageSize']})
        rows = [[self.extra.item(r, c).text() if self.extra.item(r, c) else '' for c in range(3)]
                for r in range(self.extra.rowCount())]
        if item.type == 'table':
            props['columns'] = [{'title': title, 'fieldPath': path.split('.') if path else []}
                                for title, path, _ in rows if title]
        if item.type == 'indicator':
            props['indicatorStates'] = {key: {'text': label, 'color': color or 'neutral'} for key, label, color in rows if key}
        layout = Placement(**{key: self.fields[key].value() for key in ['row', 'column', 'rowSpan', 'columnSpan']})
        actions = item.actions
        if item.type == 'navigation_button':
            target = self.destination.currentData()
            previous = item.actions.get('clicked')
            if previous is None or previous.type == 'navigate':
                if target:
                    action = previous.model_copy(deep=True) if previous else Action(type='navigate', pageId=target)
                    action.pageId = target
                    actions = {'clicked': action}
                else:
                    actions = {}
        self.commands().update(self.w.pageId, self.selected, props=Props(**props), layout=layout,
            actions=actions, columns=columns)
        self._loadedFields = self._fieldState()

    def bindChoice(self, index, key):
        choice = self.choices[index]
        self.commands().bind(self.w.pageId, key, choice)
        self.w.message.setText((choice.hint + '\n' if choice.hint else '') + '绑定已保存；新来源在下一次明确运行 / 隔离调试时生效')

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
        if self.preview.isChecked():
            return False
        kind = event.type()
        if kind in (QEvent.DragEnter, QEvent.DragMove) and event.mimeData().hasFormat(MIME):
            event.acceptProposedAction()
            return True
        if kind == QEvent.Drop and event.mimeData().hasFormat(MIME):
            try:
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
