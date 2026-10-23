from copy import deepcopy
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide2.QtWidgets import QScrollArea

from emo_master.apps.designer.state.flow_graph_model import FlowNode, FlowEdge
from emo_master.apps.designer.ui.flow_scene import FlowNodeViewModel
from emo_master.apps.designer.ui.operator_mode_presentation import operatorModePresentation
from emo_master.apps.designer.ui.param_form import SchemaParamForm
from emo_master.plugins.builtins._operator_modes import INPUT_CHOICES, currentModeSchema
from emo_master.plugins.builtins.collection_sort.operator import CollectionSortOperator
from emo_master.plugins.builtins.collection_select.operator import CollectionSelectOperator
from emo_master.plugins.builtins.mask_logic.operator import MaskLogicOperator
from emo_master.plugins.builtins.yolo_inference.operator import YoloInferenceOperator
from emo_master.plugins.builtins.rgb_statistics.operator import RgbStatisticsOperator
from emo_master.plugins.builtins._coordinate_operators import CoordinateReaderOperator
from tests.plugins.test_operator_modes import operator


def nodeFor(meta, params=None, nodeId="node"):
    return FlowNode(nodeId, meta.operatorId, meta.displayName,
                    {name: spec["type"] for name, spec in meta.inputPorts.items()},
                    {name: spec["type"] for name, spec in meta.outputPorts.items()},
                    paramSchema=deepcopy(meta.paramSchema), params=dict(params or {}))


def view(node, edges=(), dynamic=()):
    model = FlowNodeViewModel(node.nodeId, node.displayName, 0, 0, node.inputPorts, node.outputPorts,
                             operatorId=node.operatorId)
    return operatorModePresentation(model, node, edges, dynamic)


def choose(form, name, value):
    control = form._controls[name]
    assert control.findData(value) >= 0
    control.setCurrentIndex(control.findData(value))


@pytest.mark.parametrize("operatorId,kind", [
    (operatorId, kind) for operatorId, (_, choices) in INPUT_CHOICES.items() for kind in choices
])
def testObjectSelectionAndLegacyInputInference(operatorId, kind):
    selector = INPUT_CHOICES[operatorId][0]
    params = {selector: kind}
    if operatorId == "vision.geometry.extract_points":
        params["feature"] = "circleCenter" if kind == "circles" else "bboxCenter"
    node = nodeFor(operator(operatorId).meta, params)
    original = deepcopy(node)
    assert set(view(node).inputPorts) == {kind}
    node.params.pop(selector)
    edge = FlowEdge("upstream", "value", node.nodeId, kind)
    assert set(view(node, [edge]).inputPorts) == {kind}
    assert node.inputPorts == original.inputPorts and node.outputPorts == original.outputPorts
    assert selector not in node.params


@pytest.mark.parametrize("prefix,cls", [("sorted", CollectionSortOperator), ("selected", CollectionSelectOperator)])
def testLegacyDisconnectedInputUsesOutputAndRetainsIncompatibleEdges(prefix, cls):
    node = nodeFor(cls.meta)
    edge = FlowEdge(node.nodeId, prefix + "Detections", "target", "detections")
    assert set(view(node, [edge]).inputPorts) == {"detections"}
    node.params["collectionType"] = "lines"
    model = view(node, [edge])
    assert prefix + "Detections" in model.outputPorts
    assert prefix + "Detections" in model.outputPortLabels
    assert prefix + "Detections" not in view(node).outputPorts


@pytest.mark.parametrize("cls,flag,ports", [
    (YoloInferenceOperator, "outputCrop", {"croppedImage", "croppedFrame"}),
    (YoloInferenceOperator, "drawOverlay", {"overlay"}),
    (RgbStatisticsOperator, "emitMask", {"mask"}),
    (CoordinateReaderOperator, "asPolygon", {"polygon"}),
])
def testConditionalOutputsAndDynamicBindings(cls, flag, ports):
    node = nodeFor(cls.meta, {flag: False})
    assert not ports.intersection(view(node).outputPorts)
    assert ports <= set(view(node, dynamic={flag}).outputPorts)
    node.globalVariableBindings = [{"parameterPath": [flag], "variableId": "flag"}]
    assert ports <= set(view(node).outputPorts)
    node.globalVariableBindings = []
    connected = next(iter(ports))
    edge = FlowEdge(node.nodeId, connected, "target", "image")
    assert connected in view(node, [edge]).outputPortLabels
    node.params[flag] = True
    assert ports <= set(view(node).outputPorts)


