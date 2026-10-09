from __future__ import annotations

import json
from pathlib import Path

import pytest

from emo_master.apps.designer.ui.param_form import SchemaParamForm
from emo_master.plugins.builtins.yolo_inference import operator as yolo_module
from emo_master.plugins.builtins.yolo_inference.operator import YoloInferenceOperator


@pytest.mark.parametrize("schemaSource", ["operator", "manifest"])
@pytest.mark.parametrize(
    ("text", "expected"),
    [("", []), (" \n\t", []), ("[]", []), ("[0]", [0]), ("[0, 1]", [0, 1])],
    ids=["blank", "whitespace", "empty-array", "class-zero", "multiple-classes"],
)
def testYoloFormAllowsUnrestrictedOrExplicitClasses(schemaSource, text, expected):
    schema = YoloInferenceOperator.meta.paramSchema
    manifestPath = Path(yolo_module.__file__).with_name("manifest.json")
    manifestSchema = json.loads(manifestPath.read_text(encoding="utf-8"))["paramSchema"]
    assert manifestSchema == schema
    form = SchemaParamForm()
    try:
        form.setSchema(
            schema if schemaSource == "operator" else manifestSchema,
            {"modelPath": "model.onnx", "classes": [2]},
        )
        control = form._controls["classes"]
        control.setPlainText(text)
        values = form.getValues()
        assert values["classes"] == expected
        assert YoloInferenceOperator().validateParams(values) is None
        label = form._layout.labelForField(control)
        assert label.text() == schema["properties"]["classes"]["title"]
        assert "*" not in label.text()
        assert schema["properties"]["classes"]["description"] in control.toolTip()
    finally:
        form.close()
