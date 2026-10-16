from __future__ import annotations

from copy import deepcopy
from itertools import combinations
import json
from pathlib import Path
from types import SimpleNamespace

import emo_master  # noqa: F401 - preload Windows dependencies before Qt
import pytest
from PySide2.QtCore import QRectF, Qt
from PySide2.QtGui import QImage, QPainter
from PySide2.QtTest import QTest
from PySide2.QtWidgets import QGraphicsSimpleTextItem

from emo_master.apps.designer.ui.main_window import MainWindow
from emo_master.apps.designer.ui.param_form import SchemaParamForm
from emo_master.plugins.builtins.flow_if.operator import FlowIfOperator
from emo_master.plugins.builtins.flow_switch.operator import FlowSwitchOperator


def _window():
    window = MainWindow(SimpleNamespace(listOperators=lambda: []))
    example = Path(__file__).resolve().parents[2] / "examples/image_batch_while_portable/image-batch-while.emoproj"
    window.workflowController.loadPayload(json.loads(example.read_text(encoding="utf-8")), preserveEdges=True)
    window.activeWorkflowId = "main"
    return window


@pytest.mark.parametrize("mode", ["repeat", "foreach", "while"])
def testLoopOnlyShowsRelevantFieldsAndKeepsInactiveValues(mode):
    window = _window()
    node = SimpleNamespace(kind="loop", loop={"mode": mode, "bodyWorkflowId": "body"})
    form = SchemaParamForm()
    values = {"mode": mode, "bodyWorkflowId": "body", "conditionWorkflowId": "condition",
              "repeatCount": 7, "maxIterations": 100, "timeoutMs": 12}
    form.setSchema(window._nodeEditorSchema(node), values)
    assert form._controls["repeatCount"].isHidden() == (mode != "repeat")
    assert form._controls["conditionWorkflowId"].isHidden() == (mode != "while")
    assert form.getValues()["repeatCount"] == 7
    assert form.getValues()["conditionWorkflowId"] == "condition"
    assert form.getValues()["mode"] == mode
    assert form._controls["mode"].currentText() == {"repeat": "计次循环", "foreach": "逐项循环", "while": "条件循环"}[mode]
    form.setSchema(window._nodeEditorSchema(node), {"mode": mode, "bodyWorkflowId": "body"})
    if mode != "repeat":
        assert "repeatCount" not in form.getValues()
    if mode != "while":
        assert "conditionWorkflowId" not in form.getValues()


def testForEachBodyChangeRefreshesPortsAndRejectsStaleMapping(designerApplication):
    window = _window()
    for key, inputs in (("a", {"image": "image", "index": "integer"}),
                        ("b", {"frame": "image", "position": {"type": "integer"}, "text": "string"})):
        window.workflowStore.addWorkflow(key, workflowId=key)
        window.workflowStore.get(key).inputs = inputs
    nodeId = window._addLoopNode({"mode": "foreach", "contractVersion": 2,
        "bodyWorkflowId": "a", "itemInputPort": "image", "indexInputPort": "index",
        "maxIterations": 100, "timeoutMs": 0}, "ForEach")
    assert nodeId
    window.openNodeParamDialog(nodeId)
    editor = window.nodeParamDialog
    form = editor._schemaForm
    body = form._controls["bodyWorkflowId"]
    body.setCurrentIndex(body.findData("b"))
    item = form._controls["itemInputPort"]
    index = form._controls["indexInputPort"]
    assert "已失效" in item.currentText()
    assert item.currentData() == "image"
    assert "已失效" in index.currentText()
    assert item.findData("frame") >= 0
    assert index.findData("position") >= 0
    assert index.findData("text") == -1
    assert not editor.applyChanges()
    assert window.flowModel.nodes[nodeId].loop["bodyWorkflowId"] == "a"
    item.setCurrentIndex(item.findData("frame"))
    index.setCurrentIndex(index.findData("position"))
    assert editor.applyChanges()
    assert window.flowModel.nodes[nodeId].loop["bodyWorkflowId"] == "b"
    assert window.flowModel.nodes[nodeId].loop["itemInputPort"] == "frame"


def testIfLabelsAndCompareEnablementPreserveStoredValues():
    window = _window()
    node = SimpleNamespace(kind="operator", operatorId="vision.flow.if",
                           paramSchema=deepcopy(FlowIfOperator.meta.paramSchema))
    schema = window._nodeEditorSchema(node)
    form = SchemaParamForm()
    form.setSchema(schema, {"mode": "bool", "compareValue": "retained"})
    mode, compare = form._controls["mode"], form._controls["compareValue"]
    assert mode.currentText() == "真假判断"
    assert not compare.isEnabled()
    assert form.getValues()["compareValue"] == "retained"
    mode.setCurrentIndex(mode.findData("not_equals"))
    assert mode.currentText() == "不等于" and compare.isEnabled()
    assert form.getValues() == {"mode": "not_equals", "compareValue": "retained"}
    assert "xOptionLabels" not in FlowIfOperator.meta.paramSchema["properties"]["mode"]


def testForEachBodyWithoutInputPortsRemainsValid():
    window = _window()
    window.workflowStore.addWorkflow("无输入循环体", workflowId="empty")
    node = SimpleNamespace(kind="loop", loop={"mode": "foreach", "bodyWorkflowId": "empty"})
    form = SchemaParamForm()
    form.setSchema(window._nodeEditorSchema(node), node.loop)
    assert form.validationMessage() == ""
    assert form._controls["itemInputPort"].currentText() == "无输入端口"


