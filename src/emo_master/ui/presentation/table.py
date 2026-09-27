"""Bounded collection view; result-scoped rows, never a history query."""
import json
import math
import sys

from PySide2.QtCore import QAbstractTableModel, QModelIndex, Qt
from PySide2.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QTableView


def retainedBytes(value):
    pending, seen, total = [value], set(), 0
    while pending:
        item = pending.pop()
        if id(item) in seen:
            continue
        seen.add(id(item))
        total += sys.getsizeof(item)
        if isinstance(item, dict):
            pending.extend(item.keys())
            pending.extend(item.values())
        elif isinstance(item, list):
            pending.extend(item)
    return total


class CollectionModel(QAbstractTableModel):
    limit = 2 * 1024 * 1024

    def __init__(self, columns, pageSize, parent=None):
        super().__init__(parent)
        self.columns, self.pageSize = columns, pageSize
        self.rows, self.order = [], []
        self.page = 0
        self.key = ''
        self.bytesHeld = 0

    def replace(self, value, key):
        # The server has already applied the DataSource fieldPath. Only unwrap
        # the declared collection envelope; never project the source again.
        rows = value.get('items') if isinstance(value, dict) else value
        if not isinstance(rows, list) or len(rows) > 4096:
            raise ValueError('COLLECTION_UNSUPPORTED')
        # Conservative reserve also covers rendered/sort keys and both order
        # lists, not just the parsed rows. Reject before retaining the snapshot.
        size = 4 * retainedBytes(rows) + len(rows) * 128
        if size > self.limit:
            raise ValueError('UI_TABLE_BUDGET')
        self.beginResetModel()
        self.rows, self.order = rows, list(range(len(rows)))
        self.page, self.key, self.bytesHeld = 0, key, size
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else min(self.pageSize, max(0, len(self.rows) - self.page * self.pageSize))

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.columns)

    def value(self, row, column):
        value = self.rows[row]
        for part in self.columns[column].fieldPath:
            if not isinstance(value, dict) or part not in value:
                return 'FIELD_MISSING'
            value = value[part]
        return value

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid() or role != Qt.DisplayRole:
            return None
        value = self.value(self.order[self.page * self.pageSize + index.row()], index.column())
        text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
        return text if len(text) <= 1024 else text[:1024] + '…（显示截断）'

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role == Qt.DisplayRole:
            return self.columns[section].title if orientation == Qt.Horizontal else str(self.page*self.pageSize+section+1)
        return None

    def sort(self, column, order=Qt.AscendingOrder):
        if not 0 <= column < len(self.columns):
            return
        def key(row):
            value = self.value(row, column)
            if type(value) in (int, float):
                return (0, value)
            return (1, json.dumps(value, ensure_ascii=False, sort_keys=True))
        self.beginResetModel()
        self.order.sort(key=key, reverse=order == Qt.DescendingOrder)
        self.page = 0
        self.endResetModel()

    def move(self, delta):
        target = max(0, min(self.page + delta, max(0, math.ceil(len(self.rows)/self.pageSize)-1)))
        self.beginResetModel()
        self.page = target
        self.endResetModel()


class CollectionView(QWidget):
    def __init__(self, props, parent=None):
        super().__init__(parent)
        self.model = CollectionModel(props.columns, props.pageSize, self)
        layout = QVBoxLayout(self)
        self.message = QLabel('尚无集合')
        layout.addWidget(self.message)
        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.setSortingEnabled(True)
        self.table.setMinimumHeight(120)
        layout.addWidget(self.table)
        controls = QHBoxLayout()
        for title, delta in [('上一页', -1), ('下一页', 1)]:
            button = QPushButton(title)
            button.clicked.connect(lambda _checked=False, step=delta: self.move(step))
            controls.addWidget(button)
        self.pageLabel = QLabel()
        controls.addWidget(self.pageLabel)
        layout.addLayout(controls)
        self.model.modelReset.connect(self.updatePage)

    def updatePage(self):
        pages = max(1, math.ceil(len(self.model.rows)/self.model.pageSize))
        self.pageLabel.setText(f'{self.model.page+1}/{pages} · {len(self.model.rows)} 行')

    def move(self, delta):
        self.model.move(delta)

    def clear(self, message):
        self.model.replace([], '')
        self.message.setText(message)

    def submit(self, value, key, error):
        if error or not self.model.columns:
            self.clear(error or '未配置表格列')
            return
        if key == self.model.key:
            return  # preserves view-only page/sort during health updates
        try:
            self.model.replace(value, key)
            self.table.horizontalHeader().setSortIndicator(-1, Qt.AscendingOrder)
            self.message.setText('本次结果集合 · 无历史查询')
        except ValueError as problem:
            self.clear(str(problem))
