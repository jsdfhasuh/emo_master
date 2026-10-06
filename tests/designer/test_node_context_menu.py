"""Exercise native context events and the existing Designer edit commands."""
from copy import deepcopy
from types import SimpleNamespace

import pytest
import emo_master  # noqa: F401 - retain the application's Windows DLL preload order before Qt
from PySide2.QtCore import QPointF, Qt
from PySide2.QtGui import QContextMenuEvent, QTransform
from PySide2.QtTest import QTest
from PySide2.QtWidgets import QApplication, QMenu

from tests.designer.test_run_inspector_ui import deliver


PAYLOAD = {'operatorId': 'test.counter', 'displayName': '计数算子',
           'inputPorts': {'value': 'integer'}, 'outputPorts': {'count': 'integer'},
           'paramSchema': {'type': 'object', 'properties': {'offset': {'type': 'integer', 'default': 0}}}}


class Client:
    def __init__(self):
        self.starts = 0
        self.stops = 0

    def listOperators(self):
        return [SimpleNamespace(**PAYLOAD, version='1.0.0')]

    def loadProject(self, _path):
        return SimpleNamespace(ok=True, message='ok')

    def startJob(self, *_args, **_kwargs):
        self.starts += 1
        raise AssertionError('context menus must not start Jobs')

    def stopJob(self, *_args, **_kwargs):
        self.stops += 1
        raise AssertionError('context menus must not stop Jobs')


@pytest.fixture
def canvas(ownedDesignerWindow):
    client = Client()
    window = ownedDesignerWindow(client)
    window.operatorCatalogController.refreshOperators(window._classifyOperator)
    for title in ('第一算子', '第二算子'):
        window.addNodeFromOperatorPayload({**deepcopy(PAYLOAD), 'displayName': title})
    nodes = [key for key, node in window.flowModel.nodes.items() if node.kind == 'operator']
    assert len(nodes) == 2
    window.pageCoordinator.sync()
    window.pageCoordinator.session.markSaved()
    return window, nodes, client


def actions(menu):
    return {action.data(): action for action in menu.actions() if isinstance(action.data(), str)}


def contextEvent(window, point, reason=QContextMenuEvent.Mouse):
    viewport = window.flowView.viewport()
    event = QContextMenuEvent(reason, point, viewport.mapToGlobal(point))
    QApplication.sendEvent(viewport, event)
    QApplication.processEvents()


@pytest.mark.parametrize('scale', [.5, 1., 1.75])
def testRealContextEventHitsTitleAfterZoomAndSelectsOnlyClickedNode(canvas, scale):
    window, (first, second), client = canvas
    window.resize(1280, 720)
    window.show()
    QApplication.processEvents()
    scene = window.flowScene
    scene._nodeItems[first].setSelected(True)
    scene._nodeItems[second].setSelected(True)
    window.flowView.setTransform(QTransform().scale(scale, scale))
    item = scene._nodeItems[first]
    window.flowView.centerOn(item)
    point = window.flowView.mapFromScene(item.mapToScene(QPointF(55, 18)))
    before = window.pageCoordinator.session._signature()
    history = len(window.pageCoordinator.session._undo)
    contextEvent(window, point)
    menu = window.nodeContextMenu.menu
    assert menu.isVisible() and QApplication.activePopupWidget() is menu
    assert window.flowModel.selectedNodeId == first
    assert scene.getSelectedNodeIds() == [first] and not scene.getSelectedEdgeKeys()
    assert set(actions(menu)) == {'configure', 'results', 'copy', 'delete'}
    assert window.pageCoordinator.session._signature() == before
    assert len(window.pageCoordinator.session._undo) == history
    assert client.starts == client.stops == 0
    QTest.keyClick(menu, Qt.Key_Escape)
    assert not menu.isVisible()


def testKeyboardContextUsesSelectedNodeAndEmptyCanvasDoesNotOpenNodeMenu(canvas):
    window, (first, _second), _client = canvas
    window.resize(1280, 720)
    window.show()
    QApplication.processEvents()
    window.flowScene.setNodeSelected(first)
    point = window.flowView.mapFromScene(window.flowScene.getNodeCenter(first)[0],
                                         window.flowScene.getNodeCenter(first)[1])
    contextEvent(window, point, QContextMenuEvent.Keyboard)
    assert window.nodeContextMenu.menu.isVisible()
    window.nodeContextMenu.closePopup()
    window.flowScene.clearGraph()
    contextEvent(window, window.flowView.viewport().rect().center())
    assert not window.nodeContextMenu.menu.isVisible()
    assert not window.flowScene.handleNodeContextMenu(first, 0, 0)


