"""74abb field regressions: bad JSON is a draft, never an empty replacement."""
from copy import deepcopy
import json

import pytest
import emo_master  # noqa: F401 - bootstrap native Qt dependencies
from PySide2.QtCore import Qt
from PySide2.QtTest import QTest

from emo_master.apps.designer.operator_editors import EditorContext, EditorKey
from emo_master.apps.designer.operator_editors.workspace_window import OperatorWorkspaceWindow
from emo_master.apps.designer.ui.param_form import SchemaParamForm


@pytest.mark.parametrize("kind,old,text", [("array", [60, 40, 20], "[1,"),
    ("object", {"x": 1}, '{"x":'), ("array", [1], ""), ("array", [1], "{}"),
    ("object", {"x": 1}, "[]"), ("array", [1], "[NaN]")])
def testInvalidJsonApplyKeepsTextModelBaselineAndSavedFile(designerApplication, tmp_path, kind, old, text):
    path = tmp_path / "params.json"
    params = {"bounds": old}
    path.write_text(json.dumps(params), encoding="utf-8")
    before = deepcopy(params)
    applied = []
    def apply(key, value):
        applied.append(value)
        params.update(value)
        path.write_text(json.dumps(params), encoding="utf-8")
        return True
    key = EditorKey("draft", "main", "range")
    context = EditorContext(key=key, operatorId="test", version="1", previewMode="none",
        paramSchema={}, runtimeClient=object(), applyParams=apply, appendLog=lambda *_: None)
    schema = {"type": "object", "properties": {"bounds": {"type": kind, "title": "边界"}}}
    editor = OperatorWorkspaceWindow(key=key, title="参数完整性", context=context, schema=schema, values=params)
    try:
        control = editor._schemaForm._controls["bounds"]
        control.setPlainText(text)
        assert not editor.applyChanges()
        assert control.toPlainText() == text
        assert "边界" in editor._statusLabel.text()
        assert params == before and editor._loadedParams == before and not applied
        assert json.loads(path.read_text(encoding="utf-8")) == before
        assert editor.isDirty()
        with pytest.raises(ValueError):
            editor.collectParams()
        control.setPlainText(json.dumps(old))
        assert editor.applyChanges() and applied == [before]
    finally:
        editor.forceClose()


@pytest.mark.parametrize("value", [1.2345, 1.2345678901234567, 1e-30, 1e30, -0.000000123456789])
def testNumberRoundTripThroughTypingFocusApplySaveAndReopen(designerApplication, tmp_path, value):
    path = tmp_path / "params.json"
    applied = []
    def apply(_key, values):
        applied.append(deepcopy(values))
        path.write_text(json.dumps(values, allow_nan=False), encoding="utf-8")
        return True
    key = EditorKey("draft", "main", "number")
    context = EditorContext(key=key, operatorId="vision.value.number", version="1", previewMode="none",
        paramSchema={}, runtimeClient=object(), applyParams=apply, appendLog=lambda *_: None)
    schema = {"type": "object", "properties": {"value": {"type": "number"}}}
    editor = OperatorWorkspaceWindow(key=key, title="数值精度", context=context, schema=schema, values={"value": value})
    form = editor._schemaForm
    reopened = None
    try:
        editor.show()
        assert form.getValues()["value"] == value
        control = form._controls["value"]
        control.selectAll()
        QTest.keyClicks(control, str(value))
        QTest.keyClick(control, Qt.Key_Tab)
        assert form.validationMessage() == ""
        assert form.getValues()["value"] == value
        assert editor.applyChanges()
        assert applied == [{"value": value}] and editor._loadedParams == {"value": value}
        reopened = OperatorWorkspaceWindow(key=key, title="重开数值", context=context, schema=schema,
            values=json.loads(path.read_text(encoding="utf-8")))
        assert reopened.collectParams()["value"] == value
        assert not reopened.isDirty()
    finally:
        if reopened is not None:
            reopened.forceClose()
        editor.forceClose()


@pytest.mark.parametrize("text", ["", "-", "1e", "NaN", "Infinity", "1e999", "true"])
def testIncompleteAndNonfiniteNumbersRemainVisibleAndCannotValidate(designerApplication, text):
    form = SchemaParamForm()
    form.setSchema({"type": "object", "properties": {"value": {"type": "number"}}}, {"value": 1})
    try:
        form._controls["value"].setText(text)
        assert form.validationMessage()
        assert form._controls["value"].text() == text
    finally:
        form.close()


def testExclusiveNumericBoundsAndArrayItemTypesAreChecked(designerApplication):
    form = SchemaParamForm()
    try:
        form.setSchema({"type": "object", "properties": {
            "clipLimit": {"type": "number", "exclusiveMinimum": 0},
            "bounds": {"type": "array", "items": {"type": "integer"}, "minItems": 1}}},
            {"clipLimit": 0, "bounds": [1]})
        assert "必须大于 0" in form.validationMessage()
        form._controls["clipLimit"].setValue(.12345)
        form._controls["bounds"].setPlainText('[true]')
        assert "bounds[0]" in form.validationMessage()
        form._controls["bounds"].setPlainText('[]')
        assert form.validationMessage()
        form._controls["bounds"].setPlainText('[1]')
        assert not form.validationMessage()
    finally:
        form.close()
