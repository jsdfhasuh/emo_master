"""Qt adaptation of the bounded run history, separate from MainWindow."""
from datetime import datetime
import time

from PySide2.QtCore import QObject, Signal, Slot, Qt
from PySide2.QtGui import QImage, QPixmap

from emo_master.apps.designer.state.run_result_history import RunResultHistory
from emo_master.apps.designer.services.inspection_images import InspectionImages, ImageSelection
from .node_image_decode import decodeNodeImage
from .node_result_panel import NodeResultPanel
from .widgets import scrollContent


class NodeResultCoordinator(QObject):
    imageReady = Signal(int)

    def __init__(self, window, rightLayout):
        super().__init__(window)
        self.window = window
        self.history = RunResultHistory()
        self.pending = None
        self.panel = NodeResultPanel(self.history, window)
        self.imageSelection = None
        self.closed = False
        self.image = QImage()
        self.pixmap = QPixmap()
        self.imagePacket = None
        self.viewer = None
        self.qtBytes = 0
        self.peakQtBytes = 0
        self.modelUpdatedNs = 0
        self.images = InspectionImages(window.runtimeClient, decodeNodeImage, self.imageReady.emit)
        self.workflowContexts = {}
        self.selectedWorkflow = None
        self.imageReady.connect(self._imageReady, Qt.QueuedConnection)
        for old in (getattr(window, 'nodeDetailsScroll', window.nodeStatusSection), window.previewSection):
            rightLayout.removeWidget(old)
            old.hide()
        self.panelScroll = scrollContent(self.panel, name='nodeResultScroll')
        # Keep enough usable flow-canvas height for the existing toolbox on
        # very short windows, while letting result content itself scroll.
        self.panelScroll.setMinimumHeight(max(180, self.panelScroll.minimumSizeHint().height()))
        rightLayout.addWidget(self.panelScroll, 1)
        tools = getattr(window, 'runResultTools', None)
        if tools:
            self.panel.imageContent.layout().addWidget(tools)
        window.previewImageLabel = self.panel.image
        window.previewSection = self.panel.imagePage
        self.panel.selectionChanged.connect(self.refresh)
        self.panel.portChanged.connect(self.refresh)
        self.panel.logs.clicked.connect(window.openLogDialog)
        self.panel.configureRequested.connect(self.configure)
        self.panel.enlargeRequested.connect(self.openViewer)

    def blueprint(self):
        window = self.window
        definitions = []
        for workflow in window.workflowStore.listWorkflows():
            nodes = (window.flowModel.toProjectGraph()['nodes'] if workflow.workflowId == window.activeWorkflowId
                     else workflow.nodes)
            for node in nodes:
                definitions.append({**node, 'workflowId': workflow.workflowId,
                                    'displayName': node.get('displayName') or node.get('operatorId') or node['nodeId']})
        return definitions, window.workflowStore.project.get('revision', 1)

    def rememberDraft(self):
        self.pending = self.blueprint()
        return self.images

    def _workflowContext(self, key):
        if key not in self.workflowContexts:
            if not self.workflowContexts:
                history, images = self.history, self.images
            else:
                history = RunResultHistory()
                images = InspectionImages(self.window.runtimeClient, decodeNodeImage,
                    lambda generation: self.imageReady.emit(generation) if self.selectedWorkflow == key else None)
            images.notify = lambda generation: self.imageReady.emit(generation) if self.selectedWorkflow == key else None
            self.workflowContexts[key] = dict(history=history, images=images, pending=None)
        return self.workflowContexts[key]

    def selectWorkflow(self, key):
        context = self._workflowContext(key)
        if self.selectedWorkflow == key:
            return
        self.images.select(None)
        self.selectedWorkflow = key
        self.history, self.images = context['history'], context['images']
        self.panel.history = self.history
        self.imageSelection = None
        self._clearImage('已切换运行任务')
        self.refresh()

    def forWorkflow(self, method, key, *args):
        context = self._workflowContext(key)
        history = context['history']
        if method == 'onInspectionStarting':
            context['pending'] = self.blueprint()
            return context['images']
        if method == 'onInspectionAccepted':
            reply = args[0]
            definitions, revision = context['pending'] or self.blueprint()
            context['pending'] = None
            history.accept(str(reply.job_id), definitions, int(getattr(reply, 'project_revision', 0) or revision))
        elif method == 'onInspectionEvent':
            history.applyEvent(args[0])
        elif method == 'onInspectionStatus' and history.current:
            history.current.status = str(getattr(args[0], 'status', history.current.status))
            if self.selectedWorkflow == key:
                self.status(args[0])
        if self.selectedWorkflow == key:
            self.refresh()

    def accepted(self, reply):
        jobId = reply if isinstance(reply, str) else str(reply.job_id)
        definitions, revision = self.pending or self.blueprint()
        if not isinstance(reply, str):
            revision = int(getattr(reply, 'project_revision', 0) or revision)
        self.pending = None
        self.history.accept(jobId, definitions, revision)
        self.refresh()

    def applyRuntimeEvent(self, event):
        self.history.applyEvent(event)

    def status(self, reply):
        current = self.history.current
        if current and self.window.currentJobId == current.jobId:
            current.status = str(getattr(reply, 'status', current.status))
            acceptedAt = getattr(reply, 'accepted_at_ms', 0)
            if type(acceptedAt) is int and 0 < acceptedAt < 253402300799000:
                current.acceptedAtMs = acceptedAt
            if (current.status in {'COMPLETED', 'FAILED', 'ABORTED'} and self.history.selected is current
                    and self.imagePacket is None and not self.images.hasWork()):
                # A five-second waiting request may have ended during a long
                # run. Terminal readiness permits a new bounded query; a
                # successful in-flight image is never read again for status.
                self.imageSelection = None

    def refresh(self):
        if self.closed:
            return
        window = self.window
        nodeId = window.flowModel.selectedNodeId or ''
        node = window.flowModel.nodes.get(nodeId)
        draft = None
        if node:
            draft = next((item for item in self.blueprint()[0]
                          if item['workflowId'] == window.activeWorkflowId and item['nodeId'] == nodeId), None)
        view = self.panel.render(window.activeWorkflowId, nodeId, draft)
        record = view['record']
        tools = getattr(window, 'runResultTools', None)
        if tools:
            # Keep legacy shortcuts for an old, unassociated observation only.
            # Accepted inspection Jobs use the shared asset port and one log
            # entry; a mutable server filename is never a history shortcut.
            tools.setVisible(view['job'] is None)
        if tools and view['job']:
            tools.origin.setText('所选任务保存图见端口选项 · 任务 ' + view['job'].jobId[:8])
            tools.path = None  # Never open a mutable or already-cleaned output path for history.
            tools.openFile.setEnabled(False)
        identity = (view['job'].jobId if view['job'] else '', window.activeWorkflowId,
                    nodeId, record['nodeRunId'] if record else '', self.panel.ports.currentData(),
                    record['status'] if record else '')
        if identity != self.imageSelection:
            self.imageSelection = identity
            self._clearImage('尚未运行' if view['job'] is None else
                             '所选节点没有图像输出端口' if not self.panel.ports.count() else
                             '本次执行未产生有效图片' if record and record['status'] in ('FAILED', 'SKIPPED') else
                             '节点图片尚未发布')
            selection = None
            if record and record['status'] == 'COMPLETED' and self.panel.ports.currentData():
                selection = ImageSelection(window.loadedProjectPath, view['job'].jobId, window.activeWorkflowId,
                    nodeId, record['workflowRunId'], record['nodeRunId'], view['job'].revision,
                    self.panel.ports.currentData())
            self.images.select(selection)

    def _clearImage(self, message):
        self.image, self.pixmap, self.imagePacket = QImage(), QPixmap(), None
        self.qtBytes = 0
        self.panel.clearImage(message)
        if self.viewer:
            self.viewer.showResult(None, message, self.imageSelection)

    @Slot(int)  # type: ignore[operator]  # PySide2's Slot stub is not callable.
    def _imageReady(self, generation):
        if self.closed:
            return
        packet = self.images.take(generation)
        if packet is None:
            return
        self._clearImage(packet.get('message', ''))
        if 'image' not in packet:
            return
        self.imagePacket = packet
        self.image = packet['image']
        self.pixmap = QPixmap.fromImage(self.image)
        self.qtBytes = self.image.sizeInBytes() + self.pixmap.width() * self.pixmap.height() * 4
        self.peakQtBytes = max(self.peakQtBytes, self.qtBytes)
        if self.qtBytes > 24 * 1024 * 1024:
            self._clearImage('E_INSPECTION_QT_BUDGET: Qt 图片超过24 MiB')
            return
        source = packet['source']
        stamp = (datetime.fromtimestamp(source.createdAtMs / 1000).strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
                 if source.createdAtMs else '采集时间未提供')
        name = self.panel.title.text()
        compactName = name if len(name) <= 64 else name[:63] + '…'
        text = (f'{compactName} · {source.port} · {source.width} × {source.height} · {packet["format"]}\n'
                f'{stamp} · 任务 {source.originJobId[:8]} · 执行 {source.nodeRunId[:8]}')
        self.panel.imageMeta.setText(text)
        self.panel.imageMeta.setToolTip(f'{name}\nJob {source.originJobId}\nWorkflowRun {source.workflowRunId}\n'
                                       f'NodeRun {source.nodeRunId}\nCapture {source.captureId}\n资产 {source.sourceId}')
        self.panel.image.setPixmap(self.pixmap)
        self.panel.enlarge.setEnabled(True)
        self.modelUpdatedNs = time.monotonic_ns()
        if self.viewer:
            self.viewer.showResult(self.pixmap, text, self.imageSelection)

    def openViewer(self):
        if self.closed or self.pixmap.isNull():
            return
        if self.viewer is None:
            from .node_image_viewer import NodeImageViewer
            self.viewer = NodeImageViewer(self.window)
            self.viewer.closed.connect(self._viewerClosed)
        self.viewer.showResult(self.pixmap, self.panel.imageMeta.text(), self.imageSelection)
        self.viewer.show()
        self.viewer.view.fit()
        self.viewer.raise_()

    def _viewerClosed(self):
        if self.sender() is self.viewer:
            self.viewer = None

    def configure(self):
        if self.window.flowModel.selectedNodeId:
            self.window.openNodeParamDialog(self.window.flowModel.selectedNodeId)

    def reset(self):
        for context in self.workflowContexts.values():
            if context['images'] is not self.images:
                context['images'].close()
            context['history'].clear()
        self.workflowContexts.clear()
        self.selectedWorkflow = None
        self.images.reset()
        self._clearImage('工程已切换；历史已释放')
        self.history.clear()
        self.pending = None
        self.imageSelection = None
        self.refresh()

    def close(self):
        self.closed = True
        self._clearImage('检查已关闭')
        if self.viewer:
            self.viewer.close()
        self.images.close()
        for context in self.workflowContexts.values():
            if context['images'] is not self.images:
                context['images'].close()
            context['history'].clear()
        self.workflowContexts.clear()
        self.history.clear()
