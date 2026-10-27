import os
from pathlib import Path

import pytest
from PySide2.QtCore import QPointF, QRectF
from PySide2.QtGui import QColor, QPainter
from PySide2.QtWidgets import QGraphicsView

from emo_master.apps.designer.ui.flow_edge_presentation import DIM_COLOR, NORMAL_COLOR, SELECTED_COLOR
from emo_master.apps.designer.ui.flow_scene import FlowEdgeViewModel, FlowNodeViewModel


def fanoutScene(owner):
    scene = owner()
    scene.addFlowNode(FlowNodeViewModel("filter", "只选人物", 1030, 1040,
        {"detections": "detectionCollection"}, {"unused": "integer", "keptDetections": "detectionCollection"}))
    scene.addFlowNode(FlowNodeViewModel("count", "检测目标数量", 1500, 1040,
        {"detections": "detectionCollection"}, {"count": "integer"}))
    scene.addFlowNode(FlowNodeViewModel("next", "下游处理", 1500, 1200,
        {"detections": "detectionCollection"}, {}))
    keys = [("filter", "keptDetections", target, "detections") for target in ("count", "next")]
    for key in keys:
        scene.renderEdge(FlowEdgeViewModel(*key))
    scene.refreshAllRoutes()
    scene.setSceneRect(0, 0, 2500, 1800)
    return scene, keys


def testSharedWireHasPaintBoundsButNoDuplicateHitShape(ownedFlowScene):
    scene, _keys = fanoutScene(ownedFlowScene)
    assert scene._wireLayers
    for layer in scene._wireLayers.values():
        assert not layer.path().isEmpty()
        assert layer.boundingRect().contains(layer.path().controlPointRect())
        assert layer.boundingRect().width() > 0 and layer.boundingRect().height() > 0
        assert layer.shape().isEmpty()


def testPortEndStylesFollowSelectionRemovalAndClear(ownedFlowScene):
    scene, keys = fanoutScene(ownedFlowScene)
    source = ("filter", "output", "keptDetections")
    assert scene._portWireStyles[source][1] == NORMAL_COLOR
    assert ("filter", "output", "unused") not in scene._portWireStyles
    scene.setEdgeSelected(keys[0], True)
    assert scene._portWireStyles[source][1] == SELECTED_COLOR
    assert scene._portWireStyles[("count", "input", "detections")][1] == SELECTED_COLOR
    assert scene._portWireStyles[("next", "input", "detections")][1] == DIM_COLOR
    scene.removeFlowEdge(*keys[0])
    assert ("count", "input", "detections") not in scene._portWireStyles
    assert scene._portWireStyles[source][1] == NORMAL_COLOR
    scene.removeFlowEdge(*keys[1])
    assert not scene._portWireStyles
    assert all(layer.boundingRect().isEmpty() for layer in scene._wireLayers.values())
    scene.clearGraph()
    assert not scene._portWireStyles and not scene._wireLayers


@pytest.mark.parametrize("scale", [.65, 1, 1.5])
@pytest.mark.parametrize("selected", [False, True])
@pytest.mark.parametrize("hidden", [False, True])
def testPannedViewportKeepsSharedPortExitVisible(ownedFlowScene, designerApplication, scale, selected, hidden):
    scene, keys = fanoutScene(ownedFlowScene)
    if hidden:
        scene.setPortHidden('filter', 'output', 'unused', True)
        scene.refreshAllRoutes()
    view = QGraphicsView(scene)
    view.setRenderHint(QPainter.Antialiasing)
    view.resize(1100, 650)
    view.scale(scale, scale)
    view.show()
    try:
        view.centerOn(1400, 1180)
        designerApplication.processEvents()
        if selected:
            scene.setEdgeSelected(keys[0], True)
            designerApplication.processEvents()
        assert not view.viewport().rect().contains(view.mapFromScene(QPointF(0, 0)))
        for edge in scene._edgeItems.values():
            start, end = edge.sourcePort.sceneBoundingRect().center(), edge.targetPort.sceneBoundingRect().center()
            assert edge.route.points[0] == (start.x(), start.y())
            assert edge.route.points[-1] == (end.x(), end.y())

        port = scene._portItems[("filter", "output", "keptDetections")]
        start = port.sceneBoundingRect().center()
        pixmap = view.viewport().grab()
        image = pixmap.toImage()
        ratio = pixmap.devicePixelRatio()
        expected = QColor(SELECTED_COLOR if selected else NORMAL_COLOR)
        end = scene._edgeItems[keys[0]].targetPort.sceneBoundingRect().center()
        probes = [start + QPointF(offset, 0) for offset in (10, 17, 23, 28)]
        probes.append(end - QPointF(10, 0))
        for probe in probes:
            point = view.mapFromScene(probe)
            x, y = round(point.x() * ratio), round(point.y() * ratio)
            colors = [image.pixelColor(x, y + dy) for dy in range(-2, 3)]
            distance = min(max(abs(color.red() - expected.red()), abs(color.green() - expected.green()),
                               abs(color.blue() - expected.blue())) for color in colors)
            assert distance < 55, (scale, selected, probe, [color.name() for color in colors])

        screenshotDir = os.environ.get("EMO_WIRE_ENDPOINT_SCREEN_DIR")
        if screenshotDir and scale == 1 and not selected:
            target = Path(screenshotDir)
            target.mkdir(parents=True, exist_ok=True)
            assert pixmap.save(str(target / "panned-fanout.png"))
            region = view.mapFromScene(QRectF(1250, 1070, 100, 50)).boundingRect()
            assert view.viewport().grab(region).save(str(target / "port-exit-detail.png"))
    finally:
        view.close()
        view.setScene(None)
