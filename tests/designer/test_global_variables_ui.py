from copy import deepcopy
from types import SimpleNamespace

import pytest

from emo_master.apps.designer.state.workflow_store import WorkflowStore
from emo_master.apps.designer.state.workflow_package import buildWorkflowPackage, importWorkflowPackage
from emo_master.apps.designer.state.global_variables import editDefinitions
from emo_master.apps.designer.state.project_edit_session import ProjectEditSession
from emo_master.apps.designer.ui.main_window import MainWindow
from emo_master.apps.designer.ui.global_variables_dialog import GlobalVariablesDialog, ScalarValueEditor
from emo_master.apps.designer.operator_editors.controller_protocol import EditorContext, EditorKey
from emo_master.apps.designer.operator_editors.workspace_window import OperatorWorkspaceWindow
from tests.runtime.test_global_variables import boundProject, variable
from tests.designer.test_global_counters_dialog import _waitUntil


def testDefinitionsRenameUndoDeleteProtectionAndPackages():
    payload = boundProject()
    session = ProjectEditSession(payload)
    original = session.payload()
    values = deepcopy(payload["globalVariables"])
    values["v"]["name"] = "renamed"
    with session.transaction():
        editDefinitions(session.workflows, values)
    assert session.dirty
    assert session.undo()
    assert session.payload() == original
    with pytest.raises(ValueError, match="不能删除"):
        editDefinitions(session.workflows, {})
    package = buildWorkflowPackage(session.workflows, "main")
    assert package.schemaVersion == "1.1"
    assert package.globalVariables["v"].initialValue == .5
    target = WorkflowStore()
    result = importWorkflowPackage(target, package)
    assert target.toPayload()["schemaVersion"] == "2.4"
    assert target.get(result.rootWorkflowId).nodes[1]["globalVariableBindings"][0]["variableId"] == "v"
    before = target.toPayload()
    with pytest.raises(ValueError, match="冲突"):
        importWorkflowPackage(target, package)
    assert target.toPayload() == before
    copy = importWorkflowPackage(target, package, reuseVariableIds=True)
    assert copy.rootWorkflowId != result.rootWorkflowId


def testGlobalWhileDesignerAndDirectTypedNode(designerApplication):
    window = MainWindow(SimpleNamespace(listOperators=lambda: []))
    window._editGlobalVariableDefinitions({"enabled": variable(True, "boolean", "job", name="runEnabled")})
    window.createWorkflow("循环体不必回传布尔条件")
    window.activateWorkflow("main")
    nodeId = window.addWhileNode()
    assert nodeId
    node = window.flowModel.nodes[nodeId]
    assert node.loop["conditionMode"] == "globalVariable"
    window.openNodeParamDialog(nodeId)
    form = window.nodeParamDialog._schemaForm
    assert form._controls["conditionVariableId"].currentText() == "runEnabled"
    assert form._controls["conditionPort"].isHidden()
    assert window.nodeParamDialog.applyChanges()
    window.operatorEditorManager.closeAll()


def testSpecializedEditorKeepsLiteralAndSeparateBinding(designerApplication):
    from PySide2.QtWidgets import QWidget
    applied = []
    definitions = {"v": variable(.5, "number"), "b": variable(True, "boolean", name="boolean")}
    context = EditorContext(key=EditorKey("p", "w", "n"), operatorId="test", version="1", previewMode="none",
        paramSchema={"type": "object", "properties": {"value": {"type": "number"}}}, runtimeClient=None,
        applyParams=lambda *args: True, appendLog=lambda *args: None,
        variableDefinitions=lambda: definitions, applyConfiguration=lambda *args: applied.append(args) or True)
    controller = SimpleNamespace(bind=lambda *args: None, loadParams=lambda p: None,
        collectParams=lambda: {"value": 99}, validate=lambda: None, onClose=lambda: None, dispose=lambda: None)
    window = OperatorWorkspaceWindow(key=context.key, title="test", context=context, schema=context.paramSchema,
        values={"value": 99}, customRoot=QWidget(), controller=controller)
    combo = window._bindingPanel.fields[("value",)]
    assert combo.findData("b") < 0
    combo.setCurrentIndex(combo.findData("v"))
    assert window.applyChanges()
    assert applied[-1][1] == {"value": 99}
    assert applied[-1][2] == [{"parameterPath": ["value"], "variableId": "v"}]
    del definitions["v"]
    assert not window.applyChanges()
    window.forceClose()


def testManagementTableJobsConstantAndDisconnect(designerApplication):
    definitions = {"v": variable(.5, "number"), "constant": variable(True, "boolean", kind="constant", name="alwaysTrue"),
                   "job": variable(True, "boolean", "job", name="runEnabled")}
    records = [{"variableId": key, **value, "state": "current" if key != "job" else "initial",
                "value": value["initialValue"] if key != "job" else None, "revision": 1, "updatedAtMs": 1}
               for key, value in definitions.items()]
    client = SimpleNamespace(globalVariableState=lambda *args: {"variables": records, "jobs": [{"jobId": "running", "ended": False}]})
    dialog = GlobalVariablesDialog(client, lambda: definitions, lambda values: None, lambda: False, lambda: "")
    dialog.showForProject("project")
    _waitUntil(designerApplication, lambda: dialog._worker is None)
    dialog.timer.stop()
    assert dialog.jobs.findData("running") >= 0
    assert not dialog.buttons["new"].isEnabled()
    dialog.table.selectRow(0)
    assert dialog.buttons["set"].isEnabled()
    dialog.table.selectRow(1)
    assert not dialog.buttons["set"].isEnabled()
    assert "任务" in dialog.table.item(2, 5).text()
    boolean = ScalarValueEditor("boolean", True)
    assert boolean.value() is True
    dialog.bindProject("other")
    assert not dialog._records
    dialog.shutdown()
    dialog.close()
