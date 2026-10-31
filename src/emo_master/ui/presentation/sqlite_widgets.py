"""Native local-database components with short-lived background reads."""
from __future__ import annotations

import html

from PySide2.QtCore import QAbstractTableModel, QModelIndex, QObject, QRunnable, QThreadPool, Qt, Signal
from PySide2.QtWidgets import (
    QComboBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QPushButton, QSizePolicy,
    QTableView, QVBoxLayout, QWidget,
)

from emo_master.core.presentation.sqlite_reader import readPage, readValue


class _Reply(QObject):
    ready = Signal(int, object, str)


class _Read(QRunnable):
    def __init__(self, generation, query):
        super().__init__()
        self.generation, self.query = generation, query
        self.reply = _Reply()

    def run(self):
        try:
            result, error = self.query(), ''
        except (OSError, ValueError, OverflowError) as problem:
            result, error = None, str(problem)
        self.reply.ready.emit(self.generation, result, error)


class _SqliteView(QWidget):
    def __init__(self, source, preview, parent, projectRoot=None):
        super().__init__(parent)
        self.source = source.model_copy(deep=True)
        self.projectRoot = projectRoot
        self.historyClient = None
        self.componentId = ''
        self.preview, self.busy, self.loaded = bool(preview), False, False
        self._generation, self._worker, self._closed = 0, None, False
        self.message = QLabel()
        self.message.setTextFormat(Qt.PlainText)
        self.message.setWordWrap(True)
        self.refreshButton = QPushButton('刷新')
        self.refreshButton.clicked.connect(self.refresh)
        self.message.setText(self._idleMessage())

    def _idleMessage(self):
        return '设计示例 · 不读取数据库' if self.preview else self._sourceLabel() + ' · 点击刷新读取'

    def _sourceLabel(self):
        return 'Runtime SQLite' if self.source.location == 'runtime' else '本机 SQLite'

    def _history(self):
        if self.historyClient is None:
            raise ValueError('此数据由 Runtime 读取，请连接对应 Runtime')
        return self.historyClient

    def setProjectRoot(self, projectRoot):
        if self.projectRoot == projectRoot:
            return
        self.projectRoot = projectRoot
        self._generation += 1
        self.loaded = False
        self._clear()
        self.message.setText(self._idleMessage())
        self._controls()

    def setPreview(self, preview):
        if self.preview == bool(preview):
            return
        self.preview = bool(preview)
        self._generation += 1
        self.loaded = False
        self._clear()
        self.message.setText(self._idleMessage())
        self._controls()

    def showEvent(self, event):
        super().showEvent(event)
        self._closed = False
        if not self.preview and not self.loaded and not self.busy:
            self.refresh()

    def closeEvent(self, event):
        self._closed = True
        self._generation += 1
        super().closeEvent(event)

    def _start(self, query):
        if self.preview or self.busy or self._closed:
            return
        self.busy = True
        self._generation += 1
        self.message.setText('正在查询 ' + self._sourceLabel() + '…')
        self._controls()
        worker = self._worker = _Read(self._generation, query)
        worker.reply.ready.connect(self._finished, Qt.QueuedConnection)
        QThreadPool.globalInstance().start(worker)

    def _finished(self, generation, result, error):
        self.busy, self._worker = False, None
        if generation != self._generation or self.preview or self._closed:
            self._controls()
            return
        self.loaded = True
        if error:
            self._clear()
            self.message.setText(error)
        else:
            self._accept(result)
        self._controls()

    def _controls(self):
        self.refreshButton.setEnabled(not self.preview and not self.busy)


class SqliteRowsModel(QAbstractTableModel):
    def __init__(self, columns, parent=None):
        super().__init__(parent)
        self.columns, self.rows = tuple(columns), ()

    def replace(self, rows):
        self.beginResetModel()
        self.rows = tuple(rows)
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.columns)

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid() or role not in (Qt.DisplayRole, Qt.ToolTipRole):
            return None
        value = self.rows[index.row()][index.column()]
        text = 'NULL' if value is None else str(value)
        return '<qt>' + html.escape(text) + '</qt>' if role == Qt.ToolTipRole else text

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role == Qt.DisplayRole:
            return self.columns[section] if orientation == Qt.Horizontal else str(section + 1)
        return None


