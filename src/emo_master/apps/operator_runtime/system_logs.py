"""Runtime system events in Operator's log tool."""
from concurrent.futures import ThreadPoolExecutor

from PySide2.QtCore import Qt, QTimer
from PySide2.QtWidgets import (
    QAbstractItemView, QComboBox, QHBoxLayout, QLabel, QPushButton, QSplitter,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from emo_master.clients.runtime.calls import DisplayCallContext
from emo_master.clients.runtime.log_records import nextLogPage, querySystemLogPage
from emo_master.core.runtime_log_records import StructuredLogEntry
from emo_master.ui.runtime_logs.log_record_detail import LogRecordDetail


class RuntimeSystemLogs(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("runtimeSystemLogs")
        self.client, self.instance = None, ""
        self.page = None
        self.pending = None
        self._retiring = None
        self.rows = []
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="operator-system-logs")
        executor = self.executor
        self.destroyed.connect(lambda: executor.shutdown(wait=False, cancel_futures=True))
        self.poll = QTimer(self)
        self.poll.setInterval(40)
        self.poll.timeout.connect(self._finish)

        layout = QVBoxLayout(self)
        bar = QHBoxLayout()
        self.level = QComboBox()
        for name, value in (("全部级别", ""), ("错误", "ERROR"), ("警告", "WARN"),
                            ("信息", "INFO"), ("调试", "DEBUG")):
            self.level.addItem(name, value)
        bar.addWidget(self.level)
        self.refreshButton = QPushButton("查询")
        self.nextButton = QPushButton("下一页")
        self.nextButton.setEnabled(False)
        bar.addWidget(self.refreshButton)
        bar.addWidget(self.nextButton)
        bar.addStretch()
        layout.addLayout(bar)
        self.status = QLabel("等待 Runtime 连接。")
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.PlainText)
        layout.addWidget(self.status)

        split = QSplitter(Qt.Vertical)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["时间", "级别", "事件类型", "消息"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setWordWrap(False)
        self.table.setColumnWidth(0, 125)
        self.table.setColumnWidth(1, 65)
        self.table.setColumnWidth(2, 220)
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setStretchLastSection(True)
        split.addWidget(self.table)
        self.detail = LogRecordDetail()
        split.addWidget(self.detail)
        split.setSizes([260, 270])
        layout.addWidget(split, 1)
        self.table.itemSelectionChanged.connect(self._select)
        self.refreshButton.clicked.connect(lambda: self.query())
        self.nextButton.clicked.connect(lambda: self.query(nextPage=True))
        self.level.currentIndexChanged.connect(lambda: self.query())

    def setRuntime(self, client, instance):
        if client is self.client and instance == self.instance:
            return
        self.cancel()
        self.client, self.instance, self.page = client, instance, None
        self.status.setText("等待 Runtime 连接。" if not instance else "查询无任务归属的 Runtime 保留记录。")
        self.nextButton.setEnabled(False)
        self._render([])
        if self.isVisible() and instance:
            self.query()

    def showEvent(self, event):
        super().showEvent(event)
        if self.page is None and self.pending is None and self.instance:
            self.query()

    def query(self, *, nextPage=False):
        if self.client is None or not self.instance:
            return
        self.cancel()
        level = self.level.currentData()
        options = dict(runtimeInstanceId=self.instance)
        if level:
            options["levels"] = (level,)
        if nextPage and self.page is not None:
            options.update(nextLogPage(self.page))
        context = DisplayCallContext()
        self.pending = self.executor.submit(querySystemLogPage, self.client, options, context), context
        self.status.setText("正在查询 Runtime 系统事件…")
        self.nextButton.setEnabled(False)
        self.poll.start()

    def cancel(self):
        self.poll.stop()
        if self.pending is not None:
            future, context = self.pending
            self.pending = None
            context.cancel()
            if not future.cancel() and not future.done():
                self._retiring = future

    @property
    def idle(self):
        return (self._retiring is None or self._retiring.done()) and (self.pending is None or self.pending[0].done())

    def shutdown(self):
        self.cancel()
        self.executor.shutdown(wait=False, cancel_futures=True)

    def _finish(self):
        if self.pending is None:
            return
        future, _ = self.pending
        if not future.done():
            return
        self.poll.stop()
        self.pending = None
        try:
            page = future.result()
            if not page["ok"] or page["runtimeInstanceId"] != self.instance:
                raise ValueError(page["message"] or "Runtime 实例已切换。")
        except Exception as error:
            self.status.setText("日志读取失败：" + str(error))
            self.nextButton.setEnabled(bool(self.page and self.page["hasMore"]))
            return
        self.page = page
        rows = [StructuredLogEntry.fromRuntimeEvent(dict(row["event"], eventId=row["eventId"]))
                for row in page["events"]]
        self._render(rows)
        self.nextButton.setEnabled(page["hasMore"])
        self.status.setText(f"本页 {len(rows)} 条 Runtime 系统事件" if rows else "当前范围没有 Runtime 系统事件。")

    def _render(self, rows):
        self.rows = rows
        self.table.clearContents()
        self.table.setRowCount(len(rows))
        for index, entry in enumerate(rows):
            for column, text in enumerate((entry.displayTime(), entry.level, entry.eventType, entry.displayMessage())):
                item = QTableWidgetItem(text)
                item.setToolTip(text)
                item.setData(Qt.UserRole, entry.eventId)
                self.table.setItem(index, column, item)
        self.table.clearSelection()
        self.detail.clear()

    def _select(self):
        row = self.table.currentRow()
        if 0 <= row < len(self.rows) and self.table.selectedItems():
            entry = self.rows[row]
            self.detail.setEvent(entry.rawRecord(), displayMessage=entry.displayMessage())
        else:
            self.detail.clear()
