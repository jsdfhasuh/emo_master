"""Rebuilt parameter usability contract tests; no external devices required."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from emo_master.apps.designer.state.schema_utils import mergeParameterTitles
from emo_master.apps.designer.ui.node_param_dialog import NodeParamDialog
from emo_master.apps.designer.ui.param_form import SchemaParamForm


def makeForm(field, value=None):
    form = SchemaParamForm()
    form.setSchema({"type": "object", "properties": {"value": dict(field, title="测试值")}},
                   {} if value is None else {"value": value})
    return form


@pytest.mark.parametrize("kind,text", [
    ("array", ""), ("array", "[1,"), ("array", "{}"), ("array", "[NaN]"),
    ("array", "[Infinity]"), ("array", "[1e999]"), ("array", '[{"x": 1e999}]'),
    ("object", ""), ("object", "{bad}"), ("object", "[]"), ("object", '{"x": NaN}'),
    ("object", '{"x": {"y": 1e999}}'),
])
def testInvalidJsonNeverFallsBackAndCanBeCorrected(kind, text):
    original = [2] if kind == "array" else {"x": 2}
    form = makeForm({"type": kind}, original)
    control = form._controls["value"]
    control.setPlainText(text)
    assert "测试值" in form.validationMessage()
    assert control.accessibleDescription()
    with pytest.raises(ValueError):
        form.getValues()
    assert control.toPlainText() == text
    form.setWorkflowOptions(["main"])
    assert form._controls["value"] is control
    assert control.toPlainText() == text
    control.setPlainText("[3]" if kind == "array" else '{"x": 3}')
    assert form.validationMessage() == ""
    assert form.getValues()["value"] == ([3] if kind == "array" else {"x": 3})
    assert original == ([2] if kind == "array" else {"x": 2})


def testJsonErrorNamesNestedPath():
    form = makeForm({"type": "object"})
    form._controls["value"].setPlainText('{"more": [{"weight": 1e999}]}')
    assert "测试值 (value).more[0].weight" in form.validationMessage()


@pytest.mark.parametrize("value", [1.2345, 0.12345678901234566, 1e-200, 1e200, -1e200])
def testNumberRoundTripsWithoutRoundingOrArtificialClamp(value):
    form = makeForm({"type": "number"}, value)
    assert form.getValues()["value"] == value
    form._controls["value"].setValue(value)
    assert form.validationMessage() == ""
    saved = form.getValues()
    form.setSchema(form._rawSchema, saved)
    assert form.getValues() == saved


@pytest.mark.parametrize("text", ["", "-", "1e", "nan", "inf", "-Infinity", "1e999"])
def testInvalidNumbersStayEditable(text):
    form = makeForm({"type": "number"}, 1.25)
    control = form._controls["value"]
    control.setText(text)
    assert "测试值" in form.validationMessage()
    with pytest.raises(ValueError):
        form.getValues()
    assert control.text() == text
    control.setText("1.2345678901234567")
    assert form.validationMessage() == ""
    assert form.getValues()["value"] == 1.2345678901234567


@pytest.mark.parametrize("kind,key,value", [
    ("number", "exclusiveMinimum", 0), ("number", "exclusiveMaximum", 0),
    ("number", "minimum", -1), ("number", "maximum", 1),
    ("integer", "minimum", -1), ("integer", "maximum", 1),
])
def testActualBoundIsReportedWithoutChangingInput(kind, key, value):
    form = makeForm({"type": kind, key: 0, "xUnit": "像素"}, value)
    assert form.getValues()["value"] == value
    assert form.validationMessage()
    assert form.getValues()["value"] == value
    assert "像素" in form._controls["value"].toolTip()


def testNodeApplyRejectsJsonPreservesModelAndThenSucceeds():
    dialog = NodeParamDialog()
    model = {"value": [1, 2]}
    dialog.setNodeContext("node", "test", {"properties": {"value": {"type": "array", "title": "边界"}}}, model)
    dialog.setApplyHandler(lambda _node, params: model.update(params))
    control = dialog._paramForm._controls["value"]
    control.setPlainText("[3,")
    dialog._onApplyClicked()
    assert model == {"value": [1, 2]}
    assert "边界" in dialog._errorLabel.text()
    assert control.toPlainText() == "[3,"
    control.setPlainText("[3, 4]")
    dialog._onApplyClicked()
    assert model == {"value": [3, 4]}
    assert dialog._errorLabel.text() == ""


def testDisplayMergeKeepsOldBoundaryAndMissingDependencyEditable():
    saved = {"properties": {"threshold": {"type": "integer", "minimum": 2, "default": 127}}}
    catalog = {"properties": {"threshold": {"type": "number", "minimum": 0, "default": 999,
        "xMinimum": 0, "xEnabledWhen": {"mode": ["fixed"]}, "xOdd": True, "title": "阈值"}}}
    original = deepcopy(saved)
    merged = mergeParameterTitles(saved, catalog)
    assert merged["properties"]["threshold"]["minimum"] == 2
    assert merged["properties"]["threshold"]["type"] == "integer"
    assert merged["properties"]["threshold"]["default"] == 127
    assert "xEnabledWhen" not in merged["properties"]["threshold"]
    form = SchemaParamForm()
    form.setSchema(merged, {})
    assert form._controls["threshold"].isEnabled()
    assert saved == original


def testIncompleteResourcesRemainSavableAndOptionalPresenceRetained():
    form = SchemaParamForm()
    form.setSchema({"properties": {
        "modelPath": {"type": "string", "minLength": 1},
        "case": {"type": "string", "xOptionalPresence": True},
    }}, {"modelPath": ""})
    assert form.validationMessage() == ""
    assert form.getValues() == {"modelPath": ""}


def testArrayItemConstraintNamesIndex():
    form = makeForm({"type": "array", "items": {"type": "integer", "minimum": 0}}, [1, -2])
    assert "测试值 (value)[1]" in form.validationMessage()
    assert form.getValues()["value"] == [1, -2]


def testDisabledInvalidNumberReturnsMessageWithoutThrowingOrOverwriting():
    form = SchemaParamForm()
    form.setSchema({"properties": {"mode": {"type": "string", "enum": ["on", "off"]},
        "value": {"type": "number", "xEnabledWhen": {"mode": ["on"]}}}}, {"mode": "on", "value": 2})
    form._controls["value"].setText("1e")
    form._controls["mode"].setCurrentIndex(1)
    assert not form._controls["value"].isEnabled()
    assert form.validationMessage()
    assert form._controls["value"].text() == "1e"
    form._controls["mode"].setCurrentIndex(0)
    form._controls["value"].setText("1e-9")
    assert form.validationMessage() == ""


def testCameraIncompleteNumberDoesNotCrashModeChanges():
    from emo_master.plugins.builtins.huaray_camera.operator import PARAM_SCHEMA, DEFAULTS
    from emo_master.plugins.builtins.huaray_camera.parameter_form import CameraParameterForm
    form = CameraParameterForm()
    form.setSchema(PARAM_SCHEMA, dict(DEFAULTS, exposureMode="manual"))
    form._controls["exposureUs"].setText("1e")
    selector = form._controls["exposureMode"]
    selector.setCurrentIndex(selector.findData("auto"))
    assert form._controls["exposureUs"].text() == "1e"
    assert form.validationMessage()


def testModelApplySaveAndReopenPreservesExactNumberAndRejectsJson(tmp_path, monkeypatch, ownedDesignerWindow):
    from tests.designer.test_main_window_project_save_load import RuntimeClientStub
    window = ownedDesignerWindow(RuntimeClientStub(), settingsStore=SimpleNamespace(value=lambda _key, default=None: default, setValue=lambda *_args: None))
    schema = {"type": "object", "properties": {"value": {"type": "number"}, "raw": {"type": "array"}}}
    window.addNodeFromOperatorPayload({"operatorId": "test.precision", "displayName": "参数测试", "inputPorts": {}, "outputPorts": {}, "paramSchema": schema})
    node = window.flowModel.selectedNodeId
    window.flowModel.setNodeParams(node, {"value": 1.2345, "raw": [1, 2]})
    monkeypatch.setattr(window, "_requireOperatorCatalog", lambda: True)
    window.openNodeParamDialog(node)
    editor = window.nodeParamDialog
    form = editor._schemaForm
    form._controls["raw"].setPlainText("[2,")
    assert not editor.applyChanges()
    assert window.flowModel.nodes[node].params == {"value": 1.2345, "raw": [1, 2]}
    form._controls["raw"].setPlainText("[2, 3]")
    form._controls["value"].setText("0.12345678901234566")
    assert editor.applyChanges()
    directory = tmp_path / "roundtrip"
    assert window.saveProjectToDirectory(str(directory))
    assert window.loadProjectDirectory(str(directory))
    assert window.flowModel.nodes[node].params == {"value": 0.12345678901234566, "raw": [2, 3]}


@pytest.mark.parametrize("space,lower,upper,valid", [
    ("BGR", [0, 0], [255, 255], False),
    ("BGR", [0, 0, 0], [255, 255, 255], True),
    ("HSV", [0, 0, 0], [180, 255, 255], False),
    ("HSV", [0, 0, 0], [179, 255, 255], True),
    ("GRAY", [255], [254], False),
    ("GRAY", [0], [255], True),
])
def testChannelBoundsAreVisibleAndValidated(space, lower, upper, valid):
    from emo_master.plugins.builtins.in_range.operator import InRangeOperator
    form = SchemaParamForm()
    form.setSchema(InRangeOperator.meta.paramSchema, {"colorSpace": space, "lower": lower, "upper": upper})
    assert bool(form.validationMessage()) is not valid
    assert form.getValues()["lower"] == lower
    assert form.getValues()["upper"] == upper


def testTranslatedEnumKeepsStoredValue():
    from emo_master.plugins.builtins.threshold.operator import ThresholdOperator
    form = SchemaParamForm()
    form.setSchema(ThresholdOperator.meta.paramSchema, {"mode": "adaptiveMean"})
    combo = form._controls["mode"]
    assert combo.currentText() != "adaptiveMean"
    assert combo.currentData() == "adaptiveMean"
    assert form.getValues()["mode"] == "adaptiveMean"
    assert "像素" in form._controls["blockSize"].toolTip()


def testRequiredArrayCannotOptIntoBlankShortcut():
    form = SchemaParamForm()
    form.setSchema({"type": "object", "required": ["classes"], "properties": {"classes": {"type": "array", "xBlankMeansEmpty": True}}}, {})
    form._controls["classes"].setPlainText(" ")
    assert form.validationMessage()


@pytest.mark.parametrize("kind,value", [("number", "1e"), ("array", "[1,"), ("object", "{oops}")])
def testParseErrorsIncludeReadableTitleAndStableKey(kind, value):
    form = makeForm({"type": kind})
    control = form._controls["value"]
    if kind == "number":
        control.setText(value)
    else:
        control.setPlainText(value)
    assert "测试值 (value)" in form.validationMessage()
    with pytest.raises(ValueError, match=r"测试值 \(value\)"):
        form.getValues()
