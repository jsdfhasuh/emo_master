"""Compact operator log tool and a shared, single-flight asynchronous reader."""
from __future__ import annotations

import json
from pathlib import Path
import threading
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from typing import Any

from PySide2.QtCore import QAbstractTableModel, QModelIndex, QObject, QSignalBlocker, Qt, QTimer, QUrl, Signal
from PySide2.QtGui import QBrush, QColor, QDesktopServices, QTextCursor
from PySide2.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QComboBox, QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit,
    QMenu, QMessageBox, QPlainTextEdit, QPushButton, QScrollArea, QSizePolicy, QSplitter, QTableView, QVBoxLayout, QWidget,
)

from emo_master.core.runtime_log_messages import runtimeLogComponentTitle

from .log_reader import (
    JsonlTailReader, LogFilters, LogRecord, MAX_RECORDS, displayLogMessage, formatLocalTimestamp, iterLogFileRecords,
    listLogFiles, normalizedLevel, recordDetails, recordSource, safeText, snapshotLogFiles,
)
from .log_sources import RuntimeLogSources


class LogFeed(QObject):
    updated: Any = Signal()
    _completed: Any = Signal(object)
    _exportCompleted: Any = Signal(object)

    def __init__(self, directory: Path, parent=None):
        super().__init__(parent)
        self.directory, self.writer = directory, None
        self.applicationWriter = None
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
        self.exportWorker = None
        self.exportPool = None
        self.exportCancel = threading.Event()
        self.exportMessage = ""
        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self.refresh)
        self._completed.connect(self._accept)
        self._exportCompleted.connect(self._acceptExport)

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
        if active is None and self.applicationWriter is not None:
            active = self.applicationWriter.activePath
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
        self.exportCancel.set()
        if self.exportPool is not None:
            self.exportPool.shutdown(wait=False, cancel_futures=True)

    @property
    def idle(self):
        return (self.worker is None or not self.worker.is_alive()) and (
            self.exportWorker is None or self.exportWorker.done())

    def exportFiles(self, target, *, formatName, paths, filters=LogFilters()):
        from emo_master.core.log_export import exportLogs, LogExportCancelled
        if self.exportWorker is not None and not self.exportWorker.done():
            return
        ranges = snapshotLogFiles(paths)
        if not ranges:
            raise ValueError("没有选定可导出的日志文件")
        self.exportCancel.clear()
        self.exportMessage = "正在导出选定本机文件范围…"
        if self.exportPool is None:
            self.exportPool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="runtime-log-export")
        metadata = filters.exportMetadata()
        metadata["files"] = [dict(path=safeText(item.path), endOffset=item.endOffset) for item in ranges]
        def run():
            try:
                records = (record for record in iterLogFileRecords(ranges, cancelled=self.exportCancel.is_set)
                           if filters.matches(record))
                count = exportLogs(target, records, formatName=formatName, scope="local_files", filters=metadata,
                                   cancelled=self.exportCancel.is_set)
                result = f"已导出 {count} 条选定本机文件记录：{safeText(target)}"
            except LogExportCancelled:
                result = "导出已取消；目标文件未替换。"
            except Exception as error:
                result = "导出失败：" + safeText(error)
            return result
        def completed(future):
            if not future.cancelled():
                self._exportCompleted.emit(future.result())
        self.exportWorker = self.exportPool.submit(run)
        # Publish only after Future.done() is true so the terminal message and
        # export controls agree, even when Qt handles the signal immediately.
        self.exportWorker.add_done_callback(completed)
        self.updated.emit()

    def _acceptExport(self, message):
        self.exportMessage = message
        if not self.closed:
            self.updated.emit()

    def fileLabel(self, path):
        return "本次活动文件" if path == self.activePath else "历史文件"

    def sourceText(self, *, fullPath=True):
        path = self.historyPath or self.activePath
        if path is None:
            return "暂无本次活动日志文件；可选择日志目录中的历史文件。"
        mode = "（手动固定，不跟随轮转）" if self.historyPath is not None else ""
        return self.fileLabel(path) + mode + "：" + safeText(path if fullPath else path.name)

    def statusText(self, *, fullPath=True):
        lines = [self.sourceText(fullPath=fullPath)]
        if self.error:
            lines.append("读取失败（可刷新重试）：" + self.error)
        elif not self.files and not self.records:
            lines.append("日志目录暂无 runtime/runtime_app JSONL 文件。")
        if self.writer is not None:
            lines.append("日志 writer：" + ("已启用" if self.writer.enabled else "不可用"))
            if self.writer.failureMessage:
                lines.append("日志写入诊断：" + safeText(self.writer.failureMessage))
        if self.parseErrors:
            lines.append(f"跳过 {self.parseErrors} 条无效记录：{self.parseReason}")
        lines.append(f"最近记录：当前保留 {len(self.records)} 条（初读最多 2 MiB / 2000 条，视图上限 10000 条）"
                     + ("；更早记录请在原文件中查看。" if self.recentOnly else "。"))
        if self.exportMessage:
            lines.append(self.exportMessage)
        return "\n".join(lines)


