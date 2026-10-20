"""Explicit online read/compare/write, separate from saving the local project."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
from uuid import uuid4
from threading import Event

from PySide2.QtCore import Qt, QTimer
from PySide2.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QComboBox,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView, QMessageBox, QStyle)

from emo_master.core.project.configuration import projectChanges, requiredProjectChanges, changeCategory


class ProjectConfigurationDialog(QDialog):
    def __init__(self, coordinator, parent=None, *, allowRun=False, selectCategories=()):
        super().__init__(parent)
        self.c = coordinator
        self.client = coordinator.window.runtimeClient
        self.session = coordinator.session
        self.closed = False
        self.notifying = False
        self.confirming = False
        self.draftCheckpoint = self.session._checkpoint
        self.submittedCheckpoint = None
        self.revalidateAfterSnapshot = False
        self.notifiedWrites = set()
        self.receiptSummary = ''
        self.projectId = self.session.document().project.projectId
        self.scope = self._scope()
        self.local = deepcopy(self.session.payload())
        self.snapshot = None
        self.initial = False
        self.initialRuntimeId = ''
        self.initialValidated = False
        self.selectCategories = set(selectCategories)
        self.requestId = ""
        self.writePending = False
        self.pending = None
        self.kind = ""
        self.validatedSelection = None
        self.validationSelection = None
        self.uploadCancelled = Event()
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="online-configuration")
        self.setWindowTitle("工程与 Runtime 配置")
        self.resize(1050, 650)
        layout = QVBoxLayout(self)
        self.status = QLabel("正在读取在线配置")
        self.status.setWordWrap(True)
        self.status.setTextInteractionFlags(Qt.TextSelectableByMouse)
        statusRow = QHBoxLayout()
        self.statusIcon = QLabel()
        statusRow.addWidget(self.statusIcon, 0, Qt.AlignTop)
        statusRow.addWidget(self.status, 1)
        layout.addLayout(statusRow)
        formatRow = QHBoxLayout()
        self.formatStatus = QLabel()
        self.formatStatus.setWordWrap(True)
        self.upgradeButton = QPushButton('升级草稿并重新校验')
        self.upgradeButton.clicked.connect(self.upgradeDraft)
        formatRow.addWidget(self.formatStatus, 1)
        formatRow.addWidget(self.upgradeButton)
        layout.addLayout(formatRow)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["选中变更", "Runtime 在线值", "工程草稿值", "变更类别", '生效方式', '影响对象'])
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.verticalHeader().hide()
        layout.addWidget(self.table, 1)
        self.impact = QLabel('选中变更未校验')
        self.impact.setWordWrap(True)
        self.impact.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.impact)
        self.table.itemChanged.connect(self._invalidateEffects)
        self.replacementPolicy = QComboBox()
        self.replacementPolicy.addItem("不自动停止或重连", "reject")
        self.replacementPolicy.addItem("停止受影响任务，替换后恢复原连接", "stopAffected")
        self.replacementPolicy.addItem("排空受影响周期，再停止并替换 / 恢复原连接", "drainAffected")
        self.replacementPolicy.currentIndexChanged.connect(self._invalidateEffects)
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
        self.cancelUploadButton = QPushButton('取消上传')
        self.cancelUploadButton.setIcon(icon('x'))
        self.cancelUploadButton.clicked.connect(self.cancelUploads)
        self.recoverButton = QPushButton('重试资源恢复')
        self.recoverButton.setIcon(icon('refresh'))
        self.recoverButton.clicked.connect(self.retryRecovery)
        for button in (self.refreshButton, self.readButton, self.validateButton, self.writeButton):
            row.addWidget(button)
        layout.addLayout(row)
        row = QHBoxLayout()
        for button in (self.uploadButton, self.cancelUploadButton, self.recoverButton, self.queryButton,
                       self.cancelButton, self.graphButton, self.closeButton):
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
        projectId = self.snapshot["payload"]["project"]["projectId"] if self.snapshot else self.projectId
        self.reject()
        self.c.showOnline(projectId)

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
        return (not self.closed and self.c.session is self.session and self._scope() == self.scope
                and self.session.workflows.project.get('projectId') == self.projectId)

    def _feedback(self, text, level='info'):
        icons = dict(info=QStyle.SP_MessageBoxInformation, busy=QStyle.SP_BrowserReload,
                     success=QStyle.SP_DialogApplyButton, error=QStyle.SP_MessageBoxCritical,
                     warning=QStyle.SP_MessageBoxWarning)
        self.status.setText(text)
        self.status.setProperty('feedbackState', level)
        self.status.setStyleSheet('font-weight: bold;' if level in {'success', 'error', 'warning'} else '')
        self.statusIcon.setPixmap(self.style().standardIcon(icons[level]).pixmap(20, 20))

    def _notify(self, title, text, details='', *, warning=False, key=None, upgrade=False):
        self._feedback(text, 'warning' if warning else 'error')
        if not self._valid() or self.notifying or key is not None and key in self.notifiedWrites:
            return
        if key is not None:
            self.notifiedWrites.add(key)
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning if warning else QMessageBox.Critical)
        box.setWindowTitle(title)
        box.setTextFormat(Qt.PlainText)
        box.setText(text)
        if details:
            box.setDetailedText(details)
        upgradeAction = box.addButton('升级草稿并重新校验', QMessageBox.ActionRole) if upgrade else None
        box.addButton('关闭', QMessageBox.RejectRole)
        self.notifying = True
        self._controls()
        try:
            box.exec_()
        finally:
            self.notifying = False
            self._controls()
        if upgradeAction is not None and box.clickedButton() is upgradeAction and self._valid():
            self.upgradeDraft()

    def _formatFeedback(self):
        version = self.local.get('schemaVersion')
        old = version != '2.6'
        self.formatStatus.setText(f'当前草稿格式 {version}，在线配置需要 2.6。升级只修改草稿，可撤销；不会保存或写入。' if old else '')
        self.formatStatus.setVisible(old)
        self.upgradeButton.setVisible(old and version in {'2.0', '2.1', '2.2', '2.3', '2.4', '2.5'})

    def _requireFormat(self):
        if self.local.get('schemaVersion') == '2.6':
            return True
        self._notify('工程校验未通过',
            f"当前工程格式为 {self.local.get('schemaVersion')}，在线配置写入需要 2.6。\n"
            '本次没有写入 Runtime，也没有保存本地工程。\n可升级当前草稿后重新校验；升级可撤销。',
            'E_CONFIG_INVALID: online writes require schema 2.6', upgrade=not self.upgradeButton.isHidden())
        return False

    def _draftChanged(self):
        # Reuse the existing history checkpoint identity, not a second revision ledger
        # or a full-project signature scan on every timer tick.
        if not self._valid() or self.draftCheckpoint is self.session._checkpoint:
            return False
        self.draftCheckpoint = self.session._checkpoint
        self.local = deepcopy(self.session.payload())
        self.initialValidated = False
        self.initialRuntimeId = ''
        self.validationSelection = self.validatedSelection = None
        self._formatFeedback()
        if self.initial:
            self._showInitial()
        elif self.snapshot is not None:
            self._showSnapshot(self.snapshot)
        self._feedback('草稿已变化，请重新校验；原校验结论已失效。')
        self._controls()
        return True

    def _refreshDraftViews(self):
        window = self.c.window
        window.workflowController._renderActive()
        window.activeWorkflowId = self.session.workflows.activeWorkflowId
        window._refreshWorkflowTabs()
        if self.c.devices:
            self.c.devices.refresh()
        if self.c.editor:
            if self.session.document().presentation is None:
                self.c.stack.setCurrentIndex(0)
            else:
                self.c.editor.refresh()
        self.c.chrome.update()

    def upgradeDraft(self):
        if self.pending is not None or self.writePending or self.notifying or not self._valid():
            return
        try:
            if not self.c._canSync():
                raise ValueError('请先修正未提交的工程输入，再升级草稿')
            from emo_master.core.project.migration import migrateProjectPayload
            from emo_master.core.project.models import ProjectDocument
            payload = self.session.payload()
            if payload.get('schemaVersion') == '2.6':
                self._draftChanged()
                return
            candidate = migrateProjectPayload(deepcopy(payload), enableDevices=True)
            ProjectDocument.model_validate(candidate)
            with self.session.transaction():
                self.session._restore(candidate)
            self._refreshDraftViews()
            self.draftCheckpoint = self.session._checkpoint
            self.initialValidated = False
            self.initialRuntimeId = ''
            self.validationSelection = self.validatedSelection = None
            self.snapshot = None
            self.initial = False
            self.revalidateAfterSnapshot = True
            self.refresh()
        except Exception as error:
            self._notify('草稿升级未完成', str(error), self._errorDetails(error))

    @staticmethod
    def _errorDetails(error):
        return str(getattr(error, 'code', '')) + '\n' + str(error) + (
            '\n' + json.dumps(error.diagnostics, ensure_ascii=False, indent=2) if getattr(error, 'diagnostics', None) else '')

    def _controls(self):
        idle = self.pending is None and not self.notifying and not self.confirming and self._valid()
        uploading = self.pending is not None and self.kind == 'upload' and self._valid()
        ready = idle and self.snapshot is not None and not self.writePending
        initialReady = idle and self.initial and not self.writePending
        initialSupported = callable(getattr(self.client, 'initializeProjectConfiguration', None))
        writable = ready
        self.refreshButton.setEnabled(idle and not self.writePending)
        self.readButton.setEnabled(ready)
        self.writeButton.setEnabled((writable or initialReady and initialSupported) and self.table.rowCount() > 0)
        self.validateButton.setEnabled((writable or initialReady and initialSupported) and self.table.rowCount() > 0)
        self.writeButton.setText('首次写入当前工程' if self.initial else '写入 Runtime')
        validating = self.pending is not None and self.kind in {'initialValidate', 'initialPrewrite', 'validate', 'prewrite'}
        self.validateButton.setText('正在校验…' if validating else '校验完整工程' if self.initial else '校验选中变更')
        self.upgradeButton.setEnabled(idle and not self.writePending)
        uncertain = bool(getattr(self.client, 'pendingResourceTransfers', {}))
        self.uploadButton.setEnabled(ready and not uncertain and hasattr(self.client, "uploadProjectResources"))
        self.recoverButton.setEnabled(ready and not uncertain and self.snapshot.get('resourceRecoveryAvailable', False))
        self.queryButton.setEnabled(idle and (bool(self.requestId) or uncertain))
        self.queryButton.setText('查询上传结果' if uncertain else '查询写入结果')
        self.cancelUploadButton.setEnabled(
            (uploading and not self.uploadCancelled.is_set())
            or (ready and uncertain and hasattr(self.client, 'cancelProjectResourceTransfers')))
        self.cancelButton.setEnabled(idle and self.writePending)
        self.table.setEnabled(ready or initialReady)
        self.replacementPolicy.setEnabled(ready)
        if self.runButton:
            self.runButton.setEnabled(ready and not self.snapshot.get("recoveryError"))
            self.runButton.setToolTip("运行在线配置并打开独立只读监视图；本地草稿保持不变")

    def _submit(self, kind, action, *args):
        if self.pending is not None or self.notifying or self.confirming or not self._valid():
            return
        self.kind = kind
        self.submittedCheckpoint = self.session._checkpoint
        if kind in {'initialValidate', 'initialPrewrite', 'validate', 'prewrite'}:
            self.initialValidated = False
            self.validatedSelection = None
            self._feedback('正在校验完整工程…' if self.initial else '正在校验选中变更…', 'busy')
        self.pending = self.pool.submit(action, *args)
        self._controls()

    def refresh(self):
        if self.writePending or self.pending is not None or not self._valid():
            return
        self.local = deepcopy(self.session.payload())
        self.draftCheckpoint = self.session._checkpoint
        self.initialValidated = False
        self.initialRuntimeId = ''
        self.validationSelection = self.validatedSelection = None
        self._formatFeedback()
        self._feedback('正在读取在线配置…', 'busy')
        self._submit("snapshot", self.client.getOnlineProjectSnapshot, "")

    def _showInitial(self):
        self.snapshot = None
        self.initial = True
        self.initialValidated = False
        self.initialRuntimeId = ''
        self.replacementPolicy.hide()
        self.table.blockSignals(True)
        try:
            sections = list(self.local.items())
            self.table.setRowCount(len(sections))
            for row, (key, value) in enumerate(sections):
                text = json.dumps(value, ensure_ascii=False, sort_keys=True)
                for col, label in enumerate((key, '尚未加载', text[:180], '完整首次候选', '首次加载', '当前工程')):
                    item = QTableWidgetItem(label)
                    item.setToolTip(text if col == 2 else label)
                    self.table.setItem(row, col, item)
        finally:
            self.table.blockSignals(False)
        self.impact.setText('首次写入将加载完整当前草稿并准备资源；设备按既有连接策略建连。'
                            '不会保存本地工程、创建任务、采集图像或执行 PLC 节点。')
        self._feedback('Runtime 已连接，尚未加载工程；可首次写入当前工程。'
            + ('' if callable(getattr(self.client, 'initializeProjectConfiguration', None)) else '当前客户端不支持首次写入，请升级。'))
        self.c.onlineSummary = '尚未加载工程'
        self.c.chrome.update()

    def _showSnapshot(self, snapshot):
        self.initial = False
        self.replacementPolicy.show()
        self.snapshot = snapshot
        changes = projectChanges(snapshot["payload"], self.local)
        replacing = snapshot["payload"]["project"]["projectId"] != self.projectId
        if replacing and self.replacementPolicy.currentData() == "reject":
            self.replacementPolicy.setCurrentIndex(self.replacementPolicy.findData("stopAffected"))
        self.table.blockSignals(True)
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
            self.table.setItem(row, 4, QTableWidgetItem('待校验'))
            self.table.setItem(row, 5, QTableWidgetItem('待校验'))
        self.table.blockSignals(False)
        self._invalidateEffects()
        self._feedback(f"在线文档 R{snapshot['documentRevision']} / 执行计划 R{snapshot['executionRevision']}；{len(changes)} 项差异"
                            + (f"；更换工程：{snapshot['payload']['project']['projectId']} → {self.projectId}" if replacing else "")
                            + ("；恢复未完成：" + snapshot["recoveryError"] if snapshot.get("recoveryError") else "")
                            + ("；配置副本导出失败（SQLite 已提交）：" + snapshot['exportCopyError'] if snapshot.get('exportCopyError') else '')
                            + ('\n' + self.receiptSummary if self.receiptSummary else ''))
        self.c.onlineSummary = f"在线文档 R{snapshot['documentRevision']} / 执行 R{snapshot['executionRevision']}"
        if snapshot.get('recoveryError'):
            self.c.onlineSummary += ' · 恢复未完成'
        self.c.chrome.update()

    def _invalidateEffects(self, *_args):
        self.validatedSelection = None
        if self.status.property('feedbackState') == 'success':
            self._feedback('选中变更已变化，请重新校验；尚未写入 Runtime。')
        self.impact.setText('选中变更未校验')
        self.table.blockSignals(True)
        try:
            for row in range(self.table.rowCount()):
                for col in (4, 5):
                    item = self.table.item(row, col)
                    if item is not None:
                        item.setText('待校验')
                        item.setToolTip('')
        finally:
            self.table.blockSignals(False)

    def _showEffects(self, result):
        self.validatedSelection = self.validationSelection
        labels = dict(document='仅文档 / 页面外观', next_cycle='下一检测周期', idle='设备空闲后安装',
                      reconnect='退出使用后替换 / 按策略重连', stop='停止或排空受影响任务后生效')
        effects = result.get('changeEffects', {})
        self.table.blockSignals(True)
        try:
            for row in range(self.table.rowCount()):
                item = self.table.item(row, 0)
                effect = effects.get(item.data(Qt.UserRole))
                if item.checkState() != Qt.Checked or not effect:
                    continue
                self.table.item(row, 4).setText(labels.get(effect['effect'], 'Runtime 未声明'))
                details = []
                for key, title in [('devices', '设备'), ('workflows', '流程'), ('jobs', '任务')]:
                    if effect.get(key):
                        details.append(title + '：' + '、'.join(effect[key]))
                text = '\n'.join(details) or '无运行对象'
                self.table.item(row, 5).setText(text)
                self.table.item(row, 5).setToolTip(text)
            self.table.resizeRowsToContents()
        finally:
            self.table.blockSignals(False)
        consequence = []
        if result.get('cycleSafe'):
            consequence.append('无需停止；新检测采用新配置，已受理周期保持原配置')
        elif result.get('affectedJobs'):
            consequence.append('需退出的任务：' + '、'.join(result['affectedJobs']))
        if result.get('disconnectDevices'):
            consequence.append('需断开的设备：' + '、'.join(result['disconnectDevices']))
        if result.get('communicationRestart'):
            consequence.append('需切换 TCP 监听')
        if not effects:
            consequence.append('此 Runtime 未提供逐项生效说明，请升级 Runtime')
        self.impact.setText('选中变更已校验 · ' + ('；'.join(consequence) or '无正在运行的受影响对象'))

    def write(self, checked=False, *, validate=False):
        if self.notifying or self.confirming or not self._valid():
            return
        self._draftChanged()
        if self.initial:
            self._writeInitial(validate=validate)
            return
        if (self.snapshot is None or self.pending is not None
                or self.writePending or not self._valid()):
            return
        paths = [self.table.item(row, 0).data(Qt.UserRole) for row in range(self.table.rowCount())
                 if self.table.item(row, 0).checkState() == Qt.Checked]
        if not paths:
            self._notify('工程校验未通过', '请选择要校验或写入的变更；本次未写入 Runtime。')
            return
        if not self._requireFormat():
            return
        try:
            dependencies = requiredProjectChanges(self.snapshot["payload"], self.local, paths)
        except ValueError as error:
            self._notify('工程校验未通过', str(error), self._errorDetails(error))
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
        selection = (tuple(sorted(paths)), self.replacementPolicy.currentData())
        options = {} if self.replacementPolicy.currentData() == "reject" else {"replacementPolicy": self.replacementPolicy.currentData()}
        if not validate and self.validatedSelection != selection:
            self.validationSelection = selection
            self._submit('prewrite', lambda: self.client.writeProjectConfiguration(
                self.snapshot, self.local, paths, uuid4().hex, validate=True, **options))
            return
        requestId = uuid4().hex
        self.validationSelection = selection
        if not validate:
            consequence = ("\n将停止受影响的任务并可能断开 / 重连设备；不执行采集或 PLC 写入。取消不能撤销已经发生的停止。"
                           if self.replacementPolicy.currentData() != "reject" else "")
            if self.snapshot["payload"]["project"]["projectId"] != self.projectId:
                consequence += f"\n将完整替换工程 {self.snapshot['payload']['project']['projectId']} 为 {self.projectId}；旧任务、连接意图不自动恢复。"
            answer = self._confirm("写入 Runtime", f"将选中的 {len(paths)} 项变更写入在线工程？\n" + self.impact.text() + consequence)
            if answer != QMessageBox.Yes:
                return
            if not self._valid() or self._draftChanged():
                return
            self.requestId = requestId
            self.writePending = True
        self._submit("validate" if validate else "write", lambda: self.client.writeProjectConfiguration(
            self.snapshot, self.local, paths, requestId, validate=validate, **options))

    def _writeInitial(self, *, validate=False):
        if self.pending is not None or self.writePending or not self._valid():
            return
        initialize = getattr(self.client, 'initializeProjectConfiguration', None)
        if not callable(initialize):
            self._notify('无法校验工程', '当前客户端不支持首次写入，请升级；本次未写入。')
            return
        if not self._requireFormat():
            return
        if validate or not self.initialValidated:
            self._submit('initialValidate' if validate else 'initialPrewrite',
                lambda: initialize(self.local, self.c.directory, uuid4().hex, validate=True,
                    projectPath=self.c.window.projectController.currentProjectFile))
            return
        if self._confirm('首次写入 Runtime',
                '将完整当前草稿写入目标 Runtime？\n' + self.c.chrome.runtimeStatus.text() + '\n' + self.impact.text()) != QMessageBox.Yes:
            return
        if not self._valid() or self._draftChanged():
            return
        self.requestId = uuid4().hex
        self.writePending = True
        self._submit('write', lambda: initialize(self.local, self.c.directory, self.requestId,
            runtimeInstanceId=self.initialRuntimeId, projectPath=self.c.window.projectController.currentProjectFile))

    def _confirm(self, title, text):
        self.confirming = True
        self._controls()
        try:
            return QMessageBox.question(self, title, text, QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        finally:
            self.confirming = False
            self._controls()

    def query(self):
        if getattr(self.client, 'pendingResourceTransfers', {}):
            self._submit('uploadQuery', self.client.reconcileProjectResources)
            return
        if self.requestId:
            self._submit("write", self.client.getProjectWriteStatus, self.requestId)

    def cancelUploads(self):
        if not self.cancelUploadButton.isEnabled() or not self._valid():
            return
        if self.pending is not None and self.kind == 'upload':
            self.uploadCancelled.set()
            self.status.setText('已请求取消上传；等待当前请求返回后停止后续上传。'
                                '已进入提交的资源按实际结果核对；在线配置未改变。')
            self._controls()
            return
        transfers = tuple(getattr(self.client, 'pendingResourceTransfers', {}))
        details = '\n'.join(transfers)
        if QMessageBox.question(self, '取消上传', '取消以下待核对的原上传请求？已被在线配置引用的资源不会删除。\n' + details,
                                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        self._submit('uploadCancel', self.client.cancelProjectResourceTransfers, transfers)

    def _showResourceResults(self, result):
        remaining = getattr(self.client, 'pendingResourceTransfers', {})
        text = (f"完成 {len(result.get('uploaded', []))} 项，取消 {len(result.get('cancelled', []))} 项，"
                f"仍待核对 {len(remaining)} 项；在线配置未改变。")
        if result.get('cancelled'):
            text += ' 原上传已取消；未自动重传。'
        if remaining:
            text += ' 原请求：' + '、'.join(remaining) + '；可继续查询或取消上传。'
        if result.get('errors'):
            text += '\n' + '\n'.join(
                f"{v.get('resourceId', '')} {v.get('stage', '')} {v['requestTransferId']}：{v['code']}；{v['message']}"
                for v in result['errors'])
        if result.get('aborted'):
            text += '\n本批已中止；未开始项尚未上传。'
        if self.snapshot and self.snapshot.get('resourceRecoveryAvailable'):
            text += ' 资源副本完成后仍需单独重试资源恢复。'
        self.status.setText(text)

    def retryRecovery(self):
        if not self.recoverButton.isEnabled():
            return
        if QMessageBox.question(self, '重试资源恢复', '重新准备当前在线配置，不修改版本，不连接设备或启动任务？',
                                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        self.requestId = uuid4().hex
        self.writePending = True
        self._submit('write', self.client.retryProjectResourceRecovery, deepcopy(self.snapshot), self.requestId)

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
            if QMessageBox.question(self, "上传候选资源", "将以下资源传输到 Runtime；若同清单副本损坏，将用本地正确原件修复。不会提交配置或启动设备：\n" + details,
                                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
                return
            self.uploadCancelled.clear()
            snapshot = deepcopy(self.snapshot)
            self.status.setText('正在上传候选资源；可取消本批上传，在线配置未改变。')
            self._submit("upload", lambda: self.client.uploadProjectResources(snapshot, candidate,
                         self.c.directory, self.uploadCancelled, repairConfirmed=True))
        except Exception as error:
            self._notify('候选资源上传未完成', str(error), self._errorDetails(error))

    def cancelWrite(self):
        if self.requestId:
            self._submit("write", self.client.cancelProjectWrite, self.requestId)

    def poll(self):
        if self.closed or self.notifying or self.confirming:
            return
        if not self._valid():
            if self.pending is not None and self.pending.done():
                self.pending, self.kind = None, ''
            self._feedback('工程或 Runtime 连接已变化；请关闭此窗口后重新比较，原校验结论不再适用。')
            self._controls()
            return
        self._draftChanged()
        if self.pending is None or not self.pending.done():
            return
        future, kind = self.pending, self.kind
        checkpoint = self.submittedCheckpoint
        self.pending, self.kind = None, ''
        if not self._valid():
            self._controls()
            return
        if kind in {'initialValidate', 'initialPrewrite', 'validate', 'prewrite'} and checkpoint is not self.session._checkpoint:
            self._feedback('草稿已变化，请重新校验；未继续写入。')
            self._controls()
            return
        try:
            result = future.result()
            if result.get('state') == 'FAILED':
                self.writePending = False
                self.initialValidated = False
                self._invalidateEffects()
                validating = kind in {'initialValidate', 'initialPrewrite', 'validate', 'prewrite'}
                self._notify('工程校验未通过' if validating else 'Runtime 操作失败',
                    ('校验失败，尚未写入 Runtime：' if validating else 'Runtime 操作失败：') + result.get('message', '请查看详细信息'),
                    json.dumps(result, ensure_ascii=False, indent=2),
                    key=None if validating else (self.requestId, 'FAILED', json.dumps(result, sort_keys=True)))
            elif kind == "snapshot":
                self._showSnapshot(result)
                self._revalidateUpgraded()
            elif kind in {'initialValidate', 'initialPrewrite'}:
                self.initialRuntimeId = result['runtimeInstanceId']
                self.initialValidated = True
                self._feedback('完整首次候选校验通过，尚未写入 Runtime；未连接设备或启动任务', 'success')
                if kind == 'initialPrewrite':
                    self._writeInitial()
            elif kind in {'upload', 'uploadQuery', 'uploadCancel'}:
                self._showResourceResults(result)
                if result.get('errors'):
                    self._notify('资源操作未完成', self.status.text(), json.dumps(result['errors'], ensure_ascii=False, indent=2),
                        key=(kind, json.dumps(result['errors'], sort_keys=True)))
            elif kind in {"validate", 'prewrite'}:
                self._showEffects(result)
                details = []
                if not result.get("cycleSafe", True) and result.get("affectedJobs"):
                    details.append("受影响任务：" + ", ".join(result["affectedJobs"]))
                if result.get("disconnectDevices"):
                    details.append("需断开设备：" + ", ".join(result["disconnectDevices"]))
                if result.get("communicationRestart"):
                    details.append("需切换 TCP 监听")
                self._feedback("选中变更校验通过，尚未写入 Runtime" + ("；" + "；".join(details) if details else ""), 'success')
                if kind == 'prewrite':
                    self.write()
            else:
                state = result["state"]
                self.writePending = state in {"VALIDATING", "WAITING"}
                labels = {"RECOVERED": "Runtime 已恢复就绪", "COMMITTED": "已提交", "FAILED": "失败", "CANCELLED": "已取消", "VALIDATING": "校验中", "WAITING": "等待条件"}
                self._feedback(f"{labels.get(state, state)}；请求 {self.requestId}；{result.get('message', '')}"
                                    + ("；发布错误：" + result["publicationError"] if result.get("publicationError") else "")
                                    + ("；资源释放错误：" + result["cleanupError"] if result.get("cleanupError") else ""))
                if result.get('exportError'):
                    self.status.setText(self.status.text() + '；配置副本导出失败：' + result['exportError'])
                if result.get("connections") or result.get("connectionError"):
                    self.status.setText(self.status.text() + "；连接控制：" + json.dumps(
                        result.get("connections", result.get("connectionError")), ensure_ascii=False))
                if state in {"COMMITTED", "RECOVERED"}:
                    self.receiptSummary = self.status.text()
                    errors = {key: result[key] for key in ('publicationError', 'cleanupError', 'exportError', 'connectionError') if result.get(key)}
                    failedConnections = {key: value for key, value in result.get('connections', {}).items()
                                         if value.startswith('FAILED:')}
                    if failedConnections:
                        errors['connections'] = failedConnections
                    if errors:
                        self._notify('配置已提交，但后续操作异常', self.receiptSummary + '\n已提交事实保留，请先处理后续问题，不要重复写入。',
                            json.dumps(errors, ensure_ascii=False, indent=2), warning=True,
                            key=(self.requestId, state, json.dumps(errors, sort_keys=True)))
                    self.refresh()
                elif self.writePending:
                    QTimer.singleShot(300, lambda: self.query() if self.isVisible() and self.writePending else None)
        except Exception as error:
            if kind == 'snapshot' and getattr(error, 'code', '') == 'E_CONFIG_NOT_LOADED':
                self._showInitial()
                self._revalidateUpgraded()
                self._controls()
                return
            resourceResult = getattr(error, 'resourceResult', None)
            if kind == 'snapshot':
                self.initial = False
                self.snapshot = None
                self.revalidateAfterSnapshot = False
                self.c.onlineSummary = '连接或读取失败'
                self.c.chrome.update()
            if getattr(error, "code", "") in {"E_CONFIG_CONFLICT", "E_CONFIG_INSTANCE", "E_CONFIG_WRITER_BUSY",
                    "E_CONFIG_REQUEST_ID", "E_CONFIG_REQUEST_MISMATCH", "E_CONFIG_CAPACITY", "E_CONFIG_INVALID",
                    "E_CONFIG_LIMIT", "E_RUNTIME_CLOSING", "E_CONFIG_NOT_READY", 'E_CONFIG_ACTIVE', 'E_CONFIG_INITIAL_REMOTE',
                    'E_RESOURCE_BUSY'}:
                self.writePending = False
            if resourceResult is not None:
                self._showResourceResults(resourceResult)
            if getattr(error, 'code', '') == 'E_CONFIG_CANCELLED':
                self.writePending = False
                if resourceResult is None:
                    self._feedback('操作已取消；未自动重试。')
                self._controls()
                return
            unknown = self.writePending
            text = (f'写入结果尚未确认；请求 {self.requestId}，请查询原请求结果，未自动重试。\n{error}' if unknown else
                    self.status.text() if resourceResult is not None else str(error))
            validating = kind in {'initialValidate', 'initialPrewrite', 'validate', 'prewrite'}
            if validating:
                text = '工程校验未通过，尚未写入 Runtime；本地文件未改变。\n' + text
            title = ('写入结果尚未确认' if unknown else '工程校验未通过' if validating else
                     '读取 Runtime 在线配置失败' if kind == 'snapshot' else '工程与 Runtime 操作失败')
            self._notify(title, text, self._errorDetails(error),
                warning=unknown, key=(self.requestId, 'UNKNOWN', str(error)) if unknown else None)
            if getattr(error, 'code', '') in {'E_CONFIG_ACTIVE', 'E_CONFIG_INSTANCE', 'E_CONFIG_CONFLICT'}:
                self.initialValidated = False
                self.refresh()
        self._controls()

    def _revalidateUpgraded(self):
        if not self.revalidateAfterSnapshot:
            return
        self.revalidateAfterSnapshot = False  # an upgrade never resumes a pre-write command
        if not self.initial:
            for row in range(self.table.rowCount()):
                self.table.item(row, 0).setCheckState(Qt.Checked)
            if not self.table.rowCount():
                self._feedback('草稿与在线配置一致，无需写入。', 'success')
                return
        self.write(validate=True)

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
        self.closed = True
        self.uploadCancelled.set()
        self.timer.stop()
        self.pool.shutdown(wait=False, cancel_futures=True)
        super().done(result)