def testLiveWorkflowRenameRefreshesOpenDraftAndCanvas(designerApplication):
    window = _window()
    window.openNodeParamDialog("while")
    editor = window.nodeParamDialog
    editor._schemaForm._controls["maxIterations"].setValue(222)
    window.renameWorkflow("body", "单张图像处理")
    designerApplication.processEvents()
    assert editor.collectParams()["maxIterations"] == 222
    assert editor._schemaForm._controls["bodyWorkflowId"].currentText() == "单张图像处理"
    assert editor.isDirty()
    assert any("单张图像处理" in text for text, _ in window.flowScene._nodeItems["while"].model.summaryLines)
    editor._schemaForm._controls["maxIterations"].setValue(10000)


def _addBranch(window, operator, params, x, y):
    meta = operator.meta
    nodeId = window.flowModel.addNode(meta.operatorId, meta.displayName, meta.inputPorts, meta.outputPorts,
                                    paramSchema=deepcopy(meta.paramSchema))
    window.flowModel.nodes[nodeId].params = params
    window._addControlNodeToScene(nodeId, sceneX=x, sceneY=y)
    return nodeId


def testControlFlowCanvasGeometryAndBranchKeys(designerApplication, tmp_path):
    window = _window()
    scene = window.flowScene
    scene.clearGraph()
    window._addControlNodeToScene("while", sceneX=0, sceneY=0)
    subflow = window.addSubflowNode("body", sceneX=460, sceneY=0)
    repeat = window._addLoopNode({"mode": "repeat", "contractVersion": 2, "bodyWorkflowId": "body",
        "repeatCount": 3, "maxIterations": 100, "timeoutMs": 0}, "Repeat", sceneX=0, sceneY=220)
    foreach = window._addLoopNode({"mode": "foreach", "contractVersion": 2, "bodyWorkflowId": "body",
        "itemInputPort": "hasNext", "maxIterations": 100, "timeoutMs": 0}, "ForEach", sceneX=460, sceneY=220)
    ifNode = _addBranch(window, FlowIfOperator, {"mode": "equals", "compareValue": "OK"}, 0, 440)
    switchNode = _addBranch(window, FlowSwitchOperator, {"case0Value": "OK", "case1Value": "NG",
                                                       "case2Value": "OK", "case3Value": ""}, 460, 440)
    assert subflow and repeat and foreach
    assert scene._nodeItems[ifNode].model.outputPortLabels["true"] == "条件成立 (true)"
    assert set(scene._nodeItems[switchNode].model.outputPorts) == {"case0", "case1", "case2", "case3", "default"}
    assert "匹配值重复" in scene._nodeItems[switchNode].model.summaryLines[1][0]
    for node in scene._nodeItems.values():
        assert node.rect().contains(node.childrenBoundingRect())
        labels = [item for item in node.childItems() if isinstance(item, QGraphicsSimpleTextItem)]
        for first, second in combinations(labels, 2):
            assert not first.mapRectToParent(first.boundingRect()).intersects(second.mapRectToParent(second.boundingRect()))
    image = QImage(920, 790, QImage.Format_ARGB32)
    image.fill(Qt.white)
    painter = QPainter(image)
    scene.render(painter, QRectF(0, 0, 920, 790), scene.itemsBoundingRect().adjusted(-16, -16, 16, 16))
    painter.end()
    path = tmp_path / "control-flow-canvas.png"
    assert image.save(str(path))
    print(f"Control flow canvas screenshot: {path}")


def testCanvasRefreshKeepsEdgesPositionsSelectionAndRuntimeState(designerApplication):
    window = _window()
    scene = window.flowScene
    old = scene._nodeItems["while"]
    old.setPos(700, 400)
    old.setSelected(True)
    old.setRuntimeState("COMPLETED")
    before = window.flowModel.toProjectGraph()
    window.renameWorkflow("body", "长名称" * 60)
    scene.refreshAllRoutes()
    node = scene._nodeItems["while"]
    assert node.pos().x() == 700 and node.pos().y() == 400
    assert node.isSelected() and node._runtimeState == "COMPLETED"
    assert node.rect().contains(node.childrenBoundingRect())
    for edge in scene._edgeItems.values():
        assert edge.sourcePort.scene() is scene
        assert edge.targetPort.scene() is scene
        if edge.edge.fromNodeId == "while":
            assert edge.sourcePort.parentItem() is node
        if edge.edge.toNodeId == "while":
            assert edge.targetPort.parentItem() is node
    assert window.flowModel.toProjectGraph() == before


def testDoubleClickCanvasWorkflowReferenceOpensTarget(designerApplication):
    window = _window()
    window.show()
    designerApplication.processEvents()
    scene = window.flowScene
    reference = next(item for item in scene._nodeItems["while"].childItems()
                     if getattr(item, "workflowId", None) == "body")
    window.flowView.centerOn(reference)
    point = window.flowView.mapFromScene(reference.sceneBoundingRect().center())
    QTest.mouseDClick(window.flowView.viewport(), Qt.LeftButton, pos=point)
    designerApplication.processEvents()
    assert window.activeWorkflowId == "body"
