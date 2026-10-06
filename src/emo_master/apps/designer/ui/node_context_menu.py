"""Node operations share the Designer's commands, selection and result panel."""
from PySide2.QtCore import QEvent, QObject, QPoint, Qt
from PySide2.QtGui import QFontMetrics
from PySide2.QtWidgets import QAction, QMenu
from shiboken2 import isValid


class NodeContextMenu(QObject):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.menu = QMenu(window)
        self.menu.setObjectName('nodeContextMenu')
        self.menu.setToolTipsVisible(True)
        self.version = 0
        self.context = None
        self.menu.installEventFilter(self)
        window.installEventFilter(self)
        window.flowScene.setNodeContextMenuHandler(self.open)
        window.flowScene.selectionChanged.connect(self._selectionChanged)

    def _flowAvailable(self):
        if not isValid(self.window):
            return False
        controller = getattr(self.window, 'runtimeController', None)
        if controller is not None and controller._closed:
            return False
        coordinator = getattr(self.window, 'pageCoordinator', None)
        return coordinator is None or not coordinator.pageActive()

    def _select(self, nodeId):
        scene = self.window.flowScene
        if scene.getSelectedNodeIds() == [nodeId] and not scene.getSelectedEdgeKeys():
            if self.window.flowModel.selectedNodeId != nodeId:
                self.window.onNodeSelectionChanged()
            return
        previous = scene.blockSignals(True)
        try:
            scene.clearSelection()
            scene.setNodeSelected(nodeId)
        finally:
            scene.blockSignals(previous)
        if not previous:
            scene.selectionChanged.emit()

    def _valid(self, context):
        version, projectId, workflowId, nodeId, node = context
        return (version == self.version and self._flowAvailable()
                and self.window._currentProjectId() == projectId
                and self.window.activeWorkflowId == workflowId
                and self.window.flowModel.nodes.get(nodeId) is node
                and self.window.flowScene.hasNode(nodeId)
                and self.window.flowScene.getSelectedNodeId() == nodeId
                and not self.window.flowScene.getSelectedEdgeKeys())

    def _blockedReason(self, key, context):
        if not self._valid(context):
            return '节点或工程已变化，请重新右键选择'
        registry = getattr(self.window, 'designerActions', None)
        if registry is not None:
            return registry.blockedReason(key)
        node = context[-1]
        if key == 'results':
            coordinator = getattr(self.window, 'nodeResultCoordinator', None)
            return '' if coordinator is not None and not coordinator.closed else '节点结果面板不可用'
        if self.window.isJobRunning:
            return '任务运行中可查看结果；请结束运行后再修改流程'
        if self.window.flowModel.isBoundaryNode(node.nodeId):
            return '工作流输入/输出请通过工作流接口配置，不能复制或删除'
        if key == 'copy' and node.kind != 'operator':
            return '仅支持复制普通算子；控制流程请复制整个工作流'
        return ''

    def buildMenu(self, nodeId):
        self.closePopup()
        if not self._flowAvailable():
            return None
        node = self.window.flowModel.nodes.get(nodeId)
        if node is None or not self.window.flowScene.hasNode(nodeId):
            return None
        self._select(nodeId)
        context = (self.version, self.window._currentProjectId(), self.window.activeWorkflowId, nodeId, node)
        self.context = context
        self.menu.clear()
        title = node.displayName or node.operatorId or nodeId
        section = self.menu.addSection(QFontMetrics(self.menu.font()).elidedText(title, Qt.ElideRight, 280))
        section.setToolTip(title)
        entries = (('configure', '配置算子…'), ('results', '查看节点结果'),
                   ('copy', '复制算子'), ('delete', '删除算子'))
        for key, text in entries:
            if key == 'copy':
                self.menu.addSeparator()
            action = QAction(text, self.menu)
            registry = getattr(self.window, 'designerActions', None)
            if registry is not None:
                # Show the canonical key without registering a duplicate shortcut.
                action.setText(text + '\t' + registry.shortcutLabel(key))
            action.setData(key)
            reason = self._blockedReason(key, context)
            action.setEnabled(not reason)
            action.setToolTip(reason or text)
            action.triggered.connect(lambda _checked=False, key=key, action=action:
                                     self._invoke(key, context, action))
            self.menu.addAction(action)
        return self.menu

    def open(self, nodeId, screenX, screenY):
        menu = self.buildMenu(nodeId)
        if menu is not None:
            menu.popup(QPoint(screenX, screenY))

    def _invoke(self, key, context, action):
        if not isValid(action) or not action.isEnabled() or self._blockedReason(key, context):
            return
        registry = getattr(self.window, 'designerActions', None)
        if registry is not None:
            registry.invoke(key)
            return
        nodeId = context[3]
        if key == 'configure':
            self.window.openNodeParamDialog(nodeId)
        elif key == 'copy':
            self.window.duplicateSelectedNode()
        elif key == 'delete':
            self.window.deleteSelectedElements()
        elif key == 'results':
            # Reuse the current/previous selection and decoded image. No Job,
            # subscription or second image cache is created by this action.
            self.window.onNodeSelectionChanged()
            self.window.rightPanelContainer.show()
            coordinator = self.window.nodeResultCoordinator
            coordinator.panelScroll.ensureWidgetVisible(coordinator.panel)
            coordinator.panel.tabs.setFocus(Qt.OtherFocusReason)

    def _selectionChanged(self):
        if self.context is not None and not self._valid(self.context):
            self.closePopup()

    def closePopup(self):
        self.version += 1
        self.context = None
        if isValid(self.menu):
            self.menu.close()

    def eventFilter(self, watched, event):
        if (watched is self.window and event.type() in (QEvent.Close, QEvent.Hide)
                or watched is self.menu and event.type() == QEvent.KeyPress and event.key() == Qt.Key_Escape):
            self.closePopup()
        return super().eventFilter(watched, event)
