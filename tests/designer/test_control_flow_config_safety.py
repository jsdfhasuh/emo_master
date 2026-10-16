from copy import deepcopy
from types import SimpleNamespace

import emo_master  # noqa: F401 - preload Windows dependencies before Qt
import pytest

from emo_master.apps.designer.state.schema_utils import applySchemaDefaults
from emo_master.apps.designer.state.workflow_store import WorkflowStore
from emo_master.apps.designer.ui.param_form import SchemaParamForm
from emo_master.apps.designer.ui.main_window import MainWindow
from emo_master.apps.designer.state.workflow_package import buildWorkflowPackage, importWorkflowPackage
from emo_master.apps.designer.page_designer.editing import outputChoices
from emo_master.core.project.models import ProjectDocument
from emo_master.plugins.builtins.flow_switch.operator import FlowSwitchOperator
from tests.designer.test_control_flow_usability import _addBranch, _window
from tests.core.contracts.test_loop_mapping_safety import loopProgram


def switchWindow():
    # Switch fixtures must not depend on the editable camera/batch example or
    # on its unrelated cached ports and editor metadata.
    window = MainWindow(SimpleNamespace(listOperators=lambda: [FlowSwitchOperator.meta],
                                        loadProject=lambda _: SimpleNamespace(ok=True)))
    window.operatorCatalogController.applyOperators([FlowSwitchOperator.meta], lambda _: "控制流")
    return window


@pytest.mark.parametrize("params", [{}, {"case0Value": ""}, {"case1Value": "OK"},
                                    {"case0Value": "", "case2Value": "OK", "case3Value": "OK"}])
def testSwitchApplyWithoutEditsPreservesPresenceAndBranchSemantics(params):
    window = switchWindow()
    nodeId = _addBranch(window, FlowSwitchOperator, deepcopy(params), 0, 0)
    node = window.flowModel.nodes[nodeId]
    # Saved schemas from old projects do not contain the new display annotation.
    for field in node.paramSchema["properties"].values():
        field.pop("xOptionalPresence", None)
    before = deepcopy(window.flowModel.toProjectGraph())
    window.openNodeParamDialog(nodeId)
    editor = window.nodeParamDialog
    assert not editor.isDirty()
    assert window.flowModel.toProjectGraph() == before
    assert editor.collectParams() == params
    assert editor.applyChanges()
    assert node.params == params
    for value in ("", "OK", "NG"):
        operator = FlowSwitchOperator()
        assert operator.executeNode({"value": value}, node.params, {}) == operator.executeNode({"value": value}, params, {})


def testSwitchPresenceToggleSupportsEmptyValueAndRetainsUnappliedText():
    window = switchWindow()
    nodeId = _addBranch(window, FlowSwitchOperator, {}, 0, 0)
    window.openNodeParamDialog(nodeId)
    editor = window.nodeParamDialog
    control = editor._schemaForm._controls["case0Value"]
    assert not control.enabledCheckBox.isChecked()
    assert not control.valueControl.isEnabled()
    control.enabledCheckBox.setChecked(True)
    assert editor.collectParams() == {"case0Value": ""}
    assert editor.applyChanges()
    assert FlowSwitchOperator().executeNode({"value": ""}, editor.collectParams(), {})["outputs"] == {"case0": ""}
    control.valueControl.setText("draft")
    control.enabledCheckBox.setChecked(False)
    assert editor.collectParams() == {}
    control.enabledCheckBox.setChecked(True)
    assert editor.collectParams() == {"case0Value": "draft"}
    control.enabledCheckBox.setChecked(False)
    assert editor.applyChanges()
    assert window.flowModel.nodes[nodeId].params == {}


def testSwitchDefaultsDoNotInventConfiguredCases():
    assert applySchemaDefaults(FlowSwitchOperator.meta.paramSchema, {}) == {}
    assert applySchemaDefaults(FlowSwitchOperator.meta.paramSchema, {"case0Value": ""}) == {"case0Value": ""}
    schema = {"type": "object", "properties": {"text": {"type": "string", "default": "normal"}}}
    assert applySchemaDefaults(schema, {}) == {"text": "normal"}


def testSwitchPresenceRoundTripsThroughUndoRedoAndProjectSave(tmp_path):
    window = switchWindow()
    nodeId = _addBranch(window, FlowSwitchOperator, {}, 0, 0)
    # The canvas helper bypasses the normal load/creation commands. Render its
    # persisted graph before testing the parameter editor's own undo boundary.
    window.workflowController.captureActiveWorkflow()
    window.workflowController._renderActive()
    window.pageCoordinator.sync()
    window.openNodeParamDialog(nodeId)
    editor = window.nodeParamDialog
    editor._schemaForm._controls["case1Value"].enabledCheckBox.setChecked(True)
    assert editor.applyChanges()
    assert window.flowModel.nodes[nodeId].params == {"case1Value": ""}
    window.pageCoordinator.history()
    assert window.flowModel.nodes[nodeId].params == {}
    window.pageCoordinator.history(redo=True)
    assert window.flowModel.nodes[nodeId].params == {"case1Value": ""}
    destination = tmp_path / "switch-presence"
    assert window.saveProjectToDirectory(str(destination))
    assert window.loadProjectDirectory(str(destination))
    assert window.flowModel.nodes[nodeId].params == {"case1Value": ""}