def testConfigurationUsesRealExistingEditorWithoutEditingDraft(canvas):
    window, (first, _second), client = canvas
    before = window.pageCoordinator.session._signature()
    menu = window.nodeContextMenu.buildMenu(first)
    actions(menu)['configure'].trigger()
    assert window.activeParamNodeId == first
    assert window.nodeParamDialog is not None and window.nodeParamDialog.isVisible()
    assert window.operatorEditorManager.count() == 1
    assert window.pageCoordinator.session._signature() == before
    assert client.starts == client.stops == 0


def testCopyAddsExactlyOneUndoAndSaveReopenKeepsNodeAndLayout(canvas, ownedDesignerWindow, tmp_path):
    window, (first, second), client = canvas
    original = deepcopy(window.flowModel.nodes[first].params)
    originalNodes = set(window.flowModel.nodes)
    history = len(window.pageCoordinator.session._undo)
    actions(window.nodeContextMenu.buildMenu(first))['copy'].trigger()
    copies = set(window.flowModel.nodes) - originalNodes
    assert len(copies) == 1
    copy = copies.pop()
    position = window.flowScene.getNodePositions()[copy]
    assert window.flowModel.nodes[copy].params == original
    assert len(window.pageCoordinator.session._undo) == history + 1
    window.pageCoordinator.history()
    assert set(window.flowModel.nodes) == originalNodes
    window.pageCoordinator.history(redo=True)
    assert copy in window.flowModel.nodes and window.flowScene.getNodePositions()[copy] == position
    assert window.saveProjectToDirectory(str(tmp_path / '中文 保存目录'))
    target = ownedDesignerWindow(Client())
    assert target.loadProjectDirectory(str(tmp_path / '中文 保存目录'))
    assert set(target.flowModel.nodes) == originalNodes | {copy}
    assert target.flowScene.getNodePositions()[copy] == position
    assert client.starts == client.stops == 0


def testDeleteOnlyTargetAndIncidentEdgeAndUndoRestoresBoth(canvas):
    window, (first, second), _client = canvas
    originalNodes = set(window.flowModel.nodes)
    connection = window.connectPorts(first, 'count', second, 'value')
    assert connection is not None
    window.flowScene.renderEdge(connection)
    edge = next(iter(window.flowScene._edgeItems.values()))
    edge.setSelected(True)
    window.flowScene._nodeItems[second].setSelected(True)
    history = len(window.pageCoordinator.session._undo)
    actions(window.nodeContextMenu.buildMenu(first))['delete'].trigger()
    assert set(window.flowModel.nodes) == originalNodes - {first} and not window.flowModel.edges
    assert len(window.pageCoordinator.session._undo) == history + 1
    window.pageCoordinator.history()
    assert set(window.flowModel.nodes) == originalNodes
    assert len(window.flowModel.edges) == 1
    window.pageCoordinator.history(redo=True)
    assert set(window.flowModel.nodes) == originalNodes - {first}


@pytest.mark.parametrize('key', ['configure', 'copy', 'delete'])
def testRunningDisablesMutationAndTriggerRechecksState(canvas, key, monkeypatch):
    window, (first, _second), client = canvas
    staleAction = actions(window.nodeContextMenu.buildMenu(first))[key]
    before = deepcopy(window.flowModel.toProjectGraph())
    calls = []
    monkeypatch.setattr(window, 'openNodeParamDialog', lambda node: calls.append(node))
    window._setIsJobRunning(True)
    staleAction.trigger()
    assert not calls and window.flowModel.toProjectGraph() == before
    menu = window.nodeContextMenu.buildMenu(first)
    assert not actions(menu)[key].isEnabled()
    assert '运行中' in actions(menu)[key].toolTip()
    assert actions(menu)['results'].isEnabled()
    assert client.starts == client.stops == 0


@pytest.mark.parametrize('kind', ['workflow_input', 'workflow_output', 'subflow', 'loop'])
def testBoundaryAndControlNodesKeepExistingCopyRestrictions(canvas, kind):
    window, (first, _second), _client = canvas
    if kind.startswith('workflow_'):
        first = next(key for key, node in window.flowModel.nodes.items() if node.kind == kind)
    node = window.flowModel.nodes[first]
    originalKind = node.kind
    try:
        node.kind = kind
        entries = actions(window.nodeContextMenu.buildMenu(first))
        assert not entries['copy'].isEnabled()
        if kind.startswith('workflow_'):
            assert not entries['delete'].isEnabled() and not entries['configure'].isEnabled()
        else:
            assert entries['delete'].isEnabled() and entries['configure'].isEnabled()
    finally:
        node.kind = originalKind


