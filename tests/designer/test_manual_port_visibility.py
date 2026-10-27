from copy import deepcopy
from itertools import combinations
import os
from pathlib import Path

import pytest
from PySide2.QtCore import Qt
from PySide2.QtTest import QTest
from PySide2.QtWidgets import QMenu, QGraphicsView

from emo_master.apps.designer.ui.flow_scene import FlowNodeViewModel, FlowEdgeViewModel
from tests.designer.test_node_context_menu import Client, actions, contextEvent
from tests.designer.test_node_context_menu import canvas as canvas
from tests.designer.test_unused_port_folding import visible


@pytest.mark.parametrize('kind', ['operator', 'subflow', 'loop', 'workflow_input', 'workflow_output'])
def testHideOneSideAndRestoreFromNodeMenuPreservesModel(ownedFlowScene, kind):
    scene = ownedFlowScene()
    model = FlowNodeViewModel('node', '逐人筛选骑行人员', 20, 20,
        {'image': 'image', 'hasTarget': 'boolean'}, {'image': 'image', 'hasTarget': 'boolean'}, kind=kind,
        summaryLines=(('继续条件：hasTarget（布尔值）', None),),
        inputPortLabels={'hasTarget': '初始条件 (hasTarget)'}, outputPortLabels={'hasTarget': '最终条件 (hasTarget)'})
    before = deepcopy(model)
    scene.addFlowNode(model)
    scene.setPortHidden('node', 'output', 'image', True)
    assert visible(scene, 'node') == {('input', 'image'), ('input', 'hasTarget'), ('output', 'hasTarget')}
    scene.setPortHidden('node', 'input', 'image', True)
    menu = QMenu()
    scene.addPortVisibilityActions(menu, 'node')
    entries = actions(menu)
    restore = entries['showPorts'].menu().actions()
    assert [a.text() for a in restore] == ['输入 · image', '输出 · image']
    restore[1].trigger()
    assert ('output', 'image') in visible(scene, 'node')
    assert ('input', 'image') not in visible(scene, 'node')
    entries['showAllPorts'].trigger()
    assert len(visible(scene, 'node')) == 4
    assert not scene._nodeItems['node'].portVisibilityButton.isVisible()
    assert model == before
    menu.deleteLater()


@pytest.mark.parametrize('direction,name', [('input', 'value'), ('output', 'count')])
@pytest.mark.parametrize('hitLabel', [False, True])
def testRealRightClickOnPortOrLabelAndRestoreFromNode(canvas, direction, name, hitLabel):
    window, (first, _second), client = canvas
    window.resize(1280, 720)
    window.show()
    scene = window.flowScene
    window.flowView.centerOn(scene._nodeItems[first])
    before = window.pageCoordinator.session._signature()
    history = len(window.pageCoordinator.session._undo)
    port = scene._portItems[(first, direction, name)]
    target = port.label if hitLabel else port
    point = window.flowView.mapFromScene(target.sceneBoundingRect().center())
    QTest.mouseClick(window.flowView.viewport(), Qt.RightButton, pos=point)
    assert scene._dragSourcePort is None
    contextEvent(window, point)
    assert scene._portMenu.isVisible()
    actions(scene._portMenu)['hidePort'].trigger()
    scene._portMenu.close()
    assert not port.isVisible() and not port.label.isVisible()
    menu = window.nodeContextMenu.buildMenu(first)
    actions(menu)['showPorts'].menu().actions()[0].trigger()
    assert port.isVisible() and port.label.isVisible()
    assert window.pageCoordinator.session._signature() == before
    assert len(window.pageCoordinator.session._undo) == history
    assert client.starts == client.stops == 0


def testConnectedPinCannotHideAndNewConnectionRemainsVisible(canvas):
    window, (first, second), _client = canvas
    scene = window.flowScene
    scene.hideUnusedPorts(first)
    connection = window.connectPorts(first, 'count', second, 'value')
    assert connection is not None
    scene.renderEdge(connection)
    source = scene._portItems[(first, 'output', 'count')]
    assert source.isVisible()
    for nodeId, direction, name in [(first, 'output', 'count'), (second, 'input', 'value')]:
        action = actions(scene.buildPortContextMenu(nodeId, direction, name))['hidePort']
        assert not action.isEnabled() and '已连接' in action.toolTip()
        scene.setPortHidden(nodeId, direction, name, True)
        assert (direction, name) in visible(scene, nodeId)
    scene.removeFlowEdge(first, 'count', second, 'value')
    assert source.isVisible()


