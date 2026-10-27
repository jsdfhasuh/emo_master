"""Compact operator log tool and a shared, single-flight asynchronous reader."""
from __future__ import annotations

import json
from pathlib import Path
import threading
from typing import Any

from PySide2.QtCore import QObject, Qt, QTimer, QUrl, Signal
from PySide2.QtGui import QDesktopServices, QTextCursor
from PySide2.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QHBoxLayout, QHeaderView, QLabel,
    QPlainTextEdit, QPushButton, QSplitter, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from .log_reader import (
    JsonlTailReader, LogRecord, MAX_RECORDS, listLogFiles, recordDetails, recordSource, safeText,
)


class LogFeed(QObject):
    updated: Any = Signal()
    _completed: Any = Signal(object)

    def __init__(self, directory: Path, parent=None):
        super().__init__(parent)
        self.directory, self.writer = directory, None
        self.reader = JsonlTailReader()
        self.cursor = None
        self.records: list[LogRecord] = []
        self.files: tuple[Path, ...] = ()
        self.historyPath = None
        self.activePath = None
        self.error, self.parseErrors, self.parseReason = "", 0, ""
        self.recentOnly = False
        self.diagnostics: list[str] = []
        self.generation = 0
        self.worker = None
        self.visible = self.closed = False
        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self.refresh)
        self._completed.connect(self._accept)

    def setSource(self, controller):
        writer = controller.runtime.operationalLogWriter if controller else None
        if self.writer is writer:
            return
        self.writer = writer
        directory = Path(writer.directory) if writer is not None else self.directory
        if directory != self.directory:
            self.directory = directory
            self.selectFile(None)
        else:
            # A writer becoming ready must not override an operator's history
            # choice made while the project was still preparing.
            self.generation += 1
            self.updated.emit()
            self.refresh()

    def setVisible(self, visible):
        self.visible = visible and not self.closed
        if self.visible:
            self.timer.start()
            self.refresh()
        else:
            self.timer.stop()

    def selectFile(self, path):
        self.historyPath = Path(path) if path is not None else None
        self.generation += 1
        self.cursor = None
        self.records = []
        self.error, self.parseErrors, self.parseReason = "", 0, ""
        self.recentOnly = False
        self.updated.emit()
        self.refresh()

    def refresh(self):
        if self.closed or not self.visible or self.worker is not None:
            return
        active = self.writer.activePath if self.writer is not None else None
        self.activePath = Path(active) if active is not None else None
        path = self.historyPath or self.activePath
        generation, cursor, directory = self.generation, self.cursor, self.directory

        def read():
            files, result, error = (), None, ""
            try:
                files = listLogFiles(directory)
                if path is not None:
                    result = self.reader.read(path, cursor)
            except (OSError, ValueError) as failure:
                error = safeText(failure)
            self._completed.emit((generation, path, files, result, error))

        self.worker = threading.Thread(target=read, name="operator-jsonl-reader")
        self.worker.start()

    def _accept(self, value):
        if self.worker is not None:
            self.worker.join()
        self.worker = None
        if self.closed:
            return
        generation, path, files, result, error = value
        if generation != self.generation:
            self.refresh()  # A selection changed, not a second queued task.
            return
        self.files = files
        self.error = error
        if result is not None:
            self.cursor = result.cursor
            if result.reset:
                self.records = []
                self.parseErrors = 0
                self.recentOnly = result.recentOnly
            self.records.extend(result.records)
            if len(self.records) > MAX_RECORDS:
                self.recentOnly = True
                self.records = self.records[-MAX_RECORDS:]
            self.parseErrors += result.errors
            if result.reason:
                self.parseReason = result.reason
        self.updated.emit()

    def beginClose(self):
        self.closed = True
        self.visible = False
        self.timer.stop()

    @property
    def idle(self):
        return self.worker is None or not self.worker.is_alive()

    def fileLabel(self, path):
        return "本次活动文件" if path == self.activePath else "历史文件"

    def sourceText(self, *, fullPath=True):
        path = self.historyPath or self.activePath
        if path is None:
            return "暂无本次活动日志文件；可选择日志目录中的历史文件。"
        mode = "（手动固定，不跟随轮转）" if self.historyPath is not None else ""
        return self.fileLabel(path) + mode + "：" + safeText(path if fullPath else path.name)

    def statusText(self):
        lines = [self.sourceText()]
        if self.error:
            lines.append("读取失败（可刷新重试）：" + self.error)
        elif not self.files and not self.records:
            lines.append("日志目录暂无 runtime-*.jsonl 文件。")
        if self.writer is not None:
            lines.append("日志 writer：" + ("已启用" if self.writer.enabled else "不可用"))
            if self.writer.failureMessage:
                lines.append("日志写入诊断：" + safeText(self.writer.failureMessage))
        if self.parseErrors:
            lines.append(f"跳过 {self.parseErrors} 条无效记录：{self.parseReason}")
        lines.append(f"最近记录：当前保留 {len(self.records)} 条（初读最多 2 MiB / 2000 条，视图上限 10000 条）"
                     + ("；更早记录请在原文件中查看。" if self.recentOnly else "。"))
        return "\n".join(lines)


