"""Native drag/drop and schema property controls over RuntimePages widgets."""
import json

from PySide2.QtCore import Qt, QObject, QEvent, QMimeData, QTimer
from PySide2.QtGui import QDrag
from PySide2.QtWidgets import (
    QWidget, QFormLayout, QListWidget, QListWidgetItem, QTreeWidget,
    QTreeWidgetItem, QPushButton, QLineEdit, QSpinBox, QComboBox, QLabel,
    QScrollArea, QInputDialog, QTableWidget, QTableWidgetItem,
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
        for index, choice in enumerate(self.choices):
            item = QTreeWidgetItem([choice.title])
            item.setData(0, Qt.UserRole, index)
            item.setToolTip(0, choice.hint)
            self.outputs.addTopLevelItem(item)
            self.binding.addItem(choice.title, index)
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

    def previewMode(self, preview):
        self.w.renderer.editing = not preview
        self.preview.setText('返回编辑模式' if preview else '切换为模拟预览')
        self.w.renderer.banner.setText('实时只读预览' if self.w.renderer.hub else '模拟布局预览 · 无模拟业务值 · 不运行设备')
        self.install()

    def select(self, key):
        self.selected = key
        try:
            component = _component(self.w.store.snapshot(), self.w.pageId, key)
        except KeyError:
            self.selected = None
            if self.w.pageId:
                self.fields['columns'].setValue(self.w.store.snapshot().pages[self.w.pageId].layout.columns)
            self.title.setText('未选择控件 · 可调整页面列数')
            return
        self.title.setText(f'{component.type} · {component.componentId[:8]}')
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
        self.extra.setRowCount(0)
        data = [(c.title, '.'.join(c.fieldPath), '') for c in component.props.columns] if component.type == 'table' else [
            (key, style.text, style.color) for key, style in component.props.indicatorStates.items()]
        for row in data:
            index = self.extra.rowCount()
            self.extra.insertRow(index)
            for col, value in enumerate(row):
                self.extra.setItem(index, col, QTableWidgetItem(value))

    def apply(self):
        if not self.w.pageId:
            return
        columns = self.fields['columns'].value()
        if not self.selected:
            self.w.session.editPresentation(lambda p: setattr(p.pages[self.w.pageId].layout, 'columns', columns))
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
            actions = {'clicked': Action(type='navigate', pageId=target)} if target else {}
        self.commands().update(self.w.pageId, self.selected, props=Props(**props), layout=layout,
            actions=actions, columns=columns if item.type == 'container' else None)

    def bindChoice(self, index, key):
        choice = self.choices[index]
        self.commands().bind(self.w.pageId, key, choice)
        self.w.message.setText((choice.hint + '\n' if choice.hint else '') + '绑定已保存；新来源在下一次明确启动调试生效')

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
        self.w.refresh()

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
        if kind == QEvent.MouseButtonPress and event.button() == Qt.LeftButton and key:
            self.select(key)
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