class SqliteTableView(_SqliteView):
    selectedRowChanged = Signal(object)

    def __init__(self, source, pageSize=20, preview=False, parent=None, *, selectionFields=(), projectRoot=None):
        super().__init__(source, preview, parent, projectRoot)
        self.pageSize, self.page, self.hasNext = pageSize, 0, False
        self.orderBy = source.orderBy or next(iter(source.columns), '')
        self.descending = source.descending
        self._filterColumn, self._filterText = '', ''
        self.selectionFields = tuple(dict.fromkeys(selectionFields))
        self._selectionRows, self._selected = (), None
        self._rowIds, self._selectedRowId, self._restoreRowId = (), '', ''
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.message)
        filters = QHBoxLayout()
        self.filterColumn = QComboBox()
        self.filterColumn.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.filterColumn.setMinimumContentsLength(5)
        self.filterColumn.addItems(source.columns)
        self.filterColumn.setToolTip('筛选字段')
        filters.addWidget(self.filterColumn)
        self.search = QLineEdit()
        self.search.setPlaceholderText('包含关键词')
        self.search.returnPressed.connect(self.applyFilter)
        filters.addWidget(self.search, 1)
        self.filterButton = QPushButton('筛选')
        self.filterButton.clicked.connect(self.applyFilter)
        filters.addWidget(self.filterButton)
        layout.addLayout(filters)
        self.model = SqliteRowsModel(source.columns, self)
        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.setMinimumHeight(100)
        self.table.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Ignored)
        self.table.setWordWrap(False)
        self.table.setEditTriggers(QTableView.NoEditTriggers)
        self.table.setSelectionBehavior(QTableView.SelectRows)
        self.table.setSelectionMode(QTableView.SingleSelection)
        self.table.selectionModel().selectionChanged.connect(self._selectionChanged)
        header = self.table.horizontalHeader()
        header.setMinimumSectionSize(100)
        header.setSectionResizeMode(QHeaderView.Stretch)
        header.setSectionsClickable(True)
        header.setSortIndicatorShown(True)
        header.setSortIndicator(source.columns.index(self.orderBy) if self.orderBy in source.columns else -1,
                                Qt.DescendingOrder if self.descending else Qt.AscendingOrder)
        header.sectionClicked.connect(self.sortColumn)
        layout.addWidget(self.table, 1)
        controls = QHBoxLayout()
        self.previousButton = QPushButton('上一页')
        self.previousButton.clicked.connect(lambda: self.move(-1))
        self.nextButton = QPushButton('下一页')
        self.nextButton.clicked.connect(lambda: self.move(1))
        self.pageLabel = QLabel('第 1 页')
        controls.addWidget(self.previousButton)
        controls.addWidget(self.nextButton)
        controls.addWidget(self.pageLabel, 1)
        controls.addWidget(self.refreshButton)
        layout.addLayout(controls)
        self._controls()

    def refresh(self):
        if self.preview or self.busy or self._closed:
            return
        selectedRowId = self._selectedRowId
        self.clearSelection()
        self._restoreRowId = selectedRowId
        source = self.source
        arguments = dict(pageSize=self.pageSize, page=self.page,
                         filterColumn=self._filterColumn, filterText=self._filterText,
                         orderBy=self.orderBy, descending=self.descending,
                         selectionFields=self.selectionFields, projectRoot=self.projectRoot)
        self._start(lambda: self._history().readPage(self.componentId, **arguments)
                    if source.location == 'runtime' else readPage(source, **arguments))

    def applyFilter(self):
        if self.preview or self.busy:
            return
        self._filterColumn, self._filterText = self.filterColumn.currentText(), self.search.text()
        self.page = 0
        self.refresh()

    def move(self, delta):
        if self.preview or self.busy or (delta > 0 and not self.hasNext):
            return
        self.clearSelection()
        self.page = max(0, self.page + delta)
        self.refresh()

    def sortColumn(self, section):
        if self.preview or self.busy or not 0 <= section < len(self.source.columns):
            return
        column = self.source.columns[section]
        self.descending = not self.descending if self.orderBy == column else False
        self.orderBy, self.page = column, 0
        self.table.horizontalHeader().setSortIndicator(
            section, Qt.DescendingOrder if self.descending else Qt.AscendingOrder)
        self.refresh()

    def _clear(self):
        self.clearSelection()
        self._selectionRows, self._rowIds = (), ()
        self.model.replace(())
        self.hasNext = False
        self.pageLabel.setText(f'第 {self.page + 1} 页')

    def _accept(self, result):
        restoreRowId = self._restoreRowId
        self.clearSelection()
        self._selectionRows = result.selectionRows
        self._rowIds = result.rowIds
        self.model.replace(result.rows)
        self.hasNext, self.page = result.hasNext, result.page
        self.pageLabel.setText(f'第 {self.page + 1} 页 · {len(result.rows)} 行')
        self.message.setText(self._sourceLabel() + ' · ' + self.source.table if result.rows else '未找到匹配记录')
        if restoreRowId and self._rowIds.count(restoreRowId) == 1:
            # Select from this response so image consumers receive its fresh refs.
            self.table.selectRow(self._rowIds.index(restoreRowId))

    def selectedRow(self):
        return None if self._selected is None else dict(self._selected)

    def clearSelection(self):
        self._selectedRowId, self._restoreRowId = '', ''
        self.table.clearSelection()
        self._selected = None
        self.selectedRowChanged.emit(None)

    def _selectionChanged(self, _selected, _deselected):
        indices = self.table.selectionModel().selectedRows()
        row = indices[0].row() if indices else -1
        self._selected = (dict(self._selectionRows[row])
                          if not self.preview and not self.busy and 0 <= row < len(self._selectionRows) else None)
        self._selectedRowId = (self._rowIds[row]
                               if self._selected is not None and 0 <= row < len(self._rowIds) else '')
        self.selectedRowChanged.emit(self.selectedRow())

    def hideEvent(self, event):
        self.clearSelection()
        super().hideEvent(event)

    def closeEvent(self, event):
        self.clearSelection()
        super().closeEvent(event)

    def _controls(self):
        super()._controls()
        enabled = not self.preview and not self.busy
        self.previousButton.setEnabled(enabled and self.page > 0)
        self.nextButton.setEnabled(enabled and self.hasNext)
        for field in (self.filterColumn, self.search, self.filterButton):
            field.setEnabled(enabled)