def testMaskNotAndGeometryModesExposeOnlyTheirDependencies():
    node = nodeFor(MaskLogicOperator.meta, {"operation": "not"})
    assert set(view(node).inputPorts) == {"maskA", "frameA"}
    assert set(view(node, dynamic={"operation"}).inputPorts) == set(node.inputPorts)
    node = nodeFor(operator("vision.geometry.extract_points").meta, {"feature": "centroid"})
    assert set(view(node).inputPorts) == {"measurements"}
    assert set(view(node, dynamic={"feature"}).inputPorts) == set(node.inputPorts)


def testSortFieldListFollowsObjectWithoutErasingExplicitOldKey():
    form = SchemaParamForm()
    form.setSchema(CollectionSortOperator.meta.paramSchema, {"collectionType": "detections"})
    assert form._controls["key"].currentData() == ""
    assert form._controls["key"].findData("area") < 0
    assert form._controls["key"].findData("confidence") >= 0
    choose(form, "collectionType", "lines")
    assert form._controls["key"].findData("length") >= 0
    assert form._controls["key"].findData("confidence") < 0
    form.setSchema(CollectionSortOperator.meta.paramSchema, {"collectionType": "detections", "key": "area"})
    assert form.getValues()["key"] == "area"
    assert form.validationMessage()


def testGeometryInputChoicesFollowFeatureAndRequireChoiceWhenAmbiguous():
    form = SchemaParamForm()
    form.setSchema(currentModeSchema("vision.geometry.extract_points"), {})
    assert form.getValues()["inputType"] == "circles"
    choose(form, "feature", "centroid")
    assert form.getValues()["inputType"] == "measurements"
    choose(form, "feature", "bboxCenter")
    assert form.getValues()["inputType"] == "measurements"
    assert not form.validationMessage()
    form.setSchema(currentModeSchema("vision.geometry.extract_points"), {"feature": "bboxCenter"})
    assert form.validationMessage()
    choose(form, "inputType", "bbox")
    assert not form.validationMessage()


@pytest.mark.parametrize("kind", ["bbox", "measurements"])
def testGeometryCompatibleFeatureChangeKeepsSelectedObject(kind):
    form = SchemaParamForm()
    form.setSchema(currentModeSchema("vision.geometry.extract_points"),
                   {"feature": "bboxCenter", "inputType": kind})
    choose(form, "feature", "bboxTopLeft")
    assert form.getValues()["inputType"] == kind
    assert not form.validationMessage()


@pytest.mark.parametrize("bindingKind", ["globalVariableBindings", "parameterBindings", "siteBindings"])
def testGeometryDynamicFeatureDoesNotGuessObjectFromUnusedLiteral(ownedDesignerWindow, bindingKind):
    meta = operator("vision.geometry.extract_points").meta
    window = _window(ownedDesignerWindow, [meta])
    nodeId = _add(window, meta, {"feature": "circleCenter"})
    node = window.flowModel.nodes[nodeId]
    if bindingKind == "globalVariableBindings":
        node.globalVariableBindings = [{"parameterPath": ["feature"], "variableId": "feature"}]
    else:
        window.workflowStore.projectExtensions["resources"][bindingKind] = [{"target": {
            "workflowId": window.activeWorkflowId, "nodeId": nodeId, "parameterPath": ["feature"]}}]
    try:
        form = SchemaParamForm()
        form.setSchema(window._nodeEditorSchema(node), node.params)
        assert form.getValues()["inputType"] == ""
        assert form.validationMessage()
        source = window.flowModel.addNode("test.source", "Source", {}, {"bbox": node.inputPorts["bbox"]})
        window.flowModel.connectNodes(source, "bbox", nodeId, "bbox")
        form.setSchema(window._nodeEditorSchema(node), node.params)
        assert form.getValues()["inputType"] == "bbox"
        assert not form.validationMessage()
        assert "inputType" not in node.params
    finally:
        node.globalVariableBindings = []
        if bindingKind != "globalVariableBindings":
            window.workflowStore.projectExtensions["resources"][bindingKind] = []


