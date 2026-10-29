"""Explicit online read/compare/write, separate from saving the local project."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
import traceback
from uuid import uuid4
from threading import Event
from time import monotonic

from PySide2.QtCore import Qt, QTimer, Signal, QEvent
from PySide2.QtGui import QPalette
from PySide2.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QComboBox, QCheckBox,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView, QMessageBox, QStyle, QTextBrowser, QTextEdit,
    QStyledItemDelegate, QStyleOptionViewItem, QTreeWidget, QTreeWidgetItem)

from emo_master.core.project.configuration import projectConfigurationChanges, requiredProjectChanges, changeCategory
from emo_master.apps.designer.services.runtime_client import localProjectSourceChange


class ChangeLocationDelegate(QStyledItemDelegate):
    activated = Signal(int)

    def editorEvent(self, event, model, option, index):
        if event.type() == QEvent.MouseButtonRelease and event.button() == Qt.LeftButton:
            styled = QStyleOptionViewItem(option)
            self.initStyleOption(styled, index)
            rect = option.widget.style().subElementRect(QStyle.SE_ItemViewItemText, styled, option.widget)
            if rect.contains(event.pos()):
                self.activated.emit(index.row())
                return True
        return super().editorEvent(event, model, option, index)


def configurationDiagnostics(error, payload, deviceTypes, operators=(), *, localResources=False):
    """Present compiler/RPC locations, without adding another validation pass."""
    from dataclasses import asdict, is_dataclass
    diagnostics = error.get('diagnostics', error) if isinstance(error, dict) else getattr(error, 'diagnostics', {})
    issues = (error.get('issues', ()) if isinstance(error, dict) else getattr(error, 'issues', ()))
    if not issues and isinstance(diagnostics, dict):
        issues = diagnostics.get('issues', ())
    definitions = payload.get('devices', {}).get('definitions', {})
    resources = (payload.get('resources') or {}).get('items', {})
    catalog = {item['operatorId']: item for item in operators}
    result = []
    for raw in issues:
        issue = asdict(raw) if is_dataclass(raw) else raw
        if not isinstance(issue, dict):
            continue
        wid, nid = issue.get('workflowId', ''), issue.get('nodeId', '')
        flow = payload.get('workflows', {}).get(wid, {})
        node = next((value for value in flow.get('nodes', []) if value.get('nodeId') == nid), {})
        parts = str(issue.get('fieldPath', '')).split('.')
        resourceId = next((key for key in sorted(resources, key=len, reverse=True)
            if str(issue.get('fieldPath', '')).startswith('resources.items.' + key + '.')), '')
        code = issue.get('code', '')
        resourceIssue = bool(resourceId and code in {
            'E_RESOURCE_MISSING', 'E_RESOURCE_SIZE', 'E_RESOURCE_INPUT', 'E_RESOURCE_DIGEST'})
        deviceId, field = '', ''
        message = str(issue.get('message') or '配置未通过校验') + '；请查看详细信息'
        binding = node.get('deviceBindings', {}).get(parts[1], {}) if len(parts) > 1 and parts[0] == 'deviceBindings' else {}
        if binding:
            deviceId = binding.get('deviceId', '')
        if parts[:2] == ['devices', 'definitions']:
            # Device IDs may themselves contain dots; match the known identity.
            path = str(issue.get('fieldPath', ''))
            deviceId = next((key for key in sorted(definitions, key=len, reverse=True)
                if path == 'devices.definitions.' + key or path.startswith('devices.definitions.' + key + '.')), '')
            rest = path[len('devices.definitions.' + deviceId):].strip('.').split('.') if deviceId else []
            declaration = deviceTypes.get(definitions.get(deviceId, {}).get('deviceTypeId'))
            if rest:
                field = {'connection': '连接', 'defaults': '工作参数', 'policies': '策略'}.get(rest[0], rest[0])
                schema = getattr(declaration, rest[0] + 'Schema', {})
                for part in rest[1:]:
                    schema = schema.get('items', {}) if part.isdecimal() else schema.get('properties', {}).get(part, {})
                    field += ' / ' + (f'第 {int(part) + 1} 项' if part.isdecimal() else schema.get('title', {'allowedIps': '允许客户端 IPv4'}.get(part, part)))
            message = '设备待配置，请检查' + ('：' + field if field else '设备参数')
        elif parts[0] == 'deviceBindings':
            if len(parts) > 2 and parts[2] == 'selectors':
                key = parts[3] if len(parts) > 3 else ''
                field = '通道' if key == 'channelId' else '设备选择项'
                declaration = deviceTypes.get(definitions.get(deviceId, {}).get('deviceTypeId'))
                spec = getattr(declaration, 'bindingSelectors', {}).get(key, {})
                field = spec.get('title', field)
                missing = key and key not in binding.get('selectors', {}) and issue.get('code') == 'E_PARAM_INVALID'
                message = field + ('未选择，请返回节点配置选择' if missing else '选择无效，请返回节点配置重新选择')
            elif not binding or issue.get('code') == 'E_DEVICE_REFERENCE':
                message = '设备未选择或引用已失效，请返回节点配置选择设备'
            else:
                message = '设备绑定不符合要求，请返回节点配置检查设备'
        elif resourceIssue:
            if code == 'E_RESOURCE_MISSING':
                message = ('本地找不到此文件，请恢复工程目录中的文件后重试' if localResources else
                           'Runtime 缺少此文件，请点击“上传候选资源”')
            elif code in {'E_RESOURCE_SIZE', 'E_RESOURCE_DIGEST'}:
                message = ('本地文件' if localResources else 'Runtime 中的文件')
                message += ('大小' if code == 'E_RESOURCE_SIZE' else '内容') + '与工程记录不一致；'
                message += ('请恢复匹配的文件，或更新工程的资源记录后重试' if localResources else
                            '请确认本地文件正确后，点击“上传候选资源”重新上传')
            else:
                message = '文件路径无效、不是普通文件或无法作为输入；请检查文件路径，具体原因见“详细信息”'
        labels = []
        if resourceIssue:
            labels.append('资源「' + str(resources[resourceId].get('path') or resourceId) + '」')
        if flow:
            labels.append('流程「' + str(flow.get('name') or wid) + '」')
        if node:
            operator = catalog.get(node.get('operatorId'), {})
            labels.append('节点「' + str(node.get('displayName') or operator.get('displayName') or nid) + '」')
        if deviceId in definitions:
            labels.append('设备「' + str(definitions[deviceId].get('name') or deviceId) + '」')
        result.append(dict(text=' / '.join(labels) + ('：' if labels else '') + message,
            code=code, resourcePath=str(resources[resourceId].get('path') or resourceId) if resourceIssue else '',
            workflowId=wid if flow else '', nodeId=nid if node else '',
            deviceId=deviceId if deviceId in definitions else '', fieldPath=issue.get('fieldPath', ''),
            target='resources' if resourceIssue and not localResources and code != 'E_RESOURCE_INPUT' else
                   'device' if parts[:2] == ['devices', 'definitions'] and deviceId in definitions else
                   'node' if node else 'workflow' if flow else 'configuration'))
    return result


class ProjectConfigurationDialog(QDialog):
    def __init__(self, coordinator, parent=None, *, allowRun=False, selectCategories=()):
        super().__init__(parent)
        self.c = coordinator
        self.client = coordinator.window.runtimeClient
        self.session = coordinator.session
        self.closed = False
        self.notifying = False
        self.diagnosticEntries = []
        self.confirming = False
        self.draftCheckpoint = self.session._checkpoint
        self.submittedCheckpoint = None
        self.revalidateAfterSnapshot = False
        self.notifiedWrites = set()
        self.receiptSummary = ''
        self.connectionObserve = None
        self.projectId = self.session.document().project.projectId
        self.scope = self._scope()
        self.local = deepcopy(self.session.payload())
        self.snapshot = None
        self.deviceTypes, self.deviceRequirements = {}, {}
        self.initial = False
        self.initialRuntimeId = ''
        self.initialValidated = False
        self._sourceCandidate = ''
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
        self.sourceLocation = QLabel()
        self.sourceLocation.setWordWrap(True)
        self.sourceLocation.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.sourceLocation.hide()
        layout.addWidget(self.sourceLocation)
        self.useLocalSource = QCheckBox('写入时将 Runtime 工程目录切换到当前本地工程')
        self.useLocalSource.setObjectName('useLocalProjectSource')
        self.useLocalSource.setToolTip('相对模型、图片输出和数据库路径按新工程目录解析；不会复制或清空已有数据。')
        self.useLocalSource.hide()
        self.useLocalSource.toggled.connect(self._sourceSelectionChanged)
        layout.addWidget(self.useLocalSource)
        diagnosticRow = QHBoxLayout()
        self.diagnosticChoice = QComboBox()
        self.diagnosticChoice.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.diagnosticChoice.setMinimumContentsLength(20)
        self.locateButton = QPushButton('返回配置')
        self.locateButton.clicked.connect(self.locateDiagnostic)
        self.diagnosticChoice.currentIndexChanged.connect(self._diagnosticSelected)
        diagnosticRow.addWidget(self.diagnosticChoice, 1)
        diagnosticRow.addWidget(self.locateButton)
        layout.addLayout(diagnosticRow)
        self.diagnosticChoice.hide()
        self.locateButton.hide()
        formatRow = QHBoxLayout()
        self.formatStatus = QLabel()
        self.formatStatus.setWordWrap(True)
        self.upgradeButton = QPushButton('升级草稿并重新校验')
        self.upgradeButton.clicked.connect(self.upgradeDraft)
        formatRow.addWidget(self.formatStatus, 1)
        formatRow.addWidget(self.upgradeButton)
        layout.addLayout(formatRow)
        self.selectAllCheckBox = QCheckBox('全选')
        self.selectAllCheckBox.clicked.connect(self._selectAllChanges)
        layout.addWidget(self.selectAllCheckBox)
        self.table = QTableWidget(0, 6)
        self.locationDelegate = ChangeLocationDelegate(self.table)
        self.table.setItemDelegateForColumn(0, self.locationDelegate)
        self.locationDelegate.activated.connect(self.openChangeLocation)
        self.table.activated.connect(lambda index: self.openChangeLocation(index.row()) if index.column() == 0 else None)
        self.table.setHorizontalHeaderLabels(["修改位置", "操作", "改了什么", "修改类型", '何时生效', '影响哪些设备或流程'])
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setWordWrap(False)
        self.table.setTextElideMode(Qt.ElideRight)
        self.table.verticalHeader().setSectionResizeMode(QHeaderView.Fixed)
        self.table.verticalHeader().setDefaultSectionSize(36)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        for column, width in ((0, 220), (1, 65), (3, 90), (4, 150), (5, 180)):
            self.table.setColumnWidth(column, width)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.table.verticalHeader().hide()
        layout.addWidget(self.table, 1)
        detailRow = QHBoxLayout()
        detailRow.addWidget(QLabel('改动说明'), 1)
        self.technicalDetails = QCheckBox('技术详情')
        self.technicalDetails.toggled.connect(lambda: self._showChangeDetails(self.table.currentRow()))
        detailRow.addWidget(self.technicalDetails)
        layout.addLayout(detailRow)
        self.revisionDetails = QLabel()
        self.revisionDetails.setWordWrap(True)
        self.revisionDetails.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.revisionDetails.hide()
        layout.addWidget(self.revisionDetails)
        self.details = QTextBrowser()
        self.details.setMaximumHeight(140)
        self.details.setPlaceholderText('选择一项查看改动前后的内容')
        self.table.currentCellChanged.connect(self._showChangeDetails)
        layout.addWidget(self.details)
        self.impact = QLabel('尚未检查选中改动的影响')
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
        self.closeButton = QPushButton("关闭")
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

    def _selectedSourcePath(self):
        return self._sourceCandidate if self.useLocalSource.isChecked() else ''

    def _sourceSelectionChanged(self):
        self._invalidateEffects()
        self._controls()

    def openGraph(self):
        if not self._valid():
            return
        projectId = self.snapshot["payload"]["project"]["projectId"] if self.snapshot else self.projectId
        self.reject()
        self.c.showOnline(projectId)

    def runOnline(self):
        if (not self._valid() or self.pending is not None or self.writePending or not self.snapshot
                or self.snapshot.get("recoveryError") or self._selectedSourcePath()):
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

    def _notify(self, title, text, details='', *, warning=False, key=None, upgrade=False, diagnostic=None,
                localResources=False):
        text = text.strip() or title
        details = details.strip()
        if diagnostic is not None:
            localResources = localResources or self.initial
            self.diagnosticEntries = configurationDiagnostics(diagnostic, self.local, self.deviceTypes,
                self.c.window.operatorCatalogController.getCatalog(), localResources=localResources)
            self.diagnosticChoice.clear()
            for entry in self.diagnosticEntries:
                self.diagnosticChoice.addItem(entry['text'])
            self.diagnosticChoice.setVisible(bool(self.diagnosticEntries))
            self.locateButton.setVisible(True)
            self._diagnosticSelected()
            if self.diagnosticEntries:
                entries = self.diagnosticEntries
                if all(entry['code'] == 'E_RESOURCE_MISSING' and entry['resourcePath'] for entry in entries):
                    text = (f'本地缺少 {len(entries)} 个工程文件，无法完成校验：' if localResources else
                            f'Runtime 缺少 {len(entries)} 个工程文件，无法完成校验：')
                    text += '\n' + '\n'.join('- ' + entry['resourcePath'] for entry in entries[:5])
                else:
                    text = '工程校验未通过：\n' + '\n'.join(entry['text'] for entry in entries[:5])
                if len(entries) > 5:
                    text += f'\n另有 {len(entries) - 5} 项问题，可在问题列表或“详细信息”中查看。'
                if all(entry['resourcePath'] for entry in entries):
                    if localResources:
                        text += '\n\n请检查本地工程目录中的上述文件；恢复匹配的文件，或更新工程的资源记录后重试。'
                    elif any(entry['target'] == 'resources' for entry in entries):
                        text += '\n\n请点击“上传候选资源”，将本地文件传到 Runtime；完成后再点击“校验选中变更”。'
                text += '\n本次未写入 Runtime，未连接设备或启动任务；草稿和本地工程文件未改变。'
            else:
                text += '\n请查看详细信息，或返回配置继续修改；草稿和输入已保留。'
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
            detailButton = next(button for button in box.buttons() if box.buttonRole(button) == QMessageBox.ActionRole)
            detailView = box.findChild(QTextEdit)
            detailButton.setText('详细信息')
        upgradeAction = box.addButton('升级草稿并重新校验', QMessageBox.ActionRole) if upgrade else None
        locateAction = box.addButton(self.locateButton.text(), QMessageBox.ActionRole) if diagnostic is not None else None
        box.addButton('关闭', QMessageBox.RejectRole)
        if details:
            # QMessageBox resets this label during its own click dispatch.
            detailButton.clicked.connect(lambda: detailButton.setText(
                '收起详情' if detailView.isVisible() else '详细信息'), Qt.QueuedConnection)
        self.notifying = True
        self._controls()
        try:
            box.exec_()
        finally:
            self.notifying = False
            self._controls()
        if locateAction is not None and box.clickedButton() is locateAction and self._valid():
            self.locateDiagnostic()
        if upgradeAction is not None and box.clickedButton() is upgradeAction and self._valid():
            self.upgradeDraft()

    def _diagnosticSelected(self, *_args):
        index = self.diagnosticChoice.currentIndex()
        entry = self.diagnosticEntries[index] if 0 <= index < len(self.diagnosticEntries) else {}
        self.locateButton.setText({'node': '定位节点并配置', 'device': '配置设备',
            'workflow': '定位流程', 'resources': '上传候选资源' if self.snapshot is not None else '返回配置'
            }.get(entry.get('target'), '返回配置'))
        self.diagnosticChoice.setToolTip(entry.get('text', ''))

    def locateDiagnostic(self):
        if not self._valid() or self.pending is not None or self.writePending:
            return
        index = self.diagnosticChoice.currentIndex()
        entry = self.diagnosticEntries[index] if 0 <= index < len(self.diagnosticEntries) else {}
        if entry.get('target') == 'resources' and self.snapshot is not None:
            self.uploadResources()
            return
        # Closing the comparison retains the same edit session and every input.
        self.reject()
        if entry.get('target') == 'device':
            self.c.showDevices()
            workspace = self.c.devices
            if workspace is not None:
                workspace.search.clear()
                workspace.selectedId = entry['deviceId']
                workspace.refresh()
                path = entry.get('fieldPath', '')
                for key, scroll in workspace.formScrolls.items():
                    if path.startswith('devices.definitions.' + entry['deviceId'] + '.' + key):
                        workspace.tabs.setCurrentWidget(scroll)
                        break
            return
        self.c.showFlow()
        if entry.get('workflowId'):
            self.c.window.navigateToWorkflow(entry['workflowId'], entry.get('nodeId') or None)
            if entry.get('nodeId'):
                self.c.window.openNodeParamDialog(entry['nodeId'])

    def _formatFeedback(self):
        version = self.local.get('schemaVersion')
        old = version not in {'2.6', '2.7'}
        self.formatStatus.setText(f'当前草稿格式 {version}，当前新工程使用 2.7。升级只修改草稿，可撤销；不会保存或写入。' if old else '')
        self.formatStatus.setVisible(old)
        self.upgradeButton.setVisible(old and version in {'2.0', '2.1', '2.2', '2.3', '2.4', '2.5'})

    def _requireFormat(self):
        if self.local.get('schemaVersion') in {'2.6', '2.7'}:
            return True
        self._notify('工程校验未通过',
            f"当前工程格式为 {self.local.get('schemaVersion')}，请新建当前 2.7 工程；本轮不提供旧工程转换。\n"
            '本次没有写入 Runtime，也没有保存本地工程。\n可升级当前草稿后重新校验；升级可撤销。',
            'E_CONFIG_INVALID: online writes require current schema 2.7', upgrade=not self.upgradeButton.isHidden())
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
        self.diagnosticEntries = []
        self.diagnosticChoice.hide()
        self.locateButton.hide()
        if self.snapshot is None or self.table.rowCount():
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
            if payload.get('schemaVersion') in {'2.6', '2.7'}:
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
            self._notify('草稿升级未完成', self._errorText(error), self._errorDetails(error))

    @staticmethod
    def _errorText(error):
        return str(error).strip() or f'{type(error).__name__}：异常未提供具体说明，请查看详细信息。'

    @staticmethod
    def _errorDetails(error):
        from dataclasses import asdict
        details = [type(error).__name__, str(getattr(error, 'code', '')), str(error)]
        if getattr(error, 'diagnostics', None):
            details.append(json.dumps(error.diagnostics, ensure_ascii=False, indent=2))
        if getattr(error, 'issues', None):
            details.append(json.dumps({'issues': [asdict(issue) for issue in error.issues]},
                                      ensure_ascii=False, indent=2))
        if error.__traceback__ is not None:
            details.append(''.join(traceback.format_exception(type(error), error, error.__traceback__)))
        return '\n'.join(part.strip() for part in details if part.strip())

    def _controls(self):
        self._showRevisionDetails()
        idle = self.pending is None and not self.notifying and not self.confirming and self._valid()
        uploading = self.pending is not None and self.kind == 'upload' and self._valid()
        self.locateButton.setEnabled(idle and not self.writePending)
        self.diagnosticChoice.setEnabled(idle and not self.writePending)
        ready = idle and self.snapshot is not None and not self.writePending
        initialReady = idle and self.initial and not self.writePending
        initialSupported = callable(getattr(self.client, 'initializeProjectConfiguration', None))
        writable = ready
        self.refreshButton.setEnabled(idle and not self.writePending)
        self.readButton.setEnabled(ready)
        hasChanges = self.table.rowCount() > 0 or bool(self._selectedSourcePath())
        self.writeButton.setEnabled((writable or initialReady and initialSupported) and hasChanges)
        self.validateButton.setEnabled((writable or initialReady and initialSupported) and hasChanges)
        self.useLocalSource.setEnabled(ready and self.snapshot.get('projectSourceWrite') == 1)
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
        self.closeButton.setText('保持差异并关闭' if self.snapshot is not None and self.table.rowCount() > 0 else '关闭')
        self.table.setEnabled(ready or initialReady)
        self._syncSelectAll()
        self.replacementPolicy.setEnabled(ready)
        if self.runButton:
            self.runButton.setEnabled(ready and not self.snapshot.get("recoveryError") and not self._selectedSourcePath())
            self.runButton.setToolTip("运行在线配置并打开独立只读监视图；本地草稿保持不变")

    def _submit(self, kind, action, *args):
        if self.pending is not None or self.notifying or self.confirming or not self._valid():
            return
        self.kind = kind
        self.submittedCheckpoint = self.session._checkpoint
        if kind in {'initialValidate', 'initialPrewrite', 'validate', 'prewrite'}:
            self.initialValidated = False
            self.validatedSelection = None
            self.diagnosticEntries = []
            self.diagnosticChoice.hide()
            self.locateButton.hide()
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
        self._submit("snapshot", self._readSnapshot)

    def _readSnapshot(self):
        reader = getattr(self.client, 'listDeviceTypes', None)
        if callable(reader):
            self.deviceTypes, self.deviceRequirements, _ = reader()
        return self.client.getOnlineProjectSnapshot('')

    @staticmethod
    def _sectionSummary(key, value):
        titles = dict(project='工程信息', workflows='流程', resources='资源与现场绑定', presentation='工作页面',
                      runtime='运行配置', schemaVersion='工程格式', dependencies='所需算子与插件',
                      globalVariables='工程变量', workflowOrder='流程排列顺序', entryWorkflowId='启动流程',
                      production='生产设置', devices='设备配置')
        return titles.get(key, key) + (' · ' + str(len(value)) + ' 项' if isinstance(value, (dict, list)) else '：' + str(value))

    def _namedObject(self, section, key):
        for payload in (self.local, (self.snapshot or {}).get('payload', {})):
            values = payload.get(section, {})
            if section == 'devices':
                values = values.get('definitions', {})
            value = values.get(key, {})
            if value:
                return value
        return {}

    def _objectName(self, section, key):
        return self._namedObject(section, key).get('name') or key

    def _workflowNodeName(self, workflowId, nodeId):
        for payload in (self.local, (self.snapshot or {}).get('payload', {})):
            flow = payload.get('workflows', {}).get(workflowId, {})
            node = next((n for n in flow.get('nodes', []) if n['nodeId'] == nodeId), {})
            if node:
                name = node.get('displayName') or nodeId
                return {'Workflow Input': '流程输入', 'Workflow Output': '流程输出'}.get(name, name)
        return nodeId

    def _plainChangeLabels(self, change):
        parts = list(change['parts'])
        section = parts[0]
        title = self._sectionSummary(section, '').rstrip('：')
        fieldParts = parts[1:]
        schema = {}
        workflow = {}
        if section == 'workflows' and len(parts) > 1:
            workflow = self._namedObject('workflows', parts[1])
            title = '流程：' + self._objectName('workflows', parts[1])
            fieldParts = parts[2:]
            if len(parts) > 3 and parts[2] == 'nodes':
                node = next((n for n in workflow.get('nodes', []) if n['nodeId'] == parts[3]), {})
                if not node:
                    old = (self.snapshot or {}).get('payload', {}).get('workflows', {}).get(parts[1], {})
                    node = next((n for n in old.get('nodes', []) if n['nodeId'] == parts[3]), {})
                title += ' / ' + self._workflowNodeName(parts[1], parts[3])
                fieldParts = parts[4:]
                if fieldParts and fieldParts[0] == 'params':
                    schema = node.get('paramSchema', {})
        elif section == 'globalVariables' and len(parts) > 1:
            title = '工程变量：' + self._objectName(section, parts[1])
            fieldParts = parts[2:]
        names = dict(name='名称', description='说明', operators='算子清单', plugins='插件清单',
            edges='节点连线', nodes='节点', inputs='流程输入', outputs='流程输出',
            inputPorts='输入接口', outputPorts='输出接口', layout='画布布局', nodePositions='节点位置',
            params='参数', paramSchema='参数填写规则', displayName='显示名称',
            operatorId='使用的算子', targetWorkflowId='调用的流程', loop='循环设置',
            kind='类型', type='数据类型', initialValue='初始值', lifetime='有效范围',
            legacyCounterName='旧计数器关联', deviceBindings='使用的设备', enabled='是否启用',
            minimum='最小值', maximum='最大值', default='默认值', required='必填项',
            schemaVersion='格式版本', entryWorkflowId='启动流程', version='版本',
            properties='可填写参数', x='横向位置', y='纵向位置',
            vehicles='车辆检测结果', image='图片', frame='图像范围', detections='检测结果',
            hasNext='是否还有下一帧', frameIndex='原视频帧号', timestampSec='视频时间（秒）')
        field = []
        for index, part in enumerate(fieldParts):
            if schema and index > 0:
                schema = schema.get('properties', {}).get(part, {})
                label = schema.get('title') or names.get(part, part)
            elif index and fieldParts[index - 1] == 'nodePositions':
                label = self._workflowNodeName(parts[1], part)
            else:
                label = names.get(part, part)
            field.append(label)
        field = ' / '.join(field) or '完整配置'
        before, after = change.get('before'), change.get('after')
        action = {'add': '新增', 'remove': '删除'}.get(change['operation'], '修改')
        if section == 'dependencies' and fieldParts == ['operators']:
            summary = f'所需算子：原来 {len(before or [])} 个，现在 {len(after or [])} 个'
        elif section == 'workflowOrder':
            summary = '调整流程排列：' + '、'.join(self._objectName('workflows', key) for key in (after or []))
        elif section == 'globalVariables' and len(parts) == 2:
            value = after if after is not None else before
            summary = f'{action}变量；初始值：' + self._shortValue((value or {}).get('initialValue'))
        elif section == 'workflows' and len(parts) == 2:
            value = after if after is not None else before
            summary = f'{action}整个流程，包含 {len((value or {}).get("nodes", []))} 个节点'
        elif fieldParts and fieldParts[0] in {'inputs', 'outputs', 'inputPorts', 'outputPorts'}:
            types = dict(detectionCollection='检测目标集合', image='图片', bbox2d='图像区域',
                         boolean='是或否', integer='整数', number='数值', string='文本')
            def portValue(value):
                if isinstance(value, str):
                    return types.get(value, value)
                return self._shortValue(value)
            summary = field + '：' + portValue(before) + ' → ' + portValue(after)
        else:
            summary = field + '：' + self._shortValue(before) + ' → ' + self._shortValue(after)
        return title, action, summary

    def _changeLabels(self, change):
        parts, action = list(change['parts']), change['operation']
        device = len(parts) >= 3 and parts[:2] == ['devices', 'definitions']
        if not device:
            return self._plainChangeLabels(change)
        key = parts[2]
        before = (self.snapshot or {}).get('payload', {}).get('devices', {}).get('definitions', {}).get(key, {})
        after = self.local.get('devices', {}).get('definitions', {}).get(key, {})
        definition = after or before or change.get('after') or change.get('before') or {}
        declaration = self.deviceTypes.get(definition.get('deviceTypeId'))
        name = definition.get('name', key)
        if before.get('name') and after.get('name') and before['name'] != after['name']:
            name = before['name'] + ' → ' + after['name']
        title = name + ' · ' + (declaration.displayName if declaration else definition.get('deviceTypeId', '设备'))
        if len(parts) == 3:
            return title, '新增设备' if action == 'add' else '删除设备', '完整设备定义；详情见下方'
        labels = dict(name='名称', enabled='启用状态', description='说明', connection='连接', defaults='工作参数', policies='策略')
        field = labels.get(parts[3], parts[3])
        schema = getattr(declaration, parts[3] + 'Schema', {}) if declaration else {}
        for part in parts[4:]:
            schema = schema.get('properties', {}).get(part, {})
            field += ' · ' + schema.get('title', part)
        summary = field + '：' + self._shortValue(change.get('before')) + ' → ' + self._shortValue(change.get('after'))
        return title, '修改参数' if parts[3] == 'defaults' else '修改设备', summary

    @staticmethod
    def _shortValue(value):
        if value is None:
            return '未设置'
        if isinstance(value, bool):
            return '启用' if value else '禁用'
        if isinstance(value, (dict, list)):
            return f'{len(value)} 项'
        return str(value).replace('\n', ' ')[:100]

    def _showRevisionDetails(self):
        text = (f"在线文档 R{self.snapshot['documentRevision']} / 执行计划 R{self.snapshot['executionRevision']}"
                if self.snapshot is not None else '')
        self.revisionDetails.setText(text)
        self.revisionDetails.setVisible(bool(text) and self.technicalDetails.isChecked())

    def _showChangeDetails(self, row, *_args):
        self._showRevisionDetails()
        item = self.table.item(row, 0)
        if not item:
            self.details.clear()
        elif self.technicalDetails.isChecked():
            self.details.setPlainText(item.data(Qt.UserRole + 1) or '')
        else:
            headings = ['修改位置', '操作', '改动内容', '修改类型', '何时生效', '影响范围']
            self.details.setPlainText('\n'.join(heading + '：' + self.table.item(row, col).text()
                for col, heading in enumerate(headings) if self.table.item(row, col)))

    def openChangeLocation(self, row):
        if not self._valid() or self.pending is not None or self.writePending:
            return
        previousView = getattr(self, 'lastChangeDialog', None)
        if previousView is not None and previousView.isVisible():
            previousView.raise_()
            return
        item = self.table.item(row, 0)
        if item is None:
            return
        change = json.loads(item.data(Qt.UserRole + 1))
        if self._draftChanged():
            return
        parts = list(change.get('parts') or change['path'].strip('/').split('/'))
        self.table.setCurrentCell(row, 0)
        if parts[0] == 'workflows' and len(parts) >= 2:
            from emo_master.apps.designer.ui.online_graph import OnlineGraphDialog
            def graph(payload, revision):
                return dict(runtimeInstanceId='local-comparison', snapshot=dict(payload=deepcopy(payload),
                    documentRevision=revision, executionRevision=revision), ports={}, history=[], jobs=[], events=[])
            old = (self.snapshot or {}).get('payload', {})
            view = OnlineGraphDialog(None, self.projectId, self,
                snapshotData=graph(self.local, 1), comparisonData=graph(old, 0) if old else None)
            view.setWindowTitle('改动位置 · ' + item.text())
            index = view.workflow.findData(parts[1])
            if index >= 0:
                view.workflow.setCurrentIndex(index)
            nodeId = parts[3] if len(parts) >= 4 and parts[2] == 'nodes' else (
                parts[4] if len(parts) >= 5 and parts[2:4] == ['layout', 'nodePositions'] else None)
            if nodeId is not None:
                view.selectNode(nodeId)
            note = QLabel('本次定位的改动：' + self.table.item(row, 2).text())
            note.setWordWrap(True)
            view.layout().insertWidget(1, note)
            locate = QPushButton()
            def refreshLocate():
                current = self.local.get('workflows', {}).get(view.workflow.currentData())
                selected = view.node.currentData()
                exists = current is not None and (selected is None or any(n['nodeId'] == selected for n in current['nodes']))
                locate.setText('在工程中定位' if exists else '本地已删除，可在左侧查看原位置')
                locate.setEnabled(exists)
            view.workflow.currentIndexChanged.connect(refreshLocate)
            view.node.currentIndexChanged.connect(refreshLocate)
            refreshLocate()
            view.layout().addWidget(locate)
            def navigate():
                if not self._valid():
                    return
                workflowId, selected = view.workflow.currentData(), view.node.currentData()
                view.reject()
                self.reject()
                self.c.showFlow()
                self.c.window.navigateToWorkflow(workflowId, selected)
            locate.clicked.connect(navigate)
        else:
            from emo_master.apps.designer.ui.read_only_graph import displayValue
            view = QDialog(self)
            view.setWindowTitle('改动对照 · ' + item.text())
            view.resize(900, 560)
            layout = QVBoxLayout(view)
            heading = QLabel(self.table.item(row, 2).text())
            heading.setWordWrap(True)
            layout.addWidget(heading)
            tree = QTreeWidget()
            tree.setHeaderLabels(['设置项', '运行端原内容', '本地待写入'])
            tree.header().setSectionResizeMode(QHeaderView.ResizeToContents)
            tree.header().setStretchLastSection(True)
            labels = dict(name='名称', type='数据类型', kind='属性', initialValue='初始值',
                lifetime='有效范围', legacyCounterName='旧计数器关联', operatorId='算子', version='版本',
                operators='所需算子', description='说明', revision='工程版本', updatedAt='更新时间')
            def readable(key, value):
                choices = dict(type=dict(integer='整数', number='数值', boolean='是或否', string='文本'),
                    kind=dict(variable='变量', constant='只读常量'), lifetime=dict(job='每次运行重新初始化', persistent='工程持久保存'))
                if isinstance(value, str):
                    return choices.get(key, {}).get(value, value)
                return displayValue(value)
            def add(parent, key, before, after):
                structured = isinstance(before, (dict, list)) or isinstance(after, (dict, list))
                node = QTreeWidgetItem(parent, [labels.get(str(key), str(key)),
                    ('未设置' if before is None else '') if structured else readable(key, before),
                    ('未设置' if after is None else '') if structured else readable(key, after)])
                if before != after:
                    node.setBackground(1, self.palette().alternateBase())
                    node.setBackground(2, self.palette().alternateBase())
                if isinstance(before, dict) or isinstance(after, dict):
                    left = before if isinstance(before, dict) else {}
                    right = after if isinstance(after, dict) else {}
                    for name in dict.fromkeys([*left, *right]):
                        add(node, name, left.get(name), right.get(name))
                elif isinstance(before, list) or isinstance(after, list):
                    left = before if isinstance(before, list) else []
                    right = after if isinstance(after, list) else []
                    for index in range(max(len(left), len(right))):
                        add(node, f'第 {index + 1} 项', left[index] if index < len(left) else None,
                            right[index] if index < len(right) else None)
            add(tree, item.text(), change.get('before'), change.get('after'))
            tree.expandAll()
            layout.addWidget(tree, 1)
            close = QPushButton('返回差异列表')
            close.clicked.connect(view.reject)
            layout.addWidget(close)
        self.lastChangeDialog = view
        view.setWindowModality(Qt.WindowModal)
        view.show()

    def _showInitial(self):
        self.snapshot = None
        self.initial = True
        self._sourceCandidate = ''
        self.sourceLocation.hide()
        self.useLocalSource.hide()
        self.useLocalSource.blockSignals(True)
        self.useLocalSource.setChecked(False)
        self.useLocalSource.blockSignals(False)
        self.initialValidated = False
        self.initialRuntimeId = ''
        self.replacementPolicy.hide()
        self.table.blockSignals(True)
        try:
            sections = [(key, value) for key, value in self.local.items() if key != 'devices']
            sections += [(f'/devices/definitions/{key}', value)
                         for key, value in self.local.get('devices', {}).get('definitions', {}).items()]
            self.table.setRowCount(len(sections))
            for row, (key, value) in enumerate(sections):
                labels = (self._sectionSummary(key, '').rstrip('：'), '首次加载', self._sectionSummary(key, value), '完整工程', '写入后生效', '当前工程')
                if key.startswith('/devices/definitions/'):
                    labels = (*self._changeLabels(dict(parts=key.strip('/').split('/'), operation='add',
                        path=key, before=None, after=value)), '完整首次候选', '首次加载', '当前工程')
                for col, label in enumerate(labels):
                    item = QTableWidgetItem(label)
                    item.setToolTip(str(label))
                    if col == 0:
                        item.setData(Qt.UserRole + 1, json.dumps(dict(path=key, after=value), ensure_ascii=False, indent=2))
                    self.table.setItem(row, col, item)
        finally:
            self.table.blockSignals(False)
        self._showChangeDetails(self.table.currentRow())
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
        localFile = self.c.window.projectController.currentProjectFile
        sourceCandidate = localProjectSourceChange(self.client, snapshot, localFile)
        if sourceCandidate != self._sourceCandidate:
            self._sourceCandidate = sourceCandidate
            self.useLocalSource.blockSignals(True)
            self.useLocalSource.setChecked(bool(sourceCandidate) and snapshot.get('projectSourceWrite') == 1)
            self.useLocalSource.blockSignals(False)
        self.sourceLocation.setVisible(bool(snapshot.get('sourcePath')))
        self.sourceLocation.setText('本地工程：' + str(localFile or '尚未保存') + '\nRuntime 在线工程：' + str(snapshot.get('sourcePath', ''))
            + ('\n目录变化将在明确写入后生效；相对路径会跟随新目录。' if sourceCandidate else '')
            + ('\n当前 Runtime 不支持目录切换，请升级后重新比较。' if sourceCandidate and snapshot.get('projectSourceWrite') != 1 else ''))
        self.useLocalSource.setVisible(bool(sourceCandidate))
        changes = projectConfigurationChanges(snapshot["payload"], self.local)
        replacing = snapshot["payload"]["project"]["projectId"] != self.projectId
        if replacing and self.replacementPolicy.currentData() == "reject":
            self.replacementPolicy.setCurrentIndex(self.replacementPolicy.findData("stopAffected"))
        self.table.blockSignals(True)
        change = None
        try:
            self.table.setRowCount(len(changes))
            for row, change in enumerate(changes):
                labels = self._changeLabels(change)
                # Keep JSON numbers out of QVariant's signed 64-bit conversion.
                details = json.dumps(change, ensure_ascii=False, indent=2)
                item = QTableWidgetItem(labels[0])
                item.setToolTip('点击查看改动位置和前后对照：' + labels[0])
                font = item.font()
                font.setUnderline(True)
                item.setFont(font)
                item.setForeground(self.palette().brush(QPalette.Link))
                item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                item.setCheckState(Qt.Checked if replacing or changeCategory(change["parts"]) in self.selectCategories else Qt.Unchecked)
                item.setData(Qt.UserRole, change["path"])
                item.setData(Qt.UserRole + 1, details)
                self.table.setItem(row, 0, item)
                for col in (1, 2):
                    item = QTableWidgetItem(labels[col])
                    item.setToolTip(labels[col])
                    self.table.setItem(row, col, item)
                categories = {"document": "工程信息", "layout": "流程布局", "device_parameters": "设备默认参数",
                    "device_metadata": "设备描述", "device_connection": "设备连接 / 策略", "node_parameters": "节点参数 / 来源",
                    "device_binding": "设备引用", "workflow_structure": "流程结构", "resources": "资源 / 现场绑定",
                    "presentation": "工作页面", "runtime_configuration": "运行配置"}
                self.table.setItem(row, 3, QTableWidgetItem(categories[changeCategory(change["parts"])]))
                self.table.setItem(row, 4, QTableWidgetItem('检查后确定'))
                self.table.setItem(row, 5, QTableWidgetItem('检查后确定'))
        except Exception as error:
            if change is None:
                raise
            failure = RuntimeError(f"无法显示变更 {change['path']}：{self._errorText(error)}")
            failure.diagnostics = change
            raise failure from error
        finally:
            self.table.blockSignals(False)
        self._showChangeDetails(self.table.currentRow())
        self._invalidateEffects()
        self._feedback(('工程内容一致，但本地与 Runtime 工程目录不同，尚未写入。' if not changes and sourceCandidate else
                        '当前工程与 Runtime 配置一致，无需写入。' if not changes else
                        f'当前工程与 Runtime 有 {len(changes)} 项配置差异，尚未写入。')
                            + (f"；更换工程：{snapshot['payload']['project']['projectId']} → {self.projectId}" if replacing else "")
                            + ("；恢复未完成：" + snapshot["recoveryError"] if snapshot.get("recoveryError") else "")
                            + ("；配置副本导出失败（SQLite 已提交）：" + snapshot['exportCopyError'] if snapshot.get('exportCopyError') else '')
                            + ('\n' + self.receiptSummary if self.receiptSummary else ''),
                       'warning' if snapshot.get('recoveryError') or snapshot.get('exportCopyError') else 'info')
        self.c.onlineSummary = '上次比较：' + ('配置一致' if not changes else f'有 {len(changes)} 项配置差异')
        if sourceCandidate:
            self.c.onlineSummary += ' · 工程目录不同'
        if snapshot.get('recoveryError'):
            self.c.onlineSummary += ' · 恢复未完成'
        if snapshot.get('exportCopyError'):
            self.c.onlineSummary += ' · 配置副本导出失败'
        self.c.chrome.update()

    def _syncSelectAll(self):
        total = self.table.rowCount()
        selected = sum(self.table.item(row, 0).checkState() == Qt.Checked
                       for row in range(total)) if not self.initial else 0
        state = (Qt.Unchecked if not selected else
                 Qt.Checked if selected == total else Qt.PartiallyChecked)
        self.selectAllCheckBox.setVisible(not self.initial)
        self.selectAllCheckBox.setEnabled(self.table.isEnabled() and bool(total) and not self.initial)
        self.selectAllCheckBox.setTristate(state == Qt.PartiallyChecked)
        self.selectAllCheckBox.setCheckState(state)
        self.selectAllCheckBox.setText(f'全选（已选 {selected} / {total} 项）')

    def _selectAllChanges(self, checked):
        if not self.selectAllCheckBox.isEnabled() or self.initial:
            return
        blocked = self.table.blockSignals(True)
        try:
            for row in range(self.table.rowCount()):
                self.table.item(row, 0).setCheckState(Qt.Checked if checked else Qt.Unchecked)
        finally:
            self.table.blockSignals(blocked)
        self._invalidateEffects()

    def _invalidateEffects(self, *_args):
        self._syncSelectAll()
        self.validatedSelection = None
        if not self.initial and self.snapshot is not None and not self.table.rowCount() and not self._selectedSourcePath():
            self.impact.setText('无配置差异，无需校验或写入。')
            return
        if self.status.property('feedbackState') == 'success':
            self._feedback('选中变更已变化，请重新校验；尚未写入 Runtime。')
        self.impact.setText('尚未检查选中改动的影响')
        self.table.blockSignals(True)
        try:
            for row in range(self.table.rowCount()):
                for col in (4, 5):
                    item = self.table.item(row, col)
                    if item is not None:
                        item.setText('检查后确定')
                        item.setToolTip('')
        finally:
            self.table.blockSignals(False)
        self._showChangeDetails(self.table.currentRow())

    def _showEffects(self, result):
        self.validatedSelection = self.validationSelection
        labels = dict(document='只更新工程信息或显示，不改变运行', next_cycle='从下一检测周期开始', idle='设备空闲后生效',
                      reconnect='设备停止使用后更新，按所选方式重新连接', stop='受影响任务停止或完成当前工作后生效')
        effects = result.get('changeEffects', {})
        self.table.blockSignals(True)
        try:
            for row in range(self.table.rowCount()):
                item = self.table.item(row, 0)
                effect = effects.get(item.data(Qt.UserRole))
                if item.checkState() != Qt.Checked or not effect:
                    continue
                effectText = ('随工程目录切换，待受影响任务及设备操作结束后生效' if result.get('sourceChanged')
                              else labels.get(effect['effect'], '运行端未提供说明'))
                self.table.item(row, 4).setText(effectText)
                self.table.item(row, 4).setToolTip(effectText)
                details = []
                for key, title in [('devices', '设备'), ('workflows', '流程'), ('jobs', '任务')]:
                    if effect.get(key):
                        names = [self._objectName(key, value) if key != 'jobs' else value for value in effect[key]]
                        details.append(title + '：' + '、'.join(names))
                text = '\n'.join(details) or '无受影响的设备、流程或任务'
                self.table.item(row, 5).setText(text)
                self.table.item(row, 5).setToolTip(text)
        finally:
            self.table.blockSignals(False)
        self._showChangeDetails(self.table.currentRow())
        consequence = []
        if result.get('sourceChanged'):
            consequence.append('工程目录切换到：' + result['sourcePath'])
            consequence.append('本次选中变更统一在受影响任务及设备操作结束、资源释放后生效')
        if result.get('cycleSafe'):
            consequence.append('无需停止；新检测采用新配置，已受理周期保持原配置')
        elif result.get('affectedJobs'):
            consequence.append('需退出的任务：' + '、'.join(result['affectedJobs']))
        if result.get('disconnectDevices'):
            consequence.append('需断开的设备：' + '、'.join(result['disconnectDevices']))
        if result.get('restartDevices'):
            consequence.append('需重新激活设备：' + '、'.join(result['restartDevices']))
        if not effects and not result.get('sourceChanged'):
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
        sourcePath = self._selectedSourcePath()
        if not paths and not sourcePath:
            self._notify('工程校验未通过', '请选择要校验或写入的变更；本次未写入 Runtime。')
            return
        if not self._requireFormat():
            return
        try:
            dependencies = (requiredProjectChanges(self.snapshot["payload"], self.local, paths,
                deviceTypes=self.deviceTypes, operatorRegistry=self.deviceRequirements) if paths else {})
        except ValueError as error:
            self._notify('工程校验未通过', self._errorText(error), self._errorDetails(error), diagnostic=error)
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
        selection = (tuple(sorted(paths)), self.replacementPolicy.currentData(), sourcePath)
        options = {} if self.replacementPolicy.currentData() == "reject" else {"replacementPolicy": self.replacementPolicy.currentData()}
        if sourcePath:
            options['localProjectPath'] = sourcePath
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
            if sourcePath:
                consequence += '\n同时切换 Runtime 工程来源：\n' + self.snapshot['sourcePath'] + '\n→ ' + sourcePath
            selected = f'选中的 {len(paths)} 项配置变更' if paths else '工程目录变更'
            answer = self._confirm("写入 Runtime", f"将{selected}写入在线工程？\n" + self.impact.text() + consequence)
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
            self._notify('候选资源上传未完成', self._errorText(error), self._errorDetails(error))

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
        observation = self.connectionObserve
        if observation and self.pending is None:
            if monotonic() >= observation['deadline']:
                self.connectionObserve = None
                self._feedback('已提交，设备连接结果待查看；可查询原请求或查看设备在线状态。')
            elif observation['requestId'] != self.requestId:
                self.connectionObserve = None
            elif monotonic() >= observation['nextRead']:
                observation['nextRead'] = monotonic() + .3
                self._submit('connectionObserve', self.client.getProjectWriteStatus, self.requestId)
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
        snapshotReceived = False
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
                    key=None if validating else (self.requestId, 'FAILED', json.dumps(result, sort_keys=True)),
                    diagnostic=result if validating else None)
            elif kind == "snapshot":
                snapshotReceived = True
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
                if result.get("restartDevices"):
                    details.append("需重新激活设备：" + ", ".join(result['restartDevices']))
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
                    if kind != 'connectionObserve':
                        self.connectionObserve = (dict(requestId=self.requestId, deadline=monotonic()+30, nextRead=monotonic()+.3)
                            if 'PENDING' in result.get('connections', {}).values() else None)
                        self.refresh()
                    elif 'PENDING' not in result.get('connections', {}).values():
                        self.connectionObserve = None
                elif self.writePending:
                    QTimer.singleShot(300, lambda: self.query() if self.isVisible() and self.writePending else None)
        except Exception as error:
            if kind == 'connectionObserve':
                self._feedback('已提交；设备连接结果读取暂未完成，可继续查询原请求或查看设备状态。')
                self._controls()
                return
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
                self.c.onlineSummary = '在线配置已读取，差异显示失败' if snapshotReceived else '连接或读取失败'
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
            text = (f'写入结果尚未确认；请求 {self.requestId}，请查询原请求结果，未自动重试。\n{self._errorText(error)}' if unknown else
                    self.status.text() if resourceResult is not None else self._errorText(error))
            validating = kind in {'initialValidate', 'initialPrewrite', 'validate', 'prewrite'}
            localResourceError = (resourceResult is not None
                and bool(getattr(error, 'diagnostics', {}).get('issues'))
                and any(item.get('stage') == 'VALIDATE' for item in resourceResult.get('errors', [])))
            if validating:
                text = '工程校验未通过，尚未写入 Runtime；本地文件未改变。\n' + text
            title = ('写入结果尚未确认' if unknown else '工程校验未通过' if validating else
                     '本地工程文件校验未通过' if localResourceError else
                     '读取 Runtime 在线配置失败' if kind == 'snapshot' else '工程与 Runtime 操作失败')
            if kind == 'snapshot' and not unknown:
                if snapshotReceived:
                    title = '工程配置差异显示失败'
                    text = '已读取 Runtime 在线配置，但生成差异表时失败。\n' + text
                else:
                    text = '未能读取 Runtime 在线配置，请检查连接后重新比较。\n' + text
                text += '\n本次未写入 Runtime，未启动流程；本地工程文件未改变。'
            self._notify(title, text, self._errorDetails(error),
                warning=unknown, key=(self.requestId, 'UNKNOWN', str(error)) if unknown else None,
                diagnostic=error if validating or localResourceError else None, localResources=localResourceError)
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
            if not self.table.rowCount() and not self._selectedSourcePath():
                self._feedback('运行配置一致，无需写入。', 'success')
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
        self.draftCheckpoint = self.session._checkpoint
        self.initialValidated = False
        self.initialRuntimeId = ''
        self.validationSelection = self.validatedSelection = None
        self._formatFeedback()
        self._showSnapshot(self.snapshot)
        self._feedback('在线配置已读入工程草稿；本地文件未改变。\n' + self.status.text(),
                       self.status.property('feedbackState'))
        self._controls()

    def done(self, result):
        self.closed = True
        self.connectionObserve = None
        self.uploadCancelled.set()
        self.timer.stop()
        self.pool.shutdown(wait=False, cancel_futures=True)
        super().done(result)
