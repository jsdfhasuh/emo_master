"""Modeless, fixed-draft workflow debugging with Worker-confirmed controls."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path
import re
import time

from PySide2.QtCore import Qt, QTimer
from PySide2.QtGui import QPixmap
from PySide2.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog,
    QFormLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QPushButton, QPlainTextEdit,
    QSpinBox, QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget)

from emo_master.apps.designer.services.workflow_debug import WorkflowDebugConnection
from emo_master.apps.runtime.operator_debug.assets import VALUE_BYTES, decode
from emo_master.apps.runtime.operator_debug.contracts import digest, parse
from emo_master.core.contracts.port_types import normalizePortType
from .debug_values import InputRow, ValueTree, tool
from .operator_debug_window import Completion
from .param_form import SchemaParamForm
from .widgets import PreviewLabel, WrapLabel, scrollContent


def setSummaryLabel(label, text):
    full = str(text)
    summary = re.sub(r"\S{40,}", lambda match: match.group(0)[:32] + "…", full)
    label.setText(summary if len(summary) <= 180 else summary[:177] + "…")
    label.setToolTip(full)


class WorkflowDebugWindow(QDialog):
    def __init__(self, client, payload, workflowId, parent=None, *, valid=lambda: True, navigate=lambda *_: None, currentDraft=None):
        super().__init__(parent)
        self.connection = WorkflowDebugConnection(client)
        self.payload, self.workflowId = deepcopy(payload), workflowId
        self.valid, self.navigate = valid, navigate
        self.currentDraft = currentDraft
        self.startupDigest = digest(payload)
        self.draftChanged = False
        self.draftCheckedAt = 0
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="workflow-debug-ui")
        self.controls = ThreadPoolExecutor(max_workers=1, thread_name_prefix="workflow-debug-close")
        self.bridge = Completion(self)
        self.bridge.done.connect(self.completed, Qt.QueuedConnection)
        self.busy = self.closing = self.disposed = False
        self.polling = self.refreshPending = False
        self.closeSent = False
        self.state, self.session = "STARTING", {}
        self.sequence, self.pauseSequence = 0, 0
        self.current = self.displayed = None
        self.variablesFingerprint = ""
        self.pendingSnapshot = self.pendingAsset = ""
        self.capability = {}
        self.rows, self.breakpointRows = {}, []
        self.pixmap = None
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setWindowTitle("流程调试 - " + payload["workflows"][workflowId].get("name", workflowId))
        self.resize(1040, 720)
        layout = QVBoxLayout(self)
        self.status = WrapLabel("连接 Runtime")
        layout.addWidget(self.status)
        self.position = WrapLabel("启动快照 " + digest(payload)[:12])
        layout.addWidget(self.position)
        bar = QHBoxLayout()
        self.startButton = QPushButton("启动调试")
        self.startButton.clicked.connect(self.start)
        bar.addWidget(self.startButton)
        self.buttons = {}
        for key, symbol, title in (("continue", "play", "继续"), ("pause", "square", "请求暂停"),
                ("into", "git-branch", "单步进入"), ("over", "panel-left-close", "单步跳过"),
                ("out", "panel-left-open", "单步跳出"), ("trial", "sliders-horizontal", "暂停节点独立试运行")):
            callback = self.trial if key == "trial" else lambda _=False, key=key: self.command(key)
            self.buttons[key] = tool(self, symbol, title, callback)
            bar.addWidget(self.buttons[key])
        self.locate = tool(self, "search", "定位当前暂停节点", self.locateCurrent)
        bar.addWidget(self.locate)
        bar.addStretch()
        self.end = QPushButton("停止并结束")
        self.end.clicked.connect(self.close)
        bar.addWidget(self.end)
        layout.addLayout(bar)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, 1)
        self.inputBody = QWidget()
        form = QFormLayout(self.inputBody)
        form.setRowWrapPolicy(QFormLayout.WrapLongRows)
        for port, spec in payload["workflows"][workflowId].get("inputs", {}).items():
            row = InputRow(normalizePortType(spec))
            row.layout().addWidget(tool(row, "folder-open", "选择 PNG / JSON 文件", lambda _=False, port=port: self.file(port)))
            self.rows[port] = row
            label = QLabel(port + " : " + normalizePortType(spec))
            label.setWordWrap(True)
            form.addRow(label, row)
        self.tabs.addTab(scrollContent(self.inputBody), "根流程输入")
        data = QWidget()
        dataLayout = QVBoxLayout(data)
        selectors = QHBoxLayout()
        self.history, self.ports = QComboBox(), QComboBox()
        for combo in (self.history, self.ports):
            combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
            combo.setMinimumContentsLength(12)
        self.history.currentIndexChanged.connect(self.selectSnapshot)
        self.ports.currentIndexChanged.connect(self.selectPort)
        selectors.addWidget(self.history, 1)
        selectors.addWidget(self.ports, 1)
        dataLayout.addLayout(selectors)
        self.dataLabel = WrapLabel("")
        dataLayout.addWidget(self.dataLabel)
        self.image, self.data = PreviewLabel(), ValueTree()
        self.image.hide()
        dataLayout.addWidget(self.image, 1)
        dataLayout.addWidget(self.data, 1)
        self.tabs.addTab(data, "调用数据")
        self.stack, self.variables = ValueTree(), ValueTree()
        self.tabs.addTab(self.stack, "调用栈")
        self.tabs.addTab(self.variables, "变量监视")
        points = QWidget()
        pointsLayout = QVBoxLayout(points)
        self.breakpoints = QTableWidget(0, 4)
        self.breakpoints.setHorizontalHeaderLabels(["启用", "流程 / 节点", "条件", "命中起点"])
        self.breakpoints.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.breakpoints.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.breakpoints.setColumnWidth(0, 55)
        self.breakpoints.setColumnWidth(3, 90)
        for key, workflow in payload["workflows"].items():
            for node in workflow["nodes"]:
                if node.get("kind", "operator") in {"workflow_input", "workflow_output"}:
                    continue
                row = self.breakpoints.rowCount()
                self.breakpoints.insertRow(row)
                enabled, condition, hits = QCheckBox(), QLineEdit(), QSpinBox()
                hits.setRange(1, 1000000)
                self.breakpoints.setCellWidget(row, 0, enabled)
                title = QTableWidgetItem(key + " / " + node["nodeId"])
                title.setFlags(title.flags() & ~Qt.ItemIsEditable)
                self.breakpoints.setItem(row, 1, title)
                self.breakpoints.setCellWidget(row, 2, condition)
                self.breakpoints.setCellWidget(row, 3, hits)
                self.breakpointRows.append((key, node["nodeId"], enabled, condition, hits))
        pointsLayout.addWidget(self.breakpoints)
        pointBar = QHBoxLayout()
        self.applyBreakpoints = QPushButton("更新断点")
        self.applyBreakpoints.clicked.connect(self.updateBreakpoints)
        pointBar.addWidget(self.applyBreakpoints)
        self.runTo = QPushButton("运行到所选节点")
        self.runTo.clicked.connect(self.runToSelected)
        pointBar.addWidget(self.runTo)
        pointsLayout.addLayout(pointBar)
        self.tabs.addTab(points, "断点")
        self.logs = QPlainTextEdit()
        self.logs.setReadOnly(True)
        self.logs.setMaximumBlockCount(1000)
        self.tabs.addTab(self.logs, "日志")
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.poll)
        self.timer.start(250)
        self.submit("open", lambda: self.connection.openWorkflow(self.payload, workflowId))

    def submit(self, name, work, control=False):
        if self.disposed or (self.busy and not control) or (self.closing and name != "close"):
            return False
        if not control and ((name == "poll" and self.polling) or (name != "poll" and self.refreshPending)):
            return False
        if name == "poll":
            self.polling = True
        elif not control:
            self.busy = True
        if name in {"start", "command"}:
            self.refreshPending = True
        bridge = self.bridge
        def done(future):
            try:
                value, error = future.result(), None
            except Exception as failure:
                value, error = None, failure
            try:
                bridge.done.emit(name, value, error)
            except RuntimeError:
                pass
        (self.controls if control else self.pool).submit(work).add_done_callback(done)
        self.refresh()
        return True

    def refresh(self):
        idle = not self.busy and not self.refreshPending and not self.closing and not self.connection.uncertain
        current = self.current is not None and self.current.get("pauseSequence") == self.pauseSequence
        paused = idle and self.state == "PAUSED" and current and not self.session.get("flow", {}).get("trialRunning")
        self.startButton.setEnabled(idle and self.state == "READY")
        self.inputBody.setEnabled(idle and self.state == "READY")
        for key, button in self.buttons.items():
            button.setEnabled(paused if key != "pause" else idle and self.state == "RUNNING")
        self.buttons["trial"].setEnabled(paused and self.current is not None and self.current.get("phase") == "node.before"
                                          and bool(self.trialOperator()))
        self.runTo.setEnabled(paused)
        self.applyBreakpoints.setEnabled(idle and self.state in {"PAUSED", "RUNNING", "PAUSE_REQUESTED"})
        self.locate.setEnabled(current and not self.closing)
        self.end.setEnabled(not self.closing)

    def start(self):
        if not self.startButton.isEnabled():
            return
        try:
            values = {key: row.wire() for key, row in self.rows.items() if row.wire() is not None}
            self.submit("start", lambda: self.connection.start(values))
        except Exception as error:
            self.error(error)

    def command(self, action, **fields):
        sequence = self.pauseSequence
        self.submit("command", lambda: self.connection.control(action, sequence, **fields))

    def updateBreakpoints(self):
        rows = [dict(workflowId=workflow, nodeId=node, enabled=True, condition=condition.text(), hitCount=hits.value())
                for workflow, node, enabled, condition, hits in self.breakpointRows if enabled.isChecked()]
        self.command("breakpoints", breakpoints=rows)

    def runToSelected(self):
        row = self.breakpoints.currentRow()
        if 0 <= row < len(self.breakpointRows):
            workflow, node, *_ = self.breakpointRows[row]
            self.command("runTo", workflowId=workflow, nodeId=node)

    def trialOperator(self):
        if not self.current:
            return None
        identity = self.current["identity"]
        node = next((node for node in self.payload["workflows"][identity["workflowId"]]["nodes"]
                     if node["nodeId"] == self.current["nodeId"]), {})
        return next((row for row in self.capability.get("operators", [])
                     if row["operatorId"] == node.get("operatorId") and row["supported"] and not row.get("stateful", True)), None)

    def trial(self):
        operator = self.trialOperator()
        if not operator or not self.buttons["trial"].isEnabled():
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("独立试运行参数")
        dialog.resize(600, 440)
        layout = QVBoxLayout(dialog)
        form = SchemaParamForm()
        form.setSchema(operator["paramSchema"], self.current["params"])
        layout.addWidget(scrollContent(form), 1)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        sequence = self.pauseSequence
        self.timer.stop()
        answer = dialog.exec_()
        if not self.closing:
            self.timer.start(250)
        if answer == QDialog.Accepted:
            try:
                params = form.getValues()
                self.submit("command", lambda: self.connection.control("trial", sequence, params=params))
            except Exception as error:
                self.error(error)

    def file(self, port):
        path, _ = QFileDialog.getOpenFileName(self, "选择完整输入", "", "PNG / JSON (*.png *.json)")
        if not path:
            return
        def upload():
            file = Path(path)
            with file.open("rb") as stream:
                raw = stream.read(VALUE_BYTES + 1)
            mime = "image/png" if file.suffix.lower() == ".png" else "application/json"
            decode(raw, mime)
            return self.connection.upload(raw, mime, dict(kind="upload", filename=file.name))
        self.submit("file:" + port, upload)

    def poll(self):
        if self.closing or self.disposed:
            return
        if not self.valid() or not self.connection.attached():
            self.close()
            return
        if self.currentDraft is not None and time.monotonic() - self.draftCheckedAt >= 1:
            self.draftCheckedAt = time.monotonic()
            try:
                self.draftChanged = digest(self.currentDraft()) != self.startupDigest
            except Exception:
                self.draftChanged = True
        if not self.busy and not self.polling and self.capability:
            sequence = self.sequence
            self.submit("poll", lambda: self.connection.flowSnapshot(sequence))

    def error(self, error):
        message = str(getattr(error, "code", "E_DEBUG_INPUT")) + ": " + str(error)
        setSummaryLabel(self.status, message)
        self.logs.appendPlainText(message)

    def completed(self, name, value, error):
        if self.disposed:
            return
        if name == "poll":
            self.polling = False
        elif name != "close":
            self.busy = False
        if self.closing and name != "close":
            self.beginClose()
            return
        if error is not None:
            self.error(error)
            if name == "open":
                self.state = "FAULTED"
            if name == "close":
                self.closing = False
                self.closeSent = False
                self.timer.start(250)
        elif name == "open":
            self.capability = value["capability"]
            self.state = value["session"]["state"]
        elif name == "poll":
            # A poll queued before a command must not release its ACK/state guard.
            if not self.busy:
                self.refreshPending = False
            self.session, self.state = value["session"], value["session"]["state"]
            flow = self.session["flow"]
            self.status.setText(f"{self.state} | 暂停序号 {flow.get('pauseSequence', 0)} | {self.session.get('code', '')} {self.session.get('message', '')}")
            self.status.setText(self.status.text() + " | 快照 " + self.session["draftDigest"][:8]
                               + (" | 画布已更改，当前调试仍使用启动配置" if self.draftChanged else ""))
            if flow.get("terminal", {}).get("message"):
                self.status.setText(self.status.text() + " | " + flow["terminal"]["message"])
            if value["leaseError"]:
                self.status.setText("续租失败：" + value["leaseError"])
            self.sequence = value["events"]["nextSequence"]
            for event in value["events"]["events"]:
                self.logs.appendPlainText(str(event))
            if flow.get("trialError"):
                self.status.setText(self.status.text() + " | 试运行失败：" + str(flow["trialError"]))
            setSummaryLabel(self.status, self.status.text())
            fingerprint = digest(self.session["variables"])
            if fingerprint != self.variablesFingerprint:
                self.variables.setValue(self.session["variables"])
                self.variablesFingerprint = fingerprint
            selected = self.history.currentData()
            if flow.get("pauseSequence", 0) > self.pauseSequence:
                self.pauseSequence = flow["pauseSequence"]
                self.current = None
                self.stack.clear()
                setSummaryLabel(self.position, "读取已确认的暂停位置…")
                selected = flow.get("current")
                self.tabs.setCurrentIndex(1)
            elif flow.get("trial") and flow.get("trial") != getattr(self, "lastTrial", None):
                selected = self.lastTrial = flow["trial"]
            elif self.state in {"SUCCEEDED", "FAILED"} and flow.get("lastOutput") != getattr(self, "lastOutput", None):
                selected = self.lastOutput = flow.get("lastOutput")
            oldIds = [self.history.itemData(index) for index in range(self.history.count())]
            ids = [row["snapshotId"] for row in self.session["snapshots"]]
            self.history.blockSignals(True)
            if oldIds != ids:
                self.history.clear()
                for row in self.session["snapshots"]:
                    identity = row["identity"]
                    self.history.addItem(f"{identity.get('workflowId', '')} / {row['nodeId']} / {row['phase']} / {identity.get('iterationPath', [])}", row["snapshotId"])
            # Terminal state may arrive after its result is already in the list.
            self.history.setCurrentIndex(self.history.findData(selected))
            self.history.blockSignals(False)
            if selected and (not self.displayed or self.displayed["snapshotId"] != selected):
                self.pendingSnapshot = selected
        elif name.startswith("snapshot:"):
            if value["snapshotId"] == self.session.get("flow", {}).get("current"):
                self.current = value
                self.stack.setValue(value["stack"])
                identity = value["identity"]
                setSummaryLabel(self.position, f"{identity['workflowId']} / {value['nodeId']} / {value['phase']} | 调用 {identity['nodeRunId'][:12]} | 轮次 {identity['iterationPath']}")
            if self.history.currentData() == value["snapshotId"]:
                self.display(value)
        elif name.startswith("asset:"):
            selected = self.ports.currentData()
            if selected and selected[1] == name[6:]:
                if value["asset"]["mime"] == "image/png":
                    self.pixmap = QPixmap()
                    self.pixmap.loadFromData(value["content"], "PNG")
                    self.scaleImage()
                else:
                    self.data.setValue(parse(value["content"].decode(), VALUE_BYTES))
        elif name.startswith("file:"):
            self.rows[name[5:]].setReference(value, value["assetRef"])
        elif name == "close":
            self.disposed = True
            self.pool.shutdown(wait=False)
            self.controls.shutdown(wait=False)
            self.close()
            return
        self.refresh()
        if name in {"start", "command"}:
            # Keep controls locked through ACK until a subsequent read confirms state.
            self.poll()
        self.drain()

    def selectSnapshot(self, *_):
        self.pendingSnapshot = self.history.currentData() or ""
        self.drain()

    def drain(self):
        if self.busy or self.polling or self.refreshPending or self.closing or self.disposed:
            return
        if self.pendingSnapshot:
            key, self.pendingSnapshot = self.pendingSnapshot, ""
            self.submit("snapshot:" + key, lambda: self.connection.call("GetWorkflowDebugSnapshot", execution_id=key))
        elif self.pendingAsset:
            key, self.pendingAsset = self.pendingAsset, ""
            self.submit("asset:" + key, lambda: self.connection.download(key))

    def display(self, value):
        self.displayed = value
        setSummaryLabel(self.dataLabel, f"{value.get('snapshotKind', '')} | {value.get('phase', '')} | {value['snapshotId'][:12]} | {value.get('conditionError', '')}")
        self.ports.blockSignals(True)
        self.ports.clear()
        for group in ("inputs", "outputs"):
            for key in value.get(group, {}):
                self.ports.addItem(group + " / " + key, (group, "", key, ""))
            for key, asset in value.get(group + "Assets", {}).items():
                self.ports.addItem(group + " / " + key, (group, asset["assetId"], key, asset["mime"]))
        self.ports.addItem("参数与身份", ("detail", "", "", ""))
        self.ports.blockSignals(False)
        self.selectPort()

    def selectPort(self, *_):
        self.pendingAsset = ""
        self.pixmap = None
        self.image.clear()
        self.data.clear()
        selected = self.ports.currentData()
        if not selected or not self.displayed:
            return
        group, asset, key, mime = selected
        self.image.setVisible(mime == "image/png")
        self.data.setVisible(mime != "image/png")
        if asset:
            self.pendingAsset = asset
            self.drain()
        else:
            self.data.setValue(self.displayed if group == "detail" else self.displayed[group][key])

    def locateCurrent(self):
        if self.current:
            self.navigate(self.current["identity"]["workflowId"], self.current["nodeId"])

    def scaleImage(self):
        if self.pixmap is not None:
            self.image.setPixmap(self.pixmap.scaled(self.image.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.scaleImage()

    def closeEvent(self, event):
        if self.disposed:
            event.accept()
            return
        event.ignore()
        if self.closing:
            return
        self.closing = True
        self.timer.stop()
        self.status.setText("停止中，等待 Runtime 确认释放资源")
        self.beginClose()

    def beginClose(self):
        if self.closeSent:
            return
        control = "session_id" in self.connection.identity
        if not self.busy or control:
            self.closeSent = self.submit("close", self.connection.close, control=control)
