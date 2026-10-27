from copy import deepcopy
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide2.QtCore import QPoint, Qt
from PySide2.QtTest import QTest
from PySide2.QtWidgets import QLineEdit, QScrollArea

from emo_master.apps.designer.state.flow_graph_model import FlowEdge, FlowNode
from emo_master.apps.designer.ui.collection_filter_presentation import (
    OPERATOR_ID, activeFilterPorts, collectionFilterPresentation, filterCollectionType,
)
from emo_master.apps.designer.ui.flow_scene import FlowNodeViewModel
from emo_master.apps.designer.ui.param_form import SchemaParamForm
from emo_master.plugins.builtins.collection_filter.configuration import COLLECTION_TYPES, FILTER_GROUPS
from emo_master.plugins.builtins.collection_filter.operator import CollectionFilterOperator


META = CollectionFilterOperator.meta
INPUTS = {key: value["type"] for key, value in META.inputPorts.items()}
OUTPUTS = {key: value["type"] for key, value in META.outputPorts.items()}


def _node(params=None):
    return FlowNode("filter", OPERATOR_ID, "Filter", INPUTS, OUTPUTS, params=params or {})


def _view(node):
    return FlowNodeViewModel(node.nodeId, node.displayName, 0, 0, INPUTS, OUTPUTS,
                             operatorId=OPERATOR_ID)


def _window(ownedDesignerWindow):
    window = ownedDesignerWindow(SimpleNamespace(listOperators=lambda: [META]))
    window.operatorCatalogController.applyOperators([META], window._classifyOperator)
    return window


def _add(window, params=None, x=0, oldSchema=False):
    schema = deepcopy(META.paramSchema)
    if oldSchema:
        schema.pop("xCollectionFilter", None)
        schema["properties"] = {key: value for key, value in schema["properties"].items()
                                if key not in FILTER_GROUPS and key != "collectionType"}
    nodeId = window.flowModel.addNode(OPERATOR_ID, "Filter", INPUTS, OUTPUTS, paramSchema=schema)
    window.flowModel.nodes[nodeId].params = dict(params or {})
    window._addControlNodeToScene(nodeId, sceneX=x, sceneY=0)
    return nodeId


def _choose(form, key, value):
    control = form._controls[key]
    index = control.findData(value)
    assert index >= 0
    control.setCurrentIndex(index)


@pytest.mark.parametrize("kind", COLLECTION_TYPES)
def testEachTypeHasOnlyItsOwnPortsAndApplicableGroups(kind):
    params = {"collectionType": kind}
    node = _node(params)
    original = deepcopy(node)
    view = collectionFilterPresentation(_view(node), node, [])
    assert set(view.inputPorts) == {kind}
    assert set(view.outputPorts) == {"kept" + kind.capitalize(), "rejected" + kind.capitalize(),
                                     "keptCount", "rejectedCount"}
    assert node == original
    form = SchemaParamForm()
    form.setSchema(META.paramSchema, params)
    for flag, group in FILTER_GROUPS.items():
        assert form._controls[flag].isHidden() == (kind not in group["types"])
        if kind in group["types"]:
            form._controls[flag].setChecked(True)
            for field in group["fields"]:
                assert not form._controls[field].isHidden()
            form._controls[flag].setChecked(False)
            for field in group["fields"]:
                assert form._controls[field].isHidden()


def testNewNodeRequiresTypeAndDoesNotExposeAllCollections():
    node = _node()
    view = collectionFilterPresentation(_view(node), node, [])
    assert view.inputPorts == {}
    assert set(view.outputPorts) == {"keptCount", "rejectedCount"}
    form = SchemaParamForm()
    form.setSchema(META.paramSchema, {})
    assert form._controls["collectionType"].currentData() == ""
    assert "请选择筛选对象" in form.validationMessage()
    _choose(form, "collectionType", "detections")
    assert form.validationMessage() == ""
    assert all(not form._controls[flag].isChecked() for flag in FILTER_GROUPS)


def testSpatialAndTypeBindingsOnlyExposePossibleDependencies():
    params = dict(collectionType="detections", spatialMode="centerInside", spatialEnabled=False)
    assert activeFilterPorts(params)[0] == {"detections"}
    assert activeFilterPorts(params, dynamicParameters={"spatialEnabled"})[0] == {"detections", "roi"}
    assert activeFilterPorts(params, dynamicParameters={"collectionType"})[0] == set(COLLECTION_TYPES)
    assert activeFilterPorts({"collectionType": "lines"}, dynamicParameters={"spatialMode"})[0] == {"lines", "roi"}


