"""Image source choices are draft properties; choosing a path never opens it."""
from PySide2.QtCore import Signal
from PySide2.QtWidgets import (
    QWidget, QFormLayout, QHBoxLayout, QVBoxLayout, QComboBox, QLineEdit,
    QPushButton, QLabel, QFileDialog,
)

from emo_master.core.presentation.models import ImagePathSource, walkComponents


class ImageProperties(QWidget):
    modeChanged = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.tables = {}
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.form = QFormLayout()
        self.form.setRowWrapPolicy(QFormLayout.WrapAllRows)
        self.form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        layout.addLayout(self.form)
        self.mode = QComboBox()
        for title, value in [('流程结果', 'workflow'), ('静态资源', 'resource'), ('文件路径', 'path')]:
            self.mode.addItem(title, value)
        self.form.addRow('图片来源', self.mode)
        self.kind = QComboBox()
        self.kind.addItem('固定文件路径', 'fixed')
        self.kind.addItem('表格选中行', 'selected_row')
        self.form.addRow('路径来源', self.kind)
        self.path = QLineEdit()
        self.path.setPlaceholderText('绝对路径，或相对图片根目录的路径')
        self.pathRow = QWidget()
        pathLayout = QHBoxLayout(self.pathRow)
        pathLayout.setContentsMargins(0, 0, 0, 0)
        pathLayout.addWidget(self.path, 1)
        self.chooseFile = QPushButton('选择')
        pathLayout.addWidget(self.chooseFile)
        self.form.addRow('图片文件', self.pathRow)
        self.component = QComboBox()
        self.form.addRow('来源表格', self.component)
        self.field = QComboBox()
        self.field.setEditable(True)
        self.field.setInsertPolicy(QComboBox.NoInsert)
        self.field.lineEdit().setPlaceholderText('选择或输入保存图片路径的字段')
        self.form.addRow('路径字段', self.field)
        self.baseDirectory = QLineEdit()
        self.baseDirectory.setPlaceholderText('相对路径必填；“.” 表示当前工程目录')
        self.baseRow = QWidget()
        baseLayout = QHBoxLayout(self.baseRow)
        baseLayout.setContentsMargins(0, 0, 0, 0)
        baseLayout.addWidget(self.baseDirectory, 1)
        self.chooseDirectory = QPushButton('选择')
        baseLayout.addWidget(self.chooseDirectory)
        self.form.addRow('图片根目录', self.baseRow)
        self.hint = QLabel('支持 PNG、JPEG、BMP。文件路径属于显示页面的电脑；仅在本地数据预览或运行页面中读取。')
        self.hint.setWordWrap(True)
        layout.addWidget(self.hint)
        for combo in (self.mode, self.kind, self.component, self.field):
            combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
            combo.setMinimumContentsLength(8)
            combo.setMinimumWidth(0)
        self.mode.currentIndexChanged.connect(self.updateVisibility)
        self.kind.currentIndexChanged.connect(self.updateVisibility)
        self.component.currentIndexChanged.connect(self.updateFields)
        self.chooseFile.clicked.connect(self.selectFile)
        self.chooseDirectory.clicked.connect(self.selectDirectory)
        self.updateVisibility()

    def updateVisibility(self):
        isPath = self.mode.currentData() == 'path'
        selected = self.kind.currentData() == 'selected_row'
        for widget, visible in ((self.kind, isPath), (self.pathRow, isPath and not selected),
                                (self.component, isPath and selected), (self.field, isPath and selected),
                                (self.baseRow, isPath)):
            widget.setVisible(visible)
            self.form.labelForField(widget).setVisible(visible)
        self.hint.setVisible(isPath)
        self.modeChanged.emit()

    def load(self, component, presentation, pageId):
        """Load only declared choices, including stale references, with no file reads."""
        source = component.props.imagePath
        self.tables = {item.componentId: item for item in walkComponents(presentation.pages[pageId].components)
                       if item.type == 'sqlite_table'}
        self.component.blockSignals(True)
        self.component.clear()
        self.component.addItem('未选择', None)
        for key, item in self.tables.items():
            self.component.addItem((item.props.title or item.props.sqlite.table or 'SQLite 历史表格')
                                   + ' · ' + key, key)
        target = source.componentId if source else None
        if target and target not in self.tables:
            self.component.addItem('来源缺失 · ' + target, target)
        self.component.setCurrentIndex(max(0, self.component.findData(target)))
        self.component.blockSignals(False)
        self.field.clear()
        self.updateFields()
        self.field.setEditText(source.field if source else '')
        self.path.setText(source.path if source else '')
        self.baseDirectory.setText(source.baseDirectory if source else '')
        self.mode.blockSignals(True)
        self.kind.blockSignals(True)
        self.mode.setCurrentIndex(self.mode.findData('path' if source is not None else
                                  'resource' if component.props.imageResourceId else 'workflow'))
        self.kind.setCurrentIndex(self.kind.findData(source.kind if source else 'fixed'))
        self.mode.blockSignals(False)
        self.kind.blockSignals(False)
        self.updateVisibility()

    def updateFields(self):
        current = self.field.currentText()
        table = self.tables.get(self.component.currentData())
        self.field.clear()
        self.field.addItems(table.props.sqlite.columns if table is not None else [])
        self.field.setEditText(current)

    def state(self):
        return (self.mode.currentData(), self.kind.currentData(), self.path.text(),
                self.component.currentData(), self.field.currentText(), self.baseDirectory.text())

    def payload(self):
        if self.mode.currentData() != 'path':
            return None
        kind = self.kind.currentData()
        if kind == 'selected_row' and (not self.component.currentData() or not self.field.currentText()):
            raise ValueError('图片路径：请选择来源表格，并填写路径字段。')
        return ImagePathSource(kind=kind, path=self.path.text().strip() if kind == 'fixed' else '',
            componentId=self.component.currentData() if kind == 'selected_row' else None,
            field=self.field.currentText() if kind == 'selected_row' else '',
            baseDirectory=self.baseDirectory.text().strip())

    def selectFile(self):
        path, _ = QFileDialog.getOpenFileName(self, '选择图片文件', self.path.text(),
            '图片 (*.png *.jpg *.jpeg *.bmp);;所有文件 (*)')
        if path:
            self.path.setText(path)

    def selectDirectory(self):
        directory = QFileDialog.getExistingDirectory(self, '选择图片根目录', self.baseDirectory.text())
        if directory:
            self.baseDirectory.setText(directory)
