from __future__ import annotations

from copy import deepcopy
from itertools import combinations
from types import SimpleNamespace

import pytest
from PySide2.QtCore import QRectF, Qt
from PySide2.QtGui import QImage, QPainter
from PySide2.QtWidgets import QGraphicsSimpleTextItem

from emo_master.apps.designer.state.flow_graph_model import FlowEdge, FlowNode
from emo_master.apps.designer.ui.coordinate_port_presentation import (
    OPERATOR_ID, activeCoordinatePorts, coordinatePortPresentation,
)
from emo_master.apps.designer.ui.flow_scene import FlowNodeViewModel
from emo_master.core.contracts.geometry2d import Point2D
from emo_master.plugins.builtins._coordinate_operators import CoordinateCalculatorOperator
from emo_master.plugins.builtins._image_frame import defaultFrame


MODES = ("measure", "transform", "add", "subtract", "dot", "scalarMultiply")
META = CoordinateCalculatorOperator.meta
INPUTS = {key: value["type"] for key, value in META.inputPorts.items()}
OUTPUTS = {key: value["type"] for key, value in META.outputPorts.items()}


def _node(mode="transform", closed=False):
    return FlowNode("calculator", OPERATOR_ID, "Calculator", INPUTS, OUTPUTS,
                    params={"mode": mode, "closed": closed})


def _view(node):
    return FlowNodeViewModel(node.nodeId, node.displayName, 0, 0, INPUTS, OUTPUTS,
                             operatorId=OPERATOR_ID)


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("closed", [False, True])
def testVisiblePortsMatchActualExecution(mode, closed):
    node = _node(mode, closed)
    before = deepcopy(node)
    view = coordinatePortPresentation(_view(node), node, [])
    expectedInputs = {"points"}
    if mode in {"add", "subtract", "dot"}:
        expectedInputs.add("otherPoints")
    if mode == "scalarMultiply":
        expectedInputs.add("scalar")
    assert set(view.inputPorts) == expectedInputs
    space = defaultFrame(100, 100).coordinateSpace
    points = [Point2D(x, y, space).toPayload()
              for x, y in ((0, 0), (10, 0), (10, 10), (0, 10))]
    inputs = {"points": points}
    if "otherPoints" in expectedInputs:
        inputs["otherPoints"] = points
    if "scalar" in expectedInputs:
        inputs["scalar"] = 2.0
    result = CoordinateCalculatorOperator().executeNode(inputs, node.params, {})
    assert result["status"] == "ok"
    assert set(view.outputPorts) == set(result["outputs"])
    assert node == before


def testDefaultModeAndConditionalPolygon():
    assert activeCoordinatePorts({}) == activeCoordinatePorts({"mode": "transform"})
    assert "polygon" not in activeCoordinatePorts({})[1]
    assert "polygon" in activeCoordinatePorts({}, {"closed"})[1]
    assert "polygon" not in activeCoordinatePorts({"mode": "dot"}, {"closed"})[1]


def testDynamicModeAndLegacyConnectionsStayVisibleWithoutChangingContract():
    node = _node("add")
    node.globalVariableBindings = [{"parameterPath": ["mode"], "variableId": "mode"}]
    dynamic = coordinatePortPresentation(_view(node), node, [])
    assert set(dynamic.inputPorts) == set(INPUTS)
    assert set(dynamic.outputPorts) == set(OUTPUTS) - {"polygon"}
    node.globalVariableBindings = []
    edges = [FlowEdge("source", "points", node.nodeId, "scalar"),
             FlowEdge(node.nodeId, "vectors", "target", "vectors")]
    view = coordinatePortPresentation(_view(node), node, edges)
    assert "scalar" in view.inputPorts and "vectors" in view.outputPorts
    assert "不适用" in view.inputPortLabels["scalar"]
    assert "不适用" in view.outputPortLabels["vectors"]
    assert len(edges) == 2
    assert set(node.outputPorts) == set(OUTPUTS)


def _window(ownedDesignerWindow):
    window = ownedDesignerWindow(SimpleNamespace(listOperators=lambda: [META]))
    window.operatorCatalogController.applyOperators([META], window._classifyOperator)
    return window


