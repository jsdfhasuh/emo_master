"""Modeless operator debugging; all Runtime calls stay off the Qt thread."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path

from PySide2.QtCore import QObject, Qt, QTimer, Signal
from PySide2.QtGui import QPixmap
from PySide2.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QFileDialog, QFormLayout,
    QHBoxLayout, QHeaderView, QLabel, QMessageBox, QPushButton, QPlainTextEdit,
    QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget,
)

from emo_master.apps.designer.services.operator_debug import DebugConnection
from emo_master.apps.runtime.operator_debug.assets import VALUE_BYTES, decode
from emo_master.apps.runtime.operator_debug.contracts import digest, parse
from emo_master.apps.runtime.operator_debug.data import companionPort
from emo_master.core.contracts.port_types import normalizePortType
from emo_master.core.project.global_variables import definitions, variablePorts
from .debug_values import InputRow, ValueTree, tool
from .debug_artifact_tools import DebugActionFeedback, DebugArtifactTools
from .icon_map import icon
from .widgets import PreviewLabel, WrapLabel, scrollContent
from .plc_presentation import ADVANCED_FIELDS, NUMBER_TYPES, PORT_TITLES, STATES, frameTitle, plcLog, plcSchema


class Completion(QObject):
    done = Signal(str, object, object)


class OperatorDebugWindow(QDialog):
    cleanupFailed = Signal()

    def __init__(self, editor, valid=lambda: True):
        super().__init__(editor)
        self.editor, self.valid = editor, valid
        self.isPlc = editor.context.operatorId in {"communication.plc.slmp_read", "communication.plc.slmp_write"}
        self.logEvents = []
        self.connection = DebugConnection(editor.context._runtimeClient)
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="designer-debug")
        self.controls = ThreadPoolExecutor(max_workers=1, thread_name_prefix="designer-debug-control")
        self.bridge = Completion(self)
        self.bridge.done.connect(self.completed, Qt.QueuedConnection)
        self.busy = False
        self.polling = False
        self.commandVersion = 0
        self.pollCommandVersion = 0
        self.closing = False
        self.closeSent = False
        self.disposed = False
        self.state = "STARTING"
        self.executionId = ""
        self.executionStatus = ""
        self.sequence = 0
        self.records = []
        self.rows = {}
        self.sources = []
        self.capability = None
        self.fingerprint = ""
        self.detailFingerprint = ""
        self.pixmap = None
        self.pendingAsset = ""
        self.bindings = deepcopy(editor.context.collectVariableBindings())
        self.variableDefinitions = self.currentVariables()
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setWindowTitle("算子调试 - " + editor.windowTitle())
        if self.isPlc:
            self.setWindowTitle("当前算子调试 - " + editor.windowTitle())
        self.resize(900, 660)
        layout = QVBoxLayout(self)
        self.status = WrapLabel("连接 Runtime…")
        layout.addWidget(self.status)
        self.resourceNotice = WrapLabel("文件、文件夹、模型必须位于 Runtime 电脑并使用绝对路径，不自动上传。"
            "结果写入与 SQLite 写入仅使用调试临时目录/隔离库，关闭会话后清理。"
            "批量图片保留进度；会话坐标首次执行冻结，重置后重新读取。")
        layout.addWidget(self.resourceNotice)
        self.resourceNotice.hide()
        self.actionFeedback = DebugActionFeedback(self)
        self.actionStatus = self.actionFeedback.label
        layout.addWidget(self.actionStatus)
        toolbar = QHBoxLayout()
        self.run = QPushButton("单步执行")
        if self.isPlc:
            self.run.setText("执行读取" if editor.context.operatorId.endswith("slmp_read") else "执行写入")
            self.run.setToolTip("使用参数页当前配置，仅执行当前算子；不受通信测试页的手动写入锁控制。")
        self.run.setIcon(icon("play"))
        self.run.clicked.connect(self.execute)
        toolbar.addWidget(self.run)
        self.cancel = tool(self, "square", "取消本次执行", self.cancelExecution)
        self.reset = tool(self, "refresh-cw", "重置会话状态和输入", self.resetSession)
        self.copyVariables = tool(self, "upload", "复制生产持久变量当前值", self.copyState)
        toolbar.addWidget(self.cancel)
        toolbar.addWidget(self.reset)
        toolbar.addWidget(self.copyVariables)
        toolbar.addStretch()
        self.end = QPushButton("结束调试")
        self.end.clicked.connect(self.close)
        toolbar.addWidget(self.end)
        layout.addLayout(toolbar)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, 1)
        self.inputBody = QWidget()
        inputLayout = QVBoxLayout(self.inputBody)
        self.inputForm = QFormLayout()
        inputLayout.addLayout(self.inputForm)
        inputLayout.addStretch()
        self.inputForm.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        self.inputForm.setRowWrapPolicy(QFormLayout.WrapLongRows)
        self.tabs.addTab(scrollContent(self.inputBody), "输入")
        result = QWidget()
        resultLayout = QVBoxLayout(result)
        resultBar = QHBoxLayout()
        self.versions = QComboBox()
        self.versions.currentIndexChanged.connect(self.showResult)
        resultBar.addWidget(self.versions, 1)
        self.outputPorts = QComboBox()
        self.outputPorts.currentIndexChanged.connect(self.showOutput)
        resultBar.addWidget(self.outputPorts, 1)
        self.artifacts = DebugArtifactTools(self, lambda: dict(projectId=self.editor.key.projectId,
            workflowId=self.editor.key.workflowId, nodeId=self.editor.key.nodeId, operatorId=self.editor.context.operatorId),
            self.editor.collectParams, self.exportSelection)
        # Static tools belong to the input page even before open finishes or
        # when the operator has no input ports. Dynamic rows never own them.
        inputLayout.insertWidget(0, self.artifacts.panel)
        resultBar.addWidget(self.artifacts.export)
        resultBar.addWidget(self.artifacts.raw)
        resultLayout.addLayout(resultBar)
        self.resultLabel = WrapLabel("")
        resultLayout.addWidget(self.resultLabel)
        self.plcSummary = WrapLabel("")
        self.plcSummary.setVisible(self.isPlc)
        resultLayout.addWidget(self.plcSummary)
        self.rawOutput = QCheckBox("原始输出")
        self.rawOutput.setVisible(self.isPlc)
        self.rawOutput.toggled.connect(self.showOutput)
        resultLayout.addWidget(self.rawOutput)
        self.plcResults = self.resultTable(["地址", "数值", "数值类型"])
        self.plcResults.hide()
        resultLayout.addWidget(self.plcResults, 1)
        self.image = PreviewLabel()
        self.image.hide()
        resultLayout.addWidget(self.image, 1)
        self.outputTree = ValueTree()
        self.outputTree.setMinimumHeight(80)
        resultLayout.addWidget(self.outputTree, 1)
        self.tabs.addTab(result, "结果")
        self.details = ValueTree()
        if self.isPlc:
            detailPage = QWidget()
            detailLayout = QVBoxLayout(detailPage)
            self.parameterSummary = self.resultTable(["参数", "本次配置"])
            detailLayout.addWidget(self.parameterSummary, 1)
            self.rawDetails = QCheckBox("技术详情")
            self.rawDetails.toggled.connect(self.details.setVisible)
            detailLayout.addWidget(self.rawDetails)
            detailLayout.addWidget(self.details, 1)
            self.details.hide()
            self.tabs.addTab(detailPage, "参数与状态")
        else:
            self.tabs.addTab(self.details, "参数与状态")
        self.logs = QPlainTextEdit()
        self.logs.setReadOnly(True)
        self.logs.setMaximumBlockCount(1000)
        if self.isPlc:
            logPage = QWidget()
            logLayout = QVBoxLayout(logPage)
            self.rawLogs = QCheckBox("原始事件")
            self.rawLogs.toggled.connect(self.renderLogs)
            logLayout.addWidget(self.rawLogs)
            logLayout.addWidget(self.logs)
            self.tabs.addTab(logPage, "日志")
        else:
            self.tabs.addTab(self.logs, "日志")
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.poll)
        self.timer.start(250)
        self.refreshControls()
        try:
            payload = editor.context._variablePreviewProject()
            node = next(row for row in payload["workflows"][editor.key.workflowId]["nodes"] if row["nodeId"] == editor.key.nodeId)
            node["params"] = editor.collectParams()
            self.submit("open", lambda: self.connection.open(payload, editor.key, editor.context.operatorId))
        except Exception as error:
            self.error(error)

    def submit(self, name, work, *, control=False):
        if self.disposed or (self.busy and not control) or (name == 'poll' and self.polling):
            return False
        if name == 'poll':
            self.polling = True
            self.pollCommandVersion = self.commandVersion
        elif not control:
            self.busy = True
        if name in {'execute', 'reset', 'copy', 'cancel', 'close'}:
            self.commandVersion += 1
        bridge = self.bridge
        def finished(future):
            try:
                value, error = future.result(), None
            except Exception as failure:
                value, error = None, failure
            try:
                bridge.done.emit(name, value, error)
            except RuntimeError:
                pass
        (self.controls if control else self.pool).submit(work).add_done_callback(finished)
        self.refreshControls()
        return True

    def error(self, error, *, action=None):
        from .debug_errors import explainDebugError
        code = getattr(error, 'code', 'E_DEBUG_INPUT')
        message = f"{code}: " + explainDebugError(code, str(error), self.editor.context.operatorId)
        if action is None:
            self.status.setText(message)
        else:
            self.actionFeedback.failure(action, message)
        self.appendLog(message)

    @staticmethod
    def resultTable(headers):
        table = QTableWidget(0, len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.verticalHeader().hide()
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        return table

    def stateTitle(self, state):
        return STATES.get(state, state) if self.isPlc else state

    def appendLog(self, event):
        if self.isPlc:
            self.logEvents.append(event)
            del self.logEvents[:-1000]
            text = plcLog(event) if isinstance(event, dict) and not self.rawLogs.isChecked() else str(event)
        else:
            text = str(event)
        self.logs.appendPlainText(text)

    def renderLogs(self):
        self.logs.setPlainText("\n".join(
            plcLog(event) if isinstance(event, dict) and not self.rawLogs.isChecked() else str(event)
            for event in self.logEvents))

    def selectedOutputPort(self):
        return self.outputPorts.currentData(Qt.UserRole + 1) or self.outputPorts.currentText()

    def showPlcParameters(self, record=None):
        if not self.isPlc:
            return
        try:
            params = (record.get("resolvedParams") or record.get("rawParams", {})) if record else self.editor.collectParams()
        except ValueError:
            params = {"草稿参数": "输入尚未完成"}
        schema = plcSchema(self.editor.context.paramSchema).get("properties", {})
        rows = [("调试会话", self.stateTitle(self.state)), ("所选通信格式", frameTitle(params))]
        if record:
            rows.append(("执行结果", self.stateTitle(record["status"])))
        for key in dict.fromkeys([*schema, *params]):
            if key not in params or key in (*ADVANCED_FIELDS, "frameEncoding"):
                continue
            value = params[key]
            rows.append((schema.get(key, {}).get("title", key), NUMBER_TYPES.get(value, value) if key == "dataType" else value))
        self.parameterSummary.setRowCount(len(rows))
        for row, pair in enumerate(rows):
            for column, value in enumerate(pair):
                self.parameterSummary.setItem(row, column, QTableWidgetItem(str(value)))

    def refreshControls(self):
        ready = (self.state == "READY" and not self.busy and not self.closing
                 and not self.connection.uncertain and self.artifacts.pendingInput is None)
        self.run.setEnabled(ready)
        self.reset.setEnabled(ready)
        self.copyVariables.setEnabled(ready)
        self.inputBody.setEnabled(ready)
        self.cancel.setEnabled(bool(self.executionId) and self.state == "RUNNING" and not self.closing)
        self.end.setEnabled(not self.closing)
        self.artifacts.refresh(ready, not self.busy and not self.closing)

    def currentVariables(self):
        variables = self.editor.context.variableDefinitions
        return deepcopy(variables() if callable(variables) else variables or {})

    def configuration(self, params, inputs):
        return digest([params, inputs, self.editor.context.collectVariableBindings(), self.currentVariables()])

    def buildInputs(self):
        params = self.editor.collectParams()
        variables = self.currentVariables()
        dynamic = variablePorts(self.editor.context.operatorId, params, definitions(variables))
        ports = dynamic[0] if dynamic is not None else self.capability["inputPorts"]
        from emo_master.apps.runtime.operator_debug.contracts import debugInputPorts
        try:
            ports = debugInputPorts(self.editor.context.operatorId, params, ports)
        except ValueError:
            # Incomplete editor configurations remain editable; execution reports
            # their validation error instead of faulting an async UI callback.
            pass
        if getattr(self, "debugInputSpecs", None) == ports:
            return
        self.debugInputSpecs = deepcopy(ports)
        while self.inputForm.rowCount():
            self.inputForm.removeRow(0)
        self.rows = {}
        for port, spec in ports.items():
            row = InputRow(normalizePortType(spec))
            self.rows[port] = row
            row.layout().addWidget(tool(row, "folder-open", "选择 PNG / JSON 文件", lambda _checked=False, port=port: self.selectFile(port)))
            row.layout().addWidget(tool(row, "logs", "选择历史完整数据 / 调试输出", lambda _checked=False, port=port: self.selectSource(port)))
            title = spec.get("title", port) if isinstance(spec, dict) else port
            label = QLabel(title + " : " + normalizePortType(spec))
            label.setWordWrap(True)
            self.inputForm.addRow(label, row)

    def inputValues(self):
        result = {}
        for port, row in self.rows.items():
            value = row.wire()
            if value is not None:
                result[port] = value
        return result

    def execute(self):
        if not self.run.isEnabled() or not self.valid():
            return
        try:
            if self.editor.context.collectVariableBindings() != self.bindings or self.currentVariables() != self.variableDefinitions:
                raise ValueError("变量定义或绑定已变化，请结束并重新打开调试")
            self.buildInputs()
            params, inputs = deepcopy(self.editor.collectParams()), self.inputValues()
            warning = (self.capability or {}).get("executionWarning", "")
            if warning:
                answer = QMessageBox.warning(self, "确认本次真实操作", warning + "\n\n仅确认本次执行；下次执行需重新确认。",
                                             QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
                if answer != QMessageBox.Yes:
                    return
            self.fingerprint = self.configuration(params, inputs)
            self.executionId = ""
            self.pixmap = None
            self.image.clear()
            self.outputTree.clear()
            self.executionStatus = "正在准备输入与校验参数；尚未取得执行确认"
            self.updateResultLabel()
            invoke = (lambda: self.connection.execute(params, inputs, confirmSideEffects=True)) if warning else (
                lambda: self.connection.execute(params, inputs))
            if not self.submit("execute", invoke):
                raise ValueError("当前有操作处理中，请稍后重试")
        except Exception as error:
            self.executionStatus = "本次未启动执行"
            self.updateResultLabel()
            self.error(error, action="execute")

    def selectFile(self, port):
        context = self.artifacts.inputContext()
        path, _ = QFileDialog.getOpenFileName(self, "选择完整输入", "", "PNG / JSON (*.png *.json)")
        if not path:
            return
        def upload():
            file = Path(path)
            if file.stat().st_size > VALUE_BYTES:
                raise ValueError("文件超过 64 MiB")
            with file.open("rb") as stream:
                raw = stream.read(VALUE_BYTES+1)
            mime = "image/png" if file.suffix.lower() == ".png" else "application/json"
            decode(raw, mime)
            return self.connection.upload(raw, mime, dict(kind="upload", filename=file.name))
        self.artifacts.submitInput("file:" + port, upload, context, path)

    def selectSource(self, port, offset=0):
        def listing():
            value = self.connection.call("ListOperatorDebugSources", limit=100, offset=offset)
            return dict(value, offset=offset)
        self.submit("sources:" + port, listing)

    def sourceDialog(self, port, listing):
        dialog = QDialog(self)
        dialog.setWindowTitle("选择完整数据源 - " + port)
        dialog.resize(720, 220)
        layout = QVBoxLayout(dialog)
        choice = QComboBox()
        choice.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        choice.setMinimumContentsLength(30)
        for record in self.records:
            if record["status"] != "SUCCEEDED":
                continue
            for output in list(record.get("outputs", {})) + list(record.get("outputAssets", {})):
                choice.addItem(f"调试 {record['executionId'][:8]} / {output}",
                    dict(executionId=record["executionId"], port=output))
        for source in listing["sources"]:
            label = f"{source['jobId']} / {source['workflowRunId']} / {source['nodeRunId']} / {source['iterationPath']} / {source['port']} / r{source['projectRevision']} / {source['createdAtMs']}"
            choice.addItem(label, source)
            choice.setItemData(choice.count()-1, label, Qt.ToolTipRole)
        layout.addWidget(choice)
        detail = ValueTree()
        layout.addWidget(detail)
        choice.currentIndexChanged.connect(lambda: detail.setValue(choice.currentData()))
        detail.setValue(choice.currentData())
        button = QPushButton("选择")
        button.setEnabled(choice.count() > 0)
        button.clicked.connect(dialog.accept)
        layout.addWidget(button)
        pages = QHBoxLayout()
        previous = tool(dialog, "panel-left-close", "上一页历史来源", lambda: dialog.done(2))
        following = tool(dialog, "panel-left-open", "下一页历史来源", lambda: dialog.done(3))
        previous.setEnabled(listing["offset"] > 0)
        following.setEnabled(len(listing["sources"]) == 100)
        pages.addWidget(previous)
        pages.addWidget(following)
        layout.addLayout(pages)
        self.timer.stop()
        answer = dialog.exec_()
        if not self.closing and not self.disposed:
            self.timer.start(250)
        if answer in {2, 3}:
            self.selectSource(port, max(0, listing["offset"]-100) if answer == 2 else listing["nextOffset"])
            return
        if answer != QDialog.Accepted:
            return
        value = choice.currentData()
        companion = companionPort(port, self.rows)
        includeCompanion = companion in self.rows
        if "sourceId" in value:
            def importSource():
                targets = {port: self.connection.mutation("ImportOperatorDebugSource", asset_id=value["sourceId"])["asset"]}
                if includeCompanion and value.get("companionId"):
                    targets[companion] = self.connection.mutation("ImportOperatorDebugSource", asset_id=value["companionId"])["asset"]
                return targets
            self.submit("import:" + port, importSource)
        else:
            self.rows[port].setReference(value, choice.currentText())
            record = next(row for row in self.records if row["executionId"] == value["executionId"])
            outputs = dict(record.get("outputs", {}), **record.get("outputAssets", {}))
            sourceCompanion = companionPort(value["port"], outputs)
            if companion in self.rows and sourceCompanion in outputs:
                self.rows[companion].setReference(dict(executionId=value["executionId"], port=sourceCompanion),
                                                  value["executionId"] + " / " + sourceCompanion)

    def cancelExecution(self):
        if self.cancel.isEnabled():
            target = self.executionId
            self.state = "CANCELLING"
            self.submit("cancel", lambda: self.connection.mutation("CancelOperatorDebugExecution", execution_id=target), control=True)

    def resetSession(self):
        if not self.reset.isEnabled():
            return
        self.executionId = ""
        self.executionStatus = ""
        self.resultLabel.clear()
        self.records.clear()
        self.versions.clear()
        self.outputPorts.clear()
        self.outputTree.clear()
        self.plcResults.setRowCount(0)
        self.plcSummary.clear()
        self.image.clear()
        self.pixmap = None
        for row in self.rows.values():
            row.reference = None
            row.mode.setCurrentIndex(0)
        self.state = "RESETTING"
        self.submit("reset", lambda: self.connection.mutation("ResetOperatorDebugSession"))

    def copyState(self):
        answer = QMessageBox.question(self, "复制生产变量快照", "读取当前工程的生产持久变量并复制到调试会话？",
                                      QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if answer == QMessageBox.Yes:
            self.submit("copy", lambda: self.connection.mutation("CopyOperatorDebugVariables"))

    def poll(self):
        if self.closing or self.disposed:
            return
        if not self.valid() or not self.connection.attached():
            self.close()
            return
        if self.capability is None or self.busy or self.polling:
            return
        if self.records:
            try:
                stale = self.configuration(self.editor.collectParams(), self.inputValues()) != self.records[0].get("configuration")
                self.versions.setToolTip("结果对应旧配置" if stale else "结果对应当前配置")
                if stale and self.versions.currentIndex() == 0 and not self.executionStatus:
                    self.resultLabel.setText("结果对应旧配置 / " + self.stateTitle(self.records[0]["status"]))
            except Exception:
                if not self.executionStatus:
                    self.resultLabel.setText("结果对应旧配置 / 输入尚未完成")
        executionId, sequence = self.executionId, self.sequence
        self.submit("poll", lambda: self.connection.snapshot(executionId, sequence))

    def completed(self, name, value, error):
        if self.disposed:
            return
        if name == 'poll':
            self.polling = False
        elif name != "cancel":
            self.busy = False
        if self.closing and name != "close":
            self.beginClose()
            return
        if name == 'poll' and self.pollCommandVersion != self.commandVersion:
            # A read issued before a mutation must not restore READY, resurrect
            # old results, or re-enable Cancel after the user's newer command.
            self.refreshControls()
            self.drainOutput()
            return
        if error is not None:
            self.artifacts.activeInputs.pop(name, None)
            self.error(error, action=None if name in {"open", "poll", "close"} else name)
            if name == "execute":
                unstarted = self.connection.executionPhase == "preparing" and not self.connection.uncertain
                if not unstarted:
                    self.connection.uncertain = True
                self.executionStatus = ("准备失败；本次未启动执行" if unstarted else "执行状态未知；请结束会话，勿重复执行")
                self.updateResultLabel()
            if name == "open":
                self.state = "FAULTED"
            if name == "close":
                self.closeSent = False
                self.closing = False
                self.state = "FAULTED"
                self.cleanupFailed.emit()
            self.refreshControls()
            self.drainOutput()
            return
        if name == "open":
            self.capability = value["capability"]
            self.resourceNotice.setVisible(bool(self.capability.get("singleNodeOnly")) and not self.isPlc)
            self.state = value["session"]["state"]
            self.status.setText(self.stateTitle(self.state))
            self.showPlcParameters()
            self.buildInputs()
        elif name == "execute":
            self.executionId = value["executionId"]
            self.state = "RUNNING"
            self.executionStatus = "本次执行已接受，等待结果"
            self.actionFeedback.success(name)
            self.updateResultLabel()
        elif name == "poll":
            self.state = value["session"]["state"]
            self.status.setText(self.stateTitle(self.state) if self.isPlc else f"{self.state} | 缓存 {value['session']['cacheBytes']//1024} KiB | {value['session'].get('message', '')}")
            self.status.setToolTip(value['session'].get('message', ''))
            detail = dict(session={key: item for key, item in value["session"].items() if key not in {"ttlMs", "lastSequence"}},
                          execution=value["result"])
            fingerprint = digest(detail)
            if fingerprint != self.detailFingerprint and self.versions.currentIndex() <= 0:
                self.details.setValue(detail)
                self.detailFingerprint = fingerprint
                self.showPlcParameters(self.records[0] if self.records else None)
            events = value["events"]
            self.sequence = events["nextSequence"]
            if events["gap"]:
                self.appendLog("部分早期日志已淘汰")
            for event in events["events"]:
                self.appendLog(event)
            if value["leaseError"]:
                self.status.setText("续租失败：" + value["leaseError"])
            result = value["result"]
            if result and result["status"] != "RUNNING":
                self.executionId = ""
                self.executionStatus = ""
                self.records.insert(0, dict(result, configuration=self.fingerprint))
                del self.records[2:]
                self.versions.blockSignals(True)
                self.versions.clear()
                for index, record in enumerate(self.records):
                    identity = f"{record['status']} / {record['executionId'][:8]} / 参数 {digest(record['rawParams'])[:8]}"
                    title = f"{'本次' if index == 0 else '上次'} · {self.stateTitle(record['status']).removeprefix('执行')}" if self.isPlc else identity
                    self.versions.addItem(title)
                    self.versions.setItemData(index, identity, Qt.ToolTipRole)
                self.versions.blockSignals(False)
                self.showResult()
                self.tabs.setCurrentIndex(1)
        elif name.startswith("file:"):
            try:
                self.artifacts.completeFile(name, value)
            except Exception as failure:
                self.error(failure, action=name)
        elif name.startswith("sources:"):
            self.actionFeedback.success(name)
            self.sourceDialog(name[8:], value)
        elif name.startswith("import:"):
            for port, asset in value.items():
                self.rows[port].setReference({"assetRef": asset["assetId"]}, str(asset["provenance"]))
            self.actionFeedback.success(name)
        elif name in {"copy", "cancel", "reset"}:
            self.actionFeedback.success(name)
        elif name.startswith("download:"):
            if self.outputPorts.currentData() == name[9:]:
                if value["asset"]["mime"] == "image/png":
                    self.pixmap = QPixmap()
                    self.pixmap.loadFromData(value["content"], "PNG")
                    self.scaleImage()
                else:
                    self.outputTree.setValue(parse(value["content"].decode(), VALUE_BYTES))
        elif name.startswith('artifact:'):
            try:
                self.artifacts.completed(name, value)
            except Exception as failure:
                self.error(failure, action=name)
        elif name == "close":
            self.disposed = True
            self.pool.shutdown(wait=False)
            self.controls.shutdown(wait=False)
            self.close()
            return
        self.refreshControls()

        self.drainOutput()

    def drainOutput(self):
        self.artifacts.drainInputs()
        self.refreshControls()
        if self.pendingAsset and not self.busy and not self.closing and not self.disposed:
            assetId, self.pendingAsset = self.pendingAsset, ""
            if self.outputPorts.currentData() == assetId:
                self.submit("download:" + assetId, lambda: self.connection.download(assetId))

    def showResult(self, *_):
        self.outputPorts.blockSignals(True)
        self.outputPorts.clear()
        index = self.versions.currentIndex()
        self.pixmap = None
        self.image.clear()
        self.outputTree.clear()
        if 0 <= index < len(self.records):
            record = self.records[index]
            self.details.setValue(record)
            self.showPlcParameters(record)
            ports = list(record.get("outputs", {}))
            if self.isPlc and "values" in ports:
                ports.remove("values")
                ports.insert(0, "values")
            for port in ports:
                self.outputPorts.addItem(PORT_TITLES.get(port, port) if self.isPlc else port, "")
                self.outputPorts.setItemData(self.outputPorts.count() - 1, port, Qt.UserRole + 1)
            for port, asset in record.get("outputAssets", {}).items():
                self.outputPorts.addItem(PORT_TITLES.get(port, port) if self.isPlc else port, asset["assetId"])
                self.outputPorts.setItemData(self.outputPorts.count() - 1, port, Qt.UserRole + 1)
        self.updateResultLabel()
        self.outputPorts.blockSignals(False)
        self.showOutput()

    def updateResultLabel(self):
        index = self.versions.currentIndex()
        text = self.executionStatus
        if 0 <= index < len(self.records):
            record = self.records[index]
            if text:
                text += "；以下为历史结果（保留原参数身份） / "
            if self.isPlc:
                text += f"{self.stateTitle(record['status'])} | 耗时 {record.get('elapsedMs', 0):.1f} 毫秒"
                error = f"{record.get('code', '')} {record.get('message', '')}".strip()
                if error:
                    text += " | " + error
            else:
                text += f"{record['status']} | {record.get('elapsedMs', 0):.1f} ms | {record.get('code', '')} {record.get('message', '')}"
        self.resultLabel.setText(text)

    def exportSelection(self):
        index, port = self.versions.currentIndex(), self.selectedOutputPort()
        if not 0 <= index < len(self.records) or not port:
            return None
        record = self.records[index]
        if not record.get('executionId') or not record.get('status'):
            return None  # never export an incomplete/anonymous UI placeholder
        if port in record.get('outputAssets', {}):
            wire = {'assetRef': record['outputAssets'][port]['assetId']}
        elif port in record.get('outputs', {}):
            wire = {'inline': deepcopy(record['outputs'][port])}
        else:
            return None
        identity = dict(record.get('sourceIdentity', {}), executionId=record['executionId'],
                        status=record['status'], rawParams=record.get('rawParams'),
                        resolvedParams=record.get('resolvedParams'), selection='current' if index == 0 else 'history',
                        resultKind='operator-debug')
        mime = record.get('outputAssets', {}).get(port, {}).get('mime', 'application/json')
        return dict(port=port, wire=wire, identity=identity, mime=mime)

    def showOutput(self, *_):
        self.pendingAsset = ""
        self.pixmap = None
        self.image.clear()
        self.outputTree.clear()
        self.plcResults.hide()
        self.plcSummary.clear()
        index = self.versions.currentIndex()
        if not 0 <= index < len(self.records):
            return
        assetId = self.outputPorts.currentData()
        isImage = bool(assetId and self.records[index]["outputAssets"][self.selectedOutputPort()]["mime"] == "image/png")
        self.image.setVisible(isImage)
        self.outputTree.setVisible(not isImage)
        if assetId:
            self.pendingAsset = assetId
            self.drainOutput()
        else:
            record = self.records[index]
            port = self.selectedOutputPort()
            value = record.get("outputs", {}).get(port)
            self.outputTree.setValue(value)
            if self.isPlc:
                params = record.get("resolvedParams") or record.get("rawParams", {})
                self.plcSummary.setText(f"PLC {params.get('host', '')}:{params.get('port', '')} | {frameTitle(params)}")
                if not self.rawOutput.isChecked():
                    self.showPlcOutput(port, value, params)
        self.artifacts.refresh(self.run.isEnabled(), not self.busy and not self.closing)

    def showPlcOutput(self, port, value, params):
        from emo_master.core.contracts.communication import plcDeviceAddressSpan, plcWordsPerValue
        if port == "receipt" and isinstance(value, dict):
            rows = [("写入状态", "PLC 已确认写入（未回读）"),
                    ("起始地址", f"{value.get('device', '')}{value.get('startAddress', '')}"),
                    ("数值类型", NUMBER_TYPES.get(value.get("dataType"), value.get("dataType", ""))),
                    ("数值数量", value.get("valueCount", "")), ("写入字数", value.get("wordCount", "")),
                    ("尝试次数", value.get("attempts", ""))]
            self.plcResults.setColumnCount(2)
            self.plcResults.setHorizontalHeaderLabels(["项目", "结果"])
            self.plcResults.setRowCount(len(rows))
            for row, pair in enumerate(rows):
                for column, item in enumerate(pair):
                    self.plcResults.setItem(row, column, QTableWidgetItem(str(item)))
            self.outputTree.hide()
            self.plcResults.show()
            return
        rows = []
        if port == "values" and isinstance(value, dict):
            device = value.get("device", params.get("device", "D"))
            dataType = value.get("dataType", params.get("dataType", "uint16"))
            stride = 1 if dataType == "bit" else plcDeviceAddressSpan(device, plcWordsPerValue(dataType))
            start = value.get("startAddress", params.get("startAddress", 0))
            rows = [(f"{device}{start + index * stride}", item, dataType) for index, item in enumerate(value.get("values", []))]
        elif port in {"numberValue", "booleanValue"} and value is not None:
            device, dataType = params.get("device", "D"), params.get("dataType", "uint16")
            stride = 1 if dataType == "bit" else plcDeviceAddressSpan(device, plcWordsPerValue(dataType))
            address = params.get("startAddress", 0) + params.get("outputIndex", 0) * stride
            rows = [(f"{device}{address}", value, dataType)]
        if not rows:
            return
        self.plcResults.setColumnCount(3)
        self.plcResults.setHorizontalHeaderLabels(["地址", "数值", "数值类型"])
        self.plcResults.setRowCount(len(rows))
        for row, (address, number, dataType) in enumerate(rows):
            display = ("开" if number else "关") if isinstance(number, bool) else str(number)
            for column, text in enumerate((address, display, NUMBER_TYPES.get(dataType, dataType))):
                self.plcResults.setItem(row, column, QTableWidgetItem(text))
        self.outputTree.hide()
        self.plcResults.show()

    def scaleImage(self):
        if self.pixmap is not None:
            self.image.setPixmap(self.pixmap.scaled(self.image.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.scaleImage()

    def beginClose(self):
        if not self.busy and not self.closeSent:
            self.closeSent = self.submit("close", self.connection.close)

    def detach(self):
        """Parent lifetime ended; keep the pinned connection alive until cleanup finishes."""
        if self.disposed:
            return
        self.disposed = True
        self.artifacts.cancelled.set()
        self.timer.stop()
        self.connection.stop.set()
        if not self.closeSent:
            self.closeSent = True
            self.pool.submit(self.connection.close)
        self.pool.shutdown(wait=False)
        self.controls.shutdown(wait=False)

    def done(self, result):
        # QDialog's Esc/reject/accept paths bypass closeEvent. Keep the
        # resource-owning window alive until the normal async close confirms
        # retirement, exactly as the title-bar and End buttons do.
        if not self.disposed:
            self.close()
            return
        super().done(result)

    def reject(self):
        self.done(QDialog.Rejected)

    def accept(self):
        self.done(QDialog.Accepted)

    def closeEvent(self, event):
        if self.disposed:
            event.accept()
            return
        event.ignore()
        if self.closing:
            return
        self.closing = True
        self.artifacts.cancelled.set()
        self.timer.stop()
        self.status.setText("关闭中，等待 Runtime 确认释放资源…")
        self.beginClose()
        self.refreshControls()
