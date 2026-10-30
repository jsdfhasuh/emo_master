"""Direct layout choices without exposing implementation row/column coordinates."""
from PySide2.QtWidgets import QWidget, QFormLayout, QComboBox, QSpinBox, QDoubleSpinBox, QCheckBox

from emo_master.core.presentation.models import ResponsiveLayout, ResponsiveItem


class ResponsiveProperties(QWidget):
    def __init__(self):
        super().__init__()
        self.form = QFormLayout(self)
        self.form.setContentsMargins(0, 0, 0, 0)
        self.form.setRowWrapPolicy(QFormLayout.WrapAllRows)
        self.direction = self.combo('排列方式', [('上下排列', 'vertical'), ('并排排列', 'horizontal'), ('自动换行', 'wrap')])
        self.alignment = self.combo('组内对齐', [('填满', 'stretch'), ('起始边', 'start'), ('居中', 'center'), ('末尾边', 'end')])
        self.justify = self.combo('间隔分配', [('靠前', 'start'), ('居中', 'center'), ('靠后', 'end'), ('等距分布', 'space-between')])
        self.spacing = self.spin('组件间距', 0, 128)
        self.equalWidth = QCheckBox('组内组件等宽')
        self.form.addRow(self.equalWidth)
        self.groupFields = [self.direction, self.alignment, self.justify, self.spacing, self.equalWidth]
        self.widthMode = self.combo('宽度方式', [('自动填满', 'fill'), ('适合内容', 'content')])
        self.minWidth = self.spin('最小宽度', 0, 4096)
        self.preferredWidth = self.spin('期望宽度（0 为内容宽度）', 0, 8192)
        self.weight = QDoubleSpinBox()
        self.weight.setRange(0, 100)
        self.weight.setDecimals(3)
        self.form.addRow('宽度比例', self.weight)
        self.minHeight = self.spin('最小高度', 0, 4096)
        self.preferredHeight = self.spin('期望高度（0 为自动）', 0, 8192)
        self.alignSelf = self.combo('组件在组内对齐', [('跟随组设置', 'auto'), ('起始边', 'start'), ('居中', 'center'), ('末尾边', 'end'), ('填满', 'stretch')])
        self.itemFields = [self.widthMode, self.minWidth, self.preferredWidth, self.weight,
                           self.minHeight, self.preferredHeight, self.alignSelf]
        self.load(None, None)

    def combo(self, title, choices):
        widget = QComboBox()
        for label, value in choices:
            widget.addItem(label, value)
        self.form.addRow(title, widget)
        return widget

    def spin(self, title, minimum, maximum):
        widget = QSpinBox()
        widget.setRange(minimum, maximum)
        self.form.addRow(title, widget)
        return widget

    def load(self, layout, item):
        active = layout is not None and layout.type == 'responsive'
        rules = layout if active else ResponsiveLayout()
        self.direction.setCurrentIndex(self.direction.findData(rules.direction))
        self.alignment.setCurrentIndex(self.alignment.findData(rules.alignment))
        self.justify.setCurrentIndex(self.justify.findData(rules.justify))
        self.spacing.setValue(rules.spacing)
        self.equalWidth.setChecked(rules.equalWidth)
        item = item or ResponsiveItem()
        self.widthMode.setCurrentIndex(self.widthMode.findData(item.width))
        self.alignSelf.setCurrentIndex(self.alignSelf.findData(item.alignSelf))
        for name in ('minWidth', 'preferredWidth', 'weight', 'minHeight', 'preferredHeight'):
            getattr(self, name).setValue(getattr(item, name))
        for widget in self.groupFields:
            widget.setVisible(layout is not None)
            label = self.form.labelForField(widget)
            if label:
                label.setVisible(layout is not None)
        for widget in self.itemFields:
            widget.setVisible(self.parentWidget() is not None)  # refined by setItemVisible

    def setItemVisible(self, visible):
        for widget in self.itemFields:
            widget.setVisible(visible)
            label = self.form.labelForField(widget)
            if label:
                label.setVisible(visible)

    def layoutPayload(self):
        return ResponsiveLayout(direction=self.direction.currentData(), alignment=self.alignment.currentData(),
            justify=self.justify.currentData(), spacing=self.spacing.value(), equalWidth=self.equalWidth.isChecked())

    def itemPayload(self):
        return ResponsiveItem(width=self.widthMode.currentData(), alignSelf=self.alignSelf.currentData(),
            **{name: getattr(self, name).value() for name in ('minWidth', 'preferredWidth', 'weight', 'minHeight', 'preferredHeight')})

    def state(self):
        return (self.direction.currentData(), self.alignment.currentData(), self.justify.currentData(),
                self.spacing.value(), self.equalWidth.isChecked(), self.widthMode.currentData(),
                self.alignSelf.currentData(), *(getattr(self, name).value() for name in
                    ('minWidth', 'preferredWidth', 'weight', 'minHeight', 'preferredHeight')))
