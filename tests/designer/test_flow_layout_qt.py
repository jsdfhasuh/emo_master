from copy import deepcopy
from types import SimpleNamespace

import emo_master  # noqa: F401 - preload Windows native dependencies before Qt
from PySide2.QtCore import QCoreApplication, QEvent, QPoint, QPointF, Qt
from PySide2.QtTest import QTest
from PySide2.QtWidgets import QGraphicsView
from shiboken2 import isValid

from emo_master.apps.designer.ui.flow_scene import FlowEdgeViewModel, FlowNodeViewModel
from emo_master.apps.designer.ui.flow_routing import Rect, intersects


def addNode(scene, key, x, y, count=1):
    scene.addFlowNode(FlowNodeViewModel(key, key, x, y,
        {f'in{i}': 'number' for i in range(count)}, {f'out{i}': 'number' for i in range(count)}))


def testMovingUnrelatedObstacleReroutesAndBlockedSelection(ownedFlowScene, designerApplication):
    scene = ownedFlowScene()
    addNode(scene, 's', 0, 0)
    addNode(scene, 't', 800, 0)
    addNode(scene, 'obstacle', 360, 250)
    key = ('s', 'out0', 't', 'in0')
    scene.renderEdge(FlowEdgeViewModel(*key))
    designerApplication.processEvents()
    edge = scene._edgeItems[key]
    before = edge.route.points
    scene._nodeItems['obstacle'].setPos(360, 0)
    assert scene._routeTimer.isActive()
    designerApplication.processEvents()
    assert not edge.route.blocked
    assert edge.route.points != before
    obstacle = scene._nodeItems['obstacle'].sceneBoundingRect()
    rect = Rect(obstacle.left(), obstacle.top(), obstacle.right(), obstacle.bottom())
    assert all(not intersects(a, b, rect.expanded(12)) for a, b in zip(edge.route.points, edge.route.points[1:]))
    # Curved path elements demonstrate real Qt rounding, not just a polyline.
    assert any(edge.path().elementAt(i).isCurveTo() for i in range(edge.path().elementCount()))
    scene._nodeItems['obstacle'].setPos(250, 0)
    designerApplication.processEvents()
    assert edge.route.blocked and '受阻' in edge.toolTip()
    edge.setSelected(True)
    assert edge.pen().style() == Qt.DashLine
    assert scene.getSelectedEdgeKeys() == [key]
    scene.removeFlowEdge(*key)
    designerApplication.processEvents()
    assert not scene._edgeItems


def testBatchClearAndNativeDestructionCancelRefresh(ownedFlowScene, designerApplication, monkeypatch):
    scene = ownedFlowScene()
    for i in range(8):
        addNode(scene, str(i), 0, 0, i + 1)
    calls = []
    route = scene._routeEdges
    monkeypatch.setattr(scene, '_routeEdges', lambda items: (calls.append(len(items)), route(items)))
    scene.layoutNodesFlow()
    assert calls == [0]
    assert not scene._routeTimer.isActive()
    scene._nodeItems['0'].setPos(5, 10)
    scene.startConnectionDrag(scene._portItems[('0', 'output', 'out0')])
    assert scene._routeTimer.isActive()
    scene.clear()
    designerApplication.processEvents()
    assert calls == [0]
    assert not scene._nodeItems and not scene.items()
    addNode(scene, 'new', 10, 10)
    timer = scene._routeTimer
    scene.deleteLater()
    QCoreApplication.sendPostedEvents(scene, QEvent.DeferredDelete)
    designerApplication.processEvents()
    assert not isValid(scene) and not isValid(timer)


