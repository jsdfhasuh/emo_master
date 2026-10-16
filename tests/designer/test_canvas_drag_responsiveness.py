"""Avoid work on clicks and keep post-drag routing interruptible and derived."""
from itertools import count

import pytest
from PySide2.QtCore import QEvent, QPoint, QPointF, Qt
from PySide2.QtGui import QColor, QImage, QMouseEvent, QPainter
from PySide2.QtWidgets import QApplication, QGraphicsView, QStyle, QStyleOptionGraphicsItem

from emo_master.apps.designer.ui import flow_scene
from emo_master.apps.designer.ui.flow_routing import OrthogonalRouter, Route
from emo_master.apps.designer.ui.flow_scene import FlowEdgeViewModel
from emo_master.apps.designer.ui.designer_graphics_view import DesignerGraphicsView
from tests.designer.test_flow_layout_qt import addNode


def populatedScene(owner):
    scene = owner()
    addNode(scene, 's', 0, 0, 4)
    addNode(scene, 't', 750, 130, 4)
    for i in range(4):
        scene.renderEdge(FlowEdgeViewModel('s', f'out{i}', 't', f'in{i}'))
    scene.refreshAllRoutes()
    return scene


def mouse(view, kind, point, button, buttons):
    QApplication.sendEvent(view.viewport(), QMouseEvent(kind, QPointF(point),
        QPointF(view.viewport().mapToGlobal(point)), button, buttons, Qt.NoModifier))


def drainRoutes(scene):
    for _ in range(1000):
        if not scene._routesDirty:
            return
        scene._routeRefineTimer.stop()
        scene._continueRouteRefinement()
    pytest.fail('route refinement did not finish')


def testClickDoesNotRouteOrCheckpointAndDragCommitsOnce(ownedFlowScene, designerApplication, monkeypatch):
    scene = populatedScene(ownedFlowScene)
    view = QGraphicsView(scene)
    view.resize(1100, 650)
    view.show()
    designerApplication.processEvents()
    routed, committed = [], []
    route = scene._routeEdges
    monkeypatch.setattr(scene, '_routeEdges', lambda items: (routed.append(len(items)), route(items)))
    scene.editCompleted = lambda: committed.append(scene.getNodePositions())
    point = view.mapFromScene(scene._nodeItems['s'].pos() + QPointF(80, 15))
    mouse(view, QEvent.MouseButtonPress, point, Qt.LeftButton, Qt.LeftButton)
    mouse(view, QEvent.MouseButtonRelease, point, Qt.LeftButton, Qt.NoButton)
    assert not routed and not committed
    assert not scene._routeRefineTimer.isActive()
    mouse(view, QEvent.MouseButtonPress, point, Qt.LeftButton, Qt.LeftButton)
    mouse(view, QEvent.MouseMove, point + QPoint(0, 60), Qt.NoButton, Qt.LeftButton)
    duringDrag = list(routed)
    assert duringDrag and scene._routesDirty
    mouse(view, QEvent.MouseButtonRelease, point + QPoint(0, 60), Qt.LeftButton, Qt.NoButton)
    assert routed == duringDrag  # No whole-graph A* inside mouse release.
    assert len(committed) == 1 and scene._routeRefineTimer.isActive()
    nodes, requests = scene._routingInputs(list(scene._edgeItems.values()))
    drainRoutes(scene)
    assert {key: edge.route for key, edge in scene._edgeItems.items()} == OrthogonalRouter(nodes, requests).routeAll()
    assert len(committed) == 1
    view.close()
    view.setScene(None)


