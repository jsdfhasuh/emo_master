from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
import json
import re
import time


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
        )

    def displayTime(self) -> str:
        return datetime.fromtimestamp(self.timestampMs / 1000.0).strftime("%H:%M:%S.%f")[:-3]

    def displayLine(self) -> str:
        node = f"[{self.nodeId}]" if self.nodeId else ""
        event = f"[{self.eventType}]" if self.eventType else ""
        return f"[{self.displayTime()}][{self.level}]{node}{event} {self.message}"

    def detailPayload(self) -> dict[str, object]:
        return {
            "timestampMs": self.timestampMs,
            "level": self.level,
            "source": self.source,
            "eventType": self.eventType,
            "jobId": self.jobId,
            "workflowId": self.workflowId,
            "workflowRunId": self.workflowRunId,
            "nodeId": self.nodeId,
            "nodeRunId": self.nodeRunId,
            "iterationPath": list(self.iterationPath),
            "code": self.code,
            "message": self.message,
            "payload": self.payload,
        }


try:
    from PySide2.QtCore import QTimer, Qt
    from PySide2.QtWidgets import (
        QAbstractItemView,
        QCheckBox,
        QComboBox,
        QDialog,
        QDockWidget,
        QHBoxLayout,
        QGridLayout,
        QLabel,
        QLineEdit,
        QPushButton,
        QSplitter,
        QTableWidget,
        QTableWidgetItem,
        QTextEdit,
        QVBoxLayout,
        QWidget,
        QSizePolicy,
    )
    from emo_master.apps.designer.ui.icon_map import icon

    class StructuredLogView(QWidget):
        columns = ("时间", "级别", "来源", "Job", "工作流", "节点", "迭代", "事件", "错误码", "消息")

        def __init__(
            self,
            parent=None,
            *,
            onClear: Callable[[], None] | None = None,
            maximumEntries: int = DEFAULT_MAX_LOG_ENTRIES,
        ) -> None:
            super().__init__(parent)
            self.maximumEntries = max(1, int(maximumEntries))
            self._onClear = onClear
            self._entries: list[StructuredLogEntry] = []
            self._visibleEntries: list[StructuredLogEntry] = []
            self._pendingEntries: list[StructuredLogEntry] = []
            self._pendingEvicted: list[StructuredLogEntry] = []
            root = QVBoxLayout()
            filters = QGridLayout()
            self.levelFilter = _combo(("全部", "DEBUG", "INFO", "WARN", "ERROR"))
            self.sourceFilter = _combo(("全部", "runtime", "designer", "editor"))
            self.jobFilter = _combo(("全部",))
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
            filters.addWidget(self.searchInput, 1, 4, 1, 2)
            root.addLayout(filters)

            self.table = QTableWidget(0, len(self.columns))
            self.table.setObjectName("runtimeLogTable")
            self.table.setHorizontalHeaderLabels(list(self.columns))
            self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
            self.table.setSelectionMode(QAbstractItemView.SingleSelection)
            self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
            self.table.verticalHeader().setVisible(False)
            header = self.table.horizontalHeader()
            header.setStretchLastSection(False)
            header.setSectionsMovable(True)
            header.moveSection(header.visualIndex(9), 3)
            for column, width in enumerate((128, 64, 88, 120, 100, 100, 64, 100, 96, 360)):
                self.table.setColumnWidth(column, width)
            self.table.setAlternatingRowColors(True)
            self.table.verticalHeader().setDefaultSectionSize(self.fontMetrics().height() + 14)
            self.detailViewer = QTextEdit()
            self.detailViewer.setObjectName("runtimeLogDetails")
            self.detailViewer.setReadOnly(True)
            self._logViewer = self.detailViewer
            splitter = QSplitter(Qt.Vertical)
            splitter.addWidget(self.table)
            splitter.addWidget(self.detailViewer)
            splitter.setSizes([300, 120])
            root.addWidget(splitter)
            actions = QHBoxLayout()
            self.clearButton = QPushButton("清空视图")
            self.clearButton.setIcon(icon("trash-2"))
            actions.addWidget(self.autoScrollCheck)
            actions.addStretch(1)
            actions.addWidget(self.clearButton)
            root.addLayout(actions)
            self.setLayout(root)

            for combo in (
                self.levelFilter,
                self.sourceFilter,
                self.jobFilter,
                self.nodeFilter,
                self.eventTypeFilter,
            ):
                combo.currentTextChanged.connect(self._refreshTable)
            self.searchInput.textChanged.connect(self._refreshTable)
            self.autoScrollCheck.toggled.connect(self._onAutoScrollChanged)
            self.table.currentCellChanged.connect(self._showDetails)
            self.clearButton.clicked.connect(self._clearLogs)
            self._refreshTimer = QTimer(self)
            self._refreshTimer.setSingleShot(True)
            self._refreshTimer.setInterval(_BATCH_INTERVAL_MS)
            self._refreshTimer.timeout.connect(self._applyPendingEntries)

        def appendEntry(self, entry: StructuredLogEntry) -> None:
            self._entries.append(entry)
            self._pendingEntries.append(entry)
            overflow = len(self._entries) - self.maximumEntries
            if overflow > 0:
                self._pendingEvicted.extend(self._entries[:overflow])
                del self._entries[:overflow]
            if not self._refreshTimer.isActive():
                self._refreshTimer.start()

        def setEntries(self, entries: list[StructuredLogEntry]) -> None:
            self._refreshTimer.stop()
            self._entries = list(entries[-self.maximumEntries :])
            self._pendingEntries.clear()
            self._pendingEvicted.clear()
            self._refreshDynamicFilters()
            self._refreshTable()

        def appendLog(self, line: str) -> None:
            self.appendEntry(StructuredLogEntry.fromLine(line))

        def setLogs(self, lines: list[str]) -> None:
            self.setEntries([StructuredLogEntry.fromLine(line) for line in lines])

        def settings(self) -> dict[str, object]:
            return {
                "level": self.levelFilter.currentText(),
                "source": self.sourceFilter.currentText(),
                "job": self.jobFilter.currentText(),
                "node": self.nodeFilter.currentText(),
                "event": self.eventTypeFilter.currentText(),
                "search": self.searchInput.text(),
                "autoScroll": self.autoScrollCheck.isChecked(),
                "columnWidths": [self.table.columnWidth(index) for index in range(len(self.columns))],
            }

        def restoreSettings(self, value: dict[str, object]) -> None:
            self._refreshDynamicFilters()
            for combo, key, allowUnknown in (
                (self.levelFilter, "level", False),
                (self.sourceFilter, "source", False),
                (self.jobFilter, "job", True),
                (self.nodeFilter, "node", True),
                (self.eventTypeFilter, "event", True),
            ):
                requested = str(value.get(key, "全部"))
                index = combo.findText(requested)
                if index < 0 and allowUnknown and requested != "全部":
                    combo.addItem(requested)
                    index = combo.findText(requested)
                combo.setCurrentIndex(index if index >= 0 else 0)
            self.searchInput.setText(str(value.get("search", "")))
            self.autoScrollCheck.setChecked(bool(value.get("autoScroll", True)))
            widths = value.get("columnWidths", [])
            if isinstance(widths, list):
                for index, width in enumerate(widths[: len(self.columns)]):
                    if isinstance(width, int) and width > 0:
                        self.table.setColumnWidth(index, width)
            self._refreshTable()

        def _clearLogs(self) -> None:
            self._refreshTimer.stop()
            self._entries.clear()
            self._visibleEntries.clear()
            self._pendingEntries.clear()
            self._pendingEvicted.clear()
            self.table.setRowCount(0)
            self.detailViewer.clear()
            self._refreshDynamicFilters(preserveUnknown=False)
            if self._onClear is not None:
                self._onClear()

        def clearView(self) -> None:
            self._clearLogs()

        def _refreshDynamicFilters(self, *, preserveUnknown: bool = True) -> None:
            _replaceComboValues(
                self.jobFilter,
                _values(self._entries, "jobId"),
                preserveUnknown=preserveUnknown,
            )
            _replaceComboValues(
                self.nodeFilter,
                _values(self._entries, "nodeId"),
                preserveUnknown=preserveUnknown,
            )
            _replaceComboValues(
                self.eventTypeFilter,
                _values(self._entries, "eventType"),
                preserveUnknown=preserveUnknown,
            )

        def _applyPendingEntries(self) -> None:
            pending = list(self._pendingEntries)
            self._pendingEntries.clear()
            evicted = list(self._pendingEvicted)
            self._pendingEvicted.clear()
            self._refreshDynamicFilters()
            if not pending and not evicted:
                return
            activeEntryIds = {id(entry) for entry in self._entries}
            pending = [entry for entry in pending if id(entry) in activeEntryIds]
            visibleEvictions = 0
            for entry in evicted:
                if (
                    visibleEvictions < len(self._visibleEntries)
                    and self._visibleEntries[visibleEvictions] is entry
                ):
                    visibleEvictions += 1
            self.table.setUpdatesEnabled(False)
            try:
                if visibleEvictions:
                    del self._visibleEntries[:visibleEvictions]
                    for _ in range(visibleEvictions):
                        self.table.removeRow(0)
                for entry in pending:
                    if not self._matches(entry):
                        continue
                    row = len(self._visibleEntries)
                    self._visibleEntries.append(entry)
                    self.table.insertRow(row)
                    self._setTableRow(row, entry)
            finally:
                self.table.setUpdatesEnabled(True)
            if self.autoScrollCheck.isChecked() and self._visibleEntries:
                self.table.scrollToBottom()

        def _refreshTable(self, *_args) -> None:
            self._refreshTimer.stop()
            self._pendingEntries.clear()
            self._pendingEvicted.clear()
            self._visibleEntries = [entry for entry in self._entries if self._matches(entry)]
            self.table.setUpdatesEnabled(False)
            try:
                self.table.setRowCount(len(self._visibleEntries))
                for row, entry in enumerate(self._visibleEntries):
                    self._setTableRow(row, entry)
            finally:
                self.table.setUpdatesEnabled(True)
            if self.autoScrollCheck.isChecked() and self._visibleEntries:
                self.table.scrollToBottom()

        def _setTableRow(self, row: int, entry: StructuredLogEntry) -> None:
            values = (
                entry.displayTime(),
                entry.level,
                entry.source,
                entry.jobId,
                entry.workflowId,
                entry.nodeId,
                "/".join(str(item) for item in entry.iterationPath),
                entry.eventType,
                entry.code,
                entry.message,
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                self.table.setItem(row, column, item)

        def _matches(self, entry: StructuredLogEntry) -> bool:
            for combo, value in (
                (self.levelFilter, entry.level), (self.sourceFilter, entry.source),
                (self.jobFilter, entry.jobId), (self.nodeFilter, entry.nodeId),
                (self.eventTypeFilter, entry.eventType),
            ):
                selected = combo.currentText()
                if selected != "全部" and selected != value:
                    return False
            query = self.searchInput.text().strip().casefold()
            if not query:
                return True
            haystack = " ".join((
                entry.message, entry.code, entry.eventType, entry.jobId,
                entry.workflowId, entry.nodeId,
                json.dumps(entry.payload, ensure_ascii=False, default=str),
            )).casefold()
            return query in haystack

        def _showDetails(self, row: int, _column: int, *_args) -> None:
            if not 0 <= row < len(self._visibleEntries):
                self.detailViewer.clear()
                return
            self.detailViewer.setPlainText(json.dumps(
                self._visibleEntries[row].detailPayload(),
                ensure_ascii=False, indent=2, default=str,
            ))

        def _onAutoScrollChanged(self, enabled: bool) -> None:
            if enabled and self._visibleEntries:
                self.table.scrollToBottom()


    class RuntimeLogDock(QDockWidget):
        def __init__(
            self,
            parent=None,
            *,
            onClear: Callable[[], None] | None = None,
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
        """Compatibility wrapper retained for callers outside MainWindow."""

        def __init__(self) -> None:
            super().__init__()
            self.setModal(False)
            self.setWindowTitle("运行日志")
            self.resize(900, 520)
            layout = QVBoxLayout()
            self.view = StructuredLogView(self)
            self._logViewer = self.view.detailViewer
            layout.addWidget(self.view)
            self.setLayout(layout)

        def appendLog(self, line: str) -> None:
            self.view.appendLog(line)

        def setLogs(self, lines: list[str]) -> None:
            self.view.setLogs(lines)


    def _combo(values: tuple[str, ...]) -> QComboBox:
        combo = QComboBox()
        combo.addItems(list(values))
        return combo


    def _replaceComboValues(
        combo: QComboBox,
        values: list[str],
        *,
        preserveUnknown: bool = True,
    ) -> None:
        selected = combo.currentText()
        combo.blockSignals(True)
        combo.clear()
        combo.addItem("全部")
        combo.addItems(values)
        index = combo.findText(selected)
        if preserveUnknown and index < 0 and selected not in {"", "全部"}:
            combo.addItem(selected)
            index = combo.findText(selected)
        combo.setCurrentIndex(index if index >= 0 else 0)
        combo.blockSignals(False)

except Exception:  # pragma: no cover

    class StructuredLogView:  # type: ignore[no-redef]
        def __init__(
            self,
            parent=None,
            *,
            onClear: Callable[[], None] | None = None,
            maximumEntries: int = DEFAULT_MAX_LOG_ENTRIES,
        ) -> None:
            _ = parent
            self.maximumEntries = max(1, int(maximumEntries))
            self._onClear = onClear
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
            maximumEntries: int = DEFAULT_MAX_LOG_ENTRIES,
        ) -> None:
            _ = parent
            self.view = StructuredLogView(
                onClear=onClear,
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
        def __init__(self) -> None:
            super().__init__()
            self._lines = self.view._lines


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