def _add(window, mode="transform", closed=False, x=0, y=0):
    nodeId = window.flowModel.addNode(OPERATOR_ID, mode, INPUTS, OUTPUTS,
                                     paramSchema=deepcopy(META.paramSchema))
    window.flowModel.nodes[nodeId].params = {"mode": mode, "closed": closed}
    window._addControlNodeToScene(nodeId, sceneX=x, sceneY=y)
    return nodeId


def testApplyRebuildsPortRegistryAndPreservesSelectionStatePosition(ownedDesignerWindow):
    window = _window(ownedDesignerWindow)
    nodeId = _add(window)
    scene = window.flowScene
    original = scene._nodeItems[nodeId]
    original.setPos(300, 200)
    original.setSelected(True)
    original.setRuntimeState("COMPLETED")
    largeHeight = original.rect().height()
    for mode in (*MODES, "transform"):
        assert window.applyNodeParams(nodeId, {"mode": mode})
        item = scene._nodeItems[nodeId]
        inputs, outputs = activeCoordinatePorts({"mode": mode})
        assert set(item.model.inputPorts) == inputs
        assert set(item.model.outputPorts) == outputs
        assert {key[2] for key in scene._portItems if key[:2] == (nodeId, "input")} == inputs
        assert {key[2] for key in scene._portItems if key[:2] == (nodeId, "output")} == outputs
        assert item.isSelected() and item._runtimeState == "COMPLETED"
        assert (item.pos().x(), item.pos().y()) == (300, 200)
        if mode in {"add", "subtract", "dot"}:
            assert item.rect().height() < largeHeight
    assert set(window.flowModel.nodes[nodeId].outputPorts) == set(OUTPUTS)


def testConnectedInactivePortsSurviveSwitchReloadAndDisappearAfterDisconnect(ownedDesignerWindow):
    window = _window(ownedDesignerWindow)
    source = _add(window, "add", x=0)
    target = _add(window, "transform", x=700)
    edge = window.connectPorts(source, "points", target, "points")
    window.flowScene.renderEdge(edge)
    key = (source, "points", target, "points")
    assert window.applyNodeParams(source, {"mode": "dot"})
    scene = window.flowScene
    assert "不适用" in scene._nodeItems[source].model.outputPortLabels["points"]
    assert scene._edgeItems[key].sourcePort is scene._portItems[(source, "output", "points")]
    payload = window.workflowController.buildPayload()
    window.workflowController.loadPayload(payload, preserveEdges=True)
    assert key in scene._edgeItems
    assert "points" in scene._nodeItems[source].model.outputPorts
    scene._edgeItems[key].setSelected(True)
    window.deleteSelectedElements()
    assert key not in scene._edgeItems
    assert (source, "output", "points") not in scene._portItems
    assert set(scene._nodeItems[source].model.outputPorts) == {"values", "dotSum", "resultCount"}


def testEditorApplyAndReopenUseCurrentMode(ownedDesignerWindow, designerApplication):
    window = _window(ownedDesignerWindow)
    nodeId = _add(window)
    window.openNodeParamDialog(nodeId)
    editor = window.nodeParamDialog
    mode = editor._schemaForm._controls["mode"]
    mode.setCurrentIndex(mode.findData("subtract"))
    assert editor.applyChanges()
    designerApplication.processEvents()
    assert set(window.flowScene._nodeItems[nodeId].model.outputPorts) == {"vectors", "magnitudes", "resultCount"}
    payload = window.workflowController.buildPayload()
    window.workflowController.loadPayload(payload)
    assert set(window.flowScene._nodeItems[nodeId].model.outputPorts) == {"vectors", "magnitudes", "resultCount"}


def testHiddenAndRetainedInactivePortsCannotMakeNewConnections(ownedDesignerWindow):
    window = _window(ownedDesignerWindow)
    source = _add(window, "add")
    target = _add(window, "add", x=700)
    edge = window.connectPorts(source, "points", target, "points")
    window.flowScene.renderEdge(edge)
    assert window.applyNodeParams(source, {"mode": "dot"})
    assert window.connectPorts(source, "points", target, "otherPoints") is None
    assert window.connectPorts(target, "points", source, "scalar") is None
    assert set(window._connectableNodePorts(source, "output")) == {"values", "dotSum", "resultCount"}
    assert len(window.flowModel.edges) == 1