def testBoundModeValueCanReplaceUnfinishedLiteralAndUnbindRestoresValidation(ownedDesignerWindow):
    from tests.runtime.test_global_variables import variable
    meta = operator("vision.preprocess.blur").meta
    window = _window(ownedDesignerWindow, [meta])
    window.workflowStore.projectExtensions["globalVariables"] = {"sigma": variable(45.0, "number")}
    nodeId = _add(window, meta, {"mode": "mean", "sigmaColor": "unfinished"})
    window.openNodeParamDialog(nodeId)
    editor = window.nodeParamDialog
    form = editor._schemaForm
    choose(form, "mode", "bilateral")
    source = form._variableSources["sigmaColor"]
    source.setCurrentIndex(source.findData("sigma"))
    assert editor.applyChanges(), editor._statusLabel.text()
    node = window.flowModel.nodes[nodeId]
    assert node.params["sigmaColor"] == "unfinished"
    assert node.globalVariableBindings == [{"parameterPath": ["sigmaColor"], "variableId": "sigma"}]
    assert not window.flowModel._validateNodeParamsBySchema()
    editor.forceClose()
    window.openNodeParamDialog(nodeId)
    editor = window.nodeParamDialog
    form = editor._schemaForm
    assert form.getValues()["sigmaColor"] == "unfinished"
    assert editor.applyChanges(), editor._statusLabel.text()
    window.workflowStore.projectExtensions["globalVariables"]["sigma"]["initialValue"] = 0.0
    assert not editor.applyChanges()
    window.workflowStore.projectExtensions["globalVariables"]["sigma"]["initialValue"] = 45.0
    source = form._variableSources["sigmaColor"]
    source.setCurrentIndex(0)
    assert not editor.applyChanges()
    assert form._controls["sigmaColor"].accessibleDescription()
    form._controls["sigmaColor"].setText("30")
    assert editor.applyChanges(), editor._statusLabel.text()
    node = window.flowModel.nodes[nodeId]
    assert not node.globalVariableBindings and node.params["sigmaColor"] == 30


def testGeometryBoundObjectKeepsLiteralDraftAcrossFeatureChanges(ownedDesignerWindow):
    from tests.runtime.test_global_variables import variable
    meta = operator("vision.geometry.extract_points").meta
    window = _window(ownedDesignerWindow, [meta])
    window.workflowStore.projectExtensions["globalVariables"] = {"kind": variable("measurements", "string")}
    nodeId = _add(window, meta, {"feature": "centroid", "inputType": ""})
    window.flowModel.nodes[nodeId].globalVariableBindings = [{"parameterPath": ["inputType"], "variableId": "kind"}]
    window.openNodeParamDialog(nodeId)
    editor = window.nodeParamDialog
    form = editor._schemaForm
    assert form.getValues()["inputType"] == ""
    choose(form, "feature", "bboxCenter")
    assert form.getValues()["inputType"] == ""
    assert editor.applyChanges(), editor._statusLabel.text()
    source = form._variableSources["inputType"]
    source.setCurrentIndex(0)
    assert not editor.applyChanges()
    choose(form, "inputType", "measurements")
    assert editor.applyChanges(), editor._statusLabel.text()


@pytest.mark.parametrize("operatorId,params,inactive,selector,activeValue", [
    ("vision.preprocess.blur", {"mode": "mean", "sigmaColor": "unfinished"}, "sigmaColor", "mode", "bilateral"),
    ("vision.collection.select", {"collectionType": "detections", "mode": "first", "index": "unfinished"}, "index", "mode", "index"),
    ("vision.geometry.coordinate_calculator", {"mode": "measure", "rotationDegrees": "unfinished"}, "rotationDegrees", "mode", "transform"),
    ("vision.preprocess.roi", {"roiType": "bbox", "points": "unfinished"}, "points", "roiType", "polygon"),
    ("vision.preprocess.resize", {"mode": "fit", "width": 10, "height": 10, "padValue": "unfinished"}, "padValue", "mode", "letterbox"),
])
def testHiddenDraftSurvivesReopenAndIsValidatedWhenReenabled(operatorId, params, inactive, selector, activeValue):
    form = SchemaParamForm()
    schema = currentModeSchema(operatorId)
    form.setSchema(schema, params)
    assert form._controls[inactive].isHidden()
    assert not form.validationMessage()
    saved = form.getValues()
    assert saved[inactive] == "unfinished"
    form.setSchema(schema, saved)
    choose(form, selector, activeValue)
    assert not form._controls[inactive].isHidden()
    assert form.validationMessage()


def _window(ownedDesignerWindow, metas):
    window = ownedDesignerWindow(SimpleNamespace(listOperators=lambda: metas))
    window.operatorCatalogController.applyOperators(metas, window._classifyOperator)
    return window


def _add(window, meta, params, x=0):
    node = nodeFor(meta, params)
    nodeId = window.flowModel.addNode(meta.operatorId, meta.displayName, node.inputPorts, node.outputPorts,
                                    paramSchema=deepcopy(meta.paramSchema))
    window.flowModel.nodes[nodeId].params = deepcopy(params)
    window._addControlNodeToScene(nodeId, sceneX=x, sceneY=0)
    return nodeId


