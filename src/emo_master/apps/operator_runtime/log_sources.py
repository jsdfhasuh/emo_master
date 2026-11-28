"""Operator host for the shared Job workbench and its log source tabs."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event

from PySide2.QtCore import Qt, QTimer
from PySide2.QtWidgets import QFileDialog, QMessageBox, QStackedWidget, QTabBar, QVBoxLayout, QWidget

from emo_master.clients.runtime.calls import DisplayCallContext
from emo_master.clients.runtime.global_logs import RuntimeGlobalLogs
from emo_master.clients.runtime.log_records import exportWholeJob
from emo_master.core.log_export import LogExportCancelled, exportLogs
from emo_master.core.runtime_log_records import StructuredLogEntry
from emo_master.ui.runtime_logs.runtime_job_logs import RuntimeJobLogs

from .log_client import OperatorLogClient
from .system_logs import RuntimeSystemLogs


class RuntimeLogSources(QWidget):
    def __init__(self, localFiles, directory, parent=None):
        super().__init__(parent)
        self.directory = directory
        self.client = self.reader = None
        self._delivery = None
        self._readerReady = False
        self._toolVisible = False
        self.export = None
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="operator-job-log-export")
        executor = self.executor
        self.destroyed.connect(lambda: executor.shutdown(wait=False, cancel_futures=True))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.sources = QTabBar()
        self.sources.setObjectName("operatorLogSources")
        self.sources.setDrawBase(False)
        self.sources.setExpanding(False)
        for title in ("运行任务", "Runtime 系统事件", "本机 JSONL"):
            self.sources.addTab(title)
        layout.addWidget(self.sources)
        self.pages = QStackedWidget()
        self.jobLogs = RuntimeJobLogs(10000, StructuredLogEntry.fromRuntimeEvent, allowStop=False,
                                      allowAdvancedHistory=False, filteredExport=True, showJobIdentity=True)
        self.jobLogs.detail.operatorLookup = self._operatorLookup
        self.jobLogs.catalogTitle.setProperty("operatorRole", "section")
        self.jobLogs.jobTitle.setProperty("operatorRole", "section")
        self.jobLogs.openButton.setProperty("operatorRole", "primary")
        self.jobLogs.catalogHistory.setProperty("operatorRole", "quiet")
        self.jobLogs.clearFilters.setProperty("operatorRole", "quiet")
        for label in (self.jobLogs.catalogHint, self.jobLogs.rangeStatus, self.jobLogs.jobHint,
                      self.jobLogs.deviceHistoryNote, self.jobLogs.detail.notice):
            label.setProperty("operatorRole", "hint")
        for panel, role in ((self.jobLogs.filters, "filterPanel"), (self.jobLogs.drawer, "detailPanel")):
            panel.setAttribute(Qt.WA_StyledBackground, True)
            panel.setProperty("operatorRole", role)
            panel.layout().setContentsMargins(9, 7, 9, 7)
        for tree in (self.jobLogs.directory, self.jobLogs.events, self.jobLogs.detail.readable):
            tree.setAlternatingRowColors(True)
        self.jobLogs.events.setColumnWidth(3, 112)
        self.jobLogs.detail.title.setProperty("operatorRole", "section")
        self.systemLogs = RuntimeSystemLogs()
        for page in (self.jobLogs, self.systemLogs, localFiles):
            self.pages.addWidget(page)
        layout.addWidget(self.pages, 1)
        self.sources.currentChanged.connect(self._selectSource)

        self.timer = QTimer(self)
        self.timer.setInterval(75)
        self.timer.timeout.connect(self._poll)
        self.jobLogs.exportRequested.connect(self._exportWhole)
        self.jobLogs.exportFilteredRequested.connect(self._exportFiltered)
        self.jobLogs.exportCancelRequested.connect(self.cancelExport)

    def _selectSource(self, index):
        self.pages.setCurrentIndex(index)
        self._ensureReader()

    def setRuntime(self, controller):
        service = getattr(controller, "runtime", None)
        if service is getattr(self.client, "runtimeService", None):
            return
        if self.reader is not None:
            self.reader.close()
            self.reader = None
        self.cancelExport()
        if self.client is not None:
            self.client.close()
        self.client = OperatorLogClient(service) if service is not None else None
        self._delivery = None
        instance = getattr(service, "runtimeInstanceId", "")
        self.jobLogs.synchronize(self.client, instance)
        self.systemLogs.setRuntime(self.client, instance)
        self._ensureReader()

    def setToolVisible(self, visible):
        self._toolVisible = bool(visible)
        self._ensureReader()

    def _ensureReader(self):
        if not self._toolVisible or self.sources.currentIndex() != 0 or self.client is None:
            if self.reader is not None and not self.reader.finished.is_set():
                self.reader.requestStop()
            return
        if self.reader is not None:
            if self.reader.thread.is_alive() or self.reader.unsupported:
                return
            self._drain()
        instance, cursor = self._delivery or (self.jobLogs.instance, None)
        self.reader = RuntimeGlobalLogs(self.client.runtimeService, afterEventId=cursor, runtimeInstanceId=instance)
        self._readerReady = False
        self.timer.start()

    def _drain(self):
        reader = self.reader
        previous = self._delivery
        batch = reader.take(includeProgress=True)
        instance = reader.runtimeInstanceId
        self.jobLogs.synchronize(self.client, instance)
        self.systemLogs.setRuntime(self.client, instance)
        if not self._readerReady and reader.supported:
            position = reader.initialPosition or previous
            if position is not None and position[0] == instance:
                self._readerReady = True
                self._delivery = position
                self.jobLogs.progress(*position)
        for kind, value in batch:
            if kind == "event":
                self.jobLogs.appendEntry(StructuredLogEntry.fromRuntimeEvent(value))
            elif kind == "progress":
                if value[0] == instance:
                    self._readerReady = True
                    self._delivery = value
                    self.jobLogs.progress(*value)
            elif kind == "reset":
                if self._delivery is None or self._delivery[0] != value["runtimeInstanceId"]:
                    self._readerReady, self._delivery = False, None
            else:
                if value.get("code") in {"GLOBAL_LOG_DISCONNECTED", "GLOBAL_LOG_STOPPED"}:
                    self.jobLogs.interrupted()
                message = value["message"]
                self.jobLogs.catalogHint.setText(message)
                self.jobLogs.jobHint.setText(message)

    def _operatorLookup(self, operatorId, version):
        return self.client.operatorLookup(operatorId, version) if self.client else {}

    def _exportFiltered(self, scope, filters, records):
        if not records:
            self.jobLogs.exportFeedback("当前筛选没有可导出的任务记录。", False)
            return

        def write(path, formatName, _context, cancelled):
            return exportLogs(path, records, formatName=formatName, scope="filtered_job",
                              filters={**filters, **scope}, cancelled=cancelled)

        self._startExport("当前筛选", "任务当前筛选", write)

    def _exportWhole(self, scope):
        client = self.client
        if client is None:
            return

        def write(path, formatName, context, cancelled):
            return exportWholeJob(path, formatName, client, scope, context, cancelled=cancelled)

        self._startExport("完整 Job（仍可查询）", "完整任务记录", write, wholeJob=True)

    def _startExport(self, scopeText, filename, write, *, wholeJob=False):
        if self.export is not None:
            return
        formats = {"JSON Lines (*.jsonl)": "jsonl", "JSON (*.json)": "json", "文本 (*.txt)": "txt"}
        if wholeJob:
            formats["Markdown (*.md)"] = "md"
        selected, selectedFormat = QFileDialog.getSaveFileName(self, "导出" + scopeText,
            str(self.directory / (filename + ".jsonl")), ";;".join(formats))
        if not selected:
            return
        path = Path(selected)
        formatName = path.suffix.lower().lstrip(".")
        if formatName not in formats.values():
            formatName = formats.get(selectedFormat, "txt")
            path = Path(str(path) + "." + formatName)
            if path.exists() and QMessageBox.question(self, "覆盖日志文件", f"文件已存在，是否覆盖？\n{path}",
                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
                return
        context, cancellation = DisplayCallContext(), Event()
        future = self.executor.submit(write, path, formatName, context, cancellation.is_set)
        self.export = future, context, cancellation, path, scopeText
        self.jobLogs.exportFeedback("正在导出 · " + scopeText, True)
        self.timer.start()

    def cancelExport(self):
        if self.export is not None and not self.export[0].done():
            _, context, cancellation, _, _ = self.export
            cancellation.set()
            context.cancel()

    def _poll(self):
        reader = self.reader
        readerFinished = reader is None or not reader.thread.is_alive()
        if reader is not None:
            self._drain()
            if readerFinished and reader.stop.is_set() and not reader.unsupported:
                self.reader = None
                self._ensureReader()
        if self.export is not None and self.export[0].done():
            future, _, cancellation, path, scopeText = self.export
            self.export = None
            try:
                count = future.result()
            except LogExportCancelled:
                message = "日志导出已取消 · " + scopeText
            except Exception as error:
                message = ("日志导出已取消 · " + scopeText if cancellation.is_set()
                           else "日志导出失败 · " + str(error))
            else:
                message = f"已导出 {count} 条 · {scopeText} · {path}"
            self.jobLogs.exportFeedback(message, False)
        if readerFinished and self.reader in (None, reader) and self.export is None:
            self.timer.stop()

    def beginClose(self):
        self._toolVisible = False
        self.setRuntime(None)

    @property
    def idle(self):
        return (self.reader is None and (self.export is None or self.export[0].done())
                and self.jobLogs.idle and self.systemLogs.idle)

    def finishClose(self):
        self.jobLogs.shutdown()
        self.systemLogs.shutdown()
        self.executor.shutdown(wait=False, cancel_futures=True)
