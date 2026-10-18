"""Explicit online read/compare/write, separate from saving the local project."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
from uuid import uuid4
from threading import Event

from PySide2.QtCore import Qt, QTimer
from PySide2.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QComboBox,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView, QMessageBox)

from emo_master.core.project.configuration import projectChanges, requiredProjectChanges, changeCategory


class ProjectConfigurationDialog(QDialog):
    def __init__(self, coordinator, parent=None, *, allowRun=False, selectCategories=()):
        super().__init__(parent)
        self.c = coordinator
        self.client = coordinator.window.runtimeClient
        self.session = coordinator.session
        self.projectId = self.session.document().project.projectId
        self.scope = self._scope()
        self.local = deepcopy(self.session.payload())
        self.snapshot = None
        self.selectCategories = set(selectCategories)
        self.requestId = ""
        self.writePending = False
        self.pending = None
        self.kind = ""
        self.uploadCancelled = Event()
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="online-configuration")
        self.setWindowTitle("工程与 Runtime 配置")
        self.resize(1050, 650)
        layout = QVBoxLayout(self)
        self.status = QLabel("正在读取在线配置")
        self.status.setWordWrap(True)
        self.status.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.status)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["选中变更", "Runtime 在线值", "工程草稿值", "变更类别"])
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.verticalHeader().hide()
        layout.addWidget(self.table, 1)
        self.replacementPolicy = QComboBox()
        self.replacementPolicy.addItem("不自动停止或重连", "reject")
        self.replacementPolicy.addItem("停止受影响任务，替换后恢复原连接", "stopAffected")
        self.replacementPolicy.addItem("排空受影响周期，再停止并替换 / 恢复原连接", "drainAffected")
        layout.addWidget(self.replacementPolicy)
        row = QHBoxLayout()
        self.refreshButton = QPushButton("与 Runtime 比较")
        self.readButton = QPushButton("从 Runtime 读取")
        self.validateButton = QPushButton("校验选中变更")
        self.writeButton = QPushButton("写入 Runtime")
        self.queryButton = QPushButton("查询写入结果")
        self.cancelButton = QPushButton("取消待提交写入")
        self.closeButton = QPushButton("保持差异并关闭")
        self.graphButton = QPushButton("在线 / 历史只读图")
        self.uploadButton = QPushButton("上传候选资源")
        from emo_master.apps.designer.ui.icon_map import icon
        self.uploadButton.setIcon(icon("upload"))
        self.uploadButton.clicked.connect(self.uploadResources)
        for button in (self.refreshButton, self.readButton, self.validateButton, self.writeButton):
            row.addWidget(button)
        layout.addLayout(row)
        row = QHBoxLayout()
        for button in (self.uploadButton, self.queryButton, self.cancelButton, self.graphButton, self.closeButton):
            row.addWidget(button)
        layout.addLayout(row)
        self.runButton = QPushButton("运行在线版本") if allowRun else None
        if self.runButton:
            layout.addWidget(self.runButton)
            self.runButton.clicked.connect(self.runOnline)
        self.refreshButton.clicked.connect(self.refresh)
        self.readButton.clicked.connect(self.readBack)
        self.validateButton.clicked.connect(lambda: self.write(validate=True))
        self.writeButton.clicked.connect(self.write)
        self.queryButton.clicked.connect(self.query)
        self.cancelButton.clicked.connect(self.cancelWrite)
        self.closeButton.clicked.connect(self.reject)
        self.graphButton.clicked.connect(self.openGraph)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.poll)
        self.timer.start(50)
        self.refresh()

    def _scope(self):
        return (id(self.c.window.runtimeClient), getattr(self.c.window.runtimeClient, "runtimeScope", None),
                id(getattr(self.c.window.runtimeClient, "stub", None)),
                id(getattr(self.c.window.runtimeClient, "runtimeService", None)))

    def openGraph(self):
        if not self._valid():
            return
        from emo_master.apps.designer.ui.online_graph import OnlineGraphDialog
        projectId = self.snapshot["payload"]["project"]["projectId"] if self.snapshot else self.projectId
        dialog = OnlineGraphDialog(self.client, projectId, self)
        dialog.setAttribute(Qt.WA_DeleteOnClose)
        dialog.show()

    def runOnline(self):
        if not self._valid() or self.pending is not None or self.writePending or not self.snapshot or self.snapshot.get("recoveryError"):
            return
        if not self.snapshot.get("versionedStart"):
            self.status.setText("Runtime 不支持按在线版本校验启动，请先升级 Runtime")
            return
        from emo_master.apps.designer.ui.online_graph import OnlineGraphDialog
        projectId = self.snapshot["payload"]["project"]["projectId"]
        dialog = OnlineGraphDialog(self.client, projectId, self.c.window, launchSnapshot=self.snapshot)
        self.lastRunDialog = dialog
        dialog.setAttribute(Qt.WA_DeleteOnClose)
        dialog.show()
        dialog.startOnline()
        self.reject()  # The local-draft controller must not start another batch.

    def _valid(self):
        return (self.c.session is self.session and self._scope() == self.scope
                and self.session.document().project.projectId == self.projectId)

    def _controls(self):
        idle = self.pending is None and self._valid()
        ready = idle and self.snapshot is not None and not self.writePending
        writable = ready
        self.refreshButton.setEnabled(idle and not self.writePending)
        self.readButton.setEnabled(ready)
        self.writeButton.setEnabled(writable and self.table.rowCount() > 0)
        self.validateButton.setEnabled(writable and self.table.rowCount() > 0)
        self.uploadButton.setEnabled(ready and hasattr(self.client, "uploadProjectResources"))
        self.queryButton.setEnabled(idle and bool(self.requestId))
        self.cancelButton.setEnabled(idle and self.writePending)
        self.table.setEnabled(ready)
        self.replacementPolicy.setEnabled(ready)
        if self.runButton:
            self.runButton.setEnabled(ready and not self.snapshot.get("recoveryError"))
            self.runButton.setToolTip("运行在线配置并打开独立只读监视图；本地草稿保持不变")

    def _submit(self, kind, action, *args):
        if self.pending is not None or not self._valid():
            return
        self.kind = kind
        self.pending = self.pool.submit(action, *args)
        self._controls()

    def refresh(self):
        if self.writePending:
            return
        self.local = deepcopy(self.session.payload())
        self._submit("snapshot", self.client.getOnlineProjectSnapshot, "")

    def _showSnapshot(self, snapshot):
        self.snapshot = snapshot
        changes = projectChanges(snapshot["payload"], self.local)
        replacing = snapshot["payload"]["project"]["projectId"] != self.projectId
        if replacing and self.replacementPolicy.currentData() == "reject":
            self.replacementPolicy.setCurrentIndex(self.replacementPolicy.findData("stopAffected"))
        self.table.setRowCount(len(changes))
        for row, change in enumerate(changes):
            item = QTableWidgetItem(change["path"])
            item.setToolTip(change["path"])
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if replacing or changeCategory(change["parts"]) in self.selectCategories else Qt.Unchecked)
            item.setData(Qt.UserRole, change["path"])
            self.table.setItem(row, 0, item)
            for col, key in ((1, "before"), (2, "after")):
                text = json.dumps(change[key], ensure_ascii=False, sort_keys=True)
                absent = (key == "before" and change["operation"] == "add") or (key == "after" and change["operation"] == "remove")
                item = QTableWidgetItem("不存在" if absent else text[:180])
                item.setToolTip("不存在" if absent else text)
                self.table.setItem(row, col, item)
            categories = {"document": "工程信息", "layout": "流程布局", "device_parameters": "设备默认参数",
                "device_metadata": "设备描述", "device_connection": "设备连接 / 策略", "node_parameters": "节点参数 / 来源",
                "device_binding": "设备引用", "workflow_structure": "流程结构", "resources": "资源 / 现场绑定",
                "presentation": "工作页面", "runtime_configuration": "运行配置"}
            self.table.setItem(row, 3, QTableWidgetItem(categories[changeCategory(change["parts"])]))
        self.status.setText(f"在线文档 R{snapshot['documentRevision']} / 执行计划 R{snapshot['executionRevision']}；{len(changes)} 项差异"
                            + (f"；更换工程：{snapshot['payload']['project']['projectId']} → {self.projectId}" if replacing else "")
                            + ("；恢复未完成：" + snapshot["recoveryError"] if snapshot.get("recoveryError") else ""))

    def write(self, checked=False, *, validate=False):
        if (self.snapshot is None or self.pending is not None
                or self.writePending or not self._valid()):
            return
        paths = [self.table.item(row, 0).data(Qt.UserRole) for row in range(self.table.rowCount())
                 if self.table.item(row, 0).checkState() == Qt.Checked]
        if not paths:
            self.status.setText("请选择要写入的变更")
            return
        try:
            dependencies = requiredProjectChanges(self.snapshot["payload"], self.local, paths)
        except ValueError as error:
            self.status.setText(str(error))
            return
        if dependencies:
            details = "\n".join(path + "：" + ", ".join(owners) for path, owners in dependencies.items())
            answer = QMessageBox.question(self, "加入必要依赖", "选中变更还依赖以下配置；确认后将一并选中：\n" + details,
                                          QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if answer != QMessageBox.Yes:
                return
            for row in range(self.table.rowCount()):
                item = self.table.item(row, 0)
                if item.data(Qt.UserRole) in dependencies:
                    item.setCheckState(Qt.Checked)
            paths.extend(path for path in dependencies if path not in paths)
        requestId = uuid4().hex
        if not validate:
            consequence = ("\n将停止受影响的任务并可能断开 / 重连设备；不执行采集或 PLC 写入。取消不能撤销已经发生的停止。"
                           if self.replacementPolicy.currentData() != "reject" else "")
            if self.snapshot["payload"]["project"]["projectId"] != self.projectId:
                consequence += f"\n将完整替换工程 {self.snapshot['payload']['project']['projectId']} 为 {self.projectId}；旧任务、连接意图不自动恢复。"
            answer = QMessageBox.question(self, "写入 Runtime", f"将选中的 {len(paths)} 项变更写入在线工程？" + consequence,
                                          QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if answer != QMessageBox.Yes:
                return
            self.requestId = requestId
            self.writePending = True
        options = {} if self.replacementPolicy.currentData() == "reject" else {"replacementPolicy": self.replacementPolicy.currentData()}
        self._submit("validate" if validate else "write", lambda: self.client.writeProjectConfiguration(
            self.snapshot, self.local, paths, requestId, validate=validate, **options))

    def query(self):
        if self.requestId:
            self._submit("write", self.client.getProjectWriteStatus, self.requestId)

    def uploadResources(self):
        if self.snapshot is None or self.pending is not None or self.writePending or not self._valid():
            return
        from emo_master.core.project.configuration import selectedCandidate
        from emo_master.core.project.models import ProjectDocument
        paths = [self.table.item(row, 0).data(Qt.UserRole) for row in range(self.table.rowCount())
                 if self.table.item(row, 0).checkState() == Qt.Checked]
        try:
            candidate = selectedCandidate(self.snapshot["payload"], self.local, paths) if paths else deepcopy(self.snapshot["payload"])
            document = ProjectDocument.model_validate(candidate)
            items = document.resources.items if document.resources else {}
            if not items:
                self.status.setText("候选配置未声明资源")
                return
            if not self.c.directory:
                raise ValueError("请先保存工程以确定资源目录")
            details = "\n".join(f"{key}: {item.path} ({item.size} bytes)" for key, item in items.items())
            if QMessageBox.question(self, "上传候选资源", "将以下资源传输到 Runtime；不会提交配置或启动设备：\n" + details,
                                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
                return
            self.uploadCancelled.clear()
            self._submit("upload", self.client.uploadProjectResources, deepcopy(self.snapshot), candidate,
                         self.c.directory, self.uploadCancelled)
        except Exception as error:
            self.status.setText(str(error))

    def cancelWrite(self):
        if self.requestId:
            self._submit("write", self.client.cancelProjectWrite, self.requestId)

    def poll(self):
        if self.pending is None or not self.pending.done():
            return
        future, self.pending = self.pending, None
        try:
            if not self._valid():
                raise ValueError("工程或 Runtime 连接已变化；请关闭并重新比较")
            result = future.result()
            if self.kind == "snapshot":
                self._showSnapshot(result)
            elif self.kind == "upload":
                self.status.setText(f"{len(result['uploaded'])} 项资源上传并校验完成；在线配置未改变")
            elif self.kind == "validate":
                details = []
                if not result.get("cycleSafe", True) and result.get("affectedJobs"):
                    details.append("受影响任务：" + ", ".join(result["affectedJobs"]))
                if result.get("disconnectDevices"):
                    details.append("需断开设备：" + ", ".join(result["disconnectDevices"]))
                if result.get("communicationRestart"):
                    details.append("需切换 TCP 监听")
                self.status.setText("选中变更校验通过；尚未写入" + ("；" + "；".join(details) if details else ""))
            else:
                state = result["state"]
                self.writePending = state in {"VALIDATING", "WAITING"}
                labels = {"COMMITTED": "已提交", "FAILED": "失败", "CANCELLED": "已取消", "VALIDATING": "校验中", "WAITING": "等待条件"}
                self.status.setText(f"{labels.get(state, state)}；请求 {self.requestId}；{result.get('message', '')}"
                                    + ("；发布错误：" + result["publicationError"] if result.get("publicationError") else "")
                                    + ("；资源释放错误：" + result["cleanupError"] if result.get("cleanupError") else ""))
                if result.get("connections") or result.get("connectionError"):
                    self.status.setText(self.status.text() + "；连接控制：" + json.dumps(
                        result.get("connections", result.get("connectionError")), ensure_ascii=False))
                if state == "COMMITTED":
                    self.refresh()
                elif self.writePending:
                    QTimer.singleShot(300, lambda: self.query() if self.isVisible() and self.writePending else None)
        except Exception as error:
            if getattr(error, "code", "") in {"E_CONFIG_CONFLICT", "E_CONFIG_INSTANCE", "E_CONFIG_WRITER_BUSY",
                    "E_CONFIG_REQUEST_ID", "E_CONFIG_REQUEST_MISMATCH", "E_CONFIG_CAPACITY", "E_CONFIG_INVALID",
                    "E_CONFIG_LIMIT", "E_RUNTIME_CLOSING", "E_CONFIG_NOT_READY"}:
                self.writePending = False
            self.status.setText(str(error) + (f"；请求 {self.requestId}，请查询结果；未自动重试" if self.writePending else ""))
        self._controls()

    def readBack(self):
        if self.snapshot is None or self.writePending or not self._valid():
            return
        replacing = self.snapshot["payload"]["project"]["projectId"] != self.projectId
        if self.session.dirty or replacing:
            message = ("在线配置属于不同工程，将替换当前草稿的工程身份与内容。" if replacing else "当前工程有未保存修改。")
            answer = QMessageBox.question(self, "从 Runtime 读取", message + "用在线配置替换草稿？可通过撤销恢复；本地文件不会被覆盖。",
                                          QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if answer != QMessageBox.Yes:
                return
        window = self.c.window
        with self.session.transaction():
            self.session.workflows.loadPayload(self.c.normalizeDraft(deepcopy(self.snapshot["payload"])))
            window.workflowController._renderActive()
            window.workflowController.captureActiveWorkflow()
        window.activeWorkflowId = self.session.workflows.activeWorkflowId
        window._refreshWorkflowTabs()
        if self.c.devices:
            self.c.devices.refresh()
        if self.c.editor:
            self.c.editor.refresh()
        self.c.chrome.update()
        self.projectId = self.session.document().project.projectId
        self.local = deepcopy(self.session.payload())
        self._showSnapshot(self.snapshot)
        self.status.setText("在线配置已读入工程草稿；本地文件未改变")
        self._controls()

    def done(self, result):
        self.uploadCancelled.set()
        self.timer.stop()
        self.pool.shutdown(wait=False, cancel_futures=True)
        super().done(result)
