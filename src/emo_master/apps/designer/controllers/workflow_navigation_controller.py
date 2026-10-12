"""One entry point for Designer browsing, not Runtime calls or undo history."""
from __future__ import annotations

from PySide2.QtCore import QRectF, QTimer
from shiboken2 import isValid

from emo_master.apps.designer.state.navigation_history import NavigationHistory, NavigationLocation
from emo_master.apps.designer.ui.workflow_navigation_controls import NavigationLocationsPopup
from emo_master.core.workflow.loop_contracts import loopWorkflowReferenceFields
from emo_master.ui.workflow_labels import workflowDisplayNames


class WorkflowNavigationController:
    def __init__(self, window):
        self.window = window
        self.history = NavigationHistory()
        self.popup = NavigationLocationsPopup(window)
        self._busy = False
        self.reset()

    @property
    def session(self):
        return self.window._projectInstanceToken

    def reset(self):
        self.popup.hide()
        self.history.reset(self.snapshot())
        self.refresh()

    def nodeExists(self, workflowId, nodeId):
        w = self.window
        if workflowId == w.activeWorkflowId:
            return nodeId in w.flowModel.nodes
        workflow = w.workflowStore.workflows.get(workflowId)
        return workflow is not None and any(node.get('nodeId') == nodeId for node in workflow.nodes)

    def snapshot(self, workflowId=None, nodeId=None):
        w = self.window
        workflowId = workflowId or w.activeWorkflowId
        if workflowId == w.activeWorkflowId:
            center = w.flowView.mapToScene(w.flowView.viewport().rect().center())
            view = (w.flowView.getZoomFactor(), center.x(), center.y())
            nodeId = nodeId or w.flowScene.getSelectedNodeId()
        else:
            saved = w._workflowViewStates.get((w._currentProjectId(), workflowId))
            view = (saved[0], saved[1].x(), saved[1].y()) if saved else None
        return NavigationLocation(self.session, workflowId, nodeId, view)

    def captureCurrent(self):
        if self._busy:
            return
        if self.history.current and self.history.current.session != self.session:
            self.reset()
        else:
            self.history.capture(self.snapshot())

    def valid(self, location):
        return location.session == self.session and location.workflowId in self.window.workflowStore.workflows

    def canMove(self, direction):
        return self.history.destination(direction, self.valid) is not None

    def message(self, text):
        self.window.statusBar().showMessage(text, 7000)

    def navigate(self, workflowId, nodeId=None, *, source=None):
        """A single history transaction, even when both workflow and node change."""
        w = self.window
        target = NavigationLocation(self.session, workflowId, nodeId)
        if not self.valid(target) or (nodeId is not None and not self.nodeExists(workflowId, nodeId)):
            self.message('无法跳转：工作流或节点已不存在。')
            return False
        if source is not None and not self.nodeExists(*source):
            self.message('无法跳转：原调用节点已不存在。')
            return False
        if workflowId == w.activeWorkflowId and nodeId is None and source is None:
            return True
        self.captureCurrent()
        if source is not None:
            origin = self.snapshot(*source)
            if source[0] == w.activeWorkflowId:
                self.history.capture(origin)
            else:
                # An editor may remain open while the main canvas changes tabs.
                self.history.visit(origin)
        self._restore(target)
        self.history.visit(self.snapshot())
        self.refresh()
        return True

    def move(self, direction):
        self.captureCurrent()
        index = self.history.destination(direction, self.valid)
        if index is None:
            self.refresh()
            return False
        target = self.history.entries[index]
        if abs(index - self.history.index) > 1:
            self.message('已跳过被删除工作流的浏览记录。')
        self._restore(target)
        self.history.index = index
        self.history.capture(self.snapshot())
        self.refresh()
        return True

    def _restore(self, location):
        w = self.window
        self._busy = True
        try:
            w._activateWorkflow(location.workflowId)
            nodeId = location.nodeId
            if nodeId is not None and nodeId not in w.flowModel.nodes:
                self.message('已返回工作流；原定位节点已删除。')
                nodeId = None
            if nodeId is None:
                w.flowScene.clearSelection()
                w.flowModel.selectNode(None)
            else:
                w.flowScene.setNodeSelected(nodeId)
                w.flowModel.selectNode(nodeId)
            if location.view is not None:
                zoom, x, y = location.view
                w.flowView.setZoomFactor(zoom)
                w.flowView.centerOn(x, y)
            elif nodeId is not None:
                # A first visit schedules an asynchronous fit. Explicit node
                # navigation must not be reframed after this method returns.
                w.flowView.setZoomFactor(w.flowView.getZoomFactor())
            if nodeId is not None:
                center = w.flowScene.getNodeCenter(nodeId)
                if center is not None:
                    bounds = w.flowScene.getNodeBounds(nodeId)
                    visible = w.flowView.mapToScene(w.flowView.viewport().rect()).boundingRect()
                    if location.view is None or (bounds is not None and not visible.contains(QRectF(*bounds))):
                        w.flowView.centerOn(*center)
            w.onNodeSelectionChanged()
        finally:
            self._busy = False

    def synchronize(self):
        """Reconcile explicit project edits that create/delete the active workflow."""
        if not self._busy:
            self.captureCurrent()
            self.refresh()

    def refresh(self):
        w = self.window
        controls = w.workflowNavigationControls
        names = workflowDisplayNames(w.workflowStore.workflows)
        nodeId = w.flowScene.getSelectedNodeId()
        node = w.flowModel.nodes.get(nodeId)
        label = names.get(w.activeWorkflowId, w.activeWorkflowId)
        if node is not None:
            label += f' › {node.displayName} [{node.nodeId}]'
        controls.location.setText('当前位置：' + label)
        controls.location.setAccessibleName('当前位置：' + label)
        actions = getattr(w, 'designerActions', None)
        if actions is not None:
            actions.scheduleRefresh()

    def targets(self, nodeId=None, workflowId=None):
        """Lightweight definition choices from the same loop reference contract."""
        w = self.window
        workflowId = workflowId or w.activeWorkflowId
        nodeId = nodeId or w.flowScene.getSelectedNodeId()
        if workflowId == w.activeWorkflowId:
            node = w.flowModel.nodes.get(nodeId)
            if node is None:
                return []
            kind, loop, target = node.kind, node.loop, node.targetWorkflowId
        else:
            workflow = w.workflowStore.workflows.get(workflowId)
            raw = next((node for node in workflow.nodes if node.get('nodeId') == nodeId), {}) if workflow else {}
            kind, loop, target = raw.get('kind'), raw.get('loop', {}), raw.get('targetWorkflowId')
        choices = []
        if kind == 'subflow':
            choices = [('子工作流', target)]
        elif kind == 'loop':
            choices = [('循环体' if field == 'bodyWorkflowId' else '布尔条件来源（兼容）', loop.get(field))
                       for field in loopWorkflowReferenceFields(loop)]
        return [(role, target) for role, target in choices if isinstance(target, str) and target]

    def openReference(self, target, sourceWorkflowId, sourceNodeId):
        return self.navigate(target, source=(sourceWorkflowId, sourceNodeId))

    def deferReference(self, target, sourceNodeId):
        # The graphics item will be destroyed by switching scenes. Capture IDs
        # now, defer the switch, and reject stale queued events after replacement.
        w = self.window
        session, source = self.session, w.activeWorkflowId

        def dispatch():
            if not isValid(w) or w.runtimeController._closed or session != self.session:
                return
            if w.activeWorkflowId != source or target not in [key for _, key in self.targets(sourceNodeId, source)]:
                return
            self.openReference(target, source, sourceNodeId)

        QTimer.singleShot(0, dispatch)

    def goToDefinition(self):
        w = self.window
        source, nodeId = w.activeWorkflowId, w.flowScene.getSelectedNodeId()
        choices = self.targets(nodeId, source)
        if not choices:
            return
        if len(choices) == 1:
            self.openReference(choices[0][1], source, nodeId)
            return
        names = workflowDisplayNames(w.workflowStore.workflows)
        session = self.session
        rows = [(f'{role}：{names.get(target, "未找到工作流")}\n{target}',
                 dict(target=target, role=role)) for role, target in choices]

        def choose(payload):
            if session != self.session:
                return
            if (payload['role'], payload['target']) not in self.targets(nodeId, source):
                self.message('引用已变化，请重新选择跳转目标。')
                return
            self.openReference(payload['target'], source, nodeId)

        self.popup.present('选择要进入的工作流', rows, choose, w.workflowNavigationControls.definition)

    def references(self, target):
        # Inspect a copy of the live draft: do not apply editor parameters or
        # mutate/canonicalize the user's graph merely to list references.
        store = self.window.workflowController._capturedWorkflowStoreCopy()
        return [ref for ref in store.getWorkflowDependencyReferences() if ref['targetWorkflowId'] == target]

    def showReferences(self, target=None):
        w = self.window
        target = target or w.activeWorkflowId
        if target not in w.workflowStore.workflows:
            self.message('该工作流已不存在。')
            return
        names = workflowDisplayNames(w.workflowStore.workflows)
        refs = self.references(target)
        rows = []
        for ref in refs:
            source, nodeId = ref['sourceWorkflowId'], ref['sourceNodeId']
            label = (f'{names[source]} → {ref["sourceNodeName"]}\n'
                     f'{source} / {nodeId} · {ref["relationLabel"]}')
            rows.append((label, ref))
        session = self.session

        def choose(payload):
            if session != self.session:
                return
            def identity(ref):
                return ref['sourceWorkflowId'], ref['sourceNodeId'], ref['relation'], ref['targetWorkflowId']
            if target not in w.workflowStore.workflows or not any(
                    identity(ref) == identity(payload) for ref in self.references(target)):
                self.message('调用来源已变化或被删除，请重新查找。')
                return
            self.navigate(payload['sourceWorkflowId'], payload['sourceNodeId'])

        title = f'{names[target]} 的调用来源（{len(rows)}）'
        self.popup.present(title, rows, choose, w.workflowNavigationControls.references)

