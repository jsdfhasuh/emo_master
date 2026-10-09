from __future__ import annotations

from copy import deepcopy
from collections import deque
from datetime import datetime
from types import SimpleNamespace
from uuid import uuid4

from PySide2.QtCore import Qt, QTimer, Slot, QRegularExpression
from PySide2.QtGui import QDoubleValidator, QRegularExpressionValidator
from PySide2.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QLabel,
    QLineEdit, QMessageBox, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout,
    QHeaderView, QAbstractItemView, QWidget,
)

from emo_master.core.project.global_variables import VariableDefinition, validateValue
from emo_master.apps.designer.services.global_counter_worker import GlobalCounterWorker
from emo_master.apps.designer.ui.global_counters_dialog import _counterWorkerOwner
from emo_master.apps.designer.ui.icon_map import icon
from emo_master.apps.designer.ui.widgets import WrapLabel


TYPE_LABELS = {"boolean": "布尔", "integer": "整数", "number": "数值", "string": "字符串"}


class ScalarValueEditor(QWidget):
    def __init__(self, valueType, value, parent=None):
        super().__init__(parent)
        self.valueType = valueType
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        if valueType == "boolean":
            self.control = QCheckBox("true")
            self.control.setChecked(value is True)
            self.control.setText("true" if value is True else "false")
            self.control.toggled.connect(lambda checked: self.control.setText("true" if checked else "false"))
        else:
            self.control = QLineEdit(str(value))
            if valueType == "integer":
                self.control.setValidator(QRegularExpressionValidator(QRegularExpression("-?[0-9]+"), self.control))
            elif valueType == "number":
                self.control.setValidator(QDoubleValidator(self.control))
        layout.addWidget(self.control)

    def value(self):
        if self.valueType == "boolean":
            value = self.control.isChecked()
        else:
            text = self.control.text()
            value = int(text) if self.valueType == "integer" else float(text) if self.valueType == "number" else text
        return validateValue(value, self.valueType)


