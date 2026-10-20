from copy import deepcopy
from types import SimpleNamespace

import emo_master  # noqa: F401 - preload Windows dependencies before Qt
from PySide2.QtCore import Qt
from PySide2.QtWidgets import QMessageBox

from emo_master.apps.designer.ui.main_window import MainWindow
from emo_master.apps.designer.ui.param_form import SchemaParamForm
from emo_master.apps.designer.state.workflow_package import buildWorkflowPackage, importWorkflowPackage, previewWorkflowPackageImport
from emo_master.apps.designer.state.workflow_store import WorkflowStore
from tests.designer.test_control_flow_usability import _window as legacyWindow


def booleanWindow():
    window = MainWindow(SimpleNamespace(listOperators=lambda: []))
    bodyId = window.createWorkflow("单张图片处理")
    window.editWorkflowInterface({"hasNext": "boolean"}, {"hasNext": "boolean"})
    window.activateWorkflow("main")
    nodeId = window.addWhileNode()
    assert nodeId is not None
    return window, bodyId, nodeId


def testNewWhileFormExposesBooleanPortNotConditionWorkflow():
    window, _body, nodeId = booleanWindow()
    node = window.flowModel.nodes[nodeId]
    form = SchemaParamForm()
    form.setSchema(window._nodeEditorSchema(node), node.loop)
    assert form._controls["conditionMode"].currentText() == "布尔状态端口"
    assert form._controls["conditionWorkflowId"].isHidden()
    assert not form._controls["conditionPort"].isHidden()
    assert form._controls["conditionPort"].currentText() == "hasNext : boolean"
    assert form.getValues()["conditionPort"] == "hasNext"
    assert form.validationMessage() == ""


def testWhileBodyChangeRetainsInvalidConditionUntilUserSelectsReplacement(designerApplication):
    window, bodyId, nodeId = booleanWindow()
    otherId = window.workflowStore.addWorkflow("另一循环体", workflowId="other")
    other = window.workflowStore.get(otherId)
    other.inputs = {"go": {"type": "boolean"}, "count": "integer"}
    other.outputs = deepcopy(other.inputs)
    window.openNodeParamDialog(nodeId)
    form = window.nodeParamDialog._schemaForm
    body = form._controls["bodyWorkflowId"]
    body.setCurrentIndex(body.findData(otherId))
    condition = form._controls["conditionPort"]
    assert "端口已失效" in condition.currentText()
    assert condition.currentData() == "hasNext"
    assert condition.findData("go") >= 0
    assert condition.findData("count") == -1
    assert not window.nodeParamDialog.applyChanges()
    assert window.flowModel.nodes[nodeId].loop["bodyWorkflowId"] == bodyId
    condition.setCurrentIndex(condition.findData("go"))
    assert window.nodeParamDialog.applyChanges()
    assert window.flowModel.nodes[nodeId].loop["conditionPort"] == "go"


def testLegacyWhileShowsBooleanOutputAndCanExplicitlySwitchToBooleanMode(designerApplication):
    window = legacyWindow()
    window.openNodeParamDialog("while")
    form = window.nodeParamDialog._schemaForm
    assert form.getValues()["conditionMode"] == "workflow"
    assert not form._controls["conditionWorkflowId"].isHidden()
    assert form._controls["conditionPort"].isHidden()
    assert any("continue（布尔值）" in text for text, _ in window.flowScene._nodeItems["while"].model.summaryLines)
    beforeEdges = deepcopy(window.flowModel.edges)
    beforePositions = window.flowScene.getNodePositions()
    mode = form._controls["conditionMode"]
    mode.setCurrentIndex(mode.findData("boolean"))
    condition = form._controls["conditionPort"]
    condition.setCurrentIndex(condition.findData("hasNext"))
    assert form._controls["conditionWorkflowId"].isHidden()
    assert window.nodeParamDialog.applyChanges()
    node = window.flowModel.nodes["while"]
    assert node.loop["conditionMode"] == "boolean"
    assert "conditionWorkflowId" not in node.loop
    assert window.flowModel.edges == beforeEdges
    assert window.flowScene.getNodePositions() == beforePositions
    assert "condition" in window.workflowStore.workflows  # never delete a user's workflow


def testBooleanConditionIsVisibleOnCanvasAndRelationshipTree(designerApplication):
    window, _body, nodeId = booleanWindow()
    model = window.flowScene._nodeItems[nodeId].model
    assert model.inputPorts == model.outputPorts == {"hasNext": "boolean"}
    assert model.inputPortLabels == {"hasNext": "初始条件 (hasNext)"}
    assert any("hasNext（布尔值）" in text for text, _ in model.summaryLines)
    assert (nodeId, "input", "hasNext") in window.flowScene._portItems
    assert "后续每轮" in window.flowScene._portItems[(nodeId, "input", "hasNext")].toolTip()
    references = window.workflowStore.getWorkflowDependencyReferences()
    assert [entry["relation"] for entry in references] == ["while-body"]
    routes = references[0]["dataTransfers"]
    assert any("下一轮继续条件.hasNext" in row["target"] and row["portType"] == "boolean" for row in routes)
    window.refreshWorkflowDependencyTree()
    call = window.workflowDependencyTree.topLevelItem(0).child(0)
    condition = call.child(0)
    assert "hasNext : boolean" in condition.text(0)
    assert condition.data(0, Qt.UserRole)["itemType"] == "control"
    assert "true 继续，false 结束" in condition.text(0)


def testRemovingBooleanStateInvalidatesOpenEditorWithoutReplacingSelection(designerApplication, monkeypatch):
    monkeypatch.setattr(QMessageBox, "warning", lambda *_args: QMessageBox.Ok)
    window, bodyId, nodeId = booleanWindow()
    window.openNodeParamDialog(nodeId)
    window.editWorkflowInterfaceFor(bodyId, {"hasNext": "integer"}, {"hasNext": "integer"})
    designerApplication.processEvents()
    form = window.nodeParamDialog._schemaForm
    assert form._controls["conditionPort"].currentData() == "hasNext"
    assert "端口已失效" in form._controls["conditionPort"].currentText()
    assert not window.nodeParamDialog.applyChanges()


def testWhileDoesNotDefaultToAWorkflowWhenOnlyIntegerStateExists():
    window = MainWindow(SimpleNamespace(listOperators=lambda: []))
    window.createWorkflow("计数状态")
    window.editWorkflowInterface({"count": "integer"}, {"count": "integer"})
    window.activateWorkflow("main")
    assert window.addWhileNode() is None


def testBooleanWhilePackageOnlyDependsOnBodyAndPreservesPortOnImport():
    window, bodyId, nodeId = booleanWindow()
    window.workflowController.captureActiveWorkflow()
    package = buildWorkflowPackage(window.workflowStore, "main")
    assert set(package.workflows) == {"main", bodyId}
    target = WorkflowStore()
    target.addWorkflow("已有同名 ID", workflowId=bodyId)
    preview = previewWorkflowPackageImport(target, package)
    assert len(preview.dependencies) == 1
    imported = importWorkflowPackage(target, package)
    loop = next(node for node in target.get(imported.rootWorkflowId).nodes if node["kind"] == "loop")
    assert loop["loop"]["conditionMode"] == "boolean"
    assert loop["loop"]["conditionPort"] == "hasNext"
    assert "conditionWorkflowId" not in loop["loop"]
    assert loop["loop"]["bodyWorkflowId"] == imported.workflowIdMap[bodyId]
    assert loop["loop"]["bodyWorkflowId"] != bodyId
