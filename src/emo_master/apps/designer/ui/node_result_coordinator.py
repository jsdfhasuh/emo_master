"""Qt adaptation of the bounded run history, separate from MainWindow."""
from PySide2.QtCore import QObject

from emo_master.apps.designer.state.run_result_history import RunResultHistory
from .node_result_panel import NodeResultPanel


class NodeResultCoordinator(QObject):
    def __init__(self, window, rightLayout):
        super().__init__(window)
        self.window = window
        self.history = RunResultHistory()
        self.pending = None
        self.panel = NodeResultPanel(self.history, window)
        self.imageSelection = None
        self.closed = False
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

    def accepted(self, jobId):
        definitions, revision = self.pending or self.blueprint()
        self.pending = None
        self.history.accept(jobId, definitions, revision)

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
        identity = (view['job'].jobId if view['job'] else '', window.activeWorkflowId,
                    nodeId, record['nodeRunId'] if record else '', self.panel.ports.currentData())
        if identity != self.imageSelection:
            self.imageSelection = identity
            self.panel.clearImage('尚未运行' if view['job'] is None else
                                  '本次执行未产生有效图片' if record and record['status'] in ('FAILED', 'SKIPPED') else
                                  '节点图片尚未发布')

    def configure(self):
        if self.window.flowModel.selectedNodeId:
            self.window.openNodeParamDialog(self.window.flowModel.selectedNodeId)

    def reset(self):
        self.history.clear()
        self.pending = None
        self.imageSelection = None
        self.refresh()

    def close(self):
        self.closed = True
        self.history.clear()