class LogTableModel(QAbstractTableModel):
    HEADERS = ("时间", "级别", "应用", "模块", "来源", "消息")
    LEVEL_COLORS = {"ERROR": QColor("#b42318"), "WARN": QColor("#854d0e"),
                    "INFO": QColor("#175cd3"), "DEBUG": QColor("#536070")}

    def __init__(self, parent=None):
        super().__init__(parent)
        self.records: list[LogRecord] = []

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.records)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.HEADERS)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole and 0 <= section < len(self.HEADERS):
            return self.HEADERS[section]
        return super().headerData(section, orientation, role)

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid() or not 0 <= index.row() < len(self.records):
            return None
        data = self.records[index.row()].data
        column = index.column()
        if role == Qt.ForegroundRole and column == 1:
            color = self.LEVEL_COLORS.get(normalizedLevel(data.get("level")))
            return QBrush(color) if color is not None else None
        if role not in (Qt.DisplayRole, Qt.ToolTipRole):
            return None
        if column == 0:
            value = formatLocalTimestamp(data)
            if role == Qt.ToolTipRole:
                timestamps = [str(data[key]) for key in ("timestamp", "timestampMs") if data.get(key) not in (None, "")]
                if timestamps:
                    return f"显示时间：{value}\n原始时间：{' / '.join(timestamps)}"[:4096]
        elif column == 3:
            value = runtimeLogComponentTitle(str(data.get("component") or ""))
        elif column == 4:
            value = recordSource(data)
        elif column == 5:
            value = displayLogMessage(data)
        else:
            field = "level" if column == 1 else "originApp"
            value = str(data.get(field, ""))
        return value.replace("\n", " ")[:1000] if role == Qt.DisplayRole else value[:4096]

    def setRecords(self, records, *, incremental):
        previous = self.records
        if incremental and previous and records:
            # The tail feed retains the same objects for surviving records.
            # A replaced file can recycle byte offsets but creates new objects.
            dropped = next((index for index, record in enumerate(previous) if record is records[0]), len(previous))
            overlap = len(previous) - dropped
            if overlap and overlap <= len(records) and all(
                previous[dropped + index] is records[index] for index in range(overlap)
            ):
                if dropped:
                    self.beginRemoveRows(QModelIndex(), 0, dropped - 1)
                    del previous[:dropped]
                    self.endRemoveRows()
                if overlap < len(records):
                    self.beginInsertRows(QModelIndex(), overlap, len(records) - 1)
                    previous.extend(records[overlap:])
                    self.endInsertRows()
                return
        self.beginResetModel()
        self.records = records
        self.endResetModel()


