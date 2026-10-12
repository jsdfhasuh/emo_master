import pytest
from PySide2.QtCore import QEvent, QPointF, QRectF, Qt
from PySide2.QtGui import QColor, QImage, QMouseEvent, QPainter
from PySide2.QtTest import QTest
from PySide2.QtWidgets import QApplication, QGraphicsView

from emo_master.apps.designer.ui.flow_routing import Route
from emo_master.apps.designer.ui.flow_scene import FlowEdgeViewModel, FlowNodeViewModel
from emo_master.apps.designer.ui.flow_edge_presentation import NORMAL_COLOR, FOCUS_COLOR


def connectedScene(owner):
    scene = owner()
    scene.addFlowNode(FlowNodeViewModel("source", "Source", 30, 40, {}, {"out": "number"}))
    for key, x, y in (("first", 500, 40), ("second", 500, 200)):
        scene.addFlowNode(FlowNodeViewModel(key, key.title(), x, y, {"in": "number"}, {}))
        scene.renderEdge(FlowEdgeViewModel("source", "out", key, "in"))
    scene.refreshAllRoutes()
    return scene, list(scene._edgeItems.values())


def renderScene(scene, scale=1):
    bounds = QRectF(0, 0, 800, 360)
    image = QImage(int(bounds.width() * scale), int(bounds.height() * scale), QImage.Format_ARGB32)
    image.fill(QColor("white"))
    painter = QPainter(image)
    painter.setRenderHint(QPainter.Antialiasing)
    scene.render(painter, QRectF(image.rect()), bounds)
    painter.end()
    return image


def ringHasAmber(image, port, scale=1):
    center = port.sceneBoundingRect().center()
    # Sample the outer ring, away from the port's own blue outline and label.
    return any(
        image.pixelColor(x, y) == QColor("#d97706")
        for x in range(int((center.x() - 3) * scale), int((center.x() + 3) * scale) + 1)
        for y in range(int((center.y() - 10) * scale), int((center.y() - 7) * scale) + 1)
    )


def moveMouse(view, point):
    # Deliver movement through the viewport on both native and offscreen QPA.
    QApplication.sendEvent(view.viewport(), QMouseEvent(
        QEvent.MouseMove, QPointF(point), QPointF(view.viewport().mapToGlobal(point)),
        Qt.NoButton, Qt.NoButton, Qt.NoModifier,
    ))


def testSelectionRaisesOnlyTheEdgeAndPreservesRouteAndPortColors(ownedFlowScene):
    scene, (first, second) = connectedScene(ownedFlowScene)
    route = first.route
    ports = (first.sourcePort, first.targetPort)
    original = [(port.pen(), port.brush(), port.shape()) for port in ports]
    first.setSelected(True)
    assert first.pen().color() == QColor("#d97706")
    assert first.pen().widthF() == 3.2
    assert second.zValue() < first.zValue() < scene._nodeItems["source"].zValue()
    scene.refreshAllRoutes()
    assert first.route == route
    assert first.pen().color() == QColor("#d97706")
    for port, (pen, brush, shape) in zip(ports, original):
        assert port.pen() == pen and port.brush() == brush and port.shape() == shape
    scene.clearSelection()
    assert first.pen().color() == QColor(NORMAL_COLOR)
    assert first.pen().widthF() == 1.6
    assert first.zValue() == second.zValue() == -1


def testBlockedRouteRemainsDashedWhileSelected(ownedFlowScene):
    scene, (edge, _) = connectedScene(ownedFlowScene)
    blocked = Route(edge.route.points, True, "Blocked route")
    edge.applyRoute(blocked)
    assert edge.pen().color() == QColor("#dc6b17")
    edge.setSelected(True)
    assert edge.pen().color() == QColor("#d97706")
    assert edge.pen().style() == Qt.DashLine
    assert edge.route == blocked and edge.toolTip().endswith("\nBlocked route")
    assert "Source [source].out -> First [first].in" in edge.toolTip()
    edge.applyRoute(blocked)
    assert edge.pen().color() == QColor("#d97706")
    edge.setSelected(False)
    assert edge.pen().color() == QColor("#dc6b17")
    assert edge.pen().style() == Qt.DashLine


