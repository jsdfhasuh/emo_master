"""Modeless operator debugging; all Runtime calls stay off the Qt thread."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path

from PySide2.QtCore import QObject, Qt, QTimer, Signal
from PySide2.QtGui import QPixmap
from PySide2.QtWidgets import (
    QComboBox, QDialog, QFileDialog, QFormLayout, QHBoxLayout, QLabel, QMessageBox,
    QPushButton, QPlainTextEdit, QTabWidget, QVBoxLayout, QWidget,
)

from emo_master.apps.designer.services.operator_debug import DebugConnection
from emo_master.apps.runtime.operator_debug.assets import VALUE_BYTES, decode
from emo_master.apps.runtime.operator_debug.contracts import digest, parse
from emo_master.apps.runtime.operator_debug.data import companionPort
from emo_master.core.contracts.port_types import normalizePortType
from emo_master.core.project.global_variables import definitions, variablePorts
from .debug_values import InputRow, ValueTree, tool
from .icon_map import icon
from .widgets import PreviewLabel, WrapLabel, scrollContent


class Completion(QObject):
    done = Signal(str, object, object)


class OperatorDebugWindow(QDialog):
    def __init__(self, editor, valid=lambda: True):
        super().__init__(editor)
        self.editor, self.valid = editor, valid
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
        self.disposed = False
        self.state = "STARTING"
        self.executionId = ""
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
        self.resize(900, 660)
        layout = QVBoxLayout(self)
        self.status = WrapLabel("连接 Runtime…")
        layout.addWidget(self.status)
        toolbar = QHBoxLayout()
        self.run = QPushButton("单步执行")
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
        self.inputForm = QFormLayout(self.inputBody)
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
        from .debug_artifact_tools import DebugArtifactTools
        self.artifacts = DebugArtifactTools(self, lambda: dict(projectId=self.editor.key.projectId,
            workflowId=self.editor.key.workflowId, nodeId=self.editor.key.nodeId, operatorId=self.editor.context.operatorId),
            self.editor.collectParams, self.exportSelection)
        resultBar.addWidget(self.artifacts.export)
        resultBar.addWidget(self.artifacts.raw)
        resultLayout.addLayout(resultBar)
        self.resultLabel = WrapLabel("")
        resultLayout.addWidget(self.resultLabel)
        self.image = PreviewLabel()
        self.image.hide()
        resultLayout.addWidget(self.image, 1)
        self.outputTree = ValueTree()
        self.outputTree.setMinimumHeight(80)
        resultLayout.addWidget(self.outputTree, 1)
        self.tabs.addTab(result, "结果")
        self.details = ValueTree()
        self.tabs.addTab(self.details, "参数与状态")
        self.logs = QPlainTextEdit()
        self.logs.setReadOnly(True)
        self.logs.setMaximumBlockCount(1000)
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

    def error(self, error):
        from .debug_errors import explainDebugError
        code = getattr(error, 'code', 'E_DEBUG_INPUT')
        self.status.setText(f"{code}: " + explainDebugError(code, str(error), self.editor.context.operatorId))
        self.logs.appendPlainText(self.status.text())

    def refreshControls(self):
        ready = self.state == "READY" and not self.busy and not self.closing and not self.connection.uncertain
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
        if {key: row.portType for key, row in self.rows.items()} == {key: normalizePortType(value) for key, value in ports.items()}:
            return
        while self.inputForm.rowCount():
            # The portable-input toolbar is owned separately from dynamic rows.
            if self.inputForm.itemAt(0).widget() is self.artifacts.panel:
                self.inputForm.takeRow(0)
                continue
            self.inputForm.removeRow(0)
        self.rows = {}
        self.inputForm.addRow(self.artifacts.panel)
        for port, spec in ports.items():
            row = InputRow(normalizePortType(spec))
            self.rows[port] = row
            row.layout().addWidget(tool(row, "folder-open", "选择 PNG / JSON 文件", lambda _checked=False, port=port: self.selectFile(port)))
            row.layout().addWidget(tool(row, "logs", "选择历史完整数据 / 调试输出", lambda _checked=False, port=port: self.selectSource(port)))
            label = QLabel(port + " : " + normalizePortType(spec))
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
            self.fingerprint = self.configuration(params, inputs)
            self.executionId = ""
            self.pixmap = None
            self.image.clear()
            self.outputTree.clear()
            self.resultLabel.setText("本次执行中；历史结果保留原参数身份")
            self.submit("execute", lambda: self.connection.execute(params, inputs))
        except Exception as error:
            self.error(error)

    def selectFile(self, port):
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
        self.submit("file:" + port, upload)

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
        self.records.clear()
        self.versions.clear()
        self.outputPorts.clear()
        self.outputTree.clear()
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
                if stale and self.versions.currentIndex() == 0:
                    self.resultLabel.setText("结果对应旧配置 / " + self.records[0]["status"])
            except Exception:
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
            self.error(error)
            if name == "open":
                self.state = "FAULTED"
            if name == "close":
                self.closing = False
                self.state = "FAULTED"
            self.refreshControls()
            self.drainOutput()
            return
        if name == "open":
            self.capability = value["capability"]
            self.state = value["session"]["state"]
            self.buildInputs()
        elif name == "execute":
            self.executionId = value["executionId"]
            self.state = "RUNNING"
        elif name == "poll":
            self.state = value["session"]["state"]
            self.status.setText(f"{self.state} | 缓存 {value['session']['cacheBytes']//1024} KiB | {value['session'].get('message', '')}")
            detail = dict(session={key: item for key, item in value["session"].items() if key not in {"ttlMs", "lastSequence"}},
                          execution=value["result"])
            fingerprint = digest(detail)
            if fingerprint != self.detailFingerprint and self.versions.currentIndex() <= 0:
                self.details.setValue(detail)
                self.detailFingerprint = fingerprint
            events = value["events"]
            self.sequence = events["nextSequence"]
            if events["gap"]:
                self.logs.appendPlainText("部分早期日志已淘汰")
            for event in events["events"]:
                self.logs.appendPlainText(str(event))
            if value["leaseError"]:
                self.status.setText("续租失败：" + value["leaseError"])
            result = value["result"]
            if result and result["status"] != "RUNNING":
                self.executionId = ""
                self.records.insert(0, dict(result, configuration=self.fingerprint))
                del self.records[2:]
                self.versions.blockSignals(True)
                self.versions.clear()
                for record in self.records:
                    self.versions.addItem(f"{record['status']} / {record['executionId'][:8]} / 参数 {digest(record['rawParams'])[:8]}")
                self.versions.blockSignals(False)
                self.showResult()
                self.tabs.setCurrentIndex(1)
        elif name.startswith("file:"):
            self.rows[name[5:]].setReference(value, value["assetRef"])
        elif name.startswith("sources:"):
            self.sourceDialog(name[8:], value)
        elif name.startswith("import:"):
            for port, asset in value.items():
                self.rows[port].setReference({"assetRef": asset["assetId"]}, str(asset["provenance"]))
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
                self.error(failure)
        elif name == "close":
            self.disposed = True
            self.pool.shutdown(wait=False)
            self.controls.shutdown(wait=False)
            self.close()
            return
        self.refreshControls()

        self.drainOutput()

    def drainOutput(self):
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
            self.resultLabel.setText(f"{record['status']} | {record.get('elapsedMs', 0):.1f} ms | {record.get('code', '')} {record.get('message', '')}")
            self.details.setValue(record)
            for port in record.get("outputs", {}):
                self.outputPorts.addItem(port, "")
            for port, asset in record.get("outputAssets", {}).items():
                self.outputPorts.addItem(port, asset["assetId"])
        self.outputPorts.blockSignals(False)
        self.showOutput()

    def exportSelection(self):
        index, port = self.versions.currentIndex(), self.outputPorts.currentText()
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
        index = self.versions.currentIndex()
        if not 0 <= index < len(self.records):
            return
        assetId = self.outputPorts.currentData()
        isImage = bool(assetId and self.records[index]["outputAssets"][self.outputPorts.currentText()]["mime"] == "image/png")
        self.image.setVisible(isImage)
        self.outputTree.setVisible(not isImage)
        if assetId:
            self.pendingAsset = assetId
            self.drainOutput()
        else:
            self.outputTree.setValue(self.records[index].get("outputs", {}).get(self.outputPorts.currentText()))
        self.artifacts.refresh(self.run.isEnabled(), not self.busy and not self.closing)

    def scaleImage(self):
        if self.pixmap is not None:
            self.image.setPixmap(self.pixmap.scaled(self.image.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.scaleImage()

    def beginClose(self):
        if not self.busy:
            self.submit("close", self.connection.close)

    def detach(self):
        """Parent lifetime ended; keep the pinned connection alive until cleanup finishes."""
        if self.disposed:
            return
        self.disposed = True
        self.artifacts.cancelled.set()
        self.timer.stop()
        self.connection.stop.set()
        self.pool.submit(self.connection.close)
        self.pool.shutdown(wait=False)
        self.controls.shutdown(wait=False)

    def closeEvent(self, event):
        if self.disposed:
            event.accept()
            return
        event.ignore()
        self.closing = True
        self.artifacts.cancelled.set()
        self.timer.stop()
        self.status.setText("关闭中，等待 Runtime 确认释放资源…")
        self.beginClose()
        self.refreshControls()
