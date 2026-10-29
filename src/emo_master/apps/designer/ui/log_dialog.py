from __future__ import annotations

from collections.abc import Callable
from collections import Counter
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime
import json
from pathlib import Path
import re
import time
from typing import Any


_LINE_PATTERN = re.compile(r"^\[(?P<time>[^]]+)]\[(?P<level>[^]]+)]\s?(?P<message>.*)$")
DEFAULT_MAX_LOG_ENTRIES = 10000
_BATCH_INTERVAL_MS = 75


@dataclass(frozen=True)
class StructuredLogEntry:
    timestampMs: int
    level: str
    message: str
    source: str = "designer"
    eventType: str = ""
    jobId: str = ""
    workflowId: str = ""
    workflowRunId: str = ""
    nodeId: str = ""
    nodeRunId: str = ""
    iterationPath: tuple[int, ...] = ()
    code: str = ""
    payload: dict[str, object] = field(default_factory=dict)
    projectId: str = ""
    # Local provenance, assigned only to events from an accepted Job. Never
    # infer this from a node ID or from whichever project happens to be open.
    projectInstanceToken: str = ""
    workflowName: str = ""
    nodeName: str = ""

    @classmethod
    def fromLine(cls, line: str) -> "StructuredLogEntry":
        match = _LINE_PATTERN.match(str(line))
        if match is None:
            return cls(_nowMs(), "INFO", str(line))
        now = datetime.now()
        try:
            parsed = datetime.strptime(match.group("time"), "%H:%M:%S").replace(
                year=now.year, month=now.month, day=now.day
            )
            timestampMs = int(parsed.timestamp() * 1000.0)
        except ValueError:
            timestampMs = _nowMs()
        return cls(timestampMs, _level(match.group("level")), match.group("message"))

    @classmethod
    def fromRuntimeEvent(cls, event: dict[str, object]) -> "StructuredLogEntry":
        rawIteration = event.get("iterationPath", ())
        iteration = (
            tuple(
                item
                for item in rawIteration
                if isinstance(item, int) and not isinstance(item, bool)
            )
            if isinstance(rawIteration, (list, tuple))
            else ()
        )
        rawPayload = event.get("payload", {})
        timestampMs = _integer(event.get("timestampMs"), 0)
        return cls(
            timestampMs=timestampMs if timestampMs > 0 else _nowMs(),
            level=_level(event.get("level", "INFO")),
            message=str(event.get("message", "")),
            source="runtime",
            eventType=str(event.get("eventType", "")),
            jobId=str(event.get("jobId", "")),
            workflowId=str(event.get("workflowId", "")),
            workflowRunId=str(event.get("workflowRunId", "")),
            nodeId=str(event.get("nodeId", "")),
            nodeRunId=str(event.get("nodeRunId", "")),
            iterationPath=iteration,
            code=str(event.get("code", "")),
            payload=dict(rawPayload) if isinstance(rawPayload, dict) else {},
            projectId=str(event.get("projectId", "")),
            workflowName=str(event.get("workflowName", "")),
            nodeName=str(event.get("nodeName", "")),
        )

    def displayTime(self) -> str:
        return datetime.fromtimestamp(self.timestampMs / 1000.0).strftime("%H:%M:%S.%f")[:-3]

    def displayLine(self) -> str:
        node = f"[{self.nodeId}]" if self.nodeId else ""
        event = f"[{self.eventType}]" if self.eventType else ""
        return f"[{self.displayTime()}][{self.level}]{node}{event} {self.message}"

    def workflowKey(self) -> tuple[str, str]:
        return self.projectId, self.workflowId

    def nodeKey(self) -> tuple[str, str, str]:
        return self.projectId, self.workflowId, self.nodeId

    def displayWorkflow(self) -> str:
        return self.workflowName or self.workflowId

    def displayNode(self) -> str:
        return self.nodeName or self.nodeId

    def designArea(self) -> str:
        if self.source == "page_designer" or self.eventType.startswith("presentation."):
            return "page"
        if self.source in {"editor", "flow_designer"} or self.eventType.startswith(
                ("node.", "workflow.", "loop.", "job.", "artifact.", "preview.")):
            return "flow"
        return "general"

    def displayMessage(self) -> str:
        lifecycle = {
            "node.started": (f"node started: {self.nodeId}", "开始执行节点"),
            "node.completed": (f"node completed: {self.nodeId}", "节点执行完成"),
            "node.skipped": (f"node skipped: {self.nodeId}", "跳过节点"),
            "workflow.started": (f"workflow started: {self.workflowId}", "工作流开始执行"),
            "workflow.completed": (f"workflow completed: {self.workflowId}", "工作流执行完成"),
        }
        standard = lifecycle.get(self.eventType)
        return standard[1] if standard and self.message == standard[0] else self.message

    def detailPayload(self) -> dict[str, object]:
        return {
            "timestampMs": self.timestampMs,
            "level": self.level,
            "source": self.source,
            "eventType": self.eventType,
            "jobId": self.jobId,
            "projectId": self.projectId,
            "workflowId": self.workflowId,
            "workflowName": self.workflowName,
            "workflowRunId": self.workflowRunId,
            "nodeId": self.nodeId,
            "nodeName": self.nodeName,
            "nodeRunId": self.nodeRunId,
            "iterationPath": list(self.iterationPath),
            "code": self.code,
            "message": self.message,
            "payload": self.payload,
        }


