from copy import deepcopy
import json
from types import SimpleNamespace

import pytest
import emo_master  # noqa: F401 - preload Windows dependencies before Qt
from PySide2.QtWidgets import QLabel

from emo_master.apps.designer.state.schema_utils import mergeParameterTitles
from emo_master.apps.designer.ui.param_form import SchemaParamForm, parameterTitle


@pytest.mark.parametrize("title", [None, "", "  ", 123, [], {}])
def testInvalidTitleFallsBackToInternalKey(title):
    assert parameterTitle("kernelSize", {"title": title}) == "kernelSize"


def testLabelsTooltipsAndNestedValuesRemainIndependentOfTitles():
    schema = {"type": "object", "required": ["size"], "properties": {
        "size": {"type": "integer", "title": " 卷积核大小 ", "description": "必须为奇数", "default": 5},
        "mode": {"type": "string", "title": "模式", "enum": ["fast", "accurate"], "default": "fast"},
        "nested": {"type": "object", "title": "嵌套配置", "properties": {
            "enabled": {"type": "boolean", "title": "启用", "default": True}}},
        "points": {"type": "array", "title": "坐标点", "default": [{"x": 1, "y": 2}]},
        "plain": {"type": "string", "default": "original"},
    }}
    original = deepcopy(schema)
    form = SchemaParamForm()
    form.setSchema(schema, {})
    size = form._controls["size"]
    label = form._layout.labelForField(size)
    assert label.text() == "卷积核大小 *"
    assert label.toolTip() == size.toolTip() == "卷积核大小\n必须为奇数\n参数键：size"
    nested = next(iter(form._nestedFormsByControlId.values()))
    assert nested._layout.labelForField(nested._controls["enabled"]).text() == "启用"
    size.setValue(7)
    form._controls["mode"].setCurrentIndex(1)
    nested._controls["enabled"].setChecked(False)
    assert form.getValues() == {
        "size": 7, "mode": "accurate", "nested": {"enabled": False},
        "points": [{"x": 1, "y": 2}], "plain": "original",
    }
    assert json.loads(form._controls["points"].toPlainText()) == [{"x": 1, "y": 2}]
    assert schema == original
    form.close()


def testTitleOverlayPreservesOldContractAndMissingCatalog():
    saved = {"type": "object", "required": ["size"], "properties": {
        "size": {"type": "integer", "minimum": 1, "default": 3},
        "removed": {"type": "string", "title": "已有标题"},
        "nested": {"type": "object", "properties": {"x": {"type": "number"}}},
        "points": {"type": "array", "items": {"type": "object", "properties": {
            "x": {"type": "number"}}}},
    }}
    catalog = {"required": [], "properties": {
        "size": {"type": "string", "title": "大小", "default": "changed"},
        "added": {"type": "boolean", "title": "新字段"},
        "removed": {"title": " "},
        "nested": {"properties": {"x": {"title": "横坐标"}, "new": {"title": "新增"}}},
        "points": {"items": {"properties": {"x": {"title": "横坐标"}}}},
    }}
    originals = deepcopy((saved, catalog))
    result = mergeParameterTitles(saved, catalog)
    assert result["properties"]["size"] == dict(saved["properties"]["size"], title="大小")
    assert result["required"] == ["size"]
    assert result["properties"]["removed"]["title"] == "已有标题"
    assert set(result["properties"]) == set(saved["properties"])
    assert result["properties"]["nested"]["properties"] == {"x": {"type": "number", "title": "横坐标"}}
    assert result["properties"]["points"]["items"]["properties"]["x"]["title"] == "横坐标"
    assert (saved, catalog) == originals
    detached = mergeParameterTitles(saved, {})
    assert detached == saved
    detached["properties"]["size"]["default"] = 99
    assert saved == originals[0]


def testOldProjectOpensChineseWithoutDirtyingAndRoundTrips(tmp_path, monkeypatch, ownedDesignerWindow):
    from tests.designer.test_main_window_project_save_load import RuntimeClientStub

    settings = {}
    window = ownedDesignerWindow(RuntimeClientStub(), settingsStore=SimpleNamespace(
        value=lambda key, default=None: settings.get(key, default),
        setValue=lambda key, value: settings.__setitem__(key, value),
    ))
    oldSchema = {"type": "object", "properties": {"value": {"type": "number", "default": 0}}}
    window.addNodeFromOperatorPayload({
        "operatorId": "vision.value.number", "displayName": "Number",
        "inputPorts": {}, "outputPorts": {"value": "number"}, "paramSchema": oldSchema,
    })
    nodeId = window.flowModel.selectedNodeId
    window.flowModel.setNodeParams(nodeId, {"value": 2.5})
    directory = tmp_path / "old-project"
    assert window.saveProjectToDirectory(str(directory))
    monkeypatch.setattr(window, "_requireOperatorCatalog", lambda: True)
    window.operatorCatalogController.applyOperators([SimpleNamespace(
        operatorId="vision.value.number", displayName="Number",
        paramSchema={"properties": {"value": {"title": "数值", "default": 99}}},
    )], lambda _: "其他")
    before = deepcopy(window.flowModel.toProjectGraph())
    window.openNodeParamDialog(nodeId)
    editor = window.nodeParamDialog
    form = editor._schemaForm
    assert form._layout.labelForField(form._controls["value"]).text() == "数值"
    assert not editor.isDirty()
    assert not window.pageCoordinator.session.dirty
    assert window.flowModel.toProjectGraph() == before
    assert editor.collectParams() == {"value": 2.5}
    form._controls["value"].setValue(4.5)
    assert editor.applyChanges()
    assert window.flowModel.nodes[nodeId].params == {"value": 4.5}
    window.pageCoordinator.history()
    assert window.flowModel.nodes[nodeId].params == {"value": 2.5}
    window.pageCoordinator.history(redo=True)
    assert window.flowModel.nodes[nodeId].params == {"value": 4.5}
    assert window.saveProjectToDirectory(str(directory))
    assert window.loadProjectDirectory(str(directory))
    node = window.flowModel.nodes[nodeId]
    assert node.params == {"value": 4.5}
    assert node.paramSchema == oldSchema
    assert window._nodeEditorSchema(node)["properties"]["value"]["title"] == "数值"


@pytest.mark.parametrize("width", [320, 720])
def testLongChineseLabelWrapsWithoutOverlappingInput(designerApplication, width):
    form = SchemaParamForm()
    form.setSchema({"properties": {"timeout": {
        "title": "相机采集触发后等待图像返回的最长时间（毫秒）", "type": "integer",
    }}}, {})
    form.resize(width, 160)
    form.show()
    designerApplication.processEvents()
    control = form._controls["timeout"]
    label = form._layout.labelForField(control)
    assert isinstance(label, QLabel)
    assert not label.geometry().intersects(control.geometry())
    assert form.rect().contains(label.geometry())
    assert form.rect().contains(control.geometry())
    form.close()
