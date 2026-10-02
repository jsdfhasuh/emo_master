"""Responsive property groups; visibility never changes the stored model."""
from PySide2.QtWidgets import QFormLayout, QGroupBox, QVBoxLayout, QComboBox, QLabel

CONTENT = {
    'image': {'title', 'emptyText'}, 'number': {'title', 'unit', 'decimals', 'emptyText'},
    'text': {'title', 'text', 'emptyText'}, 'indicator': {'title', 'emptyText'},
    'table': {'title', 'pageSize', 'emptyText'}, 'navigation_button': {'title', 'text'},
    'container': {'title'}, 'runtime_status': {'title'},
}
LAYOUT = {'row', 'column', 'rowSpan', 'columnSpan', 'columns'}


class PropertyGroups:
    def __init__(self, tools, panel):
        self.tools = tools
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
        reverse = {widget: name for name, widget in tools.fields.items()}
        for label, widget in rows:
            if widget is tools.title:
                tools.form.addRow(widget)
                widget.setWordWrap(True)
                continue
            name = reverse.get(widget)
            group = ('布局' if name in LAYOUT else '外观' if name == 'fontSize' or widget in tools.appearance.values()
                     else '数据绑定' if widget is tools.binding else '内容')
            if group not in self.groups:
                box = QGroupBox(group)
                form = QFormLayout(box)
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
            form.addRow(wrapper)
            self.rows.append((wrapper, widget, name, group))
        for group in ('内容', '布局', '外观', '数据绑定'):
            if group in self.groups:
                tools.form.addRow(self.groups[group][0])

    def select(self, kind):
        t = self.tools
        for wrapper, widget, name, group in self.rows:
            visible = kind is not None
            if widget is t.applyButton:
                visible = True
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
