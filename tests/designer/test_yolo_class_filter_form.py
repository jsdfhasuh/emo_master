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
    [("", []), (" \n\t", []), ("[]", []), ("[0]", [0]), ("[0, 1]", [0, 1]),
     ("0, 1, 3", [0, 1, 3]), ("0，1，3", [0, 1, 3])],
    ids=["blank", "whitespace", "empty-array", "class-zero", "multiple-classes", "ids", "chinese-comma"],
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
        assert control.text() == "2"
        assert control.placeholderText() == "全部类别"
        control.setText(text)
        values = form.getValues()
        assert values["classes"] == expected
        assert YoloInferenceOperator().validateParams(values) is None
        label = form._layout.labelForField(control)
        assert label.text() == schema["properties"]["classes"]["title"]
        assert "*" not in label.text()
        assert schema["properties"]["classes"]["description"] in control.toolTip()
    finally:
        form.close()


@pytest.mark.parametrize("text", ["[1,", "0,,1", "part", "[true]", "-1", "1.5"])
def testOptionalClassFilterDoesNotTreatMalformedInputAsUnrestricted(text):
    form = SchemaParamForm()
    try:
        form.setSchema(YoloInferenceOperator.meta.paramSchema, {"modelPath": "model.onnx", "classes": [2]})
        form._controls["classes"].setText(text)
        assert form.validationMessage()
        with pytest.raises(ValueError):
            form.getValues()
        assert form._controls["classes"].text() == text
    finally:
        form.close()


@pytest.mark.parametrize("schemaSource", ["operator", "manifest"])
@pytest.mark.parametrize("params,expected", [({}, True), ({"drawOverlay": False}, False)])
def testYoloOverlayDefaultsOnWithoutOverwritingExplicitFalse(schemaSource, params, expected):
    schema = YoloInferenceOperator.meta.paramSchema
    if schemaSource == "manifest":
        schema = json.loads(Path(yolo_module.__file__).with_name("manifest.json").read_text(encoding="utf-8"))["paramSchema"]
    form = SchemaParamForm()
    try:
        form.setSchema(schema, {"modelPath": "model.onnx", **params})
        assert form._controls["drawOverlay"].isChecked() is expected
        assert form.getValues()["drawOverlay"] is expected
    finally:
        form.close()


@pytest.mark.parametrize("schemaSource", ["operator", "manifest"])
def testYoloCropFormEnablesRankOnlyWhenCropIsSelected(schemaSource):
    schema = YoloInferenceOperator.meta.paramSchema
    if schemaSource == "manifest":
        schema = json.loads(Path(yolo_module.__file__).with_name("manifest.json").read_text(encoding="utf-8"))["paramSchema"]
    form = SchemaParamForm()
    try:
        form.setSchema(schema, {"modelPath": "model.onnx"})
        crop, rank = form._controls["outputCrop"], form._controls["cropTargetIndex"]
        assert not crop.isChecked() and not rank.isEnabled()
        crop.setChecked(True)
        assert rank.isEnabled()
        assert rank.value() == 1
        assert "置信度从高到低" in rank.toolTip()
        rank.setValue(0)
        assert "不能小于 1" in form.validationMessage()
        rank.setValue(2)
        values = form.getValues()
        assert values["outputCrop"] is True and values["cropTargetIndex"] == 2
        assert YoloInferenceOperator().validateParams(values) is None
        form.setSchema(schema, values)
        assert form._controls["cropTargetIndex"].value() == 2
        form._controls["cropTargetIndex"].setValue(0)
        assert form.validationMessage()
        form._controls["outputCrop"].setChecked(False)
        assert not form._controls["cropTargetIndex"].isEnabled()
        assert form.validationMessage() == ""
    finally:
        form.close()