def testRealConnectionEntrypointsPreserveOldEdgesButBlockInactivePorts(ownedDesignerWindow, monkeypatch):
    meta = CollectionSortOperator.meta
    window = _window(ownedDesignerWindow, [meta])
    source = _add(window, meta, {"collectionType": "detections"})
    target = _add(window, meta, {"collectionType": "detections"}, 700)
    monkeypatch.setattr(window.flowScene, "getSelectedNodeIds", lambda: [source, target])
    window.connectSelectedNodes()
    edge = FlowEdge(source, "sortedDetections", target, "detections")
    assert window.flowModel.edges == [edge]
    assert window.applyNodeParams(source, {"collectionType": "lines"})
    assert edge in window.flowModel.edges
    assert window.connectPorts(source, "sortedDetections", target, "detections") is None
    assert window.connectPorts(target, "sortedDetections", source, "detections") is None
    scene = window.flowScene
    scene.showAllPorts(source)
    assert "sortedBlobs" not in scene._nodeItems[source].model.outputPorts
    payload = window.workflowController.buildPayload()
    window.workflowController.loadPayload(payload, preserveEdges=True)
    key = (source, "sortedDetections", target, "detections")
    assert key in scene._edgeItems
    scene._edgeItems[key].setSelected(True)
    window.deleteSelectedElements()
    assert (source, "output", "sortedDetections") not in scene._portItems


def testLegacyEditorInfersObjectWithoutChangingSavedNodeUntilApply(ownedDesignerWindow):
    meta = CollectionSortOperator.meta
    window = _window(ownedDesignerWindow, [meta])
    nodeId = _add(window, meta, {"key": "confidence"})
    node = window.flowModel.nodes[nodeId]
    node.paramSchema["properties"].pop("collectionType")
    node.paramSchema.pop("xModeOperator")
    source = window.flowModel.addNode("test.source", "Source", {}, {"detections": "detectionCollection"})
    window._addControlNodeToScene(source, sceneX=-600, sceneY=0)
    window.flowModel.connectNodes(source, "detections", nodeId, "detections")
    window.flowScene.refreshPresentations()
    original = deepcopy(node)
    window.openNodeParamDialog(nodeId)
    editor = window.nodeParamDialog
    assert node == original and not editor.isDirty()
    assert editor._schemaForm.getValues()["collectionType"] == "detections"
    assert editor.applyChanges(), editor._statusLabel.text()
    assert node.params["key"] == "confidence" and node.params["collectionType"] == "detections"


def testDisabledOutputIsNotConnectableUntilEnabled(ownedDesignerWindow):
    meta = RgbStatisticsOperator.meta
    window = _window(ownedDesignerWindow, [meta])
    nodeId = _add(window, meta, {})
    target = window.flowModel.addNode("test.target", "Target", {"image": "image"}, {})
    window._addControlNodeToScene(target, sceneX=600, sceneY=0)
    assert window.connectPorts(nodeId, "mask", target, "image") is None
    assert window.applyNodeParams(nodeId, {"emitMask": True})
    edge = window.connectPorts(nodeId, "mask", target, "image")
    assert edge is not None
    assert window.applyNodeParams(nodeId, {"emitMask": False})
    assert len(window.flowModel.edges) == 1
    assert window.connectPorts(nodeId, "mask", target, "image") is None


@pytest.mark.parametrize("bindingKind", ["globalVariableBindings", "parameterBindings", "siteBindings"])
def testDynamicBindingSourcesReachCanvasAndEditor(ownedDesignerWindow, bindingKind):
    meta = CollectionSortOperator.meta
    window = _window(ownedDesignerWindow, [meta])
    nodeId = _add(window, meta, {"collectionType": "detections"})
    if bindingKind == "globalVariableBindings":
        window.flowModel.nodes[nodeId].globalVariableBindings = [{"parameterPath": ["collectionType"], "variableId": "kind"}]
    else:
        window.workflowStore.projectExtensions["resources"][bindingKind] = [{"target": {
            "workflowId": window.activeWorkflowId, "nodeId": nodeId, "parameterPath": ["collectionType"]}}]
    window.flowScene.refreshPresentations()
    assert len(window.flowScene._nodeItems[nodeId].model.inputPorts) == 7
    schema = window._nodeEditorSchema(window.flowModel.nodes[nodeId])
    form = SchemaParamForm()
    form.setSchema(schema, {"collectionType": "detections"})
    assert form._controls["key"].findData("area") >= 0
    assert form._controls["key"].findData("confidence") >= 0
    window.flowModel.nodes[nodeId].globalVariableBindings = []
    if bindingKind != "globalVariableBindings":
        window.workflowStore.projectExtensions["resources"][bindingKind] = []