class RuntimeLogView(QWidget):
    def __init__(self, feed: LogFeed, parent=None):
        super().__init__(parent)
        self.feed = feed
        self._previousSource = None
        self._filePaths = None
        self._components = None
        self._keyword = ""
        self._restoringSelection = False
        self.deviceId = ""
        self.keywordTimer = QTimer(self)
        self.keywordTimer.setSingleShot(True)
        self.keywordTimer.setInterval(150)
        self.keywordTimer.timeout.connect(self._applyKeyword)
        self.scrollArea = QScrollArea()
        self.scrollArea.setFrameShape(QFrame.NoFrame)
        self.scrollArea.setWidgetResizable(True)
        body = QWidget()
        self.scrollArea.setWidget(body)
        layout = QVBoxLayout(body)
        layout.setContentsMargins(9, 6, 9, 6)
        layout.setSpacing(4)
        row = QHBoxLayout()
        self.fileCombo = QComboBox()
        self.fileCombo.setMinimumContentsLength(16)
        self.fileCombo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        row.addWidget(self.fileCombo, 1)
        self.levelCombo = QComboBox()
        for label, value in (("全部级别", ""), ("错误", "ERROR"), ("警告", "WARN"), ("信息", "INFO"), ("调试", "DEBUG")):
            self.levelCombo.addItem(label, value)
        row.addWidget(self.levelCombo)
        self.componentCombo = QComboBox()
        self.componentCombo.setMinimumContentsLength(14)
        self.componentCombo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        row.addWidget(self.componentCombo)
        layout.addLayout(row)
        row = QHBoxLayout()
        self.keywordEdit = QLineEdit()
        self.keywordEdit.setPlaceholderText("搜索已加载日志")
        self.keywordEdit.setAccessibleName("日志关键字")
        self.keywordEdit.setClearButtonEnabled(True)
        row.addWidget(self.keywordEdit, 1)
        self.resetButton = QPushButton("恢复全部")
        row.addWidget(self.resetButton)
        self.refreshButton = QPushButton("刷新")
        row.addWidget(self.refreshButton)
        self.exportButton = QPushButton("导出…")
        self.exportButton.setToolTip("按当前级别、模块、关键字和设备筛选导出")
        exportMenu = QMenu(self.exportButton)
        self.exportCurrentAction = exportMenu.addAction("当前显示记录（按当前筛选）…")
        self.exportFileAction = exportMenu.addAction("选定文件完整范围（按当前筛选）…")
        self.exportFilesAction = exportMenu.addAction("本机全部文件范围（按当前筛选）…")
        self.exportButton.setMenu(exportMenu)
        row.addWidget(self.exportButton)
        self.cancelExportButton = QPushButton("取消导出")
        row.addWidget(self.cancelExportButton)
        layout.addLayout(row)
        self.deviceFilter = QWidget()
        deviceLayout = QHBoxLayout(self.deviceFilter)
        deviceLayout.setContentsMargins(0, 0, 0, 0)
        self.deviceLabel = QLabel()
        self.deviceLabel.setTextFormat(Qt.PlainText)
        self.deviceLabel.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        deviceLayout.addWidget(self.deviceLabel, 1)
        self.clearDeviceButton = QPushButton("清除设备筛选")
        deviceLayout.addWidget(self.clearDeviceButton)
        layout.addWidget(self.deviceFilter)
        self.deviceFilter.hide()
        self.statusLabel = QLabel()
        self.statusLabel.setTextFormat(Qt.PlainText)
        self.statusLabel.setWordWrap(True)
        self.statusLabel.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        layout.addWidget(self.statusLabel)
        self.emptyLabel = QLabel()
        self.emptyLabel.setTextFormat(Qt.PlainText)
        self.emptyLabel.setWordWrap(True)
        self.emptyLabel.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        layout.addWidget(self.emptyLabel)
        self.splitter = QSplitter(Qt.Vertical)
        self.table = QTableView()
        self.model = LogTableModel(self.table)
        self.table.setModel(self.model)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setTextElideMode(Qt.ElideRight)
        self.table.setWordWrap(False)
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setStretchLastSection(True)
        for column, width in enumerate((175, 65, 80, 100, 145)):
            self.table.setColumnWidth(column, width)
        self.splitter.addWidget(self.table)
        detailHost = QWidget()
        detailLayout = QVBoxLayout(detailHost)
        detailLayout.setContentsMargins(0, 0, 0, 0)
        detailActions = QHBoxLayout()
        detailActions.addWidget(QLabel("记录详情（已脱敏）"), 1)
        self.copyDetailButton = QPushButton("复制详情")
        detailActions.addWidget(self.copyDetailButton)
        self.copyRawButton = QPushButton("复制原始 JSON")
        detailActions.addWidget(self.copyRawButton)
        detailLayout.addLayout(detailActions)
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
        self.componentCombo.currentIndexChanged.connect(self.render)
        self.keywordEdit.textChanged.connect(self._queueKeyword)
        self.keywordEdit.returnPressed.connect(self._applyKeyword)
        self.clearDeviceButton.clicked.connect(lambda: self._setDeviceFilter(""))
        self.resetButton.clicked.connect(self._resetLocalFilters)
        self.refreshButton.clicked.connect(feed.refresh)
        self.exportCurrentAction.triggered.connect(lambda: self.exportLogs(history=False))
        self.exportFileAction.triggered.connect(lambda: self.exportLogs(history=True))
        self.exportFilesAction.triggered.connect(lambda: self.exportLogs(history=True, allFiles=True))
        self.cancelExportButton.clicked.connect(feed.exportCancel.set)
        self.folderButton.clicked.connect(self.openFolder)
        self.table.selectionModel().selectionChanged.connect(self.showDetails)
        self.followLatest.toggled.connect(self._followLatestChanged)
        self.copyDetailButton.clicked.connect(lambda: self._copySelected(self.detail))
        self.copyRawButton.clicked.connect(lambda: self._copySelected(self.raw))
        feed.updated.connect(self.render)
        self.runtimeLogs = RuntimeLogSources(self.scrollArea, feed.directory)
        self.sources = self.runtimeLogs.sources
        self.jobLogs = self.runtimeLogs.jobLogs
        self.systemLogs = self.runtimeLogs.systemLogs
        viewLayout = QVBoxLayout(self)
        viewLayout.setContentsMargins(0, 0, 0, 0)
        viewLayout.addWidget(self.runtimeLogs)
        self.showDetails()
        self.render()

    def setRuntime(self, controller):
        self.runtimeLogs.setRuntime(controller)

    def setToolVisible(self, visible):
        self.runtimeLogs.setToolVisible(visible)

    def beginClose(self):
        self.runtimeLogs.beginClose()

    @property
    def idle(self):
        return self.runtimeLogs.idle

    def finishClose(self):
        self.runtimeLogs.finishClose()

    def resetFilter(self):
        self._resetLocalFilters()
        self.jobLogs.resetFilters()
        self.jobLogs.home(resetCatalog=True)
        self.sources.setCurrentIndex(0)

    def _resetLocalFilters(self):
        self.keywordTimer.stop()
        self._keyword = ""
        blockers = [QSignalBlocker(control) for control in (self.levelCombo, self.componentCombo, self.keywordEdit)]
        self.levelCombo.setCurrentIndex(0)
        self.componentCombo.setCurrentIndex(0)
        self.keywordEdit.clear()
        del blockers
        self._setDeviceFilter("")

    def setDevice(self, deviceId, deviceName=""):
        self.sources.setCurrentIndex(2)
        self._setDeviceFilter(deviceId, deviceName)

    def _setDeviceFilter(self, deviceId, deviceName=""):
        self.deviceId = deviceId or ""
        self.deviceLabel.setText("设备筛选：" + (deviceName or self.deviceId))
        self.deviceLabel.setToolTip(self.deviceId)
        self.deviceFilter.setVisible(bool(self.deviceId))
        self.render()

    def _queueKeyword(self):
        self.keywordTimer.start()

    def _applyKeyword(self):
        self.keywordTimer.stop()
        self._keyword = self.keywordEdit.text().strip().casefold()
        self.render()

    def _filters(self):
        return LogFilters(self.levelCombo.currentData() or "", self.componentCombo.currentData() or "",
                          self._keyword, self.deviceId)

    def _followLatestChanged(self, checked):
        if checked:
            horizontal = self.table.horizontalScrollBar()
            value = horizontal.value()
            self.table.scrollToBottom()
            horizontal.setValue(value)

    def _copySelected(self, source):
        if self._selectedRecord() is not None:
            QApplication.clipboard().setText(source.toPlainText())

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

    def _renderComponents(self):
        selected = self.componentCombo.currentData() or ""
        components = {record.data.get("component") for record in self.feed.records
                      if isinstance(record.data.get("component"), str) and record.data["component"]}
        if selected:
            components.add(selected)
        values = tuple(sorted(components, key=lambda name: (name.casefold(), name)))
        if values == self._components:
            return
        self._components = values
        blocker = QSignalBlocker(self.componentCombo)
        self.componentCombo.clear()
        self.componentCombo.addItem("全部模块", "")
        for name in values:
            self.componentCombo.addItem(runtimeLogComponentTitle(name), name)
            self.componentCombo.setItemData(self.componentCombo.count() - 1, name, Qt.ToolTipRole)
        self.componentCombo.setCurrentIndex(self.componentCombo.findData(selected))
        del blocker

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
        exporting = feed.exportWorker is not None and not feed.exportWorker.done()
        self.exportButton.setEnabled(not exporting)
        self.cancelExportButton.setVisible(exporting)
        self._renderDiagnostics()
        self._renderComponents()
        filters = self._filters()
        self.componentCombo.setToolTip(filters.component or "全部模块")
        records = [row for row in feed.records if filters.matches(row.data)] if filters != LogFilters() else list(feed.records)
        self.statusLabel.setText(feed.statusText(fullPath=False) + f"\n当前显示 {len(records)} 条；搜索和筛选仅限已加载记录。")
        self.statusLabel.setToolTip(feed.sourceText() if feed.historyPath or feed.activePath else "")
        if records:
            empty = ""
        elif feed.error:
            empty = "读取失败，可点击“刷新”重试。"
        elif feed.records:
            empty = "当前已加载记录无匹配；可点击“恢复全部”清除筛选。"
        else:
            empty = "暂无已加载记录。"
        self.emptyLabel.setText(empty)
        self.emptyLabel.setVisible(bool(empty))

        previous = self.model.records
        source = feed.generation, filters
        incremental = self._previousSource == source
        self._previousSource = source
        if len(records) == len(previous) and all(new is old for new, old in zip(records, previous)):
            return
        self._updateRecords(records, incremental=incremental)

    def _updateRecords(self, records, *, incremental):
        previous = self.model.records
        selected = self._selectedRecord()
        top = self.table.rowAt(0)
        topOffset = 0
        anchorRow = None
        rowsByRecord = {id(record): row for row, record in enumerate(records)}
        selectedRow = rowsByRecord.get(id(selected))
        if not self.followLatest.isChecked() and 0 <= top < len(previous):
            anchorRow = rowsByRecord.get(id(previous[top]))
            if anchorRow is not None:
                topOffset = self.table.rowViewportPosition(top)
            else:
                # Use the next surviving record, or the nearest previous one.
                neighbors = (record for record in previous[top + 1:] if id(record) in rowsByRecord)
                neighbor = next(neighbors, None)
                if neighbor is None:
                    neighbor = next((record for record in reversed(previous[:top]) if id(record) in rowsByRecord), None)
                anchorRow = rowsByRecord.get(id(neighbor))
        horizontal = self.table.horizontalScrollBar()
        horizontalValue = horizontal.value()
        self._restoringSelection = True
        try:
            self.model.setRecords(records, incremental=incremental)
            if selectedRow is not None:
                if self._selectedRecord() is not selected:
                    self.table.selectRow(selectedRow)
            else:
                self.table.selectionModel().clear()
        finally:
            self._restoringSelection = False
        if self.followLatest.isChecked():
            self.table.scrollToBottom()
        elif anchorRow is not None:
            self.table.scrollTo(self.model.index(anchorRow, 0), QAbstractItemView.PositionAtTop)
            if self.table.verticalScrollMode() == QAbstractItemView.ScrollPerPixel:
                vertical = self.table.verticalScrollBar()
                vertical.setValue(vertical.value() - topOffset)
        else:
            self.table.scrollToTop()
        horizontal.setValue(horizontalValue)
        if selected is not self._selectedRecord():
            self.showDetails()

    def _selectedRecord(self):
        rows = self.table.selectionModel().selectedRows(0)
        if rows and 0 <= rows[0].row() < len(self.model.records):
            return self.model.records[rows[0].row()]
        return None

    def showDetails(self):
        if self._restoringSelection:
            return
        record = self._selectedRecord()
        self.copyDetailButton.setEnabled(record is not None)
        self.copyRawButton.setEnabled(record is not None)
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

    def exportLogs(self, *, history=False, allFiles=False):
        from emo_master.core.log_export import exportLogs
        # Apply a pending search and freeze the displayed records or file byte ranges before the dialog.
        self._applyKeyword()
        filters = self._filters()
        retainedCount = len(self.feed.records)
        try:
            if history:
                path = self.feed.historyPath or self.feed.activePath
                paths = self.feed.files if allFiles else (path,) if path else ()
                ranges = snapshotLogFiles(paths)
                if not ranges:
                    raise ValueError("没有选定可导出的日志文件")
            else:
                records = deepcopy([record.data for record in self.model.records])
            path, selected = QFileDialog.getSaveFileName(self, "导出日志", "runtime-logs.jsonl",
                "JSONL (*.jsonl);;JSON (*.json);;文本 (*.txt)")
            if not path:
                return
            formatName = "txt" if "*.txt" in selected else "json" if "*.json)" in selected else "jsonl"
            target = Path(path)
            if not target.suffix:
                target = target.with_suffix("." + formatName)
                # The native save dialog has not confirmed this appended name.
                if target.exists() and QMessageBox.question(self, "覆盖日志文件", f"文件已存在，是否覆盖？\n{target}",
                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
                    return
            if history:
                self.feed.exportFiles(target, formatName=formatName, paths=ranges, filters=filters)
            else:
                count = exportLogs(target, records, formatName=formatName, scope="filtered",
                    filters=filters.exportMetadata(), retainedCount=retainedCount)
                self.feed.exportMessage = f"已导出 {count} 条当前显示记录：{safeText(target)}"
                self.feed.updated.emit()
        except Exception as error:
            self.feed.exportMessage = "导出失败：" + safeText(error)
            self.feed.updated.emit()