def testResultsReuseActualHistoryWithoutJobAndRepeatedSelectionDoesNotClearImage(canvas, monkeypatch):
    window, (first, _second), client = canvas
    window.flowScene.setNodeSelected(first)
    window._setCurrentJobId('job')
    coordinator = window.nodeResultCoordinator
    coordinator.accepted('job')
    deliver(window, first, value=0)
    before = window.pageCoordinator.session._signature()
    selections = []
    window.flowScene.selectionChanged.connect(lambda: selections.append(window.flowModel.selectedNodeId))
    resets = []
    monkeypatch.setattr(coordinator, '_clearImage', lambda *_args: resets.append(True))
    for _ in range(3):
        actions(window.nodeContextMenu.buildMenu(first))['results'].trigger()
    assert 'count = 0' in coordinator.panel.values.toPlainText()
    assert not selections and not resets
    assert window.pageCoordinator.session._signature() == before
    assert client.starts == client.stops == 0


@pytest.mark.parametrize('change', ['clear', 'reload', 'project', 'workspace', 'close'])
def testOldActionCannotOperateAfterContextChanges(canvas, change, monkeypatch):
    window, (first, second), _client = canvas
    handler = window.nodeContextMenu
    originalNodes = set(window.flowModel.nodes)
    menu = handler.buildMenu(first)
    action = actions(menu)['delete']
    if change == 'clear':
        window.flowScene.clearGraph()
    elif change == 'reload':
        payload = window.workflowController.buildPayload()
        window.workflowController.loadPayload(payload)
    elif change == 'project':
        window.workflowStore.project['projectId'] = 'new-project'
    elif change == 'workspace':
        with monkeypatch.context() as mode:
            mode.setattr(window.pageCoordinator, 'pageActive', lambda: True)
            action.trigger()
            assert set(window.flowModel.nodes) == originalNodes
            assert handler.buildMenu(first) is None
        return
    else:
        handler.closePopup()
    action.trigger()
    assert set(window.flowModel.nodes) == originalNodes


def testActualMenuMouseClickCopiesOnceAndClosesPopup(canvas):
    window, (first, _second), _client = canvas
    window.resize(1280, 720)
    window.show()
    QApplication.processEvents()
    before = set(window.flowModel.nodes)
    history = len(window.pageCoordinator.session._undo)
    point = window.flowView.mapFromScene(window.flowScene._nodeItems[first].scenePos() + QPointF(55, 18))
    contextEvent(window, point)
    menu = window.nodeContextMenu.menu
    action = actions(menu)['copy']
    QTest.mouseClick(menu, Qt.LeftButton, pos=menu.actionGeometry(action).center())
    QApplication.processEvents()
    assert len(set(window.flowModel.nodes) - before) == 1
    assert len(window.pageCoordinator.session._undo) == history + 1
    assert not menu.isVisible()


def testRightClickOnConnectionKeepsNodeMenuClosed(canvas):
    window, (first, second), _client = canvas
    connection = window.connectPorts(first, 'count', second, 'value')
    assert connection is not None
    window.flowScene.renderEdge(connection)
    window.resize(1280, 720)
    window.show()
    QApplication.processEvents()
    edge = next(iter(window.flowScene._edgeItems.values()))
    point = window.flowView.mapFromScene(edge.mapToScene(edge.path().pointAtPercent(.5)))
    before = deepcopy(window.flowModel.toProjectGraph())
    contextEvent(window, point)
    assert not window.nodeContextMenu.menu.isVisible()
    assert window.flowModel.toProjectGraph() == before


def testOneOwnedPopupIsReusedAndWindowHideInvalidatesActions(canvas):
    window, (first, _second), _client = canvas
    window.show()
    QApplication.processEvents()
    handler = window.nodeContextMenu
    for _ in range(20):
        assert handler.buildMenu(first) is handler.menu
    assert len([menu for menu in window.findChildren(QMenu) if menu.objectName() == 'nodeContextMenu']) == 1
    action = actions(handler.menu)['delete']
    window.hide()
    action.trigger()
    assert first in window.flowModel.nodes