@pytest.mark.parametrize("scale", [1, 1.5, 2])
def testRenderedSelectionStaysAmberAtCrossingsAndEndpoints(ownedFlowScene, scale, tmp_path):
    scene, (first, second) = connectedScene(ownedFlowScene)
    # The unselected edge was inserted last, and would otherwise cover the first.
    first.applyRoute(Route(((300., 160.), (460., 160.))))
    second.applyRoute(Route(((380., 120.), (380., 200.))))
    first.setSelected(True)
    selected = renderScene(scene, scale)
    assert selected.pixelColor(int(380 * scale), int(160 * scale)) == QColor("#d97706")
    assert ringHasAmber(selected, first.sourcePort, scale)
    assert ringHasAmber(selected, first.targetPort, scale)
    assert not ringHasAmber(selected, second.targetPort, scale)
    assert selected.save(str(tmp_path / "selected.png"))
    scene.clearSelection()
    normal = renderScene(scene, scale)
    # Subpixel-width normal wires are antialiased against the scene background.
    actual, expected = normal.pixelColor(int(380 * scale), int(160 * scale)), QColor(NORMAL_COLOR)
    assert max(abs(actual.red() - expected.red()), abs(actual.green() - expected.green()),
               abs(actual.blue() - expected.blue())) <= 8
    assert not ringHasAmber(normal, first.sourcePort, scale)
    assert not ringHasAmber(normal, first.targetPort, scale)
    assert normal.save(str(tmp_path / "normal.png"))


def testSharedSourceRingSurvivesPartialDeselectionAndEdgeRemoval(ownedFlowScene):
    scene, (first, second) = connectedScene(ownedFlowScene)
    first.setSelected(True)
    second.setSelected(True)
    first.setSelected(False)
    image = renderScene(scene)
    assert ringHasAmber(image, second.sourcePort)
    assert ringHasAmber(image, second.targetPort)
    assert not ringHasAmber(image, first.targetPort)
    source = second.sourcePort
    scene.removeFlowEdge("source", "out", "second", "in")
    image = renderScene(scene)
    assert not ringHasAmber(image, source)


@pytest.mark.parametrize("scale", [.65, 1, 1.5, 2])
def testMouseSelectionHoverAndBlankClickRestoreStyle(ownedFlowScene, designerApplication, scale, tmp_path):
    scene, (first, _) = connectedScene(ownedFlowScene)
    view = QGraphicsView(scene)
    view.setRenderHint(QPainter.Antialiasing)
    view.setMouseTracking(True)
    view.resize(1000, 650)
    view.scale(scale, scale)
    view.show()
    assert QTest.qWaitForWindowExposed(view)
    designerApplication.processEvents()
    try:
        point = view.mapFromScene(first.path().pointAtPercent(.5))
        assert view.itemAt(point) is first
        moveMouse(view, point)
        designerApplication.processEvents()
        assert first.pen().color() == QColor(FOCUS_COLOR)
        assert 2.0 < first.pen().widthF() < 3.2
        QTest.mouseClick(view.viewport(), Qt.LeftButton, Qt.NoModifier, point)
        designerApplication.processEvents()
        assert first.isSelected() and first.pen().color() == QColor("#d97706")
        assert view.grab().save(str(tmp_path / "selected-view.png"))
        blank = view.mapFromScene(QPointF(380, 320))
        assert view.itemAt(blank) is None
        moveMouse(view, blank)
        designerApplication.processEvents()
        assert first.pen().color() == QColor("#d97706")
        QTest.mouseClick(view.viewport(), Qt.LeftButton, Qt.NoModifier, blank)
        designerApplication.processEvents()
        assert not scene.selectedItems()
        assert first.pen().color() == QColor(NORMAL_COLOR)
        assert first.pen().widthF() == 1.6
    finally:
        view.close()
        view.setScene(None)