def testLegacyConnectionsInferTypeWithoutMutatingNode():
    node = _node({"classIds": [0], "minConfidence": .6})
    edges = [FlowEdge("source", "detections", "filter", "detections")]
    before = deepcopy(node)
    assert filterCollectionType(node, edges) == "detections"
    assert set(collectionFilterPresentation(_view(node), node, edges).inputPorts) == {"detections"}
    assert node == before


def testLegacyEditorInfersEnabledGroupsAndPreservesOriginalUntilApply(ownedDesignerWindow):
    window = _window(ownedDesignerWindow)
    old = {"classIds": [0], "minConfidence": .6}
    nodeId = _add(window, old, oldSchema=True)
    source = window.flowModel.addNode("test.source", "Source", {}, {"detections": "detectionCollection"})
    window._addControlNodeToScene(source, sceneX=-700, sceneY=0)
    # Existing projects load their complete saved contract before presentation.
    edge = window.flowModel.connectNodes(source, "detections", nodeId, "detections")
    window.workflowController.captureActiveWorkflow()
    window.workflowController._renderActive()
    node = window.flowModel.nodes[nodeId]
    oldSchema = deepcopy(node.paramSchema)
    window.openNodeParamDialog(nodeId)
    editor = window.nodeParamDialog
    form = editor._schemaForm
    assert node.params == old and node.paramSchema == oldSchema
    assert not editor.isDirty()
    assert form._controls["collectionType"].currentData() == "detections"
    assert form._controls["classIdsEnabled"].isChecked()
    assert form._controls["confidenceEnabled"].isChecked()
    assert not form._controls["areaEnabled"].isVisible()
    assert isinstance(form._controls["classIds"], QLineEdit)
    assert editor.applyChanges(), editor._statusLabel.text()
    assert node.params["classIds"] == [0] and node.params["minConfidence"] == .6
    assert node.params["collectionType"] == "detections"
    assert edge in window.flowModel.edges


@pytest.mark.parametrize("field,text", [("minConfidence", "bad number"), ("classIds", "bad list")])
def testDisabledInvalidDraftDoesNotBlockApplyAndSurvivesReopen(field, text):
    flag = "confidenceEnabled" if field == "minConfidence" else "classIdsEnabled"
    form = SchemaParamForm()
    form.setSchema(META.paramSchema, {"collectionType": "detections", flag: True})
    form._controls[field].setText(text)
    assert form.validationMessage()
    form._controls[flag].setChecked(False)
    assert form.validationMessage() == ""
    values = form.getValues()
    assert values[field] == text
    form.setSchema(META.paramSchema, values)
    assert form._controls[field].text() == text
    assert form.validationMessage() == ""
    form._controls[flag].setChecked(True)
    assert form.validationMessage()


def testSwitchTypeRetainsValuesButDoesNotValidateOtherTypes():
    form = SchemaParamForm()
    form.setSchema(META.paramSchema, {"collectionType": "detections", "confidenceEnabled": True,
                                     "minConfidence": .8, "maxConfidence": .2})
    assert form.validationMessage()
    _choose(form, "collectionType", "lines")
    assert form.validationMessage() == ""
    assert form.getValues()["minConfidence"] == .8
    _choose(form, "collectionType", "detections")
    assert form._controls["minConfidence"].text() == "0.8"
    assert form.validationMessage()


def testUnrelatedDynamicGroupDoesNotBypassFixedRangeValidation():
    schema = deepcopy(META.paramSchema)
    schema["xCollectionFilterDynamicParameters"] = ["spatialEnabled"]
    form = SchemaParamForm()
    form.setSchema(schema, {"collectionType": "detections", "confidenceEnabled": True,
                            "minConfidence": .8, "maxConfidence": .2})
    assert form.validationMessage()
    form._controls["maxConfidence"].setValue(.9)
    assert form.validationMessage() == ""


def testSchemaRefreshDefersFieldDestruction(designerApplication):
    from PySide2.QtCore import QCoreApplication, QEvent
    from shiboken2 import isValid
    form = SchemaParamForm()
    form.setSchema(META.paramSchema, {"collectionType": "detections"})
    oldControls = list(form._controls.values())
    oldLabels = list(form._rowLabels.values())
    form.setSchema(META.paramSchema, form.getValues())
    assert all(isValid(widget) for widget in oldControls + oldLabels)
    assert all(form._layout.indexOf(widget) == -1 for widget in oldControls + oldLabels)
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    assert not any(isValid(widget) for widget in oldControls + oldLabels)
    assert form.getValues()["collectionType"] == "detections"


