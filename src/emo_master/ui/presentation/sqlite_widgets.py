"""Native local-database components with short-lived background reads."""
from __future__ import annotations

import html

from PySide2.QtCore import QAbstractTableModel, QCoreApplication, QModelIndex, QObject, QRunnable, QRect, QSize, Qt, QThreadPool, Signal
from PySide2.QtGui import QColor, QFont
from PySide2.QtWidgets import (
    QComboBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QPushButton, QSizePolicy,
    QStyledItemDelegate, QStyle, QTableView, QVBoxLayout, QWidget,
)

from emo_master.core.presentation.sqlite_reader import readPage, readValue
from .appearance import COLORS, THEME, indicatorStyle


class _Reply(QObject):
    ready = Signal(int, object, str)


class _Read(QRunnable):
    def __init__(self, generation, query):
        super().__init__()
        self.generation, self.query = generation, query
        self.reply = _Reply(QCoreApplication.instance())

    def run(self):
        try:
            try:
                result, error = self.query(), ''
            except (OSError, ValueError, OverflowError) as problem:
                result, error = None, str(problem)
            self.reply.ready.emit(self.generation, result, error)
        finally:
            self.reply.deleteLater()


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
    def __init__(self, columns, parent=None, *, listMode=False):
        super().__init__(parent)
        self.columns, self.rows = tuple(columns), ()
        self.listMode = listMode

    def replace(self, rows):
        self.beginResetModel()
        self.rows = tuple(rows)
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else (1 if self.listMode else len(self.columns))

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid() or role not in (Qt.DisplayRole, Qt.ToolTipRole):
            return None
        if self.listMode and not self.columns:
            return None
        value = self.rows[index.row()][index.column()]
        text = 'NULL' if value is None else str(value)
        return '<qt>' + html.escape(text) + '</qt>' if role == Qt.ToolTipRole else text

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role == Qt.DisplayRole:
            return self.columns[section] if orientation == Qt.Horizontal else str(section + 1)
        return None