def testRealQtPortDragZoomHitSelectionAndNodeRelease(ownedFlowScene, designerApplication):
    scene = ownedFlowScene()
    addNode(scene, 's', 0, 0)
    addNode(scene, 't', 600, 80)
    scene.setConnectionHandler(lambda *args: FlowEdgeViewModel(*args))
    view = QGraphicsView(scene)
    view.resize(1100, 600)
    view.show()
    designerApplication.processEvents()
    try:
        start = view.mapFromScene(scene._portItems[('s', 'output', 'out0')].sceneBoundingRect().center())
        end = view.mapFromScene(scene._portItems[('t', 'input', 'in0')].sceneBoundingRect().center())
        QTest.mousePress(view.viewport(), Qt.LeftButton, Qt.NoModifier, start)
        QTest.mouseMove(view.viewport(), end)
        QTest.mouseRelease(view.viewport(), Qt.LeftButton, Qt.NoModifier, end)
        designerApplication.processEvents()
        key = ('s', 'out0', 't', 'in0')
        edge = scene._edgeItems[key]
        assert not edge.route.blocked
        before = edge.route.points
        grab = view.mapFromScene(scene._nodeItems['t'].pos() + QPointF(80, 15))
        QTest.mousePress(view.viewport(), Qt.LeftButton, Qt.NoModifier, grab)
        # Native item movement path while the node owns the mouse grab.
        assert scene.mouseGrabberItem() is scene._nodeItems['t']
        scene._nodeItems['t'].moveBy(0, 90)
        assert edge.route.points != before
        QTest.mouseRelease(view.viewport(), Qt.LeftButton, Qt.NoModifier, grab + QPoint(0, 90))
        for factor in (0.65, 1.8):
            view.scale(factor, factor)
            designerApplication.processEvents()
            # Select a long segment outside both nodes at the current zoom.
            a, b = max(zip(edge.route.points, edge.route.points[1:]),
                       key=lambda ab: abs(ab[0][0] - ab[1][0]) + abs(ab[0][1] - ab[1][1]))
            point = view.mapFromScene(QPointF((a[0] + b[0]) / 2, (a[1] + b[1]) / 2))
            QTest.mouseClick(view.viewport(), Qt.LeftButton, Qt.NoModifier, point)
            assert key in scene.getSelectedEdgeKeys()
        scene.removeFlowEdge(*key)
        assert not scene._edgeItems
    finally:
        view.close()
        view.setScene(None)


def testAutoLayoutOneUndoRedoSaveReopenAndOtherWorkflowUnchanged(ownedDesignerWindow, tmp_path, designerApplication):
    client = SimpleNamespace(listOperators=lambda: [], loadProject=lambda path: SimpleNamespace(ok=True, message='ok'))
    window = ownedDesignerWindow(client)
    window.createWorkflow('unaffected')
    other = window.activeWorkflowId
    window.createWorkflow('layout')
    active = window.activeWorkflowId
    model, scene = window.flowModel, window.flowScene
    nodes = []
    for i in range(4):
        key = model.addNode('test.number', f'Node {i}', {'in0': 'number'}, {'out0': 'number'})
        model.setNodeParams(key, {'value': i})
        nodes.append(key)
    for a, b in zip(nodes, nodes[1:]):
        model.connectNodes(a, 'out0', b, 'in0')
    window.workflowController.refreshActiveWorkflow()
    for i, key in enumerate(nodes):
        scene._nodeItems[key].setPos((3 - i) * 350, i * 100)
    c = window.pageCoordinator
    c.sync()
    before = scene.getNodePositions()
    graph = deepcopy(model.toProjectGraph())
    untouched = deepcopy(window.workflowStore.workflows[other])
    history = len(c.session._undo)
    window.autoLayoutNodes()
    after = scene.getNodePositions()
    assert all(after[b][0] > after[a][0] for a, b in zip(nodes, nodes[1:]))
    assert len(c.session._undo) == history + 1
    c.history()
    assert scene.getNodePositions() == before
    c.history(True)
    assert scene.getNodePositions() == after
    window.autoLayoutNodes()
    assert len(c.session._undo) == history + 1  # second application is a no-op
    assert model.toProjectGraph() == graph
    assert window.workflowStore.workflows[other] == untouched
    assert window.saveProjectToDirectory(str(tmp_path / 'layout-project'))
    assert window.loadProjectDirectory(str(tmp_path / 'layout-project'))
    window.workflowController.switchWorkflow(active)
    designerApplication.processEvents()
    assert scene.getNodePositions() == after
    assert model.toProjectGraph() == graph
    assert window.workflowStore.workflows[other] == untouched