def testSchemaRefreshFromFieldSignalKeepsSenderAlive(designerApplication):
    from PySide2.QtCore import QCoreApplication, QEvent
    from shiboken2 import isValid
    form = SchemaParamForm()
    form.setSchema(META.paramSchema, {"collectionType": "detections"})
    selector = form._controls["collectionType"]
    observed = []
    form.valuesChanged.connect(lambda: form.setSchema(META.paramSchema, form.getValues()))
    selector.currentIndexChanged.connect(lambda _: observed.append(selector.currentData()))
    selector.setCurrentIndex(selector.findData("blobs"))
    assert observed == ["blobs"] and isValid(selector)
    assert form.getValues()["collectionType"] == "blobs"
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    assert not isValid(selector)


def testSchemaRefreshReleasesDetachedRows(designerApplication):
    from PySide2.QtCore import QCoreApplication, QEvent
    from PySide2.QtWidgets import QWidget
    form = SchemaParamForm()
    form.setSchema(META.paramSchema, {"collectionType": "detections", "classIds": [0]})
    initialCount = len(form.findChildren(QWidget))
    for _ in range(3):
        form.setSchema(META.paramSchema, form.getValues())
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        assert len(form.findChildren(QWidget)) == initialCount
    assert form.getValues()["classIds"] == [0]


def testSpatialApplyAndManualRestoreNeverRevealInactiveType(ownedDesignerWindow):
    window = _window(ownedDesignerWindow)
    nodeId = _add(window, {"collectionType": "detections"})
    window.openNodeParamDialog(nodeId)
    editor = window.nodeParamDialog
    form = editor._schemaForm
    form._controls["spatialEnabled"].setChecked(True)
    assert form._controls["spatialMode"].currentData() != "none"
    assert editor.applyChanges()
    scene = window.flowScene
    assert set(scene._nodeItems[nodeId].model.inputPorts) == {"detections", "roi"}
    scene.hideUnusedPorts(nodeId)
    scene.showAllPorts(nodeId)
    assert {key[2] for key, item in scene._portItems.items()
            if key[:2] == (nodeId, "input") and item.isVisible()} == {"detections", "roi"}
    form._controls["spatialEnabled"].setChecked(False)
    assert editor.applyChanges()
    assert (nodeId, "input", "roi") not in scene._portItems


def testConnectedOldPortsRetainedButCannotBeNewConnectionTargets(ownedDesignerWindow):
    window = _window(ownedDesignerWindow)
    source = _add(window, {"collectionType": "detections"})
    target = _add(window, {"collectionType": "detections"}, x=700)
    scene = window.flowScene
    scene.renderEdge(window.connectPorts(source, "keptDetections", target, "detections"))
    scene._nodeItems[source].setSelected(True)
    scene._nodeItems[source].setPos(110, 120)
    assert window.applyNodeParams(source, {"collectionType": "lines"})
    assert "不适用" in scene._nodeItems[source].model.outputPortLabels["keptDetections"]
    assert scene._nodeItems[source].isSelected()
    assert (scene._nodeItems[source].pos().x(), scene._nodeItems[source].pos().y()) == (110, 120)
    assert window.connectPorts(source, "keptDetections", target, "detections") is None
    assert window.connectPorts(target, "keptDetections", source, "detections") is None
    saved = window.workflowController.buildPayload()
    window.workflowController.loadPayload(saved, preserveEdges=True)
    key = (source, "keptDetections", target, "detections")
    assert key in scene._edgeItems
    scene._edgeItems[key].setSelected(True)
    window.deleteSelectedElements()
    assert (source, "output", "keptDetections") not in scene._portItems
    assert set(window.flowModel.nodes[source].outputPorts) == set(OUTPUTS)


def testDefaultConnectionUsesPrimaryCollectionOutput(ownedDesignerWindow, monkeypatch):
    window = _window(ownedDesignerWindow)
    source = _add(window, {"collectionType": "detections"})
    target = _add(window, {"collectionType": "detections"}, x=700)
    monkeypatch.setattr(window.flowScene, "getSelectedNodeIds", lambda: [source, target])
    window.connectSelectedNodes()
    assert window.flowModel.edges == [FlowEdge(source, "keptDetections", target, "detections")]


