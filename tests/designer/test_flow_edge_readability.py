from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide2.QtCore import QPoint, QPointF, Qt
from PySide2.QtGui import QColor
from PySide2.QtTest import QTest
from PySide2.QtWidgets import QGraphicsView

from emo_master.apps.designer.ui.flow_edge_presentation import DIM_COLOR, NORMAL_COLOR
from emo_master.apps.designer.ui.flow_routing import Route, simplify
from emo_master.apps.designer.ui.flow_scene import FlowEdgeViewModel, FlowNodeViewModel, _SharedWireItem
from emo_master.apps.designer.ui.designer_graphics_view import DesignerGraphicsView
from tests.designer.test_edge_selection_highlight import renderScene


def branchScene(owner):
    scene = owner()
    scene.addFlowNode(FlowNodeViewModel("s", "Source", 20, 20, {}, {"value": "number", "other": "number"}))
    keys = []
    for i in range(4):
        scene.addFlowNode(FlowNodeViewModel(f"t{i}", "Target", 580, 20 + i * 150, {"in": "number"}, {}))
        key = ("s", "value", f"t{i}", "in")
        scene.renderEdge(FlowEdgeViewModel(*key))
        keys.append(key)
    scene._wireBatch = True
    for key in keys:
        edge = scene._edgeItems[key]
        a, b = edge.sourcePort.sceneBoundingRect().center(), edge.targetPort.sceneBoundingRect().center()
        x = a.x() + 100
        edge.applyRoute(Route(simplify(((a.x(), a.y()), (x, a.y()), (x, b.y()), (b.x(), b.y())))))
    scene._wireBatch = False
    scene._wireGeometryChanged()
    scene._routeTimer.stop()
    scene._routesDirty = False
    return scene, keys


def testNodeMultiSelectionPortAndEdgeFocusNeverSelectRelatedEdges(ownedFlowScene, monkeypatch):
    scene, keys = branchScene(ownedFlowScene)
    monkeypatch.setattr(scene, "_routeEdges", lambda _: pytest.fail("focus must not route"))
    positions = scene.getNodePositions()
    scene.setNodeSelected("t0")
    assert scene._focusedEdges == {keys[0]}
    assert scene._edgeItems[keys[1]].pen().color() == QColor(DIM_COLOR)
    assert not scene.getSelectedEdgeKeys()
    scene._nodeItems["t1"].setSelected(True)
    assert scene._focusedEdges == set(keys[:2])
    port = scene._portItems[("s", "output", "value")]
    scene.handlePortHover(port, True)
    assert scene._focusedEdges == set(keys)
    scene.handlePortHover(port, False)
    assert scene._focusedEdges == set(keys[:2])
    other = scene._portItems[("s", "output", "other")]
    scene.handlePortHover(other, True)
    assert not scene._focusedEdges
    scene.handlePortHover(other, False)
    scene.clearEdgeFocus()
    assert not scene._focusedEdges and not scene._portEmphasis
    assert scene.getNodePositions() == positions
    assert all(edge.pen().color() == QColor(NORMAL_COLOR) for edge in scene._edgeItems.values())