class RuntimeLogView(QWidget):
    def __init__(self, feed: LogFeed, parent=None):
        super().__init__(parent)
        self.feed = feed
        self._signature = None
        self._shownRecords: list[LogRecord] = []
        self._filePaths = None
        layout = QVBoxLayout(self)
        row = QHBoxLayout()
        self.fileCombo = QComboBox()
        self.fileCombo.setMinimumContentsLength(16)
        self.fileCombo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        row.addWidget(self.fileCombo, 1)
        self.levelCombo = QComboBox()
        for label, value in (("全部级别", ""), ("错误", "ERROR"), ("警告", "WARN"), ("信息", "INFO"), ("调试", "DEBUG")):
            self.levelCombo.addItem(label, value)
        row.addWidget(self.levelCombo)
        self.resetButton = QPushButton("恢复全部")
        row.addWidget(self.resetButton)
        self.refreshButton = QPushButton("刷新")
        row.addWidget(self.refreshButton)
        layout.addLayout(row)
        self.statusLabel = QLabel()
        self.statusLabel.setTextFormat(Qt.PlainText)
        self.statusLabel.setWordWrap(True)
        from PySide2.QtWidgets import QSizePolicy
        self.statusLabel.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        layout.addWidget(self.statusLabel)
        self.emptyLabel = QLabel()
        self.emptyLabel.setTextFormat(Qt.PlainText)
        layout.addWidget(self.emptyLabel)
        self.splitter = QSplitter(Qt.Vertical)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["时间", "级别", "来源", "消息"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setTextElideMode(Qt.ElideRight)
        self.table.setWordWrap(False)
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        for column, width in enumerate((175, 65, 145)):
            self.table.setColumnWidth(column, width)
        self.splitter.addWidget(self.table)
        detailHost = QWidget()
        detailLayout = QVBoxLayout(detailHost)
        detailLayout.setContentsMargins(0, 0, 0, 0)
        detailLayout.addWidget(QLabel("记录详情（只读、已脱敏；不执行或重试）"))
        self.detail = QPlainTextEdit()
        self.detail.setReadOnly(True)
        detailLayout.addWidget(self.detail)
        self.rawToggle = QCheckBox("展开原始记录（已脱敏）")
        detailLayout.addWidget(self.rawToggle)
        self.raw = QPlainTextEdit()
        self.raw.setReadOnly(True)
        self.raw.hide()
        self.rawToggle.toggled.connect(self.raw.setVisible)
        detailLayout.addWidget(self.raw)
        self.splitter.addWidget(detailHost)
        self.splitter.setSizes([280, 180])
        layout.addWidget(self.splitter, 1)
        self.diagnostics = QPlainTextEdit()
        self.diagnostics.setReadOnly(True)
        self.diagnostics.setMaximumHeight(85)
        layout.addWidget(self.diagnostics)
        bottom = QHBoxLayout()
        self.followLatest = QCheckBox("跟随最新记录")
        self.followLatest.setChecked(True)
        bottom.addWidget(self.followLatest)
        bottom.addStretch()
        self.folderButton = QPushButton("打开当前日志文件夹")
        bottom.addWidget(self.folderButton)
        layout.addLayout(bottom)
        self.folderFeedback = QLabel()
        self.folderFeedback.setTextFormat(Qt.PlainText)
        self.folderFeedback.setWordWrap(True)
        self.folderFeedback.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        layout.addWidget(self.folderFeedback)
        self.fileCombo.currentIndexChanged.connect(self._selectFile)
        self.levelCombo.currentIndexChanged.connect(self.render)
        self.resetButton.clicked.connect(self.resetFilter)
        self.refreshButton.clicked.connect(feed.refresh)
        self.folderButton.clicked.connect(self.openFolder)
        self.table.itemSelectionChanged.connect(self.showDetails)
        feed.updated.connect(self.render)
        self.render()

    def resetFilter(self):
        self.levelCombo.setCurrentIndex(0)

    def _selectFile(self, index):
        self.feed.selectFile(self.fileCombo.itemData(index))

    def _renderDiagnostics(self):
        text = "\n".join(self.feed.diagnostics)
        edit = self.diagnostics
        previous = edit.toPlainText()
        if previous != text:
            cursor = edit.textCursor()
            position, anchor, selected = cursor.position(), cursor.anchor(), cursor.selectedText()
            horizontal, vertical = edit.horizontalScrollBar().value(), edit.verticalScrollBar().value()
            if text.startswith(previous):
                # Keep the existing document layout and viewport on normal appends.
                insertion = QTextCursor(edit.document())
                insertion.movePosition(QTextCursor.End)
                insertion.insertText(text[len(previous):])
            else:
                edit.setPlainText(text)
            cursor = edit.textCursor()
            limit = edit.document().characterCount() - 1
            cursor.setPosition(min(anchor, limit))
            cursor.setPosition(min(position, limit), QTextCursor.KeepAnchor)
            # Replaced diagnostics must not leave a selection over unrelated text.
            if cursor.selectedText() != selected:
                cursor.clearSelection()
            edit.setTextCursor(cursor)
            edit.horizontalScrollBar().setValue(horizontal)
            edit.verticalScrollBar().setValue(vertical)
        edit.setVisible(bool(text))

    def render(self):
        feed = self.feed
        fileSelection = feed.files, feed.historyPath, feed.activePath
        if self._filePaths != fileSelection:
            self._filePaths = fileSelection
            self.fileCombo.blockSignals(True)
            self.fileCombo.clear()
            self.fileCombo.addItem("本次活动文件（跟随轮转）", None)
            for path in feed.files:
                self.fileCombo.addItem(feed.fileLabel(path) + " · " + safeText(path.name), str(path))
                self.fileCombo.setItemData(self.fileCombo.count() - 1, safeText(path), Qt.ToolTipRole)
            if feed.historyPath is not None:
                index = self.fileCombo.findData(str(feed.historyPath))
                if index < 0:  # Keep a removed/unreadable selected file visible for diagnosis.
                    self.fileCombo.addItem(feed.fileLabel(feed.historyPath) + " · " + safeText(feed.historyPath.name), str(feed.historyPath))
                    index = self.fileCombo.count() - 1
                self.fileCombo.setCurrentIndex(index)
            self.fileCombo.blockSignals(False)
        self.statusLabel.setText(feed.statusText())
        self._renderDiagnostics()
        level = self.levelCombo.currentData()
        records = [row for row in feed.records if not level or str(row.data.get("level", "")).upper().replace("WARNING", "WARN") == level]
        signature = (feed.generation, level, tuple((id(row), row.identity) for row in records))
        if signature == self._signature:
            return
        self._signature = signature
        selected = self.table.currentRow()
        identity = self._shownRecords[selected].identity if 0 <= selected < len(self._shownRecords) else None
        top = self.table.rowAt(0)
        anchor = self._shownRecords[top].identity if 0 <= top < len(self._shownRecords) else None
        topOffset = self.table.rowViewportPosition(top) if top >= 0 else 0
        horizontal = self.table.horizontalScrollBar().value()
        self.table.blockSignals(True)
        self.table.setRowCount(len(records))
        for rowIndex, record in enumerate(records):
            data = record.data
            values = (str(data.get("timestamp", data.get("timestampMs", ""))), str(data.get("level", "")),
                      recordSource(data), str(data.get("message", "")))
            for column, value in enumerate(values):
                item = QTableWidgetItem(value.replace("\n", " ")[:1000])
                item.setToolTip(value[:4096])
                self.table.setItem(rowIndex, column, item)
        self._shownRecords = records
        selection = next((index for index, record in enumerate(records) if record.identity == identity), -1)
        if selection >= 0:
            self.table.selectRow(selection)
        else:
            self.table.clearSelection()
            self.table.setCurrentCell(-1, -1)
        self.table.blockSignals(False)
        if self.followLatest.isChecked():
            self.table.scrollToBottom()
        else:
            # Head eviction changes row indices; retain the visible record instead.
            anchorRow = next((index for index, record in enumerate(records) if record.identity == anchor), -1)
            if anchorRow >= 0:
                self.table.scrollToItem(self.table.item(anchorRow, 0), QAbstractItemView.PositionAtTop)
                if self.table.verticalScrollMode() == QAbstractItemView.ScrollPerPixel:
                    scrollbar = self.table.verticalScrollBar()
                    scrollbar.setValue(scrollbar.value() - topOffset)
            else:
                self.table.scrollToTop()
        self.table.horizontalScrollBar().setValue(horizontal)
        self.emptyLabel.setText("无匹配记录；点击“恢复全部”查看其他级别。" if not records and level else
                                "暂无记录。" if not records else "")
        if selection < 0:
            self.showDetails()

    def showDetails(self):
        index = self.table.currentRow()
        record = self._shownRecords[index] if 0 <= index < len(self._shownRecords) else None
        self.detail.setPlainText(recordDetails(record) if record else "选择一条记录查看真实错误码和身份。")
        self.raw.setPlainText(json.dumps(record.data, ensure_ascii=False, indent=2) if record else "")

    def openFolder(self):
        path = self.feed.directory
        try:
            if not path.is_dir():
                raise OSError("目录尚不存在或不是文件夹")
            if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))):
                raise OSError("系统无法打开该目录")
        except OSError as error:
            self.folderFeedback.setText(f"打开失败：{safeText(path)}\n{safeText(error)}")
            return False
        self.folderFeedback.setText("已请求系统打开：" + safeText(path))
        return True
