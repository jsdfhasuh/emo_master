"""One project boundary shared with the existing flow controller."""
import hashlib
from copy import deepcopy
from pathlib import Path

from PySide2.QtCore import QSize, Qt, QTimer
from PySide2.QtWidgets import QMessageBox, QStackedWidget, QFileDialog, QInputDialog, QScrollArea, QFrame

from emo_master.apps.designer.state.project_edit_session import ProjectEditSession


class WorkspaceStack(QStackedWidget):
    def minimumSizeHint(self):
        # Inactive workspaces must not impose their panel minimums on the active
        # one. Small screens use panel toggles and the flow scroll surface.
        return QSize(480, 180)


class PageCoordinator:
    def __init__(self, window, flowWidget):
        self.window = window
        self.commandDepth = 0
        self.directory = None
        self.editor = None
        self.devices = None
        self.monitor = None
        self.onlineSummary = '未读取在线状态'
        self.closeApproved = None
        self._closingWebPreview = None
        self._devicePublishPending = False
        from .preview import PreviewController
        self.preview = PreviewController(self)
        initialPayload = window.workflowStore.toPayload()
        # toPayload is a save-oriented snapshot which increments revision. The
        # normal workspace becoming discoverable must not edit project metadata.
        initialPayload['project'] = deepcopy(window.workflowStore.project)
        self.session = ProjectEditSession(initialPayload,
            workflows=window.workflowStore, enablePresentation=False)
        self.stack = WorkspaceStack()
        window.takeCentralWidget()
        flowScroll = QScrollArea()
        flowScroll.setFrameShape(QFrame.NoFrame)
        flowScroll.setWidgetResizable(True)
        flowScroll.setWidget(flowWidget)
        self.stack.addWidget(flowScroll)
        window.setCentralWidget(self.stack)
        window.projectController.editCoordinator = self
        window.flowScene.editCompleted = self.sync
        from .chrome import WorkspaceChrome
        self.chrome = WorkspaceChrome(self)

    def sync(self):
        if self.editor:
            self.editor.tools.commitPending()
        if self.devices:
            self.devices.commitPending()
        self.window.workflowController.captureActiveWorkflow()
        self.session.checkpoint()
        if hasattr(self, 'chrome'):
            self.chrome.update()

    def _canSync(self):
        try:
            self.sync()
            return True
        except ValueError as error:
            self.preview.message('请先修正未提交的输入：' + str(error))
            self.chrome.update()
            return False

    def offerDraftRecovery(self, error):
        """Keep every byte of the invalid in-memory draft before rolling back."""
        from emo_master.core.project.delivery_store import atomicJson
        answer = QMessageBox.question(self.window, '项目草稿未通过校验',
            f'{error}\n可先导出恢复副本，再恢复到最后一次合法编辑；原工程文件不会改变。',
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if answer != QMessageBox.Yes:
            return False
        path, _ = QFileDialog.getSaveFileName(self.window, '导出非法草稿恢复副本',
            '项目草稿恢复.json', 'JSON (*.json)')
        if not path:
            return False
        try:
            atomicJson(Path(path), self.session.payload())
            self.session.recoverCheckpoint()
            self.window.workflowController._renderActive()
            self.window.activeWorkflowId = self.session.workflows.activeWorkflowId
            self.window._refreshWorkflowTabs()
            if self.editor:
                self.editor.refresh()
            self.chrome.update()
            self.preview.message('已保留恢复副本并恢复合法草稿；请检查后重新保存')
            return True
        except (OSError, ValueError) as failure:
            QMessageBox.warning(self.window, '恢复未完成', str(failure))
            return False

    def pageActive(self):
        return self.editor is not None and self.stack.currentWidget() is self.editor

    def deviceActive(self):
        return self.devices is not None and self.devices.isVisible()

    def flowActive(self):
        return not self.pageActive()

    def showOnline(self, projectId=None):
        # Observation never checkpoints or navigates the local draft and does
        # not require pending editor inputs to pass configuration validation.
        if self.monitor and projectId and self.monitor.projectId != projectId:
            self.closeMonitor()
        if self.monitor is None:
            from emo_master.apps.designer.ui.online_graph import OnlineGraphDialog
            dialog = OnlineGraphDialog(self.window.runtimeClient, projectId or '', self.window)
            self.monitor = dialog
            dialog.setAttribute(Qt.WA_DeleteOnClose)
            dialog.finished.connect(lambda _result: self._monitorClosed(dialog))
        self.monitor.show()
        self.monitor.raise_()
        self.monitor.activateWindow()
        return self.monitor

    def _monitorClosed(self, dialog):
        if self.monitor is dialog:
            self.monitor = None

    def closeMonitor(self):
        if self.monitor:
            dialog, self.monitor = self.monitor, None
            dialog.done(0)

    def configureRuntime(self, *, allowRun=False, selectCategories=()):
        if not self._canSync():
            return None
        from emo_master.apps.designer.ui.project_configuration import ProjectConfigurationDialog
        dialog = ProjectConfigurationDialog(self, self.window, allowRun=allowRun, selectCategories=selectCategories)
        return dialog.exec_()

    def configureFromDevices(self):
        """Yield the modal observer before entering the existing publication UI."""
        devices = self.devices
        if self._devicePublishPending or devices is None or not devices.isVisible():
            return
        if devices.pending is not None:
            devices.status.setText('请等待当前设备请求返回后再查看发布')
            return
        try:
            self.sync()  # Invalid fields must still be visible when focused.
        except ValueError as error:
            devices.status.setText(str(error))
            return
        session, scope, selected = self.session, devices._scope(), devices.selectedId
        view = (devices.tabs.currentIndex(), devices.infoTabs.currentIndex())
        scrollValues = [(bar, bar.value())
            for widget in (devices.tree, devices.table, devices.tabs.widget(0), *devices.formScrolls.values())
            for bar in (widget.horizontalScrollBar(), widget.verticalScrollBar())]
        self._devicePublishPending = True
        devices.buttons['publish'].setEnabled(False)
        devices.done(0)
        if devices.isVisible():
            self._devicePublishPending = False
            devices._updateOnlineActions()
            return
        def publish():
            try:
                if self.session is session and self.devices is devices and devices._scope() == scope:
                    self.configureRuntime()
            finally:
                self._devicePublishPending = False
                if self.session is session and self.devices is devices and devices._scope() == scope:
                    devices.selectedId = selected
                    devices.refresh()
                    devices.show()
                    devices.tabs.setCurrentIndex(view[0])
                    devices.infoTabs.setCurrentIndex(view[1])
                    devices.raise_()
                    devices.activateWindow()
                    for bar, value in scrollValues:
                        bar.setValue(value)
                    devices.readState()
                    devices._updateOnlineActions()
        QTimer.singleShot(0, publish)

    def resourceBindings(self):
        if not self._canSync():
            return
        from emo_master.apps.designer.ui.resource_bindings import ResourceBindingsDialog
        try:
            types, _requirements, _rejected = self.window.runtimeClient.listDeviceTypes()
        except Exception as error:
            self.preview.message('设备类型读取失败：' + str(error), 'ERROR')
            return
        operators = {item['operatorId']: item for item in self.window.operatorCatalogController.getCatalog()}
        ResourceBindingsDialog(self, types, operators, self.window).exec_()
        self.chrome.update()

    def showDevices(self):
        if self.deviceActive():
            self.devices.raise_()
            self.devices.activateWindow()
            return
        if not self._canSync():
            return
        if self.devices is None:
            from emo_master.apps.designer.ui.device_workspace import DeviceWorkspace
            self.devices = DeviceWorkspace(self)
            self.devices.loadMetadata()
        else:
            self.devices.refresh()
        self.devices.show()
        self.devices.raise_()
        self.devices.activateWindow()
        self.chrome.update()

    def connectRuntime(self):
        from emo_master.apps.designer.ui.runtime_connection import RuntimeConnectionDialog
        if self._canSync():
            RuntimeConnectionDialog(self).exec_()

    def attachRuntime(self, candidate):
        if self.window.isJobRunning or self.preview.active():
            raise ValueError('当前窗口仍有运行任务或页面观察者，请先结束这些操作')
        if self.devices and self.devices.pending is not None:
            raise ValueError('设备操作尚未返回，请等待完成后切换')
        if self.window._activeDebugWindows():
            raise ValueError('当前窗口的流程或算子调试尚未结束，请先关闭调试会话')
        if not self.window.operatorEditorManager.closeCameraConfigurations():
            raise ValueError('相机配置会话尚未退出')
        if not self._canSync():
            raise ValueError('请先修正工程输入')
        self.window.runtimeClient.switchConnection(candidate)
        if self.preview.observer is not None:
            from emo_master.clients.runtime.history import LazyHistoryClient
            self.preview.observer.setHistoryClient(LazyHistoryClient(self.window.runtimeClient, self.session.document()))
        if self.devices:
            self.devices.shutdown()
            self.devices.deleteLater()
            self.devices = None
        self.closeMonitor()
        self.onlineSummary = '未读取在线状态'
        self.window._resetRuntimeVisualState(clearHistory=True)
        self.window.refreshOperators()
        self.chrome.update()

    def showFlow(self):
        if not self._canSync():
            return
        self.stack.setCurrentIndex(0)
        if self.preview.observer is not None:
            self.preview.observer.hide()
        self.chrome.update()

    def showPages(self):
        if not self._canSync():
            return
        if self.session.document().presentation is None:
            answer = QMessageBox.question(self.window, '启用页面设计',
                '此项目将采用 2.2 格式。保存时保留原项目文件的 .bak 备份；旧版本需使用备份。继续？',
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if answer != QMessageBox.Yes:
                self.chrome.update()
                return
            self.session.enablePresentation()
        if self.editor is None:
            from .workspace import PageWorkspace
            self.editor = PageWorkspace(self)
            self.stack.addWidget(self.editor)
        if self.editor.renderer.config != self.session.presentation.snapshot():
            self.editor.refresh()
        self.stack.setCurrentWidget(self.editor)
        self.chrome.update()

    def history(self, redo=False):
        if self.window.isJobRunning:
            return
        if not self.window.operatorEditorManager.closeCameraConfigurations():
            return
        if not self._canSync():
            return
        if (self.session.redo if redo else self.session.undo)():
            self.window.workflowController._renderActive()
            self.window.activeWorkflowId = self.session.workflows.activeWorkflowId
            self.window._refreshWorkflowTabs()
            if self.devices:
                self.devices.refresh()
                self.devices.status.setText(('已重做' if redo else '已撤销') + '工程修改；当前设备列表已同步，在线配置未改变')
                self.devices.status.setToolTip('')
            if self.editor:
                if self.session.document().presentation is None:
                    self.showFlow()
                else:
                    self.editor.refresh()
        self.chrome.update()

    def importInput(self):
        from .resources import registerImage
        self.sync()
        document = self.session.document()
        if self.directory is None:
            raise ValueError('请先保存项目，再选择测试图片')
        if document.resources is None:
            raise ValueError('当前项目尚未启用图片测试所需的页面与资源配置，请先启用页面设计并保存项目')
        nodes = [(workflowId, node.nodeId, f'{workflow.name}/{node.displayName or node.operatorId}')
                 for workflowId, workflow in document.workflows.items() for node in workflow.nodes
                 if node.operatorId == 'vision.io.image_loader']
        if not nodes:
            raise ValueError('请先在流程中添加图片输入节点')
        # Names are user-defined and need not be unique. Keep exact node identity
        # in the selection while using friendly, unambiguous display labels.
        labels = [f'{index + 1}. {item[2]}' for index, item in enumerate(nodes)]
        label, ok = QInputDialog.getItem(self.window, '选择测试图片', '图片输入节点', labels, 0, False)
        if not ok:
            return
        workflow, node, _ = nodes[labels.index(label)]
        path, _ = QFileDialog.getOpenFileName(self.window, '选择测试图片', '', 'Images (*.png *.jpg *.jpeg *.bmp)')
        if path:
            registerImage(self.session, self.directory, workflow, node, path)
            self.preview.message('测试图片已加入项目；下一次开始测试时使用')
            self.chrome.update()

    def confirmLeave(self):
        if not self.confirmDraft():
            return False
        if self.preview.active():
            self.preview.closeAsync()
            self.preview.message('项目切换等待本观察者收尾；完成后请重新选择项目')
            return False
        return True

    def prepareClose(self):
        if not self._canSync():
            return False
        if self.closeApproved != self.session._signature():
            if not self.confirmDraft():
                return False
            self.closeApproved = self.session._signature()
        if self.preview.active():
            if not self.preview.closing:
                self.preview.closeAsync(self.window.close)
            return False
        self.preview.timer.stop()
        webPreview = getattr(self, 'webPreview', None)
        if webPreview and not webPreview.finished:
            if getattr(self, '_closingWebPreview', None) is not webPreview:
                self._closingWebPreview = webPreview
                webPreview.closed.connect(self._finishWebPreviewClose)
            if not webPreview.closing:
                webPreview.close()
            return False
        return True

    def _finishWebPreviewClose(self):
        self._closingWebPreview = None
        QTimer.singleShot(0, self.window.close)

    def confirmDraft(self):
        if not self._canSync():
            return False
        if not self.session.dirty:
            return True
        answer = QMessageBox.question(self.window, '项目有未保存修改',
            '流程和页面共用同一项目。保存后继续？',
            QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel, QMessageBox.Cancel)
        if answer == QMessageBox.Cancel:
            return False
        if answer == QMessageBox.Save:
            self.window.saveProjectAction()
            return not self.session.dirty
        return True

    def loaded(self, directory):
        self.shutdown()
        self.directory = Path(directory).resolve() if directory is not None else None
        self.window.workflowController.captureActiveWorkflow()
        self.session.acceptLoaded()
        self.showFlow()

    def saved(self, directory):
        self.directory = Path(directory).resolve() if directory is not None else None
        if self.editor is not None:
            self.editor.renderer.setProjectRoot(self.directory)
        self.preview.refreshObserver(self.session.presentation.snapshot())
        self.session.markSaved()
        self.updateWindowTitle()

    def updateWindowTitle(self):
        name = self.session.workflows.project.get('name') or '未命名工程'
        self.window.setWindowTitle(f'视觉流程设计器 · {name}' + (' *' if self.session.dirty else ''))

    def normalizeDraft(self, payload):
        from copy import deepcopy
        from .editing import manifestsFromCatalog
        from emo_master.core.contracts.port_types import normalizePortType
        runtime = getattr(self.window.runtimeClient, 'runtimeService', None)
        scan = getattr(runtime, 'pluginScanResult', None)
        manifests = ({key: value.manifest for key, value in scan.activeOperators.items()} if scan
                     else manifestsFromCatalog(self.window.operatorCatalog))
        result = deepcopy(payload)
        for workflow in result['workflows'].values():
            for node in workflow['nodes']:
                manifest = manifests.get(node.get('operatorId'))
                if manifest:
                    if not node.get('displayName'):
                        node['displayName'] = manifest.displayName
                    for field in ('inputPorts', 'outputPorts'):
                        if not node.get(field):
                            node[field] = {key: normalizePortType(spec) for key, spec in getattr(manifest, field).items()}
                    if not node.get('paramSchema'):
                        node['paramSchema'] = deepcopy(manifest.paramSchema)
        return result

    def copyResources(self, directory):
        """Save-as copies declared immutable inputs only, never DB/output trees."""
        plan = self.session.document().resources
        if not plan or not plan.items:
            return
        if self.directory is None:
            raise ValueError('资源根目录未知，不能另存')
        sourceRoot, targetRoot = Path(self.directory).resolve(), Path(directory).resolve()
        for item in plan.items.values():
            source = sourceRoot / item.path
            target = targetRoot / item.path
            if not source.resolve().is_relative_to(sourceRoot) or not target.resolve().is_relative_to(targetRoot):
                raise ValueError('资源越界')
            if source.stat().st_size != item.size:
                raise ValueError('资源大小已变化')
            digest = hashlib.sha256()
            with source.open('rb') as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                    digest.update(chunk)
            if digest.hexdigest() != item.sha256:
                raise ValueError('资源摘要已变化')
            if source.resolve() == target.resolve():
                continue
            if target.exists():
                with target.open('rb') as stream:
                    other = hashlib.sha256()
                    for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                        other.update(chunk)
                if other.hexdigest() != item.sha256:
                    raise ValueError('另存目标资源冲突，不覆盖')
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            import tempfile
            import os
            temporary = None
            try:
                with source.open('rb') as stream, tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as output:
                    temporary = Path(output.name)
                    copied = hashlib.sha256()
                    remaining = item.size
                    while remaining:
                        chunk = stream.read(min(remaining, 1024 * 1024))
                        if not chunk:
                            raise ValueError('另存期间资源改变')
                        output.write(chunk)
                        copied.update(chunk)
                        remaining -= len(chunk)
                    if stream.read(1) or copied.hexdigest() != item.sha256:
                        raise ValueError('另存期间资源改变')
                    output.flush()
                    os.fsync(output.fileno())
                # Target was absent; refuse an intervening file instead of replacing it.
                with temporary.open('rb') as stream, target.open('xb') as output:
                    import shutil
                    shutil.copyfileobj(stream, output, 1024 * 1024)
            finally:
                if temporary:
                    temporary.unlink(missing_ok=True)

    def shutdown(self):
        self.closeMonitor()
        if self.devices:
            self.devices.shutdown()
            self.devices.deleteLater()
            self.devices = None
        self.preview.retireObserver()
        if not self.preview.active():
            self.preview.coverage = None
            self.preview.selectedJob = None
            self.preview.observationLabel = ''
        if self.editor:
            self.editor.close()
            self.stack.removeWidget(self.editor)
            self.editor.deleteLater()
            self.editor = None