class _RecordListRow(QStyledItemDelegate):
    def __init__(self, columns, view, props, emptyText, parent):
        super().__init__(parent)
        self.title, self.subtitle, self.trailing = (
            columns.index(field) if field in columns else -1
            for field in (view.titleField or next(iter(columns), ''), view.subtitleField, view.trailingField))
        self.props, self.emptyText = props, emptyText

    def sizeHint(self, option, index):
        return QSize(110, 52 if self.subtitle >= 0 else 37)

    def paint(self, painter, option, index):
        row = index.model().rows[index.row()]
        rect = option.rect
        painter.save()
        painter.fillRect(rect, QColor(THEME['soft']['blue'] if option.state & QStyle.State_Selected else THEME['surface']))
        painter.setPen(QColor(THEME['border']))
        painter.drawLine(rect.bottomLeft(), rect.bottomRight())
        content = rect.adjusted(10, 4, -10, -4)
        textWidth = content.width()
        if self.trailing >= 0:
            value = row[self.trailing]
            indicator = indicatorStyle(self.props, value) if self.props is not None else None
            label = indicator.text if indicator else self.emptyText
            if indicator and self.props.showMarker is not False:
                label = '●  ' + label
            color = indicator.color if indicator else 'neutral'
            width = min(content.width() // 2, option.fontMetrics.horizontalAdvance(label) + 16)
            textWidth -= width + 9
            chip = QRect(content.right() - width + 1, content.center().y() - 11, width, 22)
            if indicator and indicator.background == 'soft':
                painter.setPen(Qt.NoPen)
                painter.setBrush(QColor(THEME['soft'][color]))
                painter.drawRoundedRect(chip, 9, 9)
            painter.setPen(QColor(COLORS[color]))
            painter.drawText(chip, Qt.AlignCenter, option.fontMetrics.elidedText(label, Qt.ElideRight, max(0, width - 8)))
        for role, field in (('title', self.title), ('subtitle', self.subtitle)):
            if field < 0:
                continue
            font = QFont(option.font)
            font.setBold(role == 'title')
            painter.setFont(font)
            painter.setPen(QColor(THEME['text'] if role == 'title' else COLORS['neutral']))
            value = row[field]
            top = content.top() if role == 'title' or self.subtitle < 0 else content.center().y()
            height = content.height() if self.subtitle < 0 else content.height() // 2
            painter.drawText(QRect(content.left(), top, max(0, textWidth), height), Qt.AlignVCenter,
                painter.fontMetrics().elidedText(self.emptyText if value in (None, '') else str(value),
                                                  Qt.ElideRight, max(0, textWidth)))
        painter.restore()


class SqliteTableView(_SqliteView):
    selectedRowChanged = Signal(object)
    stateChanged = Signal()

    def __init__(self, source, pageSize=20, preview=False, parent=None, *, selectionFields=(),
                 projectRoot=None, props=None, needsCount=False, requiresIdentity=False):
        super().__init__(source, preview, parent, projectRoot)
        historyView = props.historyView if props is not None else None
        self.listMode = historyView is not None and historyView.mode == 'list'
        self.requiresIdentity = requiresIdentity
        self.needsCount, self.count = needsCount, None
        self._countWorker = None
        self.pageSize, self.page, self.hasNext = pageSize, 0, False
        self.orderBy = source.orderBy or next(iter(source.columns), '')
        self.descending = source.descending
        self._filterColumn, self._filterText = next(iter(source.columns), ''), ''
        self.selectionFields = tuple(dict.fromkeys(selectionFields))
        self._selectionRows, self._selected = (), None
        self._rowIds, self._keys = (), ()
        self._index, self._currentKey, self._resumeKey = -1, (), ()
        self._expiredRetry = False
        self._resumeAfterWorker = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.message)
        self.filterBar = QWidget(self)
        filters = QHBoxLayout(self.filterBar)
        filters.setContentsMargins(0, 0, 0, 0)
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
        self.clearFilterButton = QPushButton('清除')
        self.clearFilterButton.clicked.connect(self.clearFilter)
        filters.addWidget(self.clearFilterButton)
        layout.addWidget(self.filterBar)
        self.filterBar.setVisible(bool(source.columns) and (historyView is None or historyView.showFilter))
        self.model = SqliteRowsModel(source.columns, self, listMode=self.listMode)
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
        header.setSectionsClickable(historyView is None or historyView.showSort)
        header.setSortIndicatorShown(historyView is None or historyView.showSort)
        header.setSortIndicator(source.columns.index(self.orderBy) if self.orderBy in source.columns else -1,
                                Qt.DescendingOrder if self.descending else Qt.AscendingOrder)
        header.sectionClicked.connect(self.sortColumn)
        self.sortField = QComboBox()
        self.sortDirection = QPushButton()
        self.sortBar = QWidget(self)
        sortControls = QHBoxLayout(self.sortBar)
        sortControls.setContentsMargins(0, 0, 0, 0)
        sortControls.addWidget(self.sortField, 1)
        sortControls.addWidget(self.sortDirection)
        layout.addWidget(self.sortBar)
        self.sortBar.setVisible(self.listMode and historyView.showSort)
        if self.listMode:
            header.hide()
            verticalHeader = self.table.verticalHeader()
            verticalHeader.hide()
            verticalHeader.setDefaultSectionSize(52 if historyView.subtitleField else 37)
            self.table.setShowGrid(False)
            self.table.setItemDelegate(_RecordListRow(source.columns, historyView,
                props, props.emptyText, self.table))
            self.sortField.addItems(list(dict.fromkeys([self.orderBy, *source.columns])))
            self.sortField.setToolTip('排序字段')
            self.sortField.currentTextChanged.connect(self.sortBy)
            self.sortDirection.clicked.connect(lambda: self.sortBy(self.orderBy))
        layout.addWidget(self.table, 1)
        self.paginationBar = QWidget(self)
        controls = QHBoxLayout(self.paginationBar)
        controls.setContentsMargins(0, 0, 0, 0)
        self.previousButton = QPushButton('上一页')
        self.previousButton.clicked.connect(lambda: self.move(-1))
        self.nextButton = QPushButton('下一页')
        self.nextButton.clicked.connect(lambda: self.move(1))
        self.pageLabel = QLabel('第 1 页')
        controls.addWidget(self.previousButton)
        controls.addWidget(self.nextButton)
        controls.addWidget(self.pageLabel, 1)
        controls.addWidget(self.refreshButton)
        layout.addWidget(self.paginationBar)
        self.paginationBar.setVisible(historyView is None or historyView.showPagination)
        self._controls()

    def refresh(self):
        self._query(self._resumeKey or self._currentKey)

    def _query(self, anchorKey=(), step=0, *, first=False, expired=False):
        if self.preview or self.busy or self._closed:
            return
        self._resumeKey = anchorKey
        self._expiredRetry = expired
        self.count = None
        self.clearSelection()
        source = self.source
        arguments = dict(pageSize=self.pageSize, page=0 if first else self.page,
                         filterColumn=self._filterColumn, filterText=self._filterText,
                         orderBy=self.orderBy, descending=self.descending,
                         anchorKey=anchorKey or None, anchorStep=step,
                         requiresIdentity=self.requiresIdentity,
                         selectionFields=self.selectionFields, projectRoot=self.projectRoot)
        self._start(lambda: self._history().readPage(self.componentId, **arguments)
                    if source.location == 'runtime' else readPage(source, **arguments))

    def applyFilter(self):
        if self.preview or self.busy:
            return
        self._filterColumn, self._filterText = self.filterColumn.currentText(), self.search.text()
        self._query(first=True)

    def clearFilter(self):
        self.search.clear()
        self.applyFilter()

    def move(self, delta):
        if self.preview or self.busy or (delta > 0 and not self.hasNext):
            return
        self.page = max(0, self.page + delta)
        self._query()

    def sortColumn(self, section):
        if self.preview or self.busy or not 0 <= section < len(self.source.columns):
            return
        self.sortBy(self.source.columns[section])

    def sortBy(self, column):
        if self.preview or self.busy:
            return
        self.descending = not self.descending if self.orderBy == column else False
        self.orderBy = column
        self.table.horizontalHeader().setSortIndicator(
            self.source.columns.index(column) if column in self.source.columns else -1,
            Qt.DescendingOrder if self.descending else Qt.AscendingOrder)
        self._query(first=True)

    def _clear(self):
        self._resumeKey = ()
        self.count = None
        self.clearSelection()
        self._selectionRows, self._rowIds, self._keys = (), (), ()
        self.model.replace(())
        self.hasNext = False
        self.pageLabel.setText(f'第 {self.page + 1} 页')

    def _accept(self, result):
        self.clearSelection()
        self._resumeKey = ()
        self._selectionRows = result.selectionRows
        self._rowIds = result.rowIds
        self._keys = result.keyValues
        self.model.replace(result.rows)
        self.hasNext, self.page = result.hasNext, result.page
        self.pageLabel.setText(f'第 {self.page + 1} 页 · {len(result.rows)} 行')
        self.message.setText('' if result.rows else '未找到匹配记录')
        self.message.setToolTip(self._sourceLabel() + ' · ' + self.source.table)
        if result.rows:
            index = self._rowIds.index(result.selectedRowId) if result.selectedRowId else 0
            self.table.selectRow(index)
        if self.needsCount:
            self._queryCount()

    def _queryCount(self):
        filters = dict(filterColumn=self._filterColumn, filterText=self._filterText)
        source, root = self.source, self.projectRoot
        worker = self._countWorker = _Read(self._generation, lambda: self._history().readValue(self.componentId, **filters)
            if source.location == 'runtime' else readValue(source, projectRoot=root, **filters))
        worker.reply.ready.connect(self._acceptCount, Qt.QueuedConnection)
        QThreadPool.globalInstance().start(worker)

    def _acceptCount(self, generation, result, error):
        if generation == self._generation:
            self._countWorker = None
            self.count = result if not error else None
            self.stateChanged.emit()

    def selectedRow(self):
        return None if self._selected is None else dict(self._selected)

    def clearSelection(self):
        if self.busy:
            self._generation += 1
        self.table.clearSelection()
        self._selected, self._index, self._currentKey = None, -1, ()
        self.selectedRowChanged.emit(None)
        self.stateChanged.emit()

    def _selectionChanged(self, _selected, _deselected):
        indices = self.table.selectionModel().selectedRows()
        row = indices[0].row() if indices else -1
        self._index = row if not self.preview and not self.busy and 0 <= row < self.model.rowCount() else -1
        self._selected = (dict(self._selectionRows[self._index])
                          if 0 <= self._index < len(self._selectionRows) else None)
        self._currentKey = self._keys[self._index] if 0 <= self._index < len(self._keys) else ()
        self._expiredRetry = False
        self.selectedRowChanged.emit(self.selectedRow())
        self.stateChanged.emit()

    def recordValue(self, reference):
        if reference.kind == 'meta':
            if reference.meta == 'count':
                return self.count
            return self.page * self.pageSize + self._index + 1 if self._index >= 0 else None
        if self._index >= 0:
            if reference.field in self.source.columns:
                return self.model.rows[self._index][self.source.columns.index(reference.field)]
        return None

    def canReview(self, action):
        if self.preview or self.busy:
            return False
        if action == 'history_first':
            return True
        if not self._currentKey:
            return False
        if action == 'history_previous':
            return self.page > 0 or self._index > 0
        return self._index + 1 < self.model.rowCount() or self.hasNext

    def review(self, action):
        if not self.canReview(action):
            return
        if action == 'history_first':
            self._query(first=True)
        else:
            self._query(self._currentKey, -1 if action == 'history_previous' else 1)

    def retryExpiredImage(self):
        if not self._expiredRetry and self._currentKey and not self.busy:
            self._query(self._currentKey, expired=True)

    def saveReviewState(self):
        return (self.page, self._filterColumn, self._filterText, self.orderBy, self.descending,
                self._currentKey or self._resumeKey)

    def restoreReviewState(self, state):
        self.page, self._filterColumn, self._filterText, self.orderBy, self.descending, self._resumeKey = state
        self.filterColumn.setCurrentText(self._filterColumn)
        self.search.setText(self._filterText)
        self.sortField.blockSignals(True)
        self.sortField.setCurrentText(self.orderBy)
        self.sortField.blockSignals(False)

    def _finished(self, generation, result, error):
        expired = self._expiredRetry
        stale = generation != self._generation
        super()._finished(generation, result, error)
        if not stale:
            self._expiredRetry = expired
        elif self._resumeAfterWorker and self.isVisible() and not self.loaded and not self.preview and not self._closed:
            self.refresh()
        self._resumeAfterWorker = False

    def showEvent(self, event):
        self._resumeAfterWorker = self.busy and not self.loaded
        super().showEvent(event)

    def hideEvent(self, event):
        self._resumeKey = self._currentKey or self._resumeKey
        self.loaded = False
        self._resumeAfterWorker = False
        self._generation += 1
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
        for field in (self.filterColumn, self.search, self.filterButton, self.clearFilterButton,
                      self.sortField, self.sortDirection, self.table):
            field.setEnabled(enabled)
        self.sortDirection.setText('降序' if self.descending else '升序')
        self.message.setVisible(bool(self.message.text()))
        self.stateChanged.emit()


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