def testVisibilityActionsRemainAvailableWhileRunningWithoutEditing(canvas):
    window, (first, _second), client = canvas
    window._setIsJobRunning(True)
    before = window.pageCoordinator.session._signature()
    menu = window.nodeContextMenu.buildMenu(first)
    entries = actions(menu)
    assert not entries['delete'].isEnabled()
    assert entries['hideUnusedPorts'].isEnabled()
    entries['hideUnusedPorts'].trigger()
    assert not visible(window.flowScene, first)
    actions(window.nodeContextMenu.buildMenu(first))['showAllPorts'].trigger()
    assert visible(window.flowScene, first) == {('input', 'value'), ('output', 'count')}
    assert window.pageCoordinator.session._signature() == before
    assert client.starts == client.stops == 0
    window._setIsJobRunning(False)


def testDisplayChoicesSurviveRefreshWorkflowSwitchSaveAndReopen(canvas, ownedDesignerWindow, tmp_path):
    window, (first, _second), _client = canvas
    scene = window.flowScene
    workflowId = window.workflowStore.activeWorkflowId
    scene.setPortHidden(first, 'output', 'count', True)
    window.workflowController.refreshActiveWorkflow()
    assert ('output', 'count') not in visible(scene, first)
    window.workflowController.createWorkflow('另一个工作流')
    window.workflowController.switchWorkflow(workflowId)
    assert ('output', 'count') not in visible(scene, first)
    assert window.saveProjectToDirectory(str(tmp_path / 'manual-ports.emoproj'))
    projectFile = window.projectController.currentProjectFile
    saved = projectFile.read_bytes()
    window.settingsStore.sync()
    target = ownedDesignerWindow(Client())
    assert target.loadProjectDirectory(str(projectFile))
    assert ('output', 'count') not in visible(target.flowScene, first)
    assert ('input', 'value') in visible(target.flowScene, first)
    assert projectFile.read_bytes() == saved
    before = target.pageCoordinator.session._signature()
    target.flowScene.showAllPorts(first)
    target.workflowController.refreshActiveWorkflow()
    assert ('output', 'count') in visible(target.flowScene, first)
    assert target.pageCoordinator.session._signature() == before
    target.settingsStore.sync()
    reopened = ownedDesignerWindow(Client())
    assert reopened.loadProjectDirectory(str(projectFile))
    assert ('output', 'count') in visible(reopened.flowScene, first)


def testPreferencesAreScopedToProjectAndWorkflow(canvas):
    window, (first, _second), _client = canvas
    window.flowScene.setPortHidden(first, 'input', 'value', True)
    original = window.workflowStore.activeWorkflowId
    window.workflowController.captureActiveWorkflow()
    payload = window.workflowStore.toPayload()
    originalProject = payload['project']['projectId']
    other = deepcopy(payload)
    other['project']['projectId'] = 'different-project'
    window.workflowController.loadPayload(other)
    assert ('input', 'value') in visible(window.flowScene, first)
    window.workflowController.loadPayload(payload)
    assert ('input', 'value') not in visible(window.flowScene, first)
    window.workflowController.createWorkflow('另一个工作流')
    assert window._readHiddenPorts(first) == set()
    window.workflowController.switchWorkflow(original)
    assert window._currentProjectId() == originalProject
    assert ('input', 'value') not in visible(window.flowScene, first)


def testLoopPinLayoutAndEndpointsAfterManualHiding(ownedFlowScene, designerApplication):
    scene = ownedFlowScene()
    ports = {'image': 'image', 'frame': 'image', 'detections': 'detectionCollection', 'count': 'integer',
             'frameIndex': 'integer', 'timestampSec': 'number', 'hasTarget': 'boolean', 'vehicles': 'detectionCollection'}
    scene.addFlowNode(FlowNodeViewModel('source', '检测结果', 20, 40, {}, ports))
    scene.addFlowNode(FlowNodeViewModel('loop', '逐人筛选骑行人员', 420, 40, ports, ports, kind='loop',
        summaryLines=(('继续条件：hasTarget（布尔值）', None), ('true：执行循环体；false：结束', None),
                      ('循环体：逐人判断骑乘', None)),
        inputPortLabels={'hasTarget': '初始条件 (hasTarget)'}, outputPortLabels={'hasTarget': '最终条件 (hasTarget)'}))
    for name in ports:
        scene.renderEdge(FlowEdgeViewModel('source', name, 'loop', name))
    before = deepcopy(scene._nodeItems['loop'].model)
    view = QGraphicsView(scene)
    view.resize(1100, 600)
    view.show()
    designerApplication.processEvents()
    scene.hideUnusedPorts('loop')
    scene.refreshAllRoutes()
    assert visible(scene, 'loop') == {('input', name) for name in ports}
    assert scene._nodeItems['loop'].model == before
    assert '8' in scene._nodeItems['loop'].portVisibilityButton.toolTip()
    for edge in scene._edgeItems.values():
        start = edge.sourcePort.sceneBoundingRect().center()
        end = edge.targetPort.sceneBoundingRect().center()
        assert edge.route.points[0] == (start.x(), start.y())
        assert edge.route.points[-1] == (end.x(), end.y())
    screenshotDir = os.environ.get('EMO_MANUAL_PORT_SCREEN_DIR')
    if screenshotDir:
        target = Path(screenshotDir)
        target.mkdir(parents=True, exist_ok=True)
        assert view.viewport().grab().save(str(target / 'loop-hidden-outputs.png'))
        scene.handleNodeContextMenu('loop', view.mapToGlobal(view.rect().center()).x(),
                                    view.mapToGlobal(view.rect().center()).y())
        designerApplication.processEvents()
        assert scene._portMenu.grab().save(str(target / 'node-port-menu.png'))
        scene._closePortMenu()
        scene.showAllPorts('loop')
        scene.refreshAllRoutes()
        designerApplication.processEvents()
        assert view.viewport().grab().save(str(target / 'loop-all-ports.png'))
    view.close()
    view.setScene(None)


