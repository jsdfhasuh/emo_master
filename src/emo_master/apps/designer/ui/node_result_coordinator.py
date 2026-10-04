"""Qt adaptation of the bounded run history, separate from MainWindow."""
from datetime import datetime
import time

from PySide2.QtCore import QObject, Signal, Slot, Qt
from PySide2.QtGui import QImage, QPixmap

from emo_master.apps.designer.state.run_result_history import RunResultHistory
from emo_master.apps.designer.services.inspection_images import InspectionImages, ImageSelection
from .node_image_decode import decodeNodeImage
from .node_result_panel import NodeResultPanel


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
        self.imageReady.connect(self._imageReady, Qt.QueuedConnection)
        for old in (getattr(window, 'nodeDetailsScroll', window.nodeStatusSection), window.previewSection):
            rightLayout.removeWidget(old)
            old.hide()
        rightLayout.addWidget(self.panel, 1)
        tools = getattr(window, 'runResultTools', None)
        if tools:
            self.panel.imagePage.layout().addWidget(tools)
        window.previewImageLabel = self.panel.image
        window.previewSection = self.panel.imagePage
        self.panel.selectionChanged.connect(self.refresh)
        self.panel.portChanged.connect(self.refresh)
        self.panel.logs.clicked.connect(window.openLogDialog)
        self.panel.configureRequested.connect(self.configure)

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

    def accepted(self, reply):
        jobId = reply if isinstance(reply, str) else str(reply.job_id)
        definitions, revision = self.pending or self.blueprint()
        if not isinstance(reply, str):
            revision = int(getattr(reply, 'project_revision', 0) or revision)
        self.pending = None
        self.history.accept(jobId, definitions, revision)
        self.refresh()

    def event(self, event):
        self.history.applyEvent(event)

    def status(self, reply):
        current = self.history.current
        if current and self.window.currentJobId == current.jobId:
            current.status = str(getattr(reply, 'status', current.status))

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
        if tools and view['job']:
            tools.origin.setText('所选任务保存图见端口选项 · 任务 ' + view['job'].jobId[:8])
            tools.path = None  # Never open a mutable or already-cleaned output path for history.
            tools.openFile.setEnabled(False)
        identity = (view['job'].jobId if view['job'] else '', window.activeWorkflowId,
                    nodeId, record['nodeRunId'] if record else '', self.panel.ports.currentData(),
                    record['status'] if record else '', view['job'].status if view['job'] else '')
        if identity != self.imageSelection:
            self.imageSelection = identity
            self._clearImage('尚未运行' if view['job'] is None else
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

    @Slot(int)  # type: ignore[operator] - PySide2's Slot stub is not callable.
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
        text = (f'{source.port} · {source.width} × {source.height} · {packet["format"]}\n'
                f'{stamp} · 任务 {source.originJobId[:8]} · 执行 {source.nodeRunId[:8]}')
        self.panel.imageMeta.setText(text)
        self.panel.imageMeta.setToolTip(f'Job {source.originJobId}\nWorkflowRun {source.workflowRunId}\n'
                                       f'NodeRun {source.nodeRunId}\nCapture {source.captureId}\n资产 {source.sourceId}')
        self.panel.image.setPixmap(self.pixmap)
        self.panel.enlarge.setEnabled(True)
        self.modelUpdatedNs = time.monotonic_ns()
        if self.viewer:
            self.viewer.showResult(self.pixmap, text, self.imageSelection)

    def configure(self):
        if self.window.flowModel.selectedNodeId:
            self.window.openNodeParamDialog(self.window.flowModel.selectedNodeId)

    def reset(self):
        self.images.reset()
        self._clearImage('工程已切换；历史已释放')
        self.history.clear()
        self.pending = None
        self.imageSelection = None
        self.refresh()

    def close(self):
        self.closed = True
        self._clearImage('检查已关闭')
        self.images.close()
        self.history.clear()
