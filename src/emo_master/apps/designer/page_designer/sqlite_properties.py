"""Local SQLite selectors; only an explicit metadata action opens a database."""
import sqlite3

from PySide2.QtCore import Qt
from PySide2.QtWidgets import (
    QWidget, QFormLayout, QHBoxLayout, QVBoxLayout, QLineEdit, QPushButton,
    QComboBox, QListWidget, QListWidgetItem, QLabel, QFileDialog, QAbstractItemView,
)


class SqliteProperties(QWidget):
    def __init__(self, parent=None, *, projectRoot=None, inspectRuntime=None):
        super().__init__(parent)
        self.schema = {}
        self.projectRoot = projectRoot or (lambda: None)
        self.inspectRuntime = inspectRuntime
        self.schemaLocation = 'display_local'
        self.schemaPath = ''
        self.kind = 'sqlite_table'
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.location = QComboBox()
        self.location.addItem('显示页面的电脑', 'display_local')
        self.location.addItem('所连接的 Runtime', 'runtime')
        layout.addWidget(self.location)
        self.databasePath = QLineEdit()
        self.databasePath.setPlaceholderText('已有 SQLite 文件；相对路径以工程目录为根')
        layout.addWidget(self.databasePath)
        buttons = QHBoxLayout()
        self.chooseButton = QPushButton('选择文件')
        self.readButton = QPushButton('读取表和字段')
        buttons.addWidget(self.chooseButton)
        buttons.addWidget(self.readButton)
        layout.addLayout(buttons)
        self.form = QFormLayout()
        self.form.setRowWrapPolicy(QFormLayout.WrapAllRows)
        self.form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        layout.addLayout(self.form)
        self.table = QComboBox()
        self.columns = QListWidget()
        self.columns.setMaximumHeight(170)
        self.columns.setDragDropMode(QAbstractItemView.InternalMove)
        self.orderBy = QComboBox()
        self.direction = QComboBox()
        self.direction.addItem('降序', True)
        self.direction.addItem('升序', False)
        self.aggregate = QComboBox()
        for title, key in [('记录总数', 'count'), ('求和', 'sum'), ('平均值', 'avg'),
                           ('最小值', 'min'), ('最大值', 'max')]:
            self.aggregate.addItem(title, key)
        self.valueColumn = QComboBox()
        self.tableWidgets = (self.columns, self.orderBy, self.direction)
        self.valueWidgets = (self.aggregate, self.valueColumn)
        for label, widget in [('数据表 / 视图', self.table), ('显示字段（勾选，可拖动排序）', self.columns),
                              ('排序字段', self.orderBy), ('排序方向', self.direction),
                              ('统计方式', self.aggregate), ('统计字段', self.valueColumn)]:
            self.form.addRow(label, widget)
            if isinstance(widget, QComboBox):
                widget.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
                widget.setMinimumContentsLength(8)
            widget.setMinimumWidth(0)
        self.message = QLabel('仅查询本机已有数据库；数据库文件不会随工程打包。')
        self.message.setWordWrap(True)
        layout.addWidget(self.message)
        self.chooseButton.clicked.connect(self.chooseDatabase)
        self.readButton.clicked.connect(self.readMetadata)
        self.table.currentIndexChanged.connect(self.tableChanged)
        self.aggregate.currentIndexChanged.connect(self.updateValueField)
        self.location.currentIndexChanged.connect(self.updateLocation)

    def updateLocation(self):
        remote = self.location.currentData() == 'runtime'
        self.chooseButton.setEnabled(not remote)
        self.readButton.setText('读取 Runtime 表和字段' if remote else '读取表和字段')
        self.databasePath.setPlaceholderText('Runtime 上已有数据库；相对其正式工程目录' if remote
                                              else '已有 SQLite 文件；相对路径以工程目录为根')
        self.message.setText('由所连接 Runtime 读取。数据库路径和表字段须显式写入 Runtime 后才用于历史查询。'
                             if remote else '读取显示页面电脑的已有数据库；数据库文件不会随工程打包。')

    @staticmethod
    def _pick(combo, values, current='', empty='未选择'):
        combo.clear()
        combo.addItem(empty, '')
        for value in dict.fromkeys([*values, *([current] if current else [])]):
            combo.addItem(value, value)
        combo.setCurrentIndex(max(0, combo.findData(current)))

    def load(self, source, kind):
        """Restore a saved choice without probing its file or changing its fields."""
        self.kind = kind
        self.location.setCurrentIndex(self.location.findData(source.location))
        self.databasePath.setText(source.databasePath)
        known = self.schema if self.schemaPath == source.databasePath and self.schemaLocation == source.location else {}
        self.table.blockSignals(True)
        self._pick(self.table, known, source.table)
        self.table.blockSignals(False)
        fields = known.get(source.table, [])
        self._fields(fields, source.columns, source.orderBy, source.valueColumn)
        self.direction.setCurrentIndex(self.direction.findData(source.descending))
        self.aggregate.setCurrentIndex(self.aggregate.findData(source.aggregate))
        for widget in self.tableWidgets:
            widget.setVisible(kind == 'sqlite_table')
            self.form.labelForField(widget).setVisible(kind == 'sqlite_table')
        for widget in self.valueWidgets:
            widget.setVisible(kind == 'sqlite_value')
            self.form.labelForField(widget).setVisible(kind == 'sqlite_value')
        self.updateValueField()
        self.updateLocation()

    def _fields(self, fields, selected=(), orderBy='', valueColumn=''):
        self.columns.clear()
        for name in dict.fromkeys([*selected, *fields]):
            item = QListWidgetItem(name)
            item.setData(Qt.UserRole, name)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable | Qt.ItemIsDragEnabled)
            item.setCheckState(Qt.Checked if name in selected else Qt.Unchecked)
            self.columns.addItem(item)
        self._pick(self.orderBy, fields, orderBy, '首个显示字段')
        self._pick(self.valueColumn, fields, valueColumn)

    def selectedColumns(self):
        return [self.columns.item(i).data(Qt.UserRole) for i in range(self.columns.count())
                if self.columns.item(i).checkState() == Qt.Checked]

    def state(self):
        return (self.databasePath.text().strip(), self.table.currentData() or '',
                tuple(self.selectedColumns()), self.orderBy.currentData() or '',
                self.direction.currentData(), self.aggregate.currentData(),
                self.valueColumn.currentData() or '', self.location.currentData())

    def payload(self):
        path, table, columns, order, descending, aggregate, value, location = self.state()
        if path and not table:
            raise ValueError('SQLite：请读取表和字段，并选择数据表。')
        if table and not path:
            raise ValueError('SQLite：请选择数据库文件。')
        if path and self.kind == 'sqlite_table' and not columns:
            raise ValueError('SQLite：请至少勾选一个显示字段。')
        if path and self.kind == 'sqlite_value' and aggregate != 'count' and not value:
            raise ValueError('SQLite：请选择统计字段。')
        if len(columns) > 32:
            raise ValueError('SQLite：最多选择 32 个显示字段。')
        return dict(databasePath=path, table=table, columns=list(columns), orderBy=order,
                    descending=descending, aggregate=aggregate, valueColumn=value, location=location)

    def chooseDatabase(self):
        if self.location.currentData() == 'runtime':
            return
        path, _ = QFileDialog.getOpenFileName(self, '选择已有 SQLite 数据库', self.databasePath.text(),
            'SQLite 数据库 (*.db *.sqlite *.sqlite3 *.db3);;所有文件 (*)')
        if path:
            self.databasePath.setText(path)
            self.readMetadata()

    def readMetadata(self):
        from emo_master.core.presentation.sqlite_reader import inspectDatabase
        path = self.databasePath.text().strip()
        try:
            if self.location.currentData() == 'runtime':
                if self.inspectRuntime is None:
                    raise ValueError('请连接 Runtime 后读取其数据库结构')
                schema = self.inspectRuntime(path, self.table.currentData() or '')
            else:
                from emo_master.core.presentation.local_paths import resolveLocalPath
                resolved = str(resolveLocalPath(path, self.projectRoot(), purpose='数据库')) if path else path
                schema = inspectDatabase(resolved)
        except (OSError, ValueError, RuntimeError, sqlite3.Error) as error:
            self.message.setText('读取失败：' + str(error))
            return
        old = self.state()
        same = self.schemaPath == path
        self.schema, self.schemaPath = schema, path
        self.schemaLocation = self.location.currentData()
        table = old[1] if old[1] in schema else next(iter(schema), '')
        self.table.blockSignals(True)
        self._pick(self.table, schema, table)
        self.table.blockSignals(False)
        fields = schema.get(table, [])
        keep = old[1] == table and (same or bool(old[2]))
        selected = [name for name in old[2] if name in fields] if keep else fields[:min(8, len(fields))]
        self._fields(fields, selected, old[3] if old[3] in fields else '', old[6] if old[6] in fields else '')
        self.message.setText(f'已读取 {len(schema)} 个表 / 视图。选择字段后点击“应用修改”。' if schema
                             else '此数据库没有可查询的数据表或视图。')

    def tableChanged(self):
        fields = self.schema.get(self.table.currentData(), []) if (self.schemaPath == self.databasePath.text().strip()
                   and self.schemaLocation == self.location.currentData()) else []
        self._fields(fields, fields[:min(8, len(fields))])

    def updateValueField(self):
        self.valueColumn.setEnabled(self.aggregate.currentData() != 'count')