@pytest.mark.parametrize('kind', ['operator', 'loop'])
@pytest.mark.parametrize('layout', ['flow', 'grid', 'append'])
def testAutomaticPlacementReservesSpaceForRestoringPorts(ownedFlowScene, designerApplication, kind, layout):
    scene = ownedFlowScene()
    ports = {f'p{i}': 'integer' for i in range(10)}
    scene.addFlowNode(FlowNodeViewModel('source', '输入数据', 20, 20, {}, {'out': 'integer'}))
    firstX, firstY = (20, 160) if layout == 'append' else (400, 20)
    scene.addFlowNode(FlowNodeViewModel('a', '可隐藏引脚的节点', firstX, firstY, ports, ports, kind=kind,
        summaryLines=(('继续条件：示例布尔值', None),) if kind == 'loop' else ()))
    firstEdge = FlowEdgeViewModel('source', 'out', 'a', 'p0')
    scene.renderEdge(firstEdge)
    fullHeight = scene._nodeItems['a'].rect().height()
    scene.hideUnusedPorts('a')
    assert scene._nodeItems['a'].rect().height() < fullHeight
    x, y = scene.nextNodePosition() if layout == 'append' else (400, 500)
    scene.addFlowNode(FlowNodeViewModel('b', '下方节点', x, y, ports, ports))
    scene.renderEdge(FlowEdgeViewModel('source', 'out', 'b', 'p0'))
    if layout == 'flow':
        scene.layoutNodesFlow()
    elif layout == 'grid':
        scene.layoutNodesGrid(columns=1)
    before = scene.getNodePositions()
    models = {key: deepcopy(item.model) for key, item in scene._nodeItems.items()}
    edges = set(scene._edgeItems)
    assert not scene._nodeItems['a'].sceneBoundingRect().intersects(scene._nodeItems['b'].sceneBoundingRect())
    for _ in range(2):
        scene.showAllPorts('a')
        designerApplication.processEvents()
        assert scene._nodeItems['a'].rect().height() == fullHeight
        assert scene.getNodePositions() == before
        for first, second in combinations(scene._nodeItems.values(), 2):
            assert not first.sceneBoundingRect().intersects(second.sceneBoundingRect())
        for edge in scene._edgeItems.values():
            start = edge.sourcePort.sceneBoundingRect().center()
            end = edge.targetPort.sceneBoundingRect().center()
            assert edge.route.points[0] == (start.x(), start.y())
            assert edge.route.points[-1] == (end.x(), end.y())
        scene.hideUnusedPorts('a')
    assert {key: item.model for key, item in scene._nodeItems.items()} == models
    assert set(scene._edgeItems) == edges
    scene.showAllPorts('a')
    screenshotDir = os.environ.get('EMO_MANUAL_PORT_SCREEN_DIR')
    if screenshotDir and layout == 'flow' and kind == 'loop':
        view = QGraphicsView(scene)
        view.resize(1100, 850)
        view.show()
        designerApplication.processEvents()
        view.fitInView(scene.itemsBoundingRect().adjusted(-20, -20, 20, 20), Qt.KeepAspectRatio)
        designerApplication.processEvents()
        for item in scene._nodeItems.values():
            assert view.viewport().rect().contains(view.mapFromScene(item.sceneBoundingRect()).boundingRect())
        target = Path(screenshotDir)
        target.mkdir(parents=True, exist_ok=True)
        assert view.viewport().grab().save(str(target / 'restored-ports-no-overlap.png'))
        view.close()
        view.setScene(None)