def testForEachChangingToSingleInputBodyKeepsStaleMappingAndBlocksApply():
    window = _window()
    for workflowId, port in (("original", "item"), ("replacement", "renamed")):
        window.workflowStore.addWorkflow(workflowId, workflowId=workflowId,
                                        inputs={port: "integer"}, outputs={})
    nodeId = window._addLoopNode({"contractVersion": 2, "mode": "foreach", "bodyWorkflowId": "original",
        "itemInputPort": "item", "maxIterations": 10, "timeoutMs": 0}, "ForEach")
    window.openNodeParamDialog(nodeId)
    editor = window.nodeParamDialog
    form = editor._schemaForm
    body = form._controls["bodyWorkflowId"]
    body.setCurrentIndex(body.findData("replacement"))
    assert form._controls["itemInputPort"].currentData() == "item"
    assert "已失效" in form._controls["itemInputPort"].currentText()
    assert not editor.applyChanges()
    assert window.flowModel.nodes[nodeId].loop["bodyWorkflowId"] == "original"
    assert window.flowModel.nodes[nodeId].loop["itemInputPort"] == "item"


def testForEachFormRejectsOverlappingPortsWithoutReplacingThem():
    window = _window()
    window.workflowStore.addWorkflow("Integer body", workflowId="integer", inputs={"value": "integer"}, outputs={"value": "integer"})
    config = {"mode": "foreach", "bodyWorkflowId": "integer", "itemInputPort": "value", "indexInputPort": "value",
              "maxIterations": 10, "timeoutMs": 0}
    form = SchemaParamForm()
    form.setSchema(window._nodeEditorSchema(SimpleNamespace(kind="loop", loop=config)), config)
    assert "不能相同" in form.validationMessage()
    assert form.getValues()["itemInputPort"] == form.getValues()["indexInputPort"] == "value"
    form._controls["indexInputPort"].setCurrentIndex(form._controls["indexInputPort"].findData(""))
    assert form.validationMessage() == ""


@pytest.mark.parametrize("mode", ["repeat", "foreach"])
def testDormantConditionDoesNotMakeWorkflowReachableOrPreventDeletion(mode):
    store = WorkflowStore()
    store.addWorkflow("Body", workflowId="body")
    store.addWorkflow("Unused old condition", workflowId="old-condition")
    config = {"mode": mode, "bodyWorkflowId": "body", "conditionWorkflowId": "old-condition"}
    store.get("main").nodes.append({"nodeId": "loop", "kind": "loop", "loop": config})
    before = deepcopy(store.workflows)
    assert store.referencesTo("old-condition") == []
    refs = store.getWorkflowDependencyReferences()
    assert [(r["relation"], r["targetWorkflowId"]) for r in refs] == [(f"{mode}-body", "body")]
    tree = store.getWorkflowDependencyTree()
    assert tree[1]["workflowIds"] == ["old-condition"]
    assert store.workflows == before
    store.deleteWorkflow("old-condition")
    assert store.get("main").nodes[-1]["loop"]["conditionWorkflowId"] == "old-condition"


@pytest.mark.parametrize("mode", ["repeat", "foreach"])
def testWorkflowPackagePreservesButDoesNotRequireDormantCondition(mode):
    store = WorkflowStore()
    store.loadPayload(loopProgram(mode, conditionWorkflowId="not-packaged"))
    package = buildWorkflowPackage(store, "main")
    target = WorkflowStore()
    result = importWorkflowPackage(target, package)
    main = target.get(result.rootWorkflowId)
    node = next(n for n in main.nodes if n.get("kind") == "loop")
    assert node["loop"]["conditionWorkflowId"] == "not-packaged"


@pytest.mark.parametrize("mode,conditionMode,active", [
    ("repeat", None, False), ("foreach", None, False),
    ("while", "boolean", False), ("while", None, True),
])
def testPageResultChoicesDoNotOfferDormantLoopCondition(mode, conditionMode, active):
    payload = loopProgram(mode, conditionWorkflowId="body")
    if conditionMode is not None:
        payload["workflows"]["main"]["nodes"][1]["loop"]["conditionMode"] = conditionMode
    document = ProjectDocument.model_validate(payload)
    before = document.model_dump()
    choices = outputChoices(document, {})
    relations = {choice.source.callPath[0].relation for choice in choices if choice.source.callPath}
    assert relations == ({"loop_body", "loop_condition"} if active else {"loop_body"})
    assert document.model_dump() == before
