from copy import deepcopy

import pytest

from emo_master.apps.designer.operator_editors.controller_protocol import EditorContext, EditorKey
from emo_master.apps.designer.operator_editors.workspace_window import OperatorWorkspaceWindow
from emo_master.apps.designer.state.schema_utils import mergeParameterTitles
from emo_master.plugins.builtins.threshold.operator import ThresholdOperator
from tests.runtime.test_global_variables import variable


def workspace(bindings=None):
    schema = deepcopy(ThresholdOperator.meta.paramSchema)
    # Saved schema before the new display annotations.
    old = deepcopy(schema)
    for field in old["properties"].values():
        for key in list(field):
            if key.startswith("x"):
                field.pop(key)
    schema = mergeParameterTitles(old, schema)
    applied = []
    definitions = {"m": variable("adaptiveMean", "string", name="mode"),
                   "i": variable(3, "integer", name="size"),
                   "b": variable(False, "boolean", name="invert")}
    context = EditorContext(key=EditorKey("p", "w", "n"), operatorId="vision.image.threshold", version="1", previewMode="none",
        paramSchema=schema, runtimeClient=None, applyParams=lambda *_args: True, appendLog=lambda *_args: None,
        variableDefinitions=lambda: definitions, variableBindings=bindings or [],
        applyConfiguration=lambda *args: applied.append(args) or True)
    window = OperatorWorkspaceWindow(key=context.key, title="Threshold", context=context, schema=schema,
        values={"mode": "fixed", "threshold": 127, "blockSize": 4, "constant": 2, "invert": False})
    return window, window._schemaForm, applied


def choose(combo, value):
    index = combo.findData(value)
    assert index >= 0
    combo.setCurrentIndex(index)


def testOldThresholdInlineSourcesRespectModeAndPreserveLiteral():
    window, form, applied = workspace()
    assert not form._controls["blockSize"].isEnabled()
    assert not form._variableSources["blockSize"].isEnabled()
    assert window.applyChanges()
    assert applied[-1][1]["blockSize"] == 4
    choose(form._controls["mode"], "adaptiveMean")
    assert form._controls["blockSize"].isEnabled()
    assert not window.applyChanges()
    assert "奇数" in window._statusLabel.text()
    assert form.getValues()["blockSize"] == 4
    form._controls["blockSize"].setValue(5)
    assert window.applyChanges()
    window.forceClose()


def testSourceSwitchCannotReenableInactiveField():
    window, form, _ = workspace()
    choose(form._controls["mode"], "adaptiveMean")
    choose(form._variableSources["blockSize"], "i")
    assert not form._controls["blockSize"].isEnabled()
    choose(form._controls["mode"], "fixed")
    choose(form._variableSources["blockSize"], "")
    assert not form._controls["blockSize"].isEnabled()
    window.forceClose()


def testUnrelatedBindingDoesNotBypassActiveFixedValueValidation():
    window, form, applied = workspace([{"parameterPath": ["invert"], "variableId": "b"}])
    choose(form._controls["mode"], "adaptiveMean")
    assert not window.applyChanges()
    assert not applied
    form._controls["blockSize"].setValue(3)
    assert window.applyChanges()
    assert applied[-1][2] == [{"parameterPath": ["invert"], "variableId": "b"}]
    window.forceClose()


def testDynamicModeDoesNotGuessUnusedLiteralMode():
    window, form, applied = workspace([{"parameterPath": ["mode"], "variableId": "m"}])
    assert form._controls["blockSize"].isEnabled()
    assert form._controls["threshold"].isEnabled()
    form._controls["threshold"].setValue(999)
    assert window.applyChanges()  # Effective mode and bounds must be validated at execution.
    assert applied[-1][1]["threshold"] == 999
    assert applied[-1][1]["blockSize"] == 4
    assert applied[-1][2] == [{"parameterPath": ["mode"], "variableId": "m"}]
    choose(form._variableSources["mode"], "")
    assert not form._controls["blockSize"].isEnabled()
    assert not window.applyChanges()  # fixed threshold999 remains invalid.
    window.forceClose()


@pytest.mark.parametrize("text", ["NaN", "1e999", "oops"])
def testDynamicModeStillRejectsInvalidOrNonfiniteNumber(text):
    window, form, applied = workspace([{"parameterPath": ["mode"], "variableId": "m"}])
    form._controls["constant"].setText(text)
    assert not window.applyChanges()
    assert not applied
    assert form._controls["constant"].text() == text
    window.forceClose()


def testModeChangeClearsOnlyNewlyInapplicableFieldError():
    window, form, applied = workspace()
    choose(form._controls["mode"], "adaptiveMean")
    assert not window.applyChanges()
    control = form._controls["blockSize"]
    assert "奇数" in control.accessibleDescription()
    assert "blockSize" in control.accessibleDescription()
    assert control.styleSheet()
    choose(form._controls["mode"], "adaptiveGaussian")
    assert control.accessibleDescription()  # Still applicable, still invalid.
    choose(form._controls["mode"], "fixed")
    assert not control.isEnabled()
    assert not control.accessibleDescription()
    assert not control.styleSheet()
    assert form.getValues()["blockSize"] == 4
    assert not applied
    assert window.applyChanges()
    assert applied[-1][1]["blockSize"] == 4
    window.forceClose()
