from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path

from PySide2.QtCore import Qt
from PySide2.QtTest import QTest
from PySide2.QtWidgets import QGraphicsView

from emo_master.apps.designer.ui.flow_scene import FlowEdgeViewModel, FlowNodeViewModel
from emo_master.apps.designer.ui.read_only_graph import ReadOnlyFlowView


def filterScene(owner):
    scene = owner()
    manifest = json.loads((Path(__file__).parents[2] / 'src/emo_master/plugins/builtins/collection_filter/manifest.json').read_text(encoding='utf-8'))
    model = FlowNodeViewModel('filter', '车辆中心必须落在人物框内', 400, 20,
        {k: v['type'] for k, v in manifest['inputPorts'].items()},
        {k: v['type'] for k, v in manifest['outputPorts'].items()}, manifest['operatorId'])
    scene.addFlowNode(model)
    scene.addFlowNode(FlowNodeViewModel('vehicles', '车辆', 0, 20, {}, {'out': 'detectionCollection'}))
    scene.addFlowNode(FlowNodeViewModel('person', '人物框', 0, 200, {}, {'bbox': 'geometry2d'}))
    scene.addFlowNode(FlowNodeViewModel('next', '下游处理', 1000, 20,
        {'items': 'detectionCollection', 'count': 'integer'}, {}))
    edges = [('vehicles', 'out', 'filter', 'detections'), ('person', 'bbox', 'filter', 'roi'),
             ('filter', 'keptDetections', 'next', 'items'), ('filter', 'keptCount', 'next', 'count')]
    return scene, model, edges


def visible(scene, node):
    return {(direction, name) for (key, direction, name), port in scene._portItems.items()
            if key == node and port.isVisible()}


def testConnectedPortsStayVisibleWithoutChangingContractOrWires(ownedFlowScene):
    scene, model, edges = filterScene(ownedFlowScene)
    original = deepcopy(model)
    node = scene._nodeItems['filter']
    originalHeight = node.rect().height()
    assert len(visible(scene, 'filter')) == len(model.inputPorts) + len(model.outputPorts)
    assert not node.portVisibilityButton.isVisible()
    for edge in edges:
        scene.renderEdge(FlowEdgeViewModel(*edge))
    assert len(visible(scene, 'filter')) == len(model.inputPorts) + len(model.outputPorts)
    scene.hideUnusedPorts('filter')
    expected = {('input', 'detections'), ('input', 'roi'), ('output', 'keptDetections'), ('output', 'keptCount')}
    assert visible(scene, 'filter') == expected
    assert node.rect().height() < originalHeight / 2
    assert model == original and set(scene._edgeItems) == set(edges)
    assert node.portVisibilityButton.isVisible()
    scene.refreshAllRoutes()
    for edge in scene._edgeItems.values():
        assert edge.sourcePort.isVisible() and edge.targetPort.isVisible()
        assert edge.route.points
        assert edge.sourcePort is scene._portItems[(edge.edge.fromNodeId, 'output', edge.edge.fromPort)]
    for key, port in scene._portItems.items():
        if key[0] == 'filter':
            assert port.isVisible() == port.label.isVisible()
    hidden = scene._portItems[('filter', 'input', 'matches')]
    assert not hidden.isVisible()
    assert scene._findNearestInputPort(hidden.sceneBoundingRect().center(), 1) is None