@pytest.mark.parametrize("width", [460, 620])
def testNativeModeEditorLayoutAndLegacyOpen(ownedDesignerWindow, designerApplication, width):
    meta = operator("vision.preprocess.blur").meta
    window = _window(ownedDesignerWindow, [meta])
    nodeId = _add(window, meta, {"mode": "mean", "sigmaColor": 45})
    before = deepcopy(window.flowModel.nodes[nodeId])
    window.openNodeParamDialog(nodeId)
    editor = window.nodeParamDialog
    editor.resize(width, 720)
    designerApplication.processEvents()
    assert window.flowModel.nodes[nodeId] == before
    form = editor._schemaForm
    assert form._controls["sigmaColor"].isHidden()
    choose(form, "mode", "bilateral")
    designerApplication.processEvents()
    visible = []
    for name, control in form._controls.items():
        box = form._fieldContainers.get(name, control)
        if box.isHidden():
            continue
        assert form.rect().contains(box.geometry()), name
        assert not form._rowLabels[name].geometry().intersects(box.geometry()), name
        visible.append(box.geometry())
    assert all(not rect.intersects(other) for index, rect in enumerate(visible) for other in visible[index + 1:])
    assert editor.applyChanges()
    assert window.flowModel.nodes[nodeId].params["sigmaColor"] == 45
    screenshots = os.environ.get("EMO_MODE_SCREEN_DIR")
    if screenshots:
        target = Path(screenshots)
        target.mkdir(parents=True, exist_ok=True)
        editor.findChild(QScrollArea).verticalScrollBar().setValue(0)
        designerApplication.processEvents()
        assert editor.grab().save(str(target / f"blur-modes-{width}.png"))


def testRoiCustomEditorHidesUnusedFieldsAndPreservesPolygonDraft(tmp_path, designerApplication):
    from emo_master.apps.designer.operator_editors import OperatorEditorManager
    from tests.designer.test_builtin_operator_editor_ui import _Runtime, _Settings
    from tests.runtime.test_global_variables import variable
    root = Path("src/emo_master/plugins/builtins/roi")
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    operatorId = manifest["operatorId"]
    manager = OperatorEditorManager(
        runtimeClient=_Runtime({operatorId: (root / manifest["editor"]["uiResource"]).read_bytes()}),
        settingsStore=_Settings(), applyParams=lambda _key, _params: True,
        appendLog=lambda _level, _message: None, cacheRoot=tmp_path / "ui-cache")
    window = manager.open(projectId="p", workflowId="main", nodeId="roi", operatorId=operatorId,
        displayName="ROI", schema=manifest["paramSchema"], values={"roiType": "bbox", "points": "unfinished"},
        variableDefinitions=lambda: {"mode": variable("bbox", "string")}, variableBindings=[],
        operatorDefinition={"displayName": manifest["displayName"], "version": manifest["version"],
                            "editorSpec": manifest["editor"], "editorIssues": []})
    try:
        controller = window._controller
        assert controller is not None
        assert controller.controls["points"].isHidden()
        assert controller.controls["centerX"].isHidden()
        assert not controller.controls["x"].isHidden()
        assert controller.collectParams()["points"] == "unfinished"
        assert controller.validate() is None
        controller.controls["roiType"].setCurrentText("polygon")
        assert not controller.controls["points"].isHidden()
        assert controller.controls["width"].isHidden()
        assert controller.validate()
        controller.controls["points"].setPlainText('[{"x":0,"y":0},{"x":10,"y":0},{"x":0,"y":10}]')
        assert controller.validate() is None
        source = window._bindingPanel.fields[("roiType",)]
        source.setCurrentIndex(source.findData("mode"))
        assert not controller.controls["width"].isHidden()
        controller.controls["padValue"].setValue(10)
        assert not controller.controls["width"].isHidden()
        source.setCurrentIndex(0)
        assert controller.controls["width"].isHidden()
        window.show()
        designerApplication.processEvents()
        screenshots = os.environ.get("EMO_MODE_SCREEN_DIR")
        if screenshots:
            assert window.grab().save(str(Path(screenshots) / "roi-polygon.png"))
    finally:
        window.forceClose()