class SqliteValueView(_SqliteView):
    def __init__(self, source, decimals=2, unit='', preview=False, parent=None, *, projectRoot=None):
        super().__init__(source, preview, parent, projectRoot)
        self.decimals, self.unit = decimals, unit
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.value = QLabel('—')
        self.value.setTextFormat(Qt.PlainText)
        self.value.setObjectName('number')
        self.value.setWordWrap(True)
        self.value.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.value, 1)
        layout.addWidget(self.message)
        controls = QHBoxLayout()
        names = {'count': '记录数', 'sum': '合计', 'avg': '平均值', 'min': '最小值', 'max': '最大值'}
        label = QLabel(names[source.aggregate])
        controls.addWidget(label, 1)
        controls.addWidget(self.refreshButton)
        layout.addLayout(controls)
        self._controls()

    def refresh(self):
        source = self.source
        projectRoot = self.projectRoot
        self._start(lambda: self._history().readValue(self.componentId)
                    if source.location == 'runtime' else readValue(source, projectRoot=projectRoot))

    def _clear(self):
        self.value.setText('—')

    def _accept(self, result):
        if result is None:
            text = '—'
        elif isinstance(result, float):
            text = f'{result:.{self.decimals}f}'
        else:
            text = str(result)
        self.value.setText(text + (' ' + self.unit if self.unit and result is not None else ''))
        self.message.setText('没有可统计的值' if result is None else self._sourceLabel() + ' · ' + self.source.table)
