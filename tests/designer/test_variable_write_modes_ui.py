from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from emo_master.apps.designer.ui.param_form import SchemaParamForm
from emo_master.plugins.builtins.variable_write.operator import SCHEMA
from emo_master.plugins.builtins.variable_write.operator import WriteVariableOperator
from emo_master.apps.designer.ui.main_window import MainWindow
from emo_master.apps.designer.operator_editors.controller_protocol import EditorKey
from emo_master.core.project.models import ProjectDocument
from tests.runtime.test_global_variables import boundProject, variable

from PySide2.QtWidgets import QComboBox, QLineEdit, QSpinBox


@pytest.mark.parametrize("value", [-(2**63), 2**63 - 1, 2**53 + 1, -2147483649, 1, 0])
def testWriterExactIntegerEditing(designerApplication, value):
    form = SchemaParamForm()
    form.setSchema(SCHEMA, {"variableId": "count", "operation": "increment", "delta": value})
    form.show()
    designerApplication.processEvents()
    assert isinstance(form._controls["delta"], QLineEdit)
    assert form.getValues()["delta"] == value
    assert not form.validationMessage()
    form._controls["delta"].setText(str(2**63 - 1))
    assert form.getValues()["delta"] == 2**63 - 1
    assert not form.validationMessage()


@pytest.mark.parametrize("text", ["", "-", "1.5", "true", str(2**63), str(-(2**63) - 1)])
def testWriterInvalidIntegerDraftIsNotTruncated(designerApplication, text):
    form = SchemaParamForm()
    form.setSchema(SCHEMA, {"variableId": "count", "operation": "increment"})
    form._controls["delta"].setText(text)
    assert form.validationMessage()
    assert form._controls["delta"].text() == text
    assert form.getValues()["delta"] != 0


def testWriterModeSwitchAndOldBoundedIntegerControls(designerApplication):
    form = SchemaParamForm()
    original = deepcopy(SCHEMA)
    form.setSchema(SCHEMA, {"variableId": "count"})
    assert isinstance(form._controls["operation"], QComboBox)
    assert form.getValues()["operation"] == "set"
    assert not form._controls["delta"].isEnabled()
    form._controls["operation"].setCurrentIndex(form._controls["operation"].findData("increment"))
    assert form._controls["delta"].isEnabled()
    form._controls["delta"].setText("9007199254740993")
    form._controls["operation"].setCurrentIndex(form._controls["operation"].findData("reset"))
    assert not form._controls["delta"].isEnabled()
    assert form.getValues()["delta"] == 9007199254740993
    assert SCHEMA == original
    form.setSchema({"type": "object", "properties": {
        "threshold": {"type": "integer", "minimum": 0, "maximum": 255, "default": 128}
    }}, {})
    assert isinstance(form._controls["threshold"], QSpinBox)
    assert form.getValues()["threshold"] == 128