def testDefaultConnectionUsesVisibleOutputInsteadOfHiddenPoints(ownedDesignerWindow, monkeypatch):
    window = _window(ownedDesignerWindow)
    source = _add(window, "dot")
    target = window.flowModel.addNode("test.sink", "Sink", {"values": "list<number>"}, {})
    window._addControlNodeToScene(target, sceneX=700, sceneY=0)
    monkeypatch.setattr(window.flowScene, "getSelectedNodeIds", lambda: [source, target])
    window.connectSelectedNodes()
    assert window.flowModel.edges == [FlowEdge(source, "values", target, "values")]
    assert (source, "values", target, "values") in window.flowScene._edgeItems


def testReplacingConnectionRemovesFormerInactivePort(ownedDesignerWindow):
    window = _window(ownedDesignerWindow)
    source = _add(window, "add")
    target = _add(window, "add", x=700)
    replacement = _add(window, "add", y=500)
    window.flowScene.renderEdge(window.connectPorts(source, "points", target, "points"))
    assert window.applyNodeParams(source, {"mode": "dot"})
    window.flowScene.renderEdge(window.connectPorts(replacement, "points", target, "points"))
    assert (source, "output", "points") not in window.flowScene._portItems
    assert len(window.flowModel.edges) == 1


@pytest.mark.parametrize("bindingKind", ["globalVariableBindings", "parameterBindings", "siteBindings"])
def testBoundModeDoesNotPretendLiteralModeIsFixed(ownedDesignerWindow, bindingKind):
    window = _window(ownedDesignerWindow)
    nodeId = _add(window, "add")
    extensions = deepcopy(window.workflowStore.projectExtensions)
    if bindingKind == "globalVariableBindings":
        window.flowModel.nodes[nodeId].globalVariableBindings = [{"parameterPath": ["mode"], "variableId": "mode"}]
    else:
        window.workflowStore.projectExtensions["resources"] = {bindingKind: [{"target": {
            "workflowId": window.activeWorkflowId, "nodeId": nodeId, "parameterPath": ["mode"],
        }}]}
    try:
        window.flowScene.refreshPresentations()
        view = window.flowScene._nodeItems[nodeId].model
        assert set(view.inputPorts) == set(INPUTS)
        assert set(view.outputPorts) == set(OUTPUTS) - {"polygon"}
        assert any("绑定" in line for line, _ in view.summaryLines)
    finally:
        # These presentation-only bindings have no backing resources/variables.
        window.flowModel.nodes[nodeId].globalVariableBindings = []
        window.workflowStore.projectExtensions = extensions


def testClosedSettingRevealsAndHidesPolygon(ownedDesignerWindow):
    window = _window(ownedDesignerWindow)
    nodeId = _add(window)
    for closed in (True, False, True):
        assert window.applyNodeParams(nodeId, {"mode": "transform", "closed": closed})
        assert ((nodeId, "output", "polygon") in window.flowScene._portItems) == closed


def testModeCanvasNativeRendering(ownedDesignerWindow, designerApplication, tmp_path):
    window = _window(ownedDesignerWindow)
    scene = window.flowScene
    scene.clearGraph()
    for index, mode in enumerate(MODES):
        _add(window, mode, closed=mode == "measure", x=(index % 3) * 480, y=(index // 3) * 480)
    window.show()
    designerApplication.processEvents()
    for node in scene._nodeItems.values():
        labels = [item for item in node.childItems() if isinstance(item, QGraphicsSimpleTextItem)]
        for label in labels:
            assert node.rect().contains(label.mapRectToParent(label.boundingRect()))
        for left, right in combinations(labels, 2):
            assert not left.mapRectToParent(left.boundingRect()).intersects(right.mapRectToParent(right.boundingRect()))
    image = QImage(1440, 960, QImage.Format_ARGB32)
    image.fill(Qt.white)
    painter = QPainter(image)
    scene.render(painter, QRectF(0, 0, 1440, 960), scene.itemsBoundingRect().adjusted(-12, -12, 12, 12))
    painter.end()
    path = tmp_path / "coordinate-mode-ports.png"
    assert image.save(str(path))
    assert any(image.pixelColor(x, y) != Qt.white for x in range(0, 1440, 10) for y in range(0, 960, 10))
    print(f"Coordinate ports screenshot: {path}")