def testLegacyBoundThresholdActivatesGroupAndDoesNotValidateDisabledDraft(ownedDesignerWindow):
    from tests.runtime.test_global_variables import variable
    window = _window(ownedDesignerWindow)
    window.workflowStore.projectExtensions["globalVariables"] = {"threshold": variable(.6, "number")}
    nodeId = _add(window, {"collectionType": "detections", "classIdsEnabled": False,
                           "classIds": "unfinished list"})
    window.flowModel.nodes[nodeId].globalVariableBindings = [
        {"parameterPath": ["minConfidence"], "variableId": "threshold"}]
    window.openNodeParamDialog(nodeId)
    editor = window.nodeParamDialog
    form = editor._schemaForm
    assert form._controls["confidenceEnabled"].isChecked()
    assert not form._controls["minConfidence"].isEnabled()
    assert form._variableSources["minConfidence"].currentData() == "threshold"
    assert form._fieldContainers["minArea"].isHidden()
    assert editor.applyChanges(), editor._statusLabel.text()
    assert window.flowModel.nodes[nodeId].params["classIds"] == "unfinished list"


@pytest.mark.parametrize("bindingKind", ["globalVariableBindings", "parameterBindings", "siteBindings"])
def testDynamicCollectionTypeKeepsPossiblePortsVisible(ownedDesignerWindow, bindingKind):
    window = _window(ownedDesignerWindow)
    nodeId = _add(window, {"collectionType": "detections"})
    extensions = deepcopy(window.workflowStore.projectExtensions)
    if bindingKind == "globalVariableBindings":
        window.flowModel.nodes[nodeId].globalVariableBindings = [
            {"parameterPath": ["collectionType"], "variableId": "type"}]
    else:
        window.workflowStore.projectExtensions["resources"] = {bindingKind: [{"target": {
            "workflowId": window.activeWorkflowId, "nodeId": nodeId, "parameterPath": ["collectionType"],
        }}]}
    try:
        window.flowScene.refreshPresentations()
        assert set(window.flowScene._nodeItems[nodeId].model.inputPorts) == set(COLLECTION_TYPES)
        schema = window._nodeEditorSchema(window.flowModel.nodes[nodeId])
        form = SchemaParamForm()
        form.setSchema(schema, {"collectionType": "detections", "minLength": 5})
        assert not form._controls["lengthEnabled"].isHidden()
        assert not form._controls["minLength"].isHidden()
        assert form._controls["minArea"].isHidden()  # A dynamic type does not enable disabled groups.
    finally:
        window.flowModel.nodes[nodeId].globalVariableBindings = []
        window.workflowStore.projectExtensions = extensions


@pytest.mark.parametrize("width", [460, 620])
def testNativeFilterEditorLayoutAndToggle(ownedDesignerWindow, designerApplication, width):
    window = _window(ownedDesignerWindow)
    nodeId = _add(window, {"collectionType": "detections", "classIds": [0], "minConfidence": .6})
    window.openNodeParamDialog(nodeId)
    editor = window.nodeParamDialog
    editor.resize(width, 720)
    designerApplication.processEvents()
    form = editor._schemaForm
    assert editor.width() == width
    first = form._controls["collectionType"].geometry()
    nextGroup = form._controls["confidenceEnabled"].geometry()
    assert 0 < nextGroup.top() - first.bottom() < 25
    visible = []
    for name, control in form._controls.items():
        box = form._fieldContainers.get(name, control)
        if box.isHidden():
            continue
        label = form._layout.labelForField(box)
        assert not label.geometry().intersects(box.geometry()), name
        assert form.rect().contains(box.geometry()), name
        visible.append((name, box.geometry()))
    for index, (name, rect) in enumerate(visible):
        for other, otherRect in visible[index + 1:]:
            assert not rect.intersects(otherRect), (name, other)
    assert not editor._applyButton.visibleRegion().isEmpty()
    assert not editor._closeButton.visibleRegion().isEmpty()
    control = form._controls["spatialEnabled"]
    form.focusField("spatialEnabled")
    designerApplication.processEvents()
    QTest.mouseClick(control, Qt.LeftButton, pos=QPoint(8, control.height() // 2))
    assert control.isChecked()
    assert not form._controls["spatialMode"].isHidden()
    assert editor.applyChanges()
    screenshotDir = os.environ.get("EMO_FILTER_SCREEN_DIR")
    if screenshotDir:
        target = Path(screenshotDir)
        target.mkdir(parents=True, exist_ok=True)
        editor.findChild(QScrollArea).verticalScrollBar().setValue(0)
        designerApplication.processEvents()
        assert editor.grab().save(str(target / f"filter-parameters-{width}.png"))
