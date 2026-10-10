"""Responsive property groups; visibility never changes the stored model."""
from PySide2.QtCore import Qt
from PySide2.QtWidgets import QFormLayout, QGroupBox, QVBoxLayout, QComboBox, QLabel, QWidget, QGridLayout, QSizePolicy

CONTENT = {
    'image': {'title', 'emptyText'}, 'number': {'title', 'unit', 'decimals', 'emptyText'},
    'text': {'title', 'text', 'emptyText'}, 'indicator': {'title', 'emptyText'},
    'table': {'title', 'pageSize', 'emptyText'}, 'navigation_button': {'title', 'text'},
    'container': {'title'}, 'runtime_status': {'title'},
}
LAYOUT = {'row', 'column', 'rowSpan', 'columnSpan', 'columns'}


def editorMessage(message):
    """Keep model diagnostics in logs/tooltips, with actionable form text."""
    for diagnostic, text in [
            ('fontSize must be', '字号请选择“自动”，或输入 8–48。'),
            ('components overlap', '组件位置重叠，请调整行、列或占用范围。'),
            ('component exceeds grid columns', '组件超出页面列数，请减小列位置或占用列数。'),
            ('duplicate indicator value', '判定映射中有重复的值，请修改或删除重复行。'),
            ('boolean indicator value', '判定值请填写 true 或 false。'),
            ('indicator key too long', '判定值太长，请缩短后再应用。'),
            ('navigation target page is missing', '请选择存在的目标页面。'),
            ('current page does not display selected result scope', '当前页面没有显示所选流程的结果，请检查数据来源。'),
            ('target page does not support selected result scope', '目标页面不支持所选流程的结果，请选择其他页面。')]:
        if diagnostic in message:
            return text
    if 'validation error' in message:
        return '属性不符合要求，请检查标出的输入；详细原因可悬停查看。'
    return message


class FieldPair(QWidget):
    def __init__(self):
        super().__init__()
        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.fields = []

    def add(self, widget):
        self.fields.append(widget)
        self.arrange()

    def arrange(self):
        columns = 2 if self.width() >= 250 else 1
        for index, widget in enumerate(self.fields):
            self.grid.addWidget(widget, index // columns, index % columns)
        self.grid.setColumnStretch(0, 1)
        self.grid.setColumnStretch(1, 1 if columns == 2 else 0)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.arrange()


class PropertyGroups:
    def __init__(self, tools, panel):
        self.tools = tools
        tools.form.setFormAlignment(Qt.AlignTop)
        rows = []
        for index in range(tools.form.rowCount()):
            label = tools.form.itemAt(index, QFormLayout.LabelRole)
            field = tools.form.itemAt(index, QFormLayout.FieldRole) or tools.form.itemAt(index, QFormLayout.SpanningRole)
            rows.append((label.widget() if label else None, field.widget()))
        while tools.form.count():
            tools.form.takeAt(0)
        while tools.form.rowCount():
            tools.form.removeRow(0)
        # Retain the existing form object/API as the outer group holder.
        self.groups = {}
        self.rows = []
        self.pairs = {}
        reverse = {widget: name for name, widget in tools.fields.items()}
        for label, widget in rows:
            if widget in (tools.title, tools.applyButton, tools.propertyError):
                if isinstance(widget, QLabel):
                    widget.setWordWrap(True)
                continue
            if widget in tools.operationButtons:
                widget.setParent(tools.w)
                widget.hide()
                continue
            name = reverse.get(widget)
            group = ('位置与大小' if name in LAYOUT else '外观' if name == 'fontSize' or widget in tools.appearance.values()
                     else '数据来源' if widget is tools.binding or widget in tools.bindingButtons else '内容')
            if group not in self.groups:
                box = QGroupBox(group)
                box.setObjectName('propertyGroup')
                box.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
                form = QFormLayout(box)
                form.setFormAlignment(Qt.AlignTop)
                form.setRowWrapPolicy(QFormLayout.WrapAllRows)
                form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
                self.groups[group] = (box, form)
            form = self.groups[group][1]
            wrapper = QGroupBox()
            wrapper.setFlat(True)
            wrapper.setStyleSheet('QGroupBox {border:0; margin:0; padding:0;}')
            layout = QVBoxLayout(wrapper)
            layout.setContentsMargins(0, 0, 0, 0)
            if label:
                if isinstance(label, QLabel):
                    label.setWordWrap(True)
                layout.addWidget(label)
            layout.addWidget(widget)
            widget.setMinimumWidth(0)
            if isinstance(widget, QComboBox):
                widget.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
                widget.setMinimumContentsLength(8)
            pairKey = ('position' if name in {'row', 'column'} else
                       'span' if name in {'rowSpan', 'columnSpan'} else None)
            if pairKey:
                if pairKey not in self.pairs:
                    self.pairs[pairKey] = FieldPair()
                    form.addRow(self.pairs[pairKey])
                self.pairs[pairKey].add(wrapper)
            else:
                form.addRow(wrapper)
            if widget in tools.bindingButtons:
                widget.setFlat(True)
            self.rows.append((wrapper, widget, name, group))
        for group in ('内容', '位置与大小', '外观', '数据来源'):
            if group in self.groups:
                tools.form.addRow(self.groups[group][0])

    def select(self, kind):
        t = self.tools
        for wrapper, widget, name, group in self.rows:
            visible = kind is not None
            if widget is t.applyButton:
                visible = True
            if widget is t.pageName:
                visible = kind is None
            if name in CONTENT.get(kind, set()):
                visible = True
            elif name in {'title', 'text', 'emptyText', 'unit', 'decimals', 'pageSize'}:
                visible = False
            if name == 'columns':
                visible = kind in (None, 'container')
            if widget in (t.destination, t.actionMode, t.actionScope):
                visible = kind == 'navigation_button'
            if widget in (t.extra, t.extraAdd, t.extraRemove, t.fieldHint):
                visible = kind in ('table', 'indicator')
            if widget is t.binding or widget in t.bindingButtons:
                visible = kind in ('image', 'number', 'text', 'indicator', 'table')
            wrapper.setVisible(visible)
        for group, (box, _form) in self.groups.items():
            box.setVisible(any(not row.isHidden() for row, _, _, g in self.rows if g == group))
        for pair in self.pairs.values():
            pair.setVisible(any(not widget.isHidden() for widget in pair.fields))
