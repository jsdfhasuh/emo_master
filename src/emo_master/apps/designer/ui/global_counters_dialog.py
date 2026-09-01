from __future__ import annotations

from datetime import datetime

from emo_master.apps.designer.services.global_counter_worker import (
    GlobalCounterWorker,
    GlobalCounterWorkerFailure,
    GlobalCounterWorkerResult,
)
from emo_master.apps.runtime.context.global_counters import (
    MAX_GLOBAL_COUNTER_VALUE,
    GlobalCounterError,
    validateGlobalCounterName,
)


REFRESH_INTERVAL_MS = 1000


try:
    from PySide2.QtCore import Qt, QTimer
    from PySide2.QtWidgets import (
        QAbstractItemView,
        QDialog,
        QHBoxLayout,
        QHeaderView,
        QInputDialog,
        QLabel,
        QLineEdit,
        QMessageBox,
        QPushButton,
        QTableWidget,
        QTableWidgetItem,
        QVBoxLayout,
    )

    class GlobalCountersDialog(QDialog):
        def __init__(self, runtimeClient, parent=None) -> None:
            super().__init__(parent)
            self.runtimeClient = runtimeClient
            self._projectId = ""
            self._generation = 0
            self._worker: GlobalCounterWorker | None = None
            self._records: list[object] = []
            self._closing = False
            self._refreshAfterWorker = False
            self._lastRefreshError = ""

            self.setModal(False)
            self.setWindowTitle("全局计数器")
            self.resize(720, 440)
            setAttribute = getattr(self, "setAttribute", None)
            if callable(setAttribute):
                setAttribute(Qt.WA_DeleteOnClose, False)

            rootLayout = QVBoxLayout()
            self._projectLabel = QLabel("当前项目：未加载")
            rootLayout.addWidget(self._projectLabel)

            self._table = QTableWidget(0, 3)
            self._table.setHorizontalHeaderLabels(["名称", "当前值", "更新时间"])
            self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
            self._table.setSelectionMode(QAbstractItemView.SingleSelection)
            self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
            header = self._table.horizontalHeader()
            header.setSectionResizeMode(0, QHeaderView.Stretch)
            header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
            header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
            rootLayout.addWidget(self._table)

            self._statusLabel = QLabel("就绪")
            rootLayout.addWidget(self._statusLabel)

            buttonRow = QHBoxLayout()
            self._newButton = QPushButton("新建")
            self._setButton = QPushButton("设值")
            self._resetButton = QPushButton("清零")
            self._refreshButton = QPushButton("刷新")
            self._closeButton = QPushButton("关闭")
            for button in (
                self._newButton,
                self._setButton,
                self._resetButton,
                self._refreshButton,
                self._closeButton,
            ):
                buttonRow.addWidget(button)
            rootLayout.addLayout(buttonRow)
            self.setLayout(rootLayout)

            self._timer = QTimer(self)
            self._timer.setInterval(REFRESH_INTERVAL_MS)
            self._timer.timeout.connect(self.refreshCounters)
            self._newButton.clicked.connect(self._promptNewCounter)
            self._setButton.clicked.connect(self._promptSetCounter)
            self._resetButton.clicked.connect(self._resetSelectedCounter)
            self._refreshButton.clicked.connect(self.refreshCounters)
            self._closeButton.clicked.connect(self.close)
            self._updateButtons()

        def bindProject(self, projectId: str) -> None:
            normalized = str(projectId or "")
            if normalized == self._projectId:
                return
            self._generation += 1
            self._projectId = normalized
            self._records = []
            self._renderRecords()
            self._projectLabel.setText(
                f"当前项目：{normalized}" if normalized else "当前项目：未加载"
            )
            if self._worker is not None:
                self._worker.requestStop()
                self._refreshAfterWorker = bool(normalized)
            elif normalized and self.isVisible():
                self.refreshCounters()
            self._updateButtons()

        def showForProject(self, projectId: str) -> None:
            self._closing = False
            self.bindProject(projectId)
            self.show()
            self.raise_()
            self.activateWindow()
            self._timer.start()
            self.refreshCounters()

        def refreshCounters(self) -> None:
            if not self._projectId or self._requestInFlight():
                return
            self._startWorker("list")

        def createCounter(self, name: str) -> bool:
            if not self._validateName(name):
                return False
            return self._startWorker("get", name=name)

        def setCounter(self, name: str, value: int) -> bool:
            if not self._validateName(name) or not _validCounterValue(value):
                QMessageBox.warning(
                    self,
                    "数值无效",
                    f"请输入 0 到 {MAX_GLOBAL_COUNTER_VALUE} 之间的整数",
                )
                return False
            return self._startWorker("set", name=name, value=value)

        def resetCounter(self, name: str) -> bool:
            if not self._validateName(name):
                return False
            return self._startWorker("reset", name=name)

        def shutdown(self) -> None:
            if self._closing:
                return
            self._closing = True
            self._generation += 1
            self._timer.stop()
            worker = self._worker
            if worker is None:
                return
            worker.requestStop()
            wait = getattr(worker, "wait", None)
            isRunning = getattr(worker, "isRunning", None)
            if callable(wait) and callable(isRunning) and isRunning():
                wait(12000)
                if isRunning():
                    wait(-1)
            self._worker = None

        def getDisplayedCounters(self) -> list[dict[str, object]]:
            return [
                {
                    "name": str(getattr(record, "name", "")),
                    "value": int(getattr(record, "value", 0)),
                    "updatedAtMs": int(getattr(record, "updatedAtMs", 0)),
                }
                for record in self._records
            ]

        def isRequestInFlight(self) -> bool:
            return self._requestInFlight()

        def refreshIntervalMs(self) -> int:
            return REFRESH_INTERVAL_MS

        def showEvent(self, event) -> None:  # type: ignore[override]
            self._timer.start()
            self.refreshCounters()
            super().showEvent(event)

        def hideEvent(self, event) -> None:  # type: ignore[override]
            self._timer.stop()
            super().hideEvent(event)

        def closeEvent(self, event) -> None:  # type: ignore[override]
            self._timer.stop()
            self._generation += 1
            if self._worker is not None:
                self._worker.requestStop()
            super().closeEvent(event)

        def _promptNewCounter(self) -> None:
            name, accepted = QInputDialog.getText(
                self,
                "新建全局计数器",
                "名称（1–128 个字符）：",
                QLineEdit.Normal,
            )
            if not accepted:
                return
            self.createCounter(name)

        def _promptSetCounter(self) -> None:
            selected = self._selectedRecord()
            if selected is None:
                QMessageBox.warning(self, "设值失败", "请先选择一个计数器")
                return
            name = str(getattr(selected, "name", ""))
            current = int(getattr(selected, "value", 0))
            text, accepted = QInputDialog.getText(
                self,
                "设置全局计数器",
                f"{name} 的新值（0–{MAX_GLOBAL_COUNTER_VALUE}）：",
                QLineEdit.Normal,
                str(current),
            )
            if not accepted:
                return
            value = self._parseValue(text)
            if value is None:
                return
            self.setCounter(name, value)

        def _resetSelectedCounter(self) -> None:
            selected = self._selectedRecord()
            if selected is None:
                QMessageBox.warning(self, "清零失败", "请先选择一个计数器")
                return
            self.resetCounter(str(getattr(selected, "name", "")))

        def _startWorker(
            self,
            operation: str,
            *,
            name: str = "",
            value: int = 0,
        ) -> bool:
            if not self._projectId or self._requestInFlight():
                return False
            worker = GlobalCounterWorker(
                self.runtimeClient,
                operation,
                self._projectId,
                self._generation,
                name=name,
                value=value,
            )
            worker.resultReady.connect(
                lambda result, currentWorker=worker: self._onWorkerResult(
                    currentWorker, result
                )
            )
            worker.failed.connect(
                lambda failure, currentWorker=worker: self._onWorkerFailure(
                    currentWorker, failure
                )
            )
            worker.finished.connect(
                lambda currentWorker=worker: self._onWorkerFinished(currentWorker)
            )
            self._worker = worker
            self._statusLabel.setText("正在刷新…" if operation == "list" else "正在提交…")
            self._updateButtons()
            worker.start()
            return True

        def _onWorkerResult(
            self,
            worker: GlobalCounterWorker,
            result: GlobalCounterWorkerResult,
        ) -> None:
            if (
                self._worker is not worker
                or result.generation != self._generation
                or self._closing
            ):
                return
            self._lastRefreshError = ""
            if result.operation == "list":
                payload = result.payload
                self._records = list(payload) if isinstance(payload, list) else []
            else:
                self._upsertRecord(result.payload)
            self._renderRecords()
            self._statusLabel.setText("已更新")

        def _onWorkerFailure(
            self,
            worker: GlobalCounterWorker,
            failure: GlobalCounterWorkerFailure,
        ) -> None:
            if (
                self._worker is not worker
                or failure.generation != self._generation
                or self._closing
            ):
                return
            message = f"{failure.code}: {failure.message}"
            self._statusLabel.setText(message)
            if failure.operation != "list" or message != self._lastRefreshError:
                QMessageBox.warning(self, "全局计数器请求失败", message)
            self._lastRefreshError = message

        def _onWorkerFinished(self, worker: GlobalCounterWorker) -> None:
            if self._worker is not worker:
                return
            self._worker = None
            self._updateButtons()
            if (
                not self._closing
                and self.isVisible()
                and self._projectId
                and self._refreshAfterWorker
            ):
                self._refreshAfterWorker = False
                self.refreshCounters()

        def _requestInFlight(self) -> bool:
            return self._worker is not None

        def _renderRecords(self) -> None:
            selectedName = self._selectedName()
            records = sorted(
                self._records,
                key=lambda record: str(getattr(record, "name", "")),
            )
            self._records = records
            self._table.setRowCount(len(records))
            selectedRow = -1
            for row, record in enumerate(records):
                name = str(getattr(record, "name", ""))
                value = int(getattr(record, "value", 0))
                updatedAtMs = int(getattr(record, "updatedAtMs", 0))
                self._table.setItem(row, 0, QTableWidgetItem(name))
                self._table.setItem(row, 1, QTableWidgetItem(str(value)))
                self._table.setItem(
                    row,
                    2,
                    QTableWidgetItem(_formatTimestamp(updatedAtMs)),
                )
                if name == selectedName:
                    selectedRow = row
            if selectedRow >= 0:
                self._table.selectRow(selectedRow)

        def _upsertRecord(self, value: object) -> None:
            name = str(getattr(value, "name", ""))
            if not name:
                return
            self._records = [
                record
                for record in self._records
                if str(getattr(record, "name", "")) != name
            ]
            self._records.append(value)

        def _selectedRecord(self) -> object | None:
            row = self._table.currentRow()
            if row < 0 or row >= len(self._records):
                return None
            return self._records[row]

        def _selectedName(self) -> str:
            selected = self._selectedRecord()
            return str(getattr(selected, "name", "")) if selected is not None else ""

        def _validateName(self, name: object) -> bool:
            try:
                validateGlobalCounterName(name)
            except GlobalCounterError as err:
                QMessageBox.warning(self, "名称无效", f"{err.code}: {err}")
                return False
            return True

        def _parseValue(self, text: str) -> int | None:
            try:
                value = int(text.strip(), 10)
            except (TypeError, ValueError):
                value = -1
            if value < 0 or value > MAX_GLOBAL_COUNTER_VALUE:
                QMessageBox.warning(
                    self,
                    "数值无效",
                    f"请输入 0 到 {MAX_GLOBAL_COUNTER_VALUE} 之间的整数",
                )
                return None
            return value

        def _updateButtons(self) -> None:
            enabled = bool(self._projectId) and not self._requestInFlight()
            for button in (
                self._newButton,
                self._setButton,
                self._resetButton,
                self._refreshButton,
            ):
                button.setEnabled(enabled)

