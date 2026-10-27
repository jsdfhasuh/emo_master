"""Read-only, visible-page-only observation of authoritative Dispatch queues."""
from datetime import datetime
from types import SimpleNamespace

from PySide2.QtCore import Qt, QTimer, Slot
from PySide2.QtWidgets import (
    QAbstractItemView, QComboBox, QGroupBox, QHBoxLayout, QHeaderView,
    QLabel, QPlainTextEdit, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from emo_master.apps.designer.services.global_counter_worker import GlobalCounterWorker
from emo_master.apps.designer.ui.global_counters_dialog import _connectCounterSlot, _counterWorkerOwner
from emo_master.apps.designer.ui.widgets import WrapLabel


STATES = {"RUNNING": "运行中", "DRAINING": "生产已结束，消费处理中", "STARTING": "准备中",
          "STOPPING": "停止中", "RETIRED": "已退休", "QUEUED": "等待", "COMPLETED": "已完成"}
POLICIES = {"wait": "等待容量", "error": "立即报错", "skip": "跳过本次投递"}


def timestamp(value):
    return datetime.fromtimestamp(value / 1000).strftime("%H:%M:%S") if value else "-"


def table(headers):
    widget = QTableWidget(0, len(headers))
    widget.setHorizontalHeaderLabels(headers)
    widget.setSelectionBehavior(QAbstractItemView.SelectRows)
    widget.setSelectionMode(QAbstractItemView.SingleSelection)
    widget.setEditTriggers(QAbstractItemView.NoEditTriggers)
    widget.setWordWrap(False)
    widget.verticalHeader().hide()
    widget.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
    widget.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
    return widget


class DispatchQueuePanel(QWidget):
    def __init__(self, getClient, getCurrentJobId, parent=None):
        super().__init__(parent)
        self.getClient, self.getCurrentJobId = getClient, getCurrentJobId
        self.projectId = ""
        self.active = False
        self.generation = 0
        self._worker = None
        self._client = None
        self.instanceId = ""
        self.lastSnapshotMs = None
        self.slots = []
        layout = QVBoxLayout(self)
        row = QHBoxLayout()
        row.addWidget(QLabel("运行组"))
        self.groups = QComboBox()
        self.groups.addItem("未选择运行组", "")
        row.addWidget(self.groups, 1)
        layout.addLayout(row)
        self.table = table(["消费流程 / 槽位", "等待 / 容量", "处理中", "累计入队", "已完成", "失败", "状态"])
        layout.addWidget(self.table, 1)
        self.details = QGroupBox("队列项与图像明细")
        self.details.setCheckable(True)
        self.details.setChecked(False)
        detailLayout = QVBoxLayout(self.details)
        self.metadata = WrapLabel("")
        self.items = table(["调用 ID", "状态", "等待位置", "入队时间", "开始处理", "图像元数据"])
        self.items.setMaximumHeight(190)
        detailLayout.addWidget(self.metadata)
        detailLayout.addWidget(self.items)
        self.metadata.hide()
        self.items.hide()
        layout.addWidget(self.details)
        self.status = WrapLabel("当前没有活动运行组")
        layout.addWidget(self.status)
        self.errorDetails = QGroupBox("错误详情")
        self.errorDetails.setCheckable(True)
        self.errorDetails.setChecked(False)
        errorLayout = QVBoxLayout(self.errorDetails)
        self.errorText = QPlainTextEdit()
        self.errorText.setReadOnly(True)
        self.errorText.setMaximumHeight(110)
        errorLayout.addWidget(self.errorText)
        self.errorDetails.toggled.connect(self.errorText.setVisible)
        self.errorText.hide()
        self.errorDetails.hide()
        layout.addWidget(self.errorDetails)
        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self.refresh)
        self.groups.currentIndexChanged.connect(self._groupChanged)
        self.table.itemSelectionChanged.connect(self._selectionChanged)
        self.details.toggled.connect(self._expanded)

    def bindProject(self, projectId):
        if self.projectId == projectId:
            return
        self.projectId = projectId
        self.generation += 1
        self.instanceId = ""
        self._clear()

    def _clear(self):
        self.groups.blockSignals(True)
        self.groups.clear()
        self.groups.addItem("未选择运行组", "")
        self.groups.blockSignals(False)
        self.table.blockSignals(True)
        self.table.setRowCount(0)
        self.table.blockSignals(False)
        self.items.setRowCount(0)
        self.slots = []
        self.lastSnapshotMs = None
        self.metadata.clear()
        self.status.setText("队列状态尚未读取")
        self.status.setToolTip("")
        self.errorDetails.hide()

    def setActive(self, active):
        if self.active != active:
            self.generation += 1
        self.active = active
        if active:
            self.timer.start()
            self.refresh()
        else:
            self.timer.stop()

    def selectedSlot(self):
        item = self.table.item(self.table.currentRow(), 0)
        return item.data(Qt.UserRole) if item else ""

    def _expanded(self, expanded):
        self.items.setVisible(expanded)
        self.metadata.setVisible(expanded)
        self._selectionChanged()

    def _selectionChanged(self):
        self.generation += 1
        self.items.setRowCount(0)
        self.metadata.clear()
        self.refresh()

    def _groupChanged(self):
        self.table.blockSignals(True)
        self.table.setRowCount(0)
        self.table.blockSignals(False)
        self.slots = []
        self.lastSnapshotMs = None
        self.status.setText("正在读取运行组…")
        self._selectionChanged()

    def refresh(self):
        if not self.active or self._worker is not None or not self.projectId:
            return
        client = self.getClient()
        if client is not self._client:
            self._client = client
            self.generation += 1
            self.instanceId = ""
            self._clear()
        method = getattr(client, "dispatchQueueState", None)
        if not callable(method):
            self.status.setText("当前 Runtime 不支持队列监控，业务变量仍可使用。")
            self.timer.stop()
            return
        project, group, slot, expanded = self.projectId, self.groups.currentData() or "", self.selectedSlot(), self.details.isChecked()
        worker = GlobalCounterWorker(SimpleNamespace(listGlobalCounters=lambda _: method(project, group, slot, expanded)),
                                     "list", project, self.generation)
        worker.setParent(_counterWorkerOwner())
        self._worker = worker
        _connectCounterSlot(worker, "finished()", self, "_finished()")
        _connectCounterSlot(worker, "finished()", worker, "deleteLater()")
        worker.start()

    @Slot()  # type: ignore[operator]
    def _finished(self):
        worker = self.sender()
        if worker is not self._worker:
            return
        self._worker = None
        if not self.active:
            return
        if worker.generation != self.generation or self.getClient() is not self._client:
            self.refresh()
            return
        if worker.error:
            prefix = "数据已过期，最后快照 " + timestamp(self.lastSnapshotMs) if self.lastSnapshotMs else "队列状态不可用"
            self.status.setText(prefix + "：" + worker.error.message.splitlines()[0][:180])
            self.status.setToolTip(worker.error.code + ": " + worker.error.message)
            self.errorText.setPlainText(worker.error.code + "\n" + worker.error.message)
            self.errorDetails.show()
            if worker.error.code == "E_DISPATCH_MONITOR_UNSUPPORTED":
                self.timer.stop()
        elif worker.result:
            refresh = self.applyState(worker.result.payload)
            if refresh:
                QTimer.singleShot(0, self.refresh)

    def applyState(self, state):
        self.errorDetails.hide()
        self.status.setToolTip("")
        instance = state["runtimeInstanceId"]
        if self.instanceId and instance != self.instanceId:
            self._clear()
        self.instanceId = instance
        selected = self.groups.currentData() or ""
        oldLabel = self.groups.currentText()
        choices = state["groups"]
        if not selected:
            job = self.getCurrentJobId()
            matches = [item for item in choices if job and job in [item["sourceJobId"], *item["consumerJobIds"]]]
            chosen = matches[0] if len(matches) == 1 else choices[0] if len(choices) == 1 else None
            selected = chosen["runGroupId"] if chosen else ""
        self.groups.blockSignals(True)
        self.groups.clear()
        self.groups.addItem("选择运行组" if choices else "没有活动运行组", "")
        for item in choices:
            label = f'{item["sourceWorkflowName"]} · {item["runGroupId"][:8]} · {STATES.get(item["state"], item["state"])}'
            self.groups.addItem(label, item["runGroupId"])
            self.groups.setItemData(self.groups.count() - 1, item["runGroupId"], Qt.ToolTipRole)
        if selected and self.groups.findData(selected) < 0:
            self.groups.addItem(oldLabel.split(" · 已不可用")[0] + " · 已不可用", selected)
        self.groups.setCurrentIndex(max(0, self.groups.findData(selected)))
        self.groups.setToolTip(self.groups.currentText() + "\n" + selected)
        self.groups.blockSignals(False)
        snapshot = state.get("selected")
        self.lastSnapshotMs = state["snapshotAtMs"]
        if snapshot is None or snapshot["runGroupId"] != selected:
            self.table.blockSignals(True)
            self.table.setRowCount(0)
            self.table.blockSignals(False)
            self.slots = []
            self.items.setRowCount(0)
            self.metadata.clear()
            exists = any(item["runGroupId"] == selected for item in choices)
            self.status.setText("正在读取运行组…" if exists else "该运行组已不可用，未保留队列快照。" if selected
                                else "请选择运行组" if choices else "当前没有活动运行组")
            return exists
        selectedSlot = self.selectedSlot()
        scroll = self.table.verticalScrollBar().value()
        self.slots = snapshot["slots"]
        self.table.blockSignals(True)
        self.table.setRowCount(len(self.slots))
        for row, slot in enumerate(self.slots):
            label = STATES.get(slot["state"], slot["state"])
            if slot["waitingProducers"]:
                label = "生产等待容量"
            elif slot["queued"] >= slot["capacity"]:
                label = "队列已满"
            values = [f'{slot["workflowName"]} / {slot["targetSlotId"]}', f'{slot["queued"]} / {slot["capacity"]}',
                      slot["processing"], slot["acceptedTotal"], slot["completedTotal"], slot["failedTotal"], label]
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                item.setData(Qt.UserRole, slot["targetSlotId"])
                item.setToolTip(str(value))
                self.table.setItem(row, column, item)
        index = next((i for i, slot in enumerate(self.slots) if slot["targetSlotId"] == selectedSlot), 0)
        self.table.selectRow(index)
        self.table.verticalScrollBar().setValue(scroll)
        self.table.blockSignals(False)
        slot = self.slots[index] if self.slots else None
        if slot:
            policies = ", ".join(POLICIES.get(policy, policy) for policy in slot["queueFullPolicies"])
            waiting = "；".join(f'{entry["jobId"]}，自 {timestamp(entry["sinceMs"])}'
                               for entry in slot["waitingProducers"]) or "无"
            self.metadata.setText(f'生产 Job：{snapshot["sourceJobId"]}\n消费 Job：{slot["jobId"]}\n'
                f'槽位：{slot["targetSlotId"]}\n'
                f'取消 {slot["cancelledTotal"]} · 拒绝 {slot["rejectedTotal"]} · '
                f'跳过 {slot.get("skippedTotal", 0)} · '
                f'策略 {policies} · 最近错误 {slot["lastErrorCode"] or "无"}\n生产等待：{waiting}')
            self._renderItems(slot.get("items", []))
        self.status.setText("快照 " + timestamp(self.lastSnapshotMs) + " · 只读 · " + STATES.get(snapshot["state"], snapshot["state"]))
        self.status.setToolTip("")
        return bool(slot and self.details.isChecked() and "items" not in slot)

    def _renderItems(self, items):
        scroll = self.items.verticalScrollBar().value()
        self.items.setRowCount(len(items))
        for row, entry in enumerate(items):
            images = [f'{image["frameId"][:8]} {image.get("shape", [])} {image.get("dtype", "")}' for image in entry["frames"]]
            if entry["frameCount"] > len(images):
                images.append(f'另有 {entry["frameCount"] - len(images)} 张')
            values = [entry["invocationId"][:12], STATES.get(entry["state"], entry["state"]),
                      entry["position"] if entry["state"] == "QUEUED" else "-", timestamp(entry["queuedAtMs"]),
                      timestamp(entry["startedAtMs"]), "; ".join(images) or "无图像"]
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                item.setToolTip(entry["invocationId"] if column == 0 else
                                "\n".join(image["frameId"] for image in entry["frames"]) if column == 5 else str(value))
                self.items.setItem(row, column, item)
        self.items.verticalScrollBar().setValue(scroll)

    def shutdown(self):
        self.setActive(False)
        if self._worker:
            self._worker.requestStop()