class VariableDefinitionDialog(QDialog):
    def __init__(self, definition=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("编辑全局变量" if definition else "新建全局变量")
        self.resize(460, 290)
        self.original = deepcopy(definition)
        raw = definition or dict(name="", type="boolean", kind="variable", initialValue=True, lifetime="job")
        layout = QVBoxLayout(self)
        self.form = QFormLayout()
        self.form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        self.name = QLineEdit(raw["name"])
        self.type = QComboBox()
        for key, label in TYPE_LABELS.items():
            self.type.addItem(label, key)
        self.type.setCurrentIndex(self.type.findData(raw["type"]))
        self.kind = QComboBox()
        self.kind.addItem("变量", "variable")
        self.kind.addItem("只读常量", "constant")
        self.kind.setCurrentIndex(self.kind.findData(raw["kind"]))
        self.lifetime = QComboBox()
        self.lifetime.addItem("每次任务初始化", "job")
        self.lifetime.addItem("项目持久化", "persistent")
        self.lifetime.setCurrentIndex(self.lifetime.findData(raw["lifetime"]))
        self.initial = ScalarValueEditor(raw["type"], raw["initialValue"])
        for name, widget in (("名称", self.name), ("类型", self.type), ("属性", self.kind),
                             ("生命周期", self.lifetime), ("初始值", self.initial)):
            self.form.addRow(name, widget)
        if definition:
            self.type.setEnabled(False)
            self.kind.setEnabled(False)
            self.lifetime.setEnabled(False)
            if definition.get("legacyCounterName"):
                self.name.setEnabled(False)
                self.initial.setEnabled(False)
        self.type.currentIndexChanged.connect(self._changeType)
        self.kind.currentIndexChanged.connect(lambda: self.lifetime.setEnabled(not self.original and self.kind.currentData() != "constant"))
        if raw["kind"] == "constant":
            self.lifetime.setEnabled(False)
        layout.addLayout(self.form)
        self.error = WrapLabel("")
        layout.addWidget(self.error)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _changeType(self):
        key = self.type.currentData()
        replacement = ScalarValueEditor(key, {"boolean": True, "integer": 0, "number": 0.0, "string": ""}[key])
        self.form.replaceWidget(self.initial, replacement)
        self.initial.hide()
        self.initial.deleteLater()
        self.initial = replacement

    def definition(self):
        return VariableDefinition.model_validate({
            **(self.original or {}), "name": self.name.text(), "type": self.type.currentData(),
            "kind": self.kind.currentData(), "lifetime": self.lifetime.currentData(),
            "initialValue": self.initial.value(),
        }).model_dump()

    def _accept(self):
        try:
            self.definition()
        except ValueError as error:
            self.error.setText(str(error))
            return
        self.accept()


class GlobalVariablesDialog(QDialog):
    def __init__(self, runtimeClient, getDefinitions, setDefinitions, isRunning, getCurrentJobId, parent=None):
        super().__init__(parent)
        self.runtimeClient = runtimeClient
        self.getDefinitions, self.setDefinitions = getDefinitions, setDefinitions
        self.isRunning, self.getCurrentJobId = isRunning, getCurrentJobId
        self._projectId = ""
        self._generation = 0
        self._worker = None
        self._pendingOperations = deque()
        self._requestOutcome = None
        self._operationStatus = ""
        self._records = {}
        self._jobs = []
        self._closing = False
        self.setWindowTitle("全局变量")
        self.resize(980, 520)
        layout = QVBoxLayout(self)
        self.projectLabel = WrapLabel("")
        layout.addWidget(self.projectLabel)
        jobRow = QHBoxLayout()
        jobRow.addWidget(QLabel("任务"))
        self.jobs = QComboBox()
        self.jobs.addItem("未选择任务", "")
        self.jobs.currentIndexChanged.connect(self._jobChanged)
        jobRow.addWidget(self.jobs, 1)
        layout.addLayout(jobRow)
        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(["名称", "类型", "属性", "生命周期", "初始值", "当前值", "更新时间"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setWordWrap(False)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        layout.addWidget(self.table, 1)
        self.status = WrapLabel("")
        layout.addWidget(self.status)
        row = QHBoxLayout()
        self.buttons = {}
        for key, label, callback, glyph in (
            ("new", "新建", self.newVariable, "plus"), ("edit", "编辑定义", self.editVariable, "sliders-horizontal"),
            ("delete", "删除", self.deleteVariable, "trash-2"), ("set", "设值", self.setValue, "sliders-horizontal"),
            ("reset", "恢复初始值", self.resetValue, "refresh-cw"),
            ("refresh", "刷新", self.refreshVariables, "refresh-cw"), ("close", "关闭", self.close, "x"),
        ):
            if key == "set":
                row.addStretch(1)
                layout.addLayout(row)
                row = QHBoxLayout()
            button = QPushButton(label)
            button.setIcon(icon(glyph))
            button.clicked.connect(callback)
            row.addWidget(button)
            self.buttons[key] = button
        layout.addLayout(row)
        self.table.itemSelectionChanged.connect(self._buttons)
        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self.refreshVariables)

    def bindProject(self, projectId):
        if projectId != self._projectId:
            self._discardPendingOperations()
            self._projectId = projectId
            self._generation += 1
            self._records.clear()
            self._jobs = []
            self.jobs.blockSignals(True)
            self.jobs.clear()
            self.jobs.addItem("未选择任务", "")
            self.jobs.blockSignals(False)
        self.projectLabel.setText("当前项目：" + projectId)
        self._render()

    def showForProject(self, projectId):
        self._closing = False
        self.bindProject(projectId)
        job = self.getCurrentJobId()
        if job and self.jobs.findData(job) < 0:
            self.jobs.addItem(str(job), job)
        self.show()
        self.raise_()
        self.timer.start()
        self.refreshVariables()

    def _jobChanged(self):
        self._discardPendingOperations()
        self._generation += 1
        self._records.clear()
        self._render()
        self.refreshVariables()

    def refreshVariables(self):
        if self._worker is None and not self._closing:
            self._request()

    def _discardPendingOperations(self):
        self._operationStatus = "项目或任务已切换，尚未发送的设值操作已取消。" if self._pendingOperations else ""
        self._pendingOperations.clear()
        self.status.setText(self._operationStatus)

    def _request(self, operation=None, *, generation=None, scope=None):
        currentScope = (self._projectId, self.jobs.currentData() or "")
        if (self._closing or (generation is not None and generation != self._generation)
                or (scope is not None and scope != currentScope)):
            if operation:
                self._operationStatus = "项目、任务或窗口状态已改变，设值未发送，请重新选择变量。"
                self.status.setText(self._operationStatus)
            return
        if self._worker is not None:
            if operation:
                self._pendingOperations.append((self._generation, currentScope, operation))
                self.status.setText("正在等待刷新完成，随后提交设值；仍会检查原值版本。")
                self._buttons()
            return
        project, job = currentScope
        outcome = {"isWrite": operation is not None, "applied": False}
        def request(_):
            if operation:
                operation(project, job)
                outcome["applied"] = True
            return self.runtimeClient.globalVariableState(project, job)
        worker = GlobalCounterWorker(SimpleNamespace(listGlobalCounters=request), "list", project, self._generation)
        worker.setParent(_counterWorkerOwner())
        self._worker = worker
        self._requestOutcome = outcome
        if operation:
            self._operationStatus = "正在提交设值…"
            self.status.setText(self._operationStatus)
        worker.finished.connect(self._finished)
        worker.finished.connect(worker.deleteLater)
        self._buttons()
        worker.start()

    @Slot()  # type: ignore[operator]
    def _finished(self):
        worker, self._worker = self._worker, None
        outcome, self._requestOutcome = self._requestOutcome or {}, None
        if worker is None or self._closing or worker.generation != self._generation:
            if worker is not None and not self._closing:
                self.refreshVariables()
            return
        if worker.error:
            self._records.clear()
            if outcome.get("isWrite"):
                self._operationStatus = ("设值已完成，但刷新失败：" if outcome.get("applied") else "设值失败：") + worker.error.message
                self.status.setText(self._operationStatus)
            else:
                self.status.setText(self._operationStatus + "刷新失败：" + worker.error.message)
        elif worker.result:
            state = worker.result.payload
            self._records = {row["variableId"]: row for row in state["variables"]}
            self._jobs = state["jobs"]
            current = self.jobs.currentData()
            self.jobs.blockSignals(True)
            self.jobs.clear()
            self.jobs.addItem("未选择任务（仅显示初始值）", "")
            for job in self._jobs:
                self.jobs.addItem(job["jobId"] + (" · 已结束" if job["ended"] else " · 运行中"), job["jobId"])
            self.jobs.setCurrentIndex(max(0, self.jobs.findData(current)))
            self.jobs.blockSignals(False)
            if outcome.get("applied"):
                self._operationStatus = "设值成功。"
            self.status.setText(self._operationStatus + "已刷新。定义变更参与工程保存；保存并重新加载 Runtime 后生效。当前值设定不修改工程。")
        self._render()
        while self._pendingOperations and self._worker is None:
            generation, scope, operation = self._pendingOperations.popleft()
            self._request(operation, generation=generation, scope=scope)
        self._buttons()

    def _definitions(self):
        result = {key: {name: row[name] for name in VariableDefinition.model_fields if name in row}
                  for key, row in self._records.items() if row.get("legacyCounterName")}
        result.update(deepcopy(self.getDefinitions()))
        return result

    def selectedId(self):
        item = self.table.item(self.table.currentRow(), 0)
        return item.data(Qt.UserRole) if item else None

    def _render(self):
        selected = self.selectedId()
        scroll = self.table.verticalScrollBar().value()
        self.table.setRowCount(0)
        for key, definition in self._definitions().items():
            row = self.table.rowCount()
            self.table.insertRow(row)
            record = self._records.get(key, {})
            current = _text(record["value"]) if record.get("state") == "current" else "未选择任务" if definition["lifetime"] == "job" and definition["kind"] != "constant" and not self.jobs.currentData() else "未同步"
            updated = record.get("updatedAtMs")
            cells = [definition["name"], TYPE_LABELS[definition["type"]], "只读常量" if definition["kind"] == "constant" else "变量",
                     "不依赖任务" if definition["kind"] == "constant" else "项目持久化" if definition["lifetime"] == "persistent" else "每次任务初始化",
                     _text(definition["initialValue"]), current,
                     datetime.fromtimestamp(updated / 1000).strftime("%Y-%m-%d %H:%M:%S") if updated else ""]
            for col, value in enumerate(cells):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                item.setData(Qt.UserRole, key)
                self.table.setItem(row, col, item)
            if key == selected:
                self.table.selectRow(row)
        self.table.verticalScrollBar().setValue(scroll)
        self._buttons()

    def _buttons(self):
        key = self.selectedId()
        definition = self._definitions().get(key, {})
        record = self._records.get(key, {})
        busy = self._worker is not None or bool(self._pendingOperations)
        running = self.isRunning() or any(not job["ended"] for job in self._jobs)
        for action in ("new", "edit", "delete"):
            self.buttons[action].setEnabled(not running and not busy and (action == "new" or key is not None))
        synced = all(record.get(field) == definition.get(field) for field in VariableDefinition.model_fields)
        writable = not busy and synced and definition.get("kind") == "variable" and record.get("state") == "current"
        if definition.get("lifetime") == "job":
            writable = writable and any(job["jobId"] == self.jobs.currentData() and not job["ended"] for job in self._jobs)
        for action in ("set", "reset"):
            self.buttons[action].setEnabled(writable)
        self.buttons["refresh"].setEnabled(not busy)

    def _applyDefinitions(self, values):
        try:
            if self.isRunning() or any(not job["ended"] for job in self._jobs):
                raise ValueError("任务运行期间不能修改变量定义")
            self.setDefinitions(values)
            self._render()
        except (ValueError, KeyError) as error:
            self.status.setText(str(error))

    def newVariable(self):
        dialog = VariableDefinitionDialog(parent=self)
        if dialog.exec_() == QDialog.Accepted:
            values = self._definitions()
            values[str(uuid4())] = dialog.definition()
            self._applyDefinitions(values)

    def editVariable(self):
        key = self.selectedId()
        values = self._definitions()
        if key not in values:
            return
        dialog = VariableDefinitionDialog(values[key], self)
        if dialog.exec_() == QDialog.Accepted:
            values[key] = dialog.definition()
            self._applyDefinitions(values)

    def deleteVariable(self):
        key = self.selectedId()
        values = self._definitions()
        if key in values and QMessageBox.question(self, "删除变量", values[key]["name"]) == QMessageBox.Yes:
            del values[key]
            self._applyDefinitions(values)

    def setValue(self):
        key = self.selectedId()
        record = self._records.get(key)
        if not record or record.get("revision") is None:
            return
        generation = self._generation
        scope = (self._projectId, self.jobs.currentData() or "")
        dialog = QDialog(self)
        dialog.setWindowTitle("设值：" + record["name"])
        layout = QVBoxLayout(dialog)
        editor = ScalarValueEditor(record["type"], record["value"])
        layout.addWidget(editor)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        layout.addWidget(buttons)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        if dialog.exec_() == QDialog.Accepted:
            try:
                value = editor.value()
            except ValueError as error:
                self.status.setText(str(error))
                return
            self._request(lambda project, job: self.runtimeClient.setGlobalVariable(project, key, value, record["revision"], job),
                          generation=generation, scope=scope)

    def resetValue(self):
        key = self.selectedId()
        record = self._records.get(key)
        if record and record.get("revision") is not None:
            self._request(lambda project, job: self.runtimeClient.resetGlobalVariable(project, key, record["revision"], job))

    def shutdown(self):
        self._closing = True
        self._generation += 1
        self._pendingOperations.clear()
        self.timer.stop()
        if self._worker:
            self._worker.requestStop()

    def closeEvent(self, event):
        self.shutdown()
        super().closeEvent(event)


def _text(value):
    return "true" if value is True else "false" if value is False else str(value)