except Exception:  # pragma: no cover

    class GlobalCountersDialog:  # type: ignore[no-redef]
        def __init__(self, runtimeClient, parent=None) -> None:
            _ = parent
            self.runtimeClient = runtimeClient
            self._projectId = ""
            self._generation = 0
            self._worker: GlobalCounterWorker | None = None
            self._records: list[object] = []
            self._visible = False
            self._closing = False
            self._refreshAfterWorker = False
            self.lastError = ""

        def bindProject(self, projectId: str) -> None:
            normalized = str(projectId or "")
            if normalized == self._projectId:
                return
            self._generation += 1
            self._projectId = normalized
            self._records = []
            if self._worker is not None:
                self._worker.requestStop()
                self._refreshAfterWorker = bool(normalized)
            elif normalized and self._visible:
                self.refreshCounters()

        def showForProject(self, projectId: str) -> None:
            self._closing = False
            self.bindProject(projectId)
            self._visible = True
            self.refreshCounters()

        def refreshCounters(self) -> None:
            if not self._projectId or self.isRequestInFlight():
                return
            self._startWorker("list")

        def createCounter(self, name: str) -> bool:
            if not _validCounterName(name):
                return False
            return self._startWorker("get", name=name)

        def setCounter(self, name: str, value: int) -> bool:
            if not _validCounterName(name) or not _validCounterValue(value):
                return False
            return self._startWorker("set", name=name, value=value)

        def resetCounter(self, name: str) -> bool:
            if not _validCounterName(name):
                return False
            return self._startWorker("reset", name=name)

        def _startWorker(
            self,
            operation: str,
            *,
            name: str = "",
            value: int = 0,
        ) -> bool:
            if not self._projectId or self.isRequestInFlight():
                return False
            worker = GlobalCounterWorker(
                self.runtimeClient,
                operation,
                self._projectId,
                self._generation,
                name=name,
                value=value,
            )
            worker.resultReady.connect(self._onResult)
            worker.failed.connect(self._onFailure)
            worker.finished.connect(lambda: self._onFinished(worker))
            self._worker = worker
            worker.start()
            return True

        def shutdown(self) -> None:
            self._closing = True
            self._generation += 1
            worker = self._worker
            if worker is not None:
                worker.requestStop()
                worker.wait(-1)
            self._worker = None

        def close(self) -> None:
            self._visible = False
            self._generation += 1
            if self._worker is not None:
                self._worker.requestStop()

        def isVisible(self) -> bool:
            return self._visible

        def isRequestInFlight(self) -> bool:
            return self._worker is not None and self._worker.isRunning()

        def refreshIntervalMs(self) -> int:
            return REFRESH_INTERVAL_MS

        def getDisplayedCounters(self) -> list[dict[str, object]]:
            return [
                {
                    "name": str(getattr(record, "name", "")),
                    "value": int(getattr(record, "value", 0)),
                    "updatedAtMs": int(getattr(record, "updatedAtMs", 0)),
                }
                for record in self._records
            ]

        def _onResult(self, result: GlobalCounterWorkerResult) -> None:
            if result.generation == self._generation and not self._closing:
                if result.operation == "list":
                    payload = result.payload
                    self._records = list(payload) if isinstance(payload, list) else []
                else:
                    name = str(getattr(result.payload, "name", ""))
                    self._records = [
                        record
                        for record in self._records
                        if str(getattr(record, "name", "")) != name
                    ]
                    if name:
                        self._records.append(result.payload)
                self._records.sort(key=lambda record: str(getattr(record, "name", "")))
                self.lastError = ""

        def _onFailure(self, failure: GlobalCounterWorkerFailure) -> None:
            if failure.generation == self._generation and not self._closing:
                self.lastError = f"{failure.code}: {failure.message}"

        def _onFinished(self, worker: GlobalCounterWorker) -> None:
            if self._worker is worker:
                self._worker = None
                if (
                    self._refreshAfterWorker
                    and self._visible
                    and self._projectId
                    and not self._closing
                ):
                    self._refreshAfterWorker = False
                    self.refreshCounters()


def _formatTimestamp(timestampMs: int) -> str:
    if timestampMs <= 0:
        return "-"
    try:
        return datetime.fromtimestamp(timestampMs / 1000.0).astimezone().strftime(
            "%Y-%m-%d %H:%M:%S"
        )
    except (OverflowError, OSError, ValueError):
        return "-"


def _validCounterName(name: object) -> bool:
    try:
        validateGlobalCounterName(name)
    except GlobalCounterError:
        return False
    return True


def _validCounterValue(value: object) -> bool:
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and 0 <= value <= MAX_GLOBAL_COUNTER_VALUE
    )