@pytest.mark.parametrize("scale", [.5, 1, 1.5, 2])
def testSharedTrunkClickRequiresDisambiguationAndPreviewDoesNotSelect(ownedFlowScene, designerApplication, scale):
    scene, keys = branchScene(ownedFlowScene)
    view = QGraphicsView(scene)
    view.setMouseTracking(True)
    view.resize(1100, 800)
    view.scale(scale, scale)
    view.show()
    designerApplication.processEvents()
    source = scene._portItems[("s", "output", "value")].sceneBoundingRect().center()
    point = source + QPointF(50, 0)
    try:
        assert all(not edge.shape().contains(point) for edge in scene._edgeItems.values())
        assert isinstance(view.itemAt(view.mapFromScene(point)), _SharedWireItem)
        scene.setEdgeSelected(keys[0], True)
        QTest.mouseClick(view.viewport(), Qt.LeftButton, Qt.NoModifier, view.mapFromScene(point))
        designerApplication.processEvents()
        menu = scene._branchMenu
        assert menu is not None and len(menu.actions()) == 4
        assert scene.getSelectedEdgeKeys() == [keys[0]]
        action = menu.actions()[2]
        assert "t2" in action.text() and action.data() == keys[2]
        action.hovered.emit()
        assert scene._focusedEdges == {keys[0], keys[2]}
        assert scene.getSelectedEdgeKeys() == [keys[0]]
        QTest.keyClick(menu, Qt.Key_Delete)
        assert len(scene._edgeItems) == 4 and scene.getSelectedEdgeKeys() == [keys[0]]
        QTest.keyClick(menu, Qt.Key_Escape)
        designerApplication.processEvents()
        assert scene._branchMenu is None
        assert scene.getSelectedEdgeKeys() == [keys[0]]
        scene._showEdgeMenu(keys, view.mapToGlobal(QPoint(60, 60)))
        menu = scene._branchMenu
        target = menu.actions()[2]
        QTest.mouseClick(menu, Qt.LeftButton, Qt.NoModifier, menu.actionGeometry(target).center())
        designerApplication.processEvents()
        assert scene.getSelectedEdgeKeys() == [keys[2]]
        assert not scene._edgeItems[keys[1]].isSelected()
    finally:
        scene._closeEdgeMenu()
        view.close()
        view.setScene(None)


def testSingleSelectedBranchColorsCommonTrunkButNotSiblings(ownedFlowScene):
    scene, keys = branchScene(ownedFlowScene)
    scene.setEdgeSelected(keys[2], True)
    source = scene._portItems[("s", "output", "value")].sceneBoundingRect().center()
    image = renderScene(scene, 2)
    assert image.pixelColor(int((source.x() + 40) * 2), int(source.y() * 2)) == QColor("#d97706")
    assert scene._edgeItems[keys[2]].pen().color() == QColor("#d97706")
    assert scene._edgeItems[keys[1]].pen().color() == QColor(DIM_COLOR)
    assert scene._portEmphasis[("t2", "input", "in")] == "#d97706"
    assert ("t1", "input", "in") not in scene._portEmphasis
    scene.setEdgeSelected(keys[3], True)
    scene.setEdgeSelected(keys[2], False)
    assert scene._portEmphasis[("s", "output", "value")] == "#d97706"


def testMenuIsInvalidatedByGeometryAndClearCannotRetainOldCallbacks(ownedFlowScene):
    scene, keys = branchScene(ownedFlowScene)
    scene._showEdgeMenu(keys, QPoint(40, 40))
    menu = scene._branchMenu
    action = menu.actions()[1]
    scene.removeFlowEdge(*keys[1])
    assert scene._branchMenu is None
    action.trigger()
    assert not scene.getSelectedEdgeKeys()
    scene.clearGraph()
    assert not scene._sharedWireItems and not scene._wireLayers
    assert not scene._portEmphasis and not scene._focusedEdges
    assert not scene.items()


def testDragAndEscapeRestoreFocusWithoutConnecting(ownedFlowScene, designerApplication):
    scene, keys = branchScene(ownedFlowScene)
    view = QGraphicsView(scene)
    view.resize(1000, 800)
    view.show()
    designerApplication.processEvents()
    try:
        port = scene._portItems[("s", "output", "value")]
        scene.handlePortHover(port, True)
        QTest.mousePress(view.viewport(), Qt.LeftButton, Qt.NoModifier, view.mapFromScene(port.sceneBoundingRect().center()))
        assert scene._dragSourcePort is port and scene._dragPathItem is not None
        assert scene._hoverPort is None
        QTest.keyClick(view, Qt.Key_Escape)
        assert scene._dragSourcePort is None and scene._dragPathItem is None
        assert scene.getDragHintText() == "" and not scene._focusedEdges
        assert set(scene._edgeItems) == set(keys)
    finally:
        view.close()
        view.setScene(None)