@pytest.mark.parametrize('mutation', ['move', 'delete_edge', 'delete_node', 'clear'])
def testNewEditCancelsPartialRouteSnapshot(ownedFlowScene, monkeypatch, mutation):
    scene = populatedScene(ownedFlowScene)
    clock = count()
    monkeypatch.setattr(flow_scene, 'perf_counter', lambda: next(clock) * .004)
    scene._routesDirty = True
    scene._scheduleRouteRefinement()
    scene._routeRefineTimer.stop()
    scene._continueRouteRefinement()
    assert len(scene._refinedRoutes) < len(scene._edgeItems)  # Yield before the whole graph.
    assert scene._routeRefinement is not None and scene._routeRefineTimer.isActive()
    if mutation == 'move':
        scene._nodeItems['t'].moveBy(0, 130)
    elif mutation == 'delete_edge':
        scene.removeFlowEdge('s', 'out0', 't', 'in0')
    elif mutation == 'delete_node':
        scene.removeFlowNode('t')
    else:
        scene.clearGraph()
    assert scene._routeRefinement is None
    assert not scene._refinedRoutes and not scene._routeRefineTimer.isActive()
    scene.refreshAllRoutes()
    nodes, requests = scene._routingInputs(list(scene._edgeItems.values()))
    assert {key: edge.route for key, edge in scene._edgeItems.items()} == OrthogonalRouter(nodes, requests).routeAll()


def testHoldingNodePausesRefinementUntilRelease(ownedFlowScene, designerApplication):
    scene = populatedScene(ownedFlowScene)
    view = QGraphicsView(scene)
    view.show()
    designerApplication.processEvents()
    node = scene._nodeItems['s']
    node.grabMouse()
    scene._routesDirty = True
    scene._continueRouteRefinement()
    assert scene._routeRefinement is None and scene._routeRefineTimer.isActive()
    node.ungrabMouse()
    drainRoutes(scene)
    assert not scene._routesDirty
    view.close()
    view.setScene(None)


@pytest.mark.parametrize('scale', [1, 1.5])
def testSelectedEdgePaintsOnlyItsPathNotBoundingRectangle(ownedFlowScene, scale, tmp_path):
    scene = populatedScene(ownedFlowScene)
    key, edge = next(iter(scene._edgeItems.items()))
    edge.applyRoute(Route(((20., 20.), (170., 20.), (170., 120.), (290., 120.))))
    edge.setSelected(True)
    option = QStyleOptionGraphicsItem()
    option.state |= QStyle.State_Selected
    image = QImage(int(330 * scale), int(160 * scale), QImage.Format_ARGB32)
    image.fill(QColor('white'))
    painter = QPainter(image)
    painter.scale(scale, scale)
    painter.setRenderHint(QPainter.Antialiasing)
    edge.paint(painter, option)
    painter.end()
    # Empty left boundary of the path's bounding box must stay white.
    for x in range(int(16 * scale), int(24 * scale)):
        for y in range(int(40 * scale), int(100 * scale)):
            assert image.pixelColor(x, y) == QColor('white')
    assert image.pixelColor(int(170 * scale), int(70 * scale)) != QColor('white')
    assert scene.getSelectedEdgeKeys() == [key]
    assert edge.pen().widthF() == 3.2
    assert image.save(str(tmp_path / f'selected-edge-{scale}.png'))


def testEmptyAreaInsideBentEdgeRemainsPannableButTheLineIsSelectable(ownedFlowScene, designerApplication):
    scene = populatedScene(ownedFlowScene)
    edge = next(iter(scene._edgeItems.values()))
    edge.applyRoute(Route(((1000., 1000.), (1150., 1000.), (1150., 1100.), (1270., 1100.))))
    view = DesignerGraphicsView(scene)
    view.resize(700, 420)
    view.show()
    view.centerOn(1135, 1050)
    designerApplication.processEvents()
    blank = QPointF(1090, 1020)
    assert not edge.shape().contains(blank)
    point = view.mapFromScene(blank)
    assert view.itemAt(point) is None
    before = view.mapToScene(view.viewport().rect().center())
    assert view.beginPanAt(point.x(), point.y())
    view.updatePanTo(point.x() + 40, point.y() + 30)
    view.endPan()
    after = view.mapToScene(view.viewport().rect().center())
    assert (after - before).manhattanLength() > 60
    # Keep a usable hit tolerance near the line, not a solid polygon interior.
    point = view.mapFromScene(QPointF(1152, 1070))
    assert view.itemAt(point) is edge
    assert not view.beginPanAt(point.x(), point.y())
    mouse(view, QEvent.MouseButtonPress, point, Qt.LeftButton, Qt.LeftButton)
    mouse(view, QEvent.MouseButtonRelease, point, Qt.LeftButton, Qt.NoButton)
    assert edge.isSelected()
    view.close()
    view.setScene(None)