def testExpansionClicksAndPresentationRefreshPreserveState(ownedFlowScene, designerApplication):
    scene, model, edges = filterScene(ownedFlowScene)
    for edge in edges:
        scene.renderEdge(FlowEdgeViewModel(*edge))
    scene.hideUnusedPorts('filter')
    before = deepcopy(model)
    positions = scene.getNodePositions()
    view = QGraphicsView(scene)
    view.resize(1300, 800)
    view.show()
    try:
        designerApplication.processEvents()
        node = scene._nodeItems['filter']
        point = view.mapFromScene(node.portVisibilityButton.sceneBoundingRect().center())
        QTest.mouseClick(view.viewport(), Qt.LeftButton, pos=point)
        assert len(visible(scene, 'filter')) == len(model.inputPorts) + len(model.outputPorts)
        assert not scene._hiddenPorts['filter']
        assert not node.portVisibilityButton.isVisible()
        scene.setPortHidden('filter', 'input', 'matches', True)
        scene.presentationProvider = lambda item: replace(item, title=item.title + '!')
        scene.refreshPresentations()
        node = scene._nodeItems['filter']
        assert ('input', 'matches') not in visible(scene, 'filter')
        point = view.mapFromScene(node.portVisibilityButton.sceneBoundingRect().center())
        QTest.mouseClick(view.viewport(), Qt.LeftButton, pos=point)
        assert len(visible(scene, 'filter')) == len(model.inputPorts) + len(model.outputPorts)
        assert scene.getNodePositions() == positions
        assert model == before and set(scene._edgeItems) == set(edges)
    finally:
        view.close()


def testReconnectionAndRemovalUpdateVisibility(ownedFlowScene):
    scene, model, edges = filterScene(ownedFlowScene)
    for edge in edges:
        scene.renderEdge(FlowEdgeViewModel(*edge))
    scene.hideUnusedPorts('filter')
    edge = ('filter', 'rejectedDetections', 'next', 'items')
    scene.renderEdge(FlowEdgeViewModel(*edge))
    assert ('output', 'rejectedDetections') in visible(scene, 'filter')
    assert ('output', 'keptDetections') in visible(scene, 'filter')
    scene.hideUnusedPorts('filter')
    assert ('output', 'keptDetections') not in visible(scene, 'filter')
    scene.removeFlowNode('next')
    assert visible(scene, 'filter') == {('input', 'detections'), ('input', 'roi'),
                                        ('output', 'rejectedDetections'), ('output', 'keptCount')}
    scene.removeFlowEdge(*edges[0])
    scene.removeFlowEdge(*edges[1])
    assert len(visible(scene, 'filter')) == 4
    scene.showAllPorts('filter')
    assert len(visible(scene, 'filter')) == len(model.inputPorts) + len(model.outputPorts)
    assert not scene._nodeItems['filter'].portVisibilityButton.isVisible()


def testReadOnlyCanvasCanExpandPortsWithoutEditing(ownedFlowScene, designerApplication):
    scene, model, edges = filterScene(ownedFlowScene)
    for edge in edges:
        scene.renderEdge(FlowEdgeViewModel(*edge))
    scene.hideUnusedPorts('filter')
    scene.setNodeComparisonColor('filter', '#b77900')
    view = ReadOnlyFlowView(scene)
    view.resize(1300, 800)
    view.show()
    try:
        designerApplication.processEvents()
        node = scene._nodeItems['filter']
        before = deepcopy(model)
        point = view.mapFromScene(node.portVisibilityButton.sceneBoundingRect().center())
        QTest.mouseClick(view.viewport(), Qt.LeftButton, pos=point)
        assert len(visible(scene, 'filter')) == len(model.inputPorts) + len(model.outputPorts)
        assert node._comparisonColor == '#b77900'
        assert not view.isInteractive() and model == before
        assert set(scene._edgeItems) == set(edges)
    finally:
        view.close()


def testConnectionDragKeepsGeometryStableUntilCompletion(ownedFlowScene):
    scene, model, edges = filterScene(ownedFlowScene)
    scene.renderEdge(FlowEdgeViewModel(*edges[0]))
    scene.hideUnusedPorts('filter')
    scene.showAllPorts('filter')
    source = scene._portItems[('filter', 'output', 'keptDetections')]
    scene.startConnectionDrag(source)
    original = source.sceneBoundingRect()
    scene.renderEdge(FlowEdgeViewModel(*edges[2]))
    assert source.sceneBoundingRect() == original
    scene.endDragPreview()
    scene.hideUnusedPorts('filter')
    assert source.isVisible() and set(scene._edgeItems) == {edges[0], edges[2]}