@pytest.mark.parametrize("scale", [.5, 1, 1.5, 2])
def testBlankPanClearsFocusAndRemainsPannable(ownedFlowScene, designerApplication, scale):
    scene, keys = branchScene(ownedFlowScene)
    view = DesignerGraphicsView(scene)
    view.resize(1000, 800)
    view.show()
    designerApplication.processEvents()
    view.setZoomFactor(scale)
    view.centerOn(QPointF(450, 680))
    positions = scene.getNodePositions()
    point = view.mapFromScene(QPointF(450, 680))
    before = view.mapToScene(point)
    try:
        scene.setEdgeSelected(keys[1], True)
        assert view.beginPanAt(point.x(), point.y())
        assert not scene.getSelectedEdgeKeys() and not scene._focusedEdges
        view.updatePanTo(point.x() + 60, point.y() + 45)
        after = view.mapToScene(point)
        assert after.x() - before.x() == pytest.approx(-60 / scale, abs=1 / scale)
        assert after.y() - before.y() == pytest.approx(-45 / scale, abs=1 / scale)
        view.endPan()
        assert not view.isPanning()
        assert scene.getNodePositions() == positions
        assert set(scene._edgeItems) == set(keys)
    finally:
        view.close()
        view.setScene(None)


def testBindingsStaySeparateAndCannotEnterBranchMenu(ownedFlowScene):
    scene, keys = branchScene(ownedFlowScene)
    scene.setBindingDependencies("t0", ["s"])
    assert len(scene._bindingItems) == 1
    assert scene._bindingItems[0].acceptedMouseButtons() == Qt.NoButton
    assert set(scene._edgeItems) == set(keys)
    assert not scene._bindingItems[0].flags() & scene._bindingItems[0].ItemIsSelectable


def testDesignerMenuDeleteUndoAndViewOnlySaveReopen(ownedDesignerWindow, tmp_path):
    client = SimpleNamespace(listOperators=lambda: [], loadProject=lambda _: SimpleNamespace(ok=True, message="stub"))
    window = ownedDesignerWindow(client)
    fixture = Path(__file__).resolve().parents[2] / "examples/flow_edge_readability/project.emoproj"
    assert window.loadProjectDirectory(str(fixture), confirmed=True)
    window.activateWorkflow("fanout")
    scene = window.flowScene
    scene.refreshAllRoutes()
    keys = sorted(key for key in scene._edgeItems if key[:2] == ("seed", "value"))
    assert len(keys) == 4
    graph = deepcopy(window.flowModel.toProjectGraph())
    positions = scene.getNodePositions()
    session = window.pageCoordinator.session
    history = len(session._undo)
    scene.setNodeSelected("seed")
    scene._showEdgeMenu(keys, QPoint(60, 60))
    window.deleteSelectedElements()
    assert window.flowModel.toProjectGraph() == graph
    assert len(session._undo) == history
    scene._chooseMenuEdge(keys[2], scene._wireEpoch, Qt.NoModifier)
    scene._closeEdgeMenu()
    window.deleteSelectedElements()
    assert len(window.flowModel.edges) == len(graph["edges"]) - 1
    assert set(scene._edgeItems) == {tuple((e["fromNode"], e["fromPort"], e["toNode"], e["toPort"])) for e in graph["edges"]} - {keys[2]}
    window.pageCoordinator.history()
    assert window.flowModel.toProjectGraph() == graph
    window.pageCoordinator.history(True)
    assert len(window.flowModel.edges) == len(graph["edges"]) - 1
    window.pageCoordinator.history()
    assert window.flowModel.toProjectGraph() == graph
    scene = window.flowScene
    scene.setNodeSelected("seed")
    scene.handlePortHover(scene._portItems[("seed", "output", "value")], True)
    scene.clearEdgeFocus()
    assert len(session._undo) == history
    assert scene.getNodePositions() == positions
    target = tmp_path / "reopened"
    assert window.saveProjectToDirectory(str(target))
    assert window.loadProjectDirectory(str(target), confirmed=True)
    window.activateWorkflow("fanout")
    assert window.flowModel.toProjectGraph() == graph
    assert window.flowScene.getNodePositions() == positions
    assert not window.flowScene._focusedEdges