def testLegacyWriterOpenApplyPortGuardAndSaveReload(designerApplication, tmp_path):
    payload = boundProject()
    payload["globalVariables"] = {"v": variable(0, "integer", "job")}
    workflow = payload["workflows"]["main"]
    workflow["inputs"] = {"value": "integer"}
    workflow["outputs"] = {"value": "integer"}
    workflow["nodes"][1] = {"nodeId": "number", "kind": "operator", "operatorId": WriteVariableOperator.meta.operatorId,
        "params": {"variableId": "v"}, "paramSchema": {"type": "object", "properties": {"variableId": {"type": "string"}}, "required": ["variableId"]},
        "inputPorts": {"value": "integer"}, "outputPorts": {"value": "integer"}}
    workflow["edges"].insert(0, dict(fromNode="input", fromPort="value", toNode="number", toPort="value"))
    window = MainWindow(SimpleNamespace(listOperators=lambda: []))
    window.workflowController.loadPayload(payload)
    window.operatorCatalogController.operatorCatalog = [{"operatorId": WriteVariableOperator.meta.operatorId, "paramSchema": SCHEMA}]
    window.operatorCatalogController.hasCatalog = True
    window.openNodeParamDialog("number")
    editor = window.nodeParamDialog
    before = deepcopy(window.flowModel.nodes["number"].params)
    assert not editor.isDirty()
    assert before == {"variableId": "v"}
    assert not editor._bindingPanel.fields.get(("operation",))
    assert not editor._bindingPanel.fields.get(("variableId",))
    combo = editor._schemaForm._controls["operation"]
    combo.setCurrentIndex(combo.findData("increment"))
    assert not editor.applyChanges()  # must not silently remove the old value edge
    assert window.flowModel.nodes["number"].params == before
    assert any(e.toPort == "value" and e.toNode == "number" for e in window.flowModel.edges)
    window.flowModel.edges = [e for e in window.flowModel.edges if e.toNode != "number"]
    editor._schemaForm._controls["delta"].setText("9007199254740993")
    assert editor.applyChanges()
    assert window.flowModel.nodes["number"].inputPorts == {"after": "any"}
    window.flowModel.connectNodes("input", "value", "number", "after")
    saved = window.workflowController.buildPayload()
    path = tmp_path / "saved.json"
    path.write_text(json.dumps(saved), encoding="utf-8")
    loaded = ProjectDocument.model_validate_json(path.read_text(encoding="utf-8"))
    node = next(n for n in loaded.workflows["main"].nodes if n.nodeId == "number")
    assert node.params["delta"] == 9007199254740993
    assert node.params["operation"] == "increment"
    editor.forceClose()
    window.workflowController.loadPayload(loaded.toPayload())
    assert window.flowModel.nodes["number"].inputPorts == {"after": "any"}
    key = EditorKey(window._currentProjectId(), "main", "number")
    # Static operation/variable selection cannot be turned into a dynamic binding.
    from emo_master.core.project.global_variables import VariableError
    with pytest.raises(VariableError):
        window._applyEditorConfiguration(key, node.params, [{"parameterPath": ["operation"], "variableId": "v"}])


def testInactiveWorkflowWriterGuardRefreshAndSaveReload(designerApplication):
    payload = boundProject()
    payload["globalVariables"] = {"v": variable(0, "integer", "job")}
    workflow = payload["workflows"]["main"]
    workflow["inputs"] = {"value": "integer"}
    workflow["outputs"] = {"value": "integer"}
    workflow["nodes"][1] = {"nodeId": "number", "kind": "operator", "operatorId": WriteVariableOperator.meta.operatorId,
        "params": {"variableId": "v"}, "paramSchema": SCHEMA, "inputPorts": {"value": "integer"},
        "outputPorts": {"value": "integer"}}
    workflow["edges"].insert(0, dict(fromNode="input", fromPort="value", toNode="number", toPort="value"))
    payload["workflows"]["other"] = deepcopy(workflow)
    payload["workflowOrder"].append("other")
    window = MainWindow(SimpleNamespace(listOperators=lambda: []))
    window.workflowController.loadPayload(payload)
    window.operatorCatalogController.operatorCatalog = [{"operatorId": WriteVariableOperator.meta.operatorId, "paramSchema": SCHEMA}]
    window.operatorCatalogController.hasCatalog = True
    key = EditorKey(window._currentProjectId(), "other", "number")
    from emo_master.core.project.global_variables import VariableError
    with pytest.raises(VariableError, match="操作改变端口"):
        window._applyEditorConfiguration(key, {"variableId": "v", "operation": "increment"}, [])
    other = window.workflowStore.get("other")
    assert other.nodes[1]["params"] == {"variableId": "v"}
    assert len(other.edges) == 2
    other.edges = [edge for edge in other.edges if edge["toNode"] != "number"]
    assert window._applyEditorConfiguration(key, {"variableId": "v", "operation": "reset"}, [])
    assert other.nodes[1]["inputPorts"] == {"after": "any"}
    assert window.activeWorkflowId == "main"
    assert window.flowModel.nodes["number"].inputPorts == {"value": "integer"}
    other.edges.insert(0, dict(fromNode="input", fromPort="value", toNode="number", toPort="after"))
    saved = ProjectDocument.model_validate(window.workflowController.buildPayload())
    window.workflowController.loadPayload(saved.toPayload())
    restored = window.workflowStore.get("other").nodes[1]
    assert restored["params"]["operation"] == "reset"
    assert restored["inputPorts"] == {"after": "any"}