try:
    from PySide2.QtCore import QTimer, Qt, Signal
    from PySide2.QtWidgets import (
        QAbstractItemView,
        QCheckBox,
        QComboBox,
        QDialog,
        QDockWidget,
        QFileDialog,
        QHBoxLayout,
        QGridLayout,
        QLabel,
        QLineEdit,
        QMenu,
        QMessageBox,
        QPushButton,
        QSplitter,
        QTabBar,
        QTableWidget,
        QTableWidgetItem,
        QVBoxLayout,
        QWidget,
        QSizePolicy,
    )
    from emo_master.apps.designer.ui.icon_map import icon
    from emo_master.apps.designer.ui.log_json_tree import LogJsonTree

    class _LogFilterComboBox(QComboBox):
        popupClosed: Any = Signal()

        def hidePopup(self) -> None:
            super().hidePopup()
            self.popupClosed.emit()


    class StructuredLogView(QWidget):
        diagnosticCreated: Any = Signal(object)
        columns = ("时间", "级别", "来源", "Job", "工作流", "节点", "迭代", "事件", "错误码", "消息")

        def __init__(
            self,
            parent=None,
            *,
            onClear: Callable[[], None] | None = None,
            onNavigate: Callable[[StructuredLogEntry], str] | None = None,
            maximumEntries: int = DEFAULT_MAX_LOG_ENTRIES,
        ) -> None:
            super().__init__(parent)
            self.maximumEntries = max(1, int(maximumEntries))
            self._onClear = onClear
            self._onNavigate = onNavigate
            self._entries: list[StructuredLogEntry] = []
            self._visibleEntries: list[StructuredLogEntry] = []
            self._pendingEntries: list[StructuredLogEntry] = []
            self._pendingEvicted: list[StructuredLogEntry] = []
            self._detailEntry: StructuredLogEntry | None = None
            self._viewDirty = False
            self._filterCounts: dict[str, Counter[Any]] = {
                name: Counter() for name in ("jobId", "eventType", "workflow", "node")}
            self._workflowNames: dict[tuple[str, ...], str] = {}
            self._nodeNames: dict[tuple[str, ...], str] = {}
            self._errorCount = 0
            self._visibleErrorCount = 0
            self._exportDirectory = Path.home()
            root = QVBoxLayout()
            filters = QGridLayout()
            self.levelFilter = _combo(("全部", "DEBUG", "INFO", "WARN", "ERROR"))
            self.sourceFilter = _combo(("全部", "runtime", "designer", "editor"))
            self.sourceFilter.addItem("流程设计", "flow_designer")
            self.sourceFilter.addItem("页面设计", "page_designer")
            self.jobFilter = _combo(("全部",))
            self.workflowFilter = _combo(("全部",))
            self.nodeFilter = _combo(("全部",))
            self.eventTypeFilter = _combo(("全部",))
            self.searchInput = QLineEdit()
            self.searchInput.setPlaceholderText("搜索日志")
            self.autoScrollCheck = QCheckBox("自动滚动")
            self.autoScrollCheck.setChecked(True)
            for index, (label, widget) in enumerate((
                ("等级", self.levelFilter),
                ("来源", self.sourceFilter),
                ("Job", self.jobFilter),
                ("工作流", self.workflowFilter),
                ("节点", self.nodeFilter),
                ("事件", self.eventTypeFilter),
            )):
                widget.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
                widget.setMinimumContentsLength(6)
                widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
                row, column = divmod(index, 3)
                filters.addWidget(QLabel(label), row, column * 2)
                filters.addWidget(widget, row, column * 2 + 1)
            self.searchInput.setMinimumWidth(80)
            root.addLayout(filters)
            searchRow = QHBoxLayout()
            searchRow.addWidget(self.searchInput, 1)
            self.resetFiltersButton = QPushButton("清除筛选")
            self.resetFiltersButton.setToolTip("恢复显示全部日志，不删除日志内容")
            searchRow.addWidget(self.resetFiltersButton)
            root.addLayout(searchRow)
            self.areaTabs = QTabBar()
            self.areaTabs.setObjectName("runtimeLogAreaTabs")
            self.areaTabs.setAccessibleName("日志分类")
            self.areaTabs.setExpanding(True)
            self.areaTabs.setDrawBase(False)
            for label, area, hint in (
                ("全部", "all", "显示所有日志，包括工程、连接和设备等通用记录"),
                ("流程设计", "flow", "工作流、节点执行和算子编辑日志"),
                ("页面设计", "page", "页面设计操作、结果展示异常与恢复日志"),
            ):
                index = self.areaTabs.addTab(label)
                self.areaTabs.setTabData(index, area)
                self.areaTabs.setTabToolTip(index, hint)
            root.addWidget(self.areaTabs)
            filterStatus = QHBoxLayout()
            self.filterStatusLabel = QLabel()
            self.filterStatusLabel.setObjectName("runtimeLogFilterStatus")
            self.filterStatusLabel.setWordWrap(True)
            self.showErrorsButton = QPushButton("清除筛选看错误")
            self.showErrorsButton.setObjectName("runtimeLogShowErrors")
            self.showErrorsButton.setToolTip("清除所有筛选并选中最新错误；保留日志内容")
            filterStatus.addWidget(self.filterStatusLabel, 1)
            filterStatus.addWidget(self.showErrorsButton)
            root.addLayout(filterStatus)

            self.table = QTableWidget(0, len(self.columns))
            self.table.setObjectName("runtimeLogTable")
            self.table.setHorizontalHeaderLabels(list(self.columns))
            self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
            self.table.setSelectionMode(QAbstractItemView.SingleSelection)
            self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
            self.table.setVerticalScrollMode(QAbstractItemView.ScrollPerItem)
            self.table.verticalHeader().setVisible(False)
            header = self.table.horizontalHeader()
            header.setStretchLastSection(False)
            header.setSectionsMovable(True)
            for visualIndex, column in enumerate((0, 1, 4, 5, 9, 2, 6, 7, 3, 8)):
                header.moveSection(header.visualIndex(column), visualIndex)
            for column, width in enumerate((128, 64, 88, 120, 160, 180, 64, 100, 96, 360)):
                self.table.setColumnWidth(column, width)
            self.table.setAlternatingRowColors(True)
            self.table.verticalHeader().setDefaultSectionSize(self.fontMetrics().height() + 14)
            self.detailViewer = LogJsonTree()
            self.detailViewer.setObjectName("runtimeLogDetails")
            self._logViewer = self.detailViewer
            details = QWidget()
            detailLayout = QVBoxLayout(details)
            detailLayout.setContentsMargins(0, 0, 0, 0)
            detailActions = QHBoxLayout()
            detailActions.addWidget(QLabel("日志详情 · JSON"))
            detailActions.addStretch(1)
            self.copyDetailsButton = QPushButton("复制完整 JSON")
            self.copyDetailsButton.setAutoDefault(False)
            self.copyDetailsButton.setEnabled(False)
            self.copyDetailsButton.clicked.connect(self.detailViewer.copyJson)
            detailActions.addWidget(self.copyDetailsButton)
            detailLayout.addLayout(detailActions)
            detailLayout.addWidget(self.detailViewer)
            splitter = QSplitter(Qt.Vertical)
            splitter.addWidget(self.table)
            splitter.addWidget(details)
            splitter.setSizes([300, 200])
            root.addWidget(splitter)
            self.navigationHint = QLabel("选择一条日志，可查看详情或定位节点")
            self.navigationHint.setObjectName("runtimeLogNavigationHint")
            self.navigationHint.setWordWrap(True)
            root.addWidget(self.navigationHint)
            actions = QHBoxLayout()
            self.locateButton = QPushButton("定位节点")
            self.locateButton.setObjectName("runtimeLogLocateNode")
            self.locateButton.setToolTip("定位到日志所属工作流中的节点（也可双击日志）")
            self.locateButton.setEnabled(False)
            self.clearButton = QPushButton("清空视图")
            self.clearButton.setIcon(icon("trash-2"))
            actions.addWidget(self.autoScrollCheck)
            self.logDirectoryButton = QPushButton("本机日志目录")
            self.logDirectoryButton.setAutoDefault(False)
            self.logDirectoryButton.setEnabled(False)
            actions.addWidget(self.logDirectoryButton)
            actions.addStretch(1)
            self.exportButton = QPushButton("导出日志")
            self.exportButton.setObjectName("runtimeLogExport")
            self.exportButton.setAutoDefault(False)
            self.exportButton.setToolTip("导出窗口当前保留的日志及完整详情；不含已清空或超过缓存上限的历史记录")
            self.exportMenu = QMenu(self.exportButton)
            self.exportFilteredAction = self.exportMenu.addAction("导出当前筛选结果…")
            self.exportAllAction = self.exportMenu.addAction("导出全部缓存日志…")
            self.exportFilteredAction.triggered.connect(lambda: self.exportLogs(filtered=True))
            self.exportAllAction.triggered.connect(lambda: self.exportLogs(filtered=False))
            self.exportMenu.aboutToShow.connect(self._updateExportActions)
            self.exportButton.setMenu(self.exportMenu)
            actions.addWidget(self.exportButton)
            actions.addWidget(self.locateButton)
            actions.addWidget(self.clearButton)
            root.addLayout(actions)
            self.setLayout(root)

            for combo in (
                self.levelFilter,
                self.sourceFilter,
                self.jobFilter,
                self.eventTypeFilter,
            ):
                combo.currentTextChanged.connect(self._refreshTable)
            self.workflowFilter.currentIndexChanged.connect(self._onWorkflowFilterChanged)
            self.nodeFilter.currentIndexChanged.connect(self._refreshTable)
            self.searchInput.textChanged.connect(self._refreshTable)
            self.areaTabs.currentChanged.connect(self._refreshTable)
            self.autoScrollCheck.toggled.connect(self._onAutoScrollChanged)
            self.table.verticalScrollBar().valueChanged.connect(self._onScrollChanged)
            self.table.verticalScrollBar().rangeChanged.connect(self._onScrollRangeChanged)
            self.table.currentCellChanged.connect(self._showDetails)
            self.table.cellDoubleClicked.connect(self._navigateFromRow)
            self.locateButton.clicked.connect(self._navigateSelected)
            self.showErrorsButton.clicked.connect(self._showAllErrors)
            self.resetFiltersButton.clicked.connect(self._resetFilters)
            self.clearButton.clicked.connect(self._clearLogs)
            self._refreshTimer = QTimer(self)
            self._refreshTimer.setSingleShot(True)
            self._refreshTimer.setInterval(_BATCH_INTERVAL_MS)
            self._refreshTimer.timeout.connect(self._flushVisibleEntries)
            for combo in (self.jobFilter, self.workflowFilter, self.nodeFilter, self.eventTypeFilter):
                combo.popupClosed.connect(self._refreshTimer.start)
            self._updateFilterStatus()

        def _updateExportActions(self):
            filteredCount = sum(self._matches(entry) for entry in self._entries)
            self.exportFilteredAction.setText(f"导出当前筛选结果（{filteredCount} 条）…")
            self.exportAllAction.setText(f"导出全部缓存日志（{len(self._entries)} 条）…")
            self.exportFilteredAction.setEnabled(filteredCount > 0)
            self.exportAllAction.setEnabled(bool(self._entries))

        def exportLogs(self, filtered=True):
            from emo_master.apps.designer.services.log_export import exportLogs as writeLogExport

            # The table may still have a pending batch. Capture the underlying
            # cache before the native save dialog starts its nested event loop.
            retainedCount = len(self._entries)
            records = deepcopy([entry.detailPayload() for entry in self._entries
                                if not filtered or self._matches(entry)])
            if not records:
                self.navigationHint.setText("当前筛选结果没有可导出的日志" if filtered else "当前没有可导出的缓存日志")
                return
            filters = {key: value for key, value in self.settings().items()
                       if key not in {"autoScroll", "columnWidths"}} if filtered else {}
            scope = "filtered" if filtered else "all_cached"
            label = "当前筛选结果" if filtered else "全部缓存日志"
            formats = {"JSON 日志 (*.json)": "json", "JSON Lines 日志 (*.jsonl)": "jsonl",
                       "文本日志 (*.txt)": "txt"}
            initial = self._exportDirectory / f"日志-{label}-{datetime.now():%Y%m%d-%H%M%S}.json"
            selected, selectedFilter = QFileDialog.getSaveFileName(
                self, "导出日志 · " + label, str(initial), ";;".join(formats))
            if not selected:
                return
            path = Path(selected)
            formatName = formats.get(selectedFilter, "json")
            # Respect an explicit supported extension. Only the suffix we add
            # needs another overwrite check; the save dialog owns other checks.
            if path.suffix.lower().lstrip(".") in formats.values():
                formatName = path.suffix.lower().lstrip(".")
            else:
                path = Path(str(path) + "." + formatName)
                if path.exists() and QMessageBox.question(self, "覆盖日志文件", f"文件已存在，是否覆盖？\n{path}",
                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
                    return
            context = dict(path=str(path), format=formatName, scope=scope, count=len(records),
                           retainedCount=retainedCount, filters=filters)
            try:
                writeLogExport(path, records, formatName=formatName, scope=scope,
                               filters=filters, retainedCount=retainedCount)
            except (OSError, ValueError, TypeError) as error:
                message = f"日志导出失败：{error}\n保存位置：{path}"
                self.navigationHint.setText(message)
                self.diagnosticCreated.emit(dict(level="ERROR", code="DESIGNER_LOG_EXPORT_FAILED",
                    eventType="designer.log_export.failed", message=message,
                    payload=dict(context, reason=str(error), errorType=type(error).__name__)))
                QMessageBox.warning(self, "日志导出失败", message)
                return
            self._exportDirectory = path.parent
            message = f"已导出 {len(records)} 条日志：{path}"
            self.navigationHint.setText(message)
            self.diagnosticCreated.emit(dict(level="INFO", code="DESIGNER_LOG_EXPORTED",
                eventType="designer.log_export.completed", message=message, payload=context))

        def setLogDirectory(self, directory, onError=None):
            self._logDirectory = directory
            self._directoryError = onError
            self.logDirectoryButton.setEnabled(True)
            self.logDirectoryButton.setToolTip(
                f"Designer 操作和客户端异常日志：{directory}\n任务执行日志保存在 Runtime 的日志目录。")
            self.logDirectoryButton.clicked.connect(self._openLogDirectory)

        def _openLogDirectory(self):
            from PySide2.QtCore import QUrl
            from PySide2.QtGui import QDesktopServices
            try:
                self._logDirectory.mkdir(parents=True, exist_ok=True)
                if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._logDirectory))):
                    raise OSError("系统无法打开目录")
            except OSError as error:
                message = f"无法打开日志目录 {self._logDirectory}：{error}"
                self.navigationHint.setText(message)
                if self._directoryError is not None:
                    self._directoryError(message)

        def appendEntry(self, entry: StructuredLogEntry) -> None:
            self._entries.append(entry)
            self._countEntry(entry, 1)
            overflow = len(self._entries) - self.maximumEntries
            evicted = self._entries[:max(0, overflow)]
            if overflow > 0:
                for previous in evicted:
                    self._countEntry(previous, -1)
                del self._entries[:overflow]
            if not self.isVisible() or self._viewDirty:
                self._viewDirty = True
                return
            self._pendingEntries.append(entry)
            self._pendingEvicted.extend(evicted)
            if len(self._pendingEntries) > self.maximumEntries:
                self._pendingEntries.clear()
                self._pendingEvicted.clear()
                self._viewDirty = True
            if not self._refreshTimer.isActive():
                self._refreshTimer.start()

        def _countEntry(self, entry: StructuredLogEntry, delta: int) -> None:
            self._errorCount += delta * _isError(entry)
            for name, key in (("jobId", entry.jobId), ("eventType", entry.eventType),
                              ("workflow", entry.workflowKey() if entry.workflowId else None),
                              ("node", entry.nodeKey() if entry.nodeId else None)):
                if not key:
                    continue
                counts = self._filterCounts[name]
                counts[key] += delta
                if counts[key] == 0:
                    del counts[key]
                names = self._workflowNames if name == "workflow" else self._nodeNames if name == "node" else None
                if names is not None and isinstance(key, tuple):
                    if delta > 0:
                        names[key] = entry.displayWorkflow() if name == "workflow" else entry.displayNode()
                    elif key not in counts:
                        names.pop(key, None)

        def hideEvent(self, event) -> None:
            self._refreshTimer.stop()
            self._viewDirty = self._viewDirty or bool(self._pendingEntries or self._pendingEvicted)
            self._pendingEntries.clear()
            self._pendingEvicted.clear()
            super().hideEvent(event)

        def showEvent(self, event) -> None:
            super().showEvent(event)
            self._applyPendingEntries()

        def setEntries(self, entries: list[StructuredLogEntry]) -> None:
            self._refreshTimer.stop()
            self._entries = list(entries[-self.maximumEntries :])
            self._pendingEntries.clear()
            self._pendingEvicted.clear()
            for counts in self._filterCounts.values():
                counts.clear()
            self._workflowNames.clear()
            self._nodeNames.clear()
            self._errorCount = 0
            for entry in self._entries:
                self._countEntry(entry, 1)
            self._refreshDynamicFilters()
            self._refreshTable()

        def appendLog(self, line: str) -> None:
            self.appendEntry(StructuredLogEntry.fromLine(line))

        def setLogs(self, lines: list[str]) -> None:
            self.setEntries([StructuredLogEntry.fromLine(line) for line in lines])

        def settings(self) -> dict[str, object]:
            return {
                "area": self.areaTabs.tabData(self.areaTabs.currentIndex()),
                "level": self.levelFilter.currentText(),
                "source": self.sourceFilter.currentData() or self.sourceFilter.currentText(),
                "job": self.jobFilter.currentText(),
                "workflow": _contextSetting(self.workflowFilter),
                "workflowLabel": self.workflowFilter.currentText(),
                "node": _contextSetting(self.nodeFilter),
                "nodeLabel": self.nodeFilter.currentText(),
                "event": self.eventTypeFilter.currentText(),
                "search": self.searchInput.text(),
                "autoScroll": self.autoScrollCheck.isChecked(),
                "columnWidths": [self.table.columnWidth(index) for index in range(len(self.columns))],
            }

        def restoreSettings(self, value: dict[str, object]) -> None:
            area = value.get("area", "all")
            self.areaTabs.setCurrentIndex(next((index for index in range(self.areaTabs.count())
                                               if self.areaTabs.tabData(index) == area), 0))
            self._refreshDynamicFilters()
            for combo, key, allowUnknown in (
                (self.levelFilter, "level", False),
                (self.sourceFilter, "source", False),
                (self.jobFilter, "job", True),
                (self.eventTypeFilter, "event", True),
            ):
                requested = str(value.get(key, "全部"))
                index = combo.findData(requested) if combo is self.sourceFilter else -1
                if index < 0:
                    index = combo.findText(requested)
                if index < 0 and allowUnknown and requested != "全部":
                    combo.addItem(requested)
                    index = combo.findText(requested)
                combo.setCurrentIndex(index if index >= 0 else 0)
            _restoreContextChoice(self.workflowFilter, value.get("workflow"),
                                  value.get("workflowLabel"), 2)
            self._refreshContextFilters()
            _restoreContextChoice(self.nodeFilter, value.get("node"),
                                  value.get("nodeLabel"), 3, allowLegacy=True)
            self.searchInput.setText(str(value.get("search", "")))
            self.autoScrollCheck.setChecked(bool(value.get("autoScroll", True)))
            widths = value.get("columnWidths", [])
            if isinstance(widths, list):
                for index, width in enumerate(widths[: len(self.columns)]):
                    if isinstance(width, int) and width > 0:
                        self.table.setColumnWidth(index, width)
            self._refreshTable()

        def _clearLogs(self) -> None:
            self.setEntries([])
            self._refreshDynamicFilters(preserveUnknown=False)
            self._updateFilterStatus()
            self._showDetails(-1, 0)
            if self._onClear is not None:
                self._onClear()

        def clearView(self) -> None:
            self._clearLogs()

        def _refreshDynamicFilters(self, *, preserveUnknown: bool = True) -> None:
            _replaceComboValues(
                self.jobFilter,
                sorted(self._filterCounts["jobId"]),
                preserveUnknown=preserveUnknown,
            )
            self._refreshContextFilters(preserveUnknown=preserveUnknown)
            _replaceComboValues(
                self.eventTypeFilter,
                sorted(self._filterCounts["eventType"]),
                preserveUnknown=preserveUnknown,
            )

        def _refreshContextFilters(self, *, preserveUnknown: bool = True) -> None:
            workflows = self._workflowNames
            counts = Counter(workflows.values())
            workflowLabels = {
                key: (f"{label} · {key[1]}" if counts[label] > 1 else label)
                for key, label in workflows.items()
            }
            duplicates = Counter(workflowLabels.values())
            workflowLabels = {
                key: (f"{label} · {key[0]}" if duplicates[label] > 1 else label)
                for key, label in workflowLabels.items()
            }
            _replaceContextChoices(self.workflowFilter, workflowLabels, preserveUnknown=preserveUnknown)
            workflowKey = _contextKey(self.workflowFilter)
            nodes = {
                key: f"{workflowLabels.get(key[:2], '未关联工作流')} / {name}"
                for key, name in self._nodeNames.items()
                if workflowKey is None or key[:2] == workflowKey
            }
            counts = Counter(nodes.values())
            nodeLabels = {
                key: (f"{label} · {key[2]}" if counts[label] > 1 else label)
                for key, label in nodes.items()
            }
            _replaceContextChoices(self.nodeFilter, nodeLabels, preserveUnknown=preserveUnknown)

        def _onWorkflowFilterChanged(self, *_args) -> None:
            workflowKey = _contextKey(self.workflowFilter)
            nodeKey = _contextKey(self.nodeFilter)
            if workflowKey is not None and nodeKey is not None and (
                    not isinstance(nodeKey, tuple) or nodeKey[:2] != workflowKey):
                self.nodeFilter.setCurrentIndex(0)
            self._refreshContextFilters()
            self._refreshTable()

        def _flushVisibleEntries(self) -> None:
            if self.isVisible():
                self._applyPendingEntries()

        def _applyPendingEntries(self) -> None:
            self._refreshTimer.stop()
            if self._viewDirty:
                self._refreshDynamicFilters()
                self._refreshTable()
                return
            pending = list(self._pendingEntries)
            self._pendingEntries.clear()
            evicted = list(self._pendingEvicted)
            self._pendingEvicted.clear()
            self._refreshDynamicFilters()
            if not pending and not evicted:
                return
            pending = [entry for entry in pending[-self.maximumEntries:] if self._matches(entry)]
            visibleEvictions = 0
            for entry in evicted:
                if (
                    visibleEvictions < len(self._visibleEntries)
                    and self._visibleEntries[visibleEvictions] is entry
                ):
                    visibleEvictions += 1
            if not pending and not visibleEvictions:
                self._updateFilterStatus()
                return
            scrollBar = self.table.verticalScrollBar()
            scrollPosition = scrollBar.value()
            scrollBlocked = scrollBar.blockSignals(True)
            tableBlocked = self.table.blockSignals(True)
            self.table.setUpdatesEnabled(False)
            try:
                if visibleEvictions:
                    self._visibleErrorCount -= sum(_isError(entry) for entry in self._visibleEntries[:visibleEvictions])
                    del self._visibleEntries[:visibleEvictions]
                    for _ in range(visibleEvictions):
                        self.table.removeRow(0)
                for entry in pending:
                    row = len(self._visibleEntries)
                    self._visibleEntries.append(entry)
                    self._visibleErrorCount += _isError(entry)
                    self.table.insertRow(row)
                    self._setTableRow(row, entry)
                if self.autoScrollCheck.isChecked() and self._visibleEntries:
                    self.table.scrollToBottom()
                else:
                    # The table scrolls by row; retain the same historical entry
                    # when the bounded buffer removes rows above the viewport.
                    scrollBar.setValue(max(0, scrollPosition - visibleEvictions))
            finally:
                self.table.setUpdatesEnabled(True)
                self.table.blockSignals(tableBlocked)
                scrollBar.blockSignals(scrollBlocked)
            self._updateFilterStatus()
            self._showDetails(self.table.currentRow(), 0)

        def _refreshTable(self, *_args) -> None:
            # A popup close may have queued a filter refresh. Keep it armed
            # even when this selection consumes the pending rows immediately.
            self._pendingEntries.clear()
            self._pendingEvicted.clear()
            selected = self._detailEntry
            scrollBar = self.table.verticalScrollBar()
            position = scrollBar.value()
            anchor = self._visibleEntries[position] if position < len(self._visibleEntries) else None
            self._visibleEntries = [entry for entry in self._entries if self._matches(entry)]
            self._visibleErrorCount = sum(_isError(entry) for entry in self._visibleEntries)
            self._viewDirty = False
            scrollBlocked = scrollBar.blockSignals(True)
            tableBlocked = self.table.blockSignals(True)
            self.table.setUpdatesEnabled(False)
            try:
                self.table.setRowCount(len(self._visibleEntries))
                for row, entry in enumerate(self._visibleEntries):
                    self._setTableRow(row, entry)
                selectedRow = next((row for row, entry in enumerate(self._visibleEntries) if entry is selected), -1)
                self.table.setCurrentCell(selectedRow, 0)
                if self.autoScrollCheck.isChecked() and self._visibleEntries:
                    self.table.scrollToBottom()
                else:
                    scrollBar.setValue(next((row for row, entry in enumerate(self._visibleEntries)
                                            if entry is anchor), position))
            finally:
                self.table.setUpdatesEnabled(True)
                self.table.blockSignals(tableBlocked)
                scrollBar.blockSignals(scrollBlocked)
            self._updateFilterStatus()
            self._showDetails(self.table.currentRow(), 0)

        def _updateFilterStatus(self) -> None:
            hiddenErrors = self._errorCount - self._visibleErrorCount
            filtered = any(combo.currentIndex() > 0 for combo in (
                self.levelFilter, self.sourceFilter, self.jobFilter, self.workflowFilter,
                self.nodeFilter, self.eventTypeFilter,
            )) or bool(self.searchInput.text().strip()) or self.areaTabs.currentIndex() > 0
            suffix = (
                f"；当前筛选隐藏了 {hiddenErrors} 条错误"
                if filtered else "；未设置筛选"
            )
            self.filterStatusLabel.setText(
                f"显示 {len(self._visibleEntries)} / {len(self._entries)} 条日志{suffix}"
            )
            self.showErrorsButton.setEnabled(hiddenErrors > 0)
            self.resetFiltersButton.setEnabled(filtered)

        def _resetFilters(self) -> None:
            controls = (
                self.areaTabs, self.levelFilter, self.sourceFilter, self.jobFilter, self.workflowFilter,
                self.nodeFilter, self.eventTypeFilter, self.searchInput,
            )
            for control in controls:
                control.blockSignals(True)
            try:
                for combo in controls[:-1]:
                    combo.setCurrentIndex(0)
                self.searchInput.clear()
            finally:
                for control in controls:
                    control.blockSignals(False)
            self._refreshContextFilters()
            self._refreshTable()

        def _showAllErrors(self) -> None:
            self._resetFilters()
            for row in range(len(self._visibleEntries) - 1, -1, -1):
                if _isError(self._visibleEntries[row]):
                    self.table.setCurrentCell(row, 0)
                    self.table.scrollToItem(self.table.item(row, 0))
                    break

        def _setTableRow(self, row: int, entry: StructuredLogEntry) -> None:
            values = (
                entry.displayTime(),
                entry.level,
                {"page_designer": "页面设计", "flow_designer": "流程设计"}.get(entry.source, entry.source),
                entry.jobId,
                entry.displayWorkflow(),
                entry.displayNode(),
                "/".join(str(item) for item in entry.iterationPath),
                entry.eventType,
                entry.code,
                entry.displayMessage(),
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column in (4, 5):
                    item.setToolTip(
                        f"工作流：{entry.displayWorkflow()}\n工作流 ID：{entry.workflowId}\n"
                        f"节点：{entry.displayNode()}\n节点 ID：{entry.nodeId}"
                    )
                else:
                    item.setToolTip(entry.message if column == 9 else value)
                self.table.setItem(row, column, item)

        def _matches(self, entry: StructuredLogEntry) -> bool:
            area = self.areaTabs.tabData(self.areaTabs.currentIndex())
            if area != "all" and area != entry.designArea():
                return False
            for combo, value in (
                (self.levelFilter, entry.level), (self.sourceFilter, entry.source),
                (self.jobFilter, entry.jobId),
                (self.eventTypeFilter, entry.eventType),
            ):
                selected = combo.currentData() or combo.currentText()
                if selected != "全部" and selected != value:
                    return False
            workflowKey = _contextKey(self.workflowFilter)
            if workflowKey is not None and workflowKey != entry.workflowKey():
                return False
            nodeKey = _contextKey(self.nodeFilter)
            if nodeKey is not None:
                # Old preferences held only a node ID, across all workflows.
                actual = entry.nodeId if isinstance(nodeKey, str) else entry.nodeKey()
                if nodeKey != actual:
                    return False
            query = self.searchInput.text().strip().casefold()
            if not query:
                return True
            haystack = " ".join((
                entry.message, entry.code, entry.eventType, entry.jobId,
                entry.workflowId, entry.nodeId, entry.workflowName, entry.nodeName,
                entry.displayMessage(),
                json.dumps(entry.payload, ensure_ascii=False, default=str),
            )).casefold()
            return query in haystack

        def _showDetails(self, row: int, _column: int, *_args) -> None:
            if not 0 <= row < len(self._visibleEntries):
                self._detailEntry = None
                self.detailViewer.clear()
                self.copyDetailsButton.setEnabled(False)
                self.locateButton.setEnabled(False)
                self.navigationHint.setText("选择一条日志，可查看详情或定位节点")
                return
            entry = self._visibleEntries[row]
            if entry is self._detailEntry:
                return
            self._detailEntry = entry
            self.detailViewer.setPayload(entry.detailPayload())
            self.copyDetailsButton.setEnabled(True)
            self.locateButton.setEnabled(bool(entry.nodeId) and self._onNavigate is not None)
            if not entry.nodeId:
                self.navigationHint.setText("此日志未提供节点关联，无法定位。请查看错误详情与相关节点日志。")
            elif self._onNavigate is None:
                self.navigationHint.setText("当前日志窗口未连接流程画布，无法定位节点。")
            else:
                self.navigationHint.setText("点击“定位节点”或双击日志，可在所属工作流中查看节点。")

        def _navigateFromRow(self, row: int, column: int) -> None:
            self.table.setCurrentCell(row, column)
            self._navigateSelected()

        def _navigateSelected(self) -> None:
            row = self.table.currentRow()
            self._showDetails(row, 0)
            if not 0 <= row < len(self._visibleEntries):
                return
            entry = self._visibleEntries[row]
            if entry.nodeId and self._onNavigate is not None:
                self.navigationHint.setText(self._onNavigate(entry))

        def _onAutoScrollChanged(self, enabled: bool) -> None:
            if enabled and self._visibleEntries:
                self.table.scrollToBottom()

        def _onScrollChanged(self, value: int) -> None:
            if value < self.table.verticalScrollBar().maximum():
                self.autoScrollCheck.setChecked(False)

        def _onScrollRangeChanged(self, _minimum: int, _maximum: int) -> None:
            self._onAutoScrollChanged(self.autoScrollCheck.isChecked())


    class RuntimeLogDock(QDockWidget):
        def __init__(
            self,
            parent=None,
            *,
            onClear: Callable[[], None] | None = None,
            onNavigate: Callable[[StructuredLogEntry], str] | None = None,
            maximumEntries: int = DEFAULT_MAX_LOG_ENTRIES,
        ) -> None:
            super().__init__("运行日志", parent)
            self.setObjectName("runtimeLogDock")
            self.setAllowedAreas(Qt.BottomDockWidgetArea | Qt.TopDockWidgetArea)
            self.setFeatures(
                QDockWidget.DockWidgetClosable
                | QDockWidget.DockWidgetMovable
                | QDockWidget.DockWidgetFloatable
            )
            self.view = StructuredLogView(
                self,
                onClear=onClear,
                onNavigate=onNavigate,
                maximumEntries=maximumEntries,
            )
            self.setWidget(self.view)

        def appendEntry(self, entry: StructuredLogEntry) -> None:
            self.view.appendEntry(entry)

        def setEntries(self, entries: list[StructuredLogEntry]) -> None:
            self.view.setEntries(entries)

        def appendLog(self, line: str) -> None:
            self.view.appendLog(line)

        def setLogs(self, lines: list[str]) -> None:
            self.view.setLogs(lines)


    class LogDialog(QDialog):
        """Reusable, non-modal runtime log window owned by the Designer."""

        def __init__(
            self,
            parent=None,
            *,
            onClear: Callable[[], None] | None = None,
            onNavigate: Callable[[StructuredLogEntry], str] | None = None,
            maximumEntries: int = DEFAULT_MAX_LOG_ENTRIES,
        ) -> None:
            super().__init__(parent)
            self.setObjectName("runtimeLogDialog")
            self.setModal(False)
            self.setWindowFlag(Qt.WindowContextHelpButtonHint, False)
            self.setWindowFlag(Qt.WindowMinMaxButtonsHint, True)
            self.setWindowTitle("运行日志")
            self.resize(1200, 720)
            layout = QVBoxLayout()
            layout.setContentsMargins(0, 0, 0, 0)
            self.view = StructuredLogView(
                self,
                onClear=onClear,
                onNavigate=onNavigate,
                maximumEntries=maximumEntries,
            )
            # Searching with Enter must not trigger a QDialog default action.
            for button in (self.view.resetFiltersButton, self.view.showErrorsButton,
                           self.view.locateButton, self.view.clearButton):
                button.setAutoDefault(False)
            self._logViewer = self.view.detailViewer
            layout.addWidget(self.view)
            self.setLayout(layout)

        def appendEntry(self, entry: StructuredLogEntry) -> None:
            self.view.appendEntry(entry)

        def setEntries(self, entries: list[StructuredLogEntry]) -> None:
            self.view.setEntries(entries)

        def appendLog(self, line: str) -> None:
            self.view.appendLog(line)

        def setLogs(self, lines: list[str]) -> None:
            self.view.setLogs(lines)


    def _combo(values: tuple[str, ...]) -> _LogFilterComboBox:
        combo = _LogFilterComboBox()
        combo.addItems(list(values))
        return combo


    def _contextSetting(combo: QComboBox) -> object:
        key = _contextKey(combo)
        return list(key) if isinstance(key, tuple) else (key or "全部")


    def _contextKey(combo: QComboBox):
        value = combo.currentData()
        return tuple(value) if isinstance(value, list) else value


    def _restoreContextChoice(combo, key, label, dimensions, *, allowLegacy=False) -> None:
        if isinstance(key, (list, tuple)) and len(key) == dimensions and all(isinstance(part, str) for part in key):
            key = tuple(key)
            defaultLabel = " / ".join(part for part in key[1:] if part)
        elif allowLegacy and isinstance(key, str) and key not in {"", "全部"}:
            defaultLabel = f"全部工作流 / {key}"
        else:
            combo.setCurrentIndex(0)
            return
        data = list(key) if isinstance(key, tuple) else key
        index = combo.findData(data)
        if index < 0:
            combo.addItem(str(label or defaultLabel), data)
            index = combo.count() - 1
        combo.setCurrentIndex(index)


    def _replaceContextChoices(combo, choices, *, preserveUnknown=True) -> None:
        selected, label = _contextKey(combo), combo.currentText()
        # QVariantList compares by value; Python tuple user data does not.
        items = [("全部", None)] + [
            (text, list(key))
            for key, text in sorted(choices.items(), key=lambda item: (item[1], item[0]))
        ]
        data = list(selected) if isinstance(selected, tuple) else selected
        index = next((i for i, (_, key) in enumerate(items) if key == data), -1)
        if preserveUnknown and selected is not None and index < 0:
            items.append((label, data))
            index = len(items) - 1
        _replaceFilterItems(combo, items, max(0, index))


    def _replaceComboValues(
        combo: QComboBox,
        values: list[str],
        *,
        preserveUnknown: bool = True,
    ) -> None:
        selected = combo.currentText()
        labels = ["全部", *values]
        index = labels.index(selected) if selected in labels else -1
        if preserveUnknown and index < 0 and selected not in {"", "全部"}:
            labels.append(selected)
            index = len(labels) - 1
        _replaceFilterItems(combo, [(label, None) for label in labels], max(0, index))


    def _replaceFilterItems(combo, items, selectedIndex) -> None:
        # Never reset an open popup's hover/scroll state. Closing it schedules
        # another batch so deferred options arrive even if logging has stopped.
        if combo.view().isVisible():
            return
        if items == [(combo.itemText(i), combo.itemData(i)) for i in range(combo.count())]:
            return
        blocked = combo.blockSignals(True)
        try:
            combo.clear()
            for text, data in items:
                combo.addItem(text, data)
            combo.setCurrentIndex(selectedIndex)
        finally:
            combo.blockSignals(blocked)

except Exception:  # pragma: no cover

    class StructuredLogView:  # type: ignore[no-redef]
        def __init__(
            self,
            parent=None,
            *,
            onClear: Callable[[], None] | None = None,
            onNavigate: Callable[[StructuredLogEntry], str] | None = None,
            maximumEntries: int = DEFAULT_MAX_LOG_ENTRIES,
        ) -> None:
            _ = parent
            self.maximumEntries = max(1, int(maximumEntries))
            self._onClear = onClear
            self._onNavigate = onNavigate
            self._entries: list[StructuredLogEntry] = []
            self._lines: list[str] = []

        def appendEntry(self, entry: StructuredLogEntry) -> None:
            self._entries.append(entry)
            self._lines.append(entry.displayLine())
            overflow = len(self._entries) - self.maximumEntries
            if overflow > 0:
                del self._entries[:overflow]
                del self._lines[:overflow]

        def setEntries(self, entries: list[StructuredLogEntry]) -> None:
            self._entries.clear()
            self._entries.extend(entries[-self.maximumEntries :])
            self._lines.clear()
            self._lines.extend(entry.displayLine() for entry in self._entries)

        def appendLog(self, line: str) -> None:
            self.appendEntry(StructuredLogEntry.fromLine(line))

        def setLogs(self, lines: list[str]) -> None:
            self.setEntries(
                [StructuredLogEntry.fromLine(line) for line in lines]
            )

        def clearView(self) -> None:
            self._lines.clear()
            self._entries.clear()
            if self._onClear is not None:
                self._onClear()

        def settings(self) -> dict[str, object]:
            return {}

        def restoreSettings(self, value: dict[str, object]) -> None:
            _ = value


    class RuntimeLogDock:  # type: ignore[no-redef]
        def __init__(
            self,
            parent=None,
            *,
            onClear: Callable[[], None] | None = None,
            onNavigate: Callable[[StructuredLogEntry], str] | None = None,
            maximumEntries: int = DEFAULT_MAX_LOG_ENTRIES,
        ) -> None:
            _ = parent
            self.view = StructuredLogView(
                onClear=onClear,
                onNavigate=onNavigate,
                maximumEntries=maximumEntries,
            )
            self._visible = False
            self._floating = False

        def appendEntry(self, entry: StructuredLogEntry) -> None:
            self.view.appendEntry(entry)

        def setEntries(self, entries: list[StructuredLogEntry]) -> None:
            self.view.setEntries(entries)

        def appendLog(self, line: str) -> None:
            self.view.appendLog(line)

        def setLogs(self, lines: list[str]) -> None:
            self.view.setLogs(lines)

        def show(self) -> None:
            self._visible = True

        def hide(self) -> None:
            self._visible = False

        def close(self) -> None:
            self._visible = False

        def isVisible(self) -> bool:
            return self._visible

        def setFloating(self, value: bool) -> None:
            self._floating = bool(value)

        def isFloating(self) -> bool:
            return self._floating

        def raise_(self) -> None:
            self._visible = True

        def activateWindow(self) -> None:
            self._visible = True


    class LogDialog(RuntimeLogDock):  # type: ignore[no-redef]
        def __init__(
            self,
            parent=None,
            *,
            onClear: Callable[[], None] | None = None,
            onNavigate: Callable[[StructuredLogEntry], str] | None = None,
            maximumEntries: int = DEFAULT_MAX_LOG_ENTRIES,
        ) -> None:
            super().__init__(
                parent,
                onClear=onClear,
                onNavigate=onNavigate,
                maximumEntries=maximumEntries,
            )
            self._lines = self.view._lines


def _isError(entry: StructuredLogEntry) -> bool:
    return entry.level == "ERROR" or entry.eventType.endswith(".failed")


def _values(entries: list[StructuredLogEntry], fieldName: str) -> list[str]:
    return sorted({
        str(getattr(entry, fieldName))
        for entry in entries
        if str(getattr(entry, fieldName))
    })


def _integer(value: object, default: int) -> int:
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return default
    return default


def _level(value: object) -> str:
    normalized = str(value).strip().upper()
    if normalized == "WARNING":
        normalized = "WARN"
    return normalized if normalized in {"DEBUG", "INFO", "WARN", "ERROR"} else "INFO"


def _nowMs() -> int:
    return int(time.time() * 1000.0)
