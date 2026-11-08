from copy import deepcopy

import pytest
from PySide2.QtCore import QCoreApplication, QEvent, Qt
from PySide2.QtTest import QTest
from PySide2.QtWidgets import QScrollArea, QWidget
from shiboken2 import isValid

from emo_master.apps.designer.operator_editors import EditorContext, EditorKey
from emo_master.apps.designer.operator_editors.workspace_window import OperatorWorkspaceWindow
from emo_master.apps.designer.services.runtime_client import RuntimeClient
from emo_master.apps.designer.ui.operator_debug_window import OperatorDebugWindow
from tests.designer.test_operator_debug_window import editorFor, wait
from tests.runtime.operator_debug_fixture import project
from tests.runtime.test_operator_debug_rpc import runtime as runtime


def retireEditor(editor):
    editor.forceClose()
    def retired():
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        return not isValid(editor)
    wait(retired)


def testDebugSharesOriginalDraftAndRestoresItAfterRepeatedClose(runtime):
    editor, payload, before, calls = editorFor(runtime)
    form = editor._schemaForm
    source = form.parentWidget().parentWidget()
    assert isinstance(source, QScrollArea) and source.widget() is form
    assert not editor.isDirty()
    try:
        for value in (7, 11):
            dialog = OperatorDebugWindow(editor)
            editor._debugWindow = dialog
            dialog.show()
            wait(lambda: dialog.run.isEnabled())
            assert dialog.parameters.form is form
            assert source.widget() is dialog.parameters.placeholder
            assert form.isVisibleTo(dialog)
            QTest.mouseClick(dialog.parameters.toggle, Qt.LeftButton)
            assert not dialog.parameters.body.isVisible()
            assert dialog.parameters.toggle.arrowType() == Qt.RightArrow
            QTest.mouseClick(dialog.parameters.toggle, Qt.LeftButton)
            assert dialog.parameters.body.isVisible()
            assert dialog.parameters.toggle.arrowType() == Qt.DownArrow
            if value == 7:
                assert not editor.isDirty()
            form._controls["value"].setValue(value)
            assert editor.collectParams()["value"] == value and editor.isDirty()
            QTest.mouseClick(dialog.run, Qt.LeftButton)
            wait(lambda: len(dialog.records) == 1 and dialog.run.isEnabled())
            assert dialog.records[0]["rawParams"]["value"] == value
            assert dialog.records[0]["outputs"] == {"value": value}
            dialog.close()
            wait(lambda: not isValid(dialog))
            assert isValid(form) and source.widget() is form and form.isVisibleTo(editor)
            assert editor.collectParams()["value"] == value
            assert payload == before and calls == []
    finally:
        retireEditor(editor)


@pytest.mark.parametrize("width", [620, 900])
def testYoloFoldShowsModelAndAllOriginalControlsWithoutChangingParameters(runtime, tmp_path, record_property, width):
    operator = "vision.inference.yolo"
    schema = runtime.pluginScanResult.activeOperators[operator].manifest.paramSchema
    payload = project(operator)
    context = EditorContext(key=EditorKey("draft", "main", "node"), operatorId=operator,
        version="1", previewMode="none", paramSchema=schema, runtimeClient=RuntimeClient(runtime),
        applyParams=lambda *_: pytest.fail("debug must not apply parameters"), appendLog=lambda *_: None,
        getPreviewProject=lambda _: payload, variableDefinitions=lambda: {})
    editor = OperatorWorkspaceWindow(key=context.key, title="YOLO", context=context, schema=schema,
        values={"modelPath": "models/2026_10_19_ln_1_0.75.onnx", "confidence": .35, "drawOverlay": False,
                "outputCrop": True, "cropTargetIndex": 1})
    editor.show()
    original = deepcopy(editor.collectParams())
    form = editor._schemaForm
    controls = dict(form._controls)
    dialog = OperatorDebugWindow(editor)
    editor._debugWindow = dialog
    dialog.resize(width, 720)
    dialog.show()
    try:
        wait(lambda: dialog.run.isEnabled())
        assert dialog.parameters.toggle.isChecked()
        assert dialog.parameters.form is form and form._controls == controls
        assert set(schema["properties"]) == set(form._controls)
        assert editor.collectParams() == original and not editor.isDirty()
        assert "modelPath" in controls and "confidence" in controls and "drawOverlay" in controls
        assert form.isVisibleTo(dialog)
        assert dialog.inputForm.rowCount() == 2
        capture = tmp_path / f"debug-yolo-parameters-{width}.png"
        assert dialog.grab().save(str(capture))
        record_property("screenshot", str(capture))
        scroll = dialog.tabs.widget(0)
        assert isinstance(scroll, QScrollArea)
        scroll.ensureWidgetVisible(controls["modelPath"])
        assert not controls["modelPath"].visibleRegion().isEmpty()
        QTest.mouseClick(dialog.parameters.toggle, Qt.LeftButton)
        assert not dialog.parameters.body.isVisible()
        assert editor.collectParams() == original and not editor.isDirty()
        dialog.close()
        wait(lambda: not isValid(dialog))
        assert isValid(form) and form.isVisibleTo(editor)
        assert editor.collectParams() == original and not editor.isDirty()
    finally:
        retireEditor(editor)


def testSchemaRefreshStaysSharedAndParentCloseReleasesDebug(runtime):
    editor, *_ = editorFor(runtime)
    form = editor._schemaForm
    dialog = OperatorDebugWindow(editor)
    editor._debugWindow = dialog
    dialog.show()
    try:
        wait(lambda: dialog.run.isEnabled())
        form.setSchema({"type": "object", "properties": {"value": {"type": "number"}}}, {"value": 19})
        assert dialog.parameters.form is form
        QTest.mouseClick(dialog.run, Qt.LeftButton)
        wait(lambda: len(dialog.records) == 1 and dialog.run.isEnabled())
        assert dialog.records[0]["outputs"]["value"] == 19
        editor.forceClose()
        wait(lambda: not isValid(editor) and not isValid(dialog))
        assert not runtime.operatorDebugManager.ownsResources()
    finally:
        if isValid(editor):
            retireEditor(editor)


class CustomController:
    def bind(self, root, context):
        self.context = context

    def loadParams(self, params):
        self.params = dict(params)

    def collectParams(self):
        return dict(self.params)

    def onClose(self):
        pass

    def dispose(self):
        pass


def testCustomEditorSnapshotReflectsActualDraftWithoutReplacingItsController(runtime):
    operator = "vision.value.number"
    schema = {"type": "object", "properties": {"value": {"type": "number", "title": "数值"}}}
    payload = project(operator)
    controller = CustomController()
    context = EditorContext(key=EditorKey("draft", "main", "node"), operatorId=operator,
        version="1", previewMode="none", paramSchema=schema, runtimeClient=RuntimeClient(runtime),
        applyParams=lambda *_: pytest.fail("debug must not apply"), appendLog=lambda *_: None,
        getPreviewProject=lambda _: payload)
    editor = OperatorWorkspaceWindow(key=context.key, title="Custom", context=context, schema=schema,
        values={"value": 3}, customRoot=QWidget(), controller=controller)
    editor.show()
    dialog = OperatorDebugWindow(editor)
    editor._debugWindow = dialog
    dialog.show()
    try:
        wait(lambda: dialog.run.isEnabled())
        assert dialog.parameters.form is None
        assert dialog.parameters.snapshot.topLevelItem(0).child(0).text(0) == "数值 (value)"
        assert not editor.isDirty()
        controller.params = None
        dialog.parameters.refresh()
        assert dialog.parameters.error.text() and dialog.parameters.snapshot.topLevelItemCount() == 0
        controller.loadParams({"value": 23})
        wait(lambda: dialog.parameters.lastValues == {"value": 23})
        assert not dialog.parameters.error.text()
        QTest.mouseClick(dialog.run, Qt.LeftButton)
        wait(lambda: len(dialog.records) == 1 and dialog.run.isEnabled())
        assert dialog.records[0]["outputs"] == {"value": 23}
        assert editor._controller is controller
    finally:
        dialog.close()
        wait(lambda: not isValid(dialog))
        retireEditor(editor)


def testBoundValueRetainsSourceSelectorAndUsesSessionVariable(runtime):
    from tests.runtime.test_global_variables import variable
    payload = project("vision.value.number")
    variables = {"v": variable(31, "number")}
    payload["globalVariables"] = variables
    bindings = [{"parameterPath": ["value"], "variableId": "v"}]
    schema = {"type": "object", "properties": {"value": {"type": "number"}}}
    context = EditorContext(key=EditorKey("draft", "main", "node"), operatorId="vision.value.number",
        version="1", previewMode="none", paramSchema=schema, runtimeClient=RuntimeClient(runtime),
        applyParams=lambda *_: pytest.fail("debug must not apply"), appendLog=lambda *_: None,
        getPreviewProject=lambda _: payload, variableDefinitions=lambda: variables, variableBindings=bindings)
    editor = OperatorWorkspaceWindow(key=context.key, title="Bound", context=context, schema=schema, values={"value": 99})
    editor.show()
    form = editor._schemaForm
    source = form._variableSources["value"]
    dialog = OperatorDebugWindow(editor)
    editor._debugWindow = dialog
    dialog.show()
    try:
        wait(lambda: dialog.run.isEnabled())
        assert dialog.parameters.form._variableSources["value"] is source
        assert source.currentData() == "v" and source.isVisibleTo(dialog)
        assert not form._controls["value"].isEnabled()
        QTest.mouseClick(dialog.run, Qt.LeftButton)
        wait(lambda: len(dialog.records) == 1 and dialog.run.isEnabled())
        assert dialog.records[0]["rawParams"]["value"] == 99
        assert dialog.records[0]["outputs"]["value"] == 31
        assert editor.collectParams()["value"] == 99 and not editor.isDirty()
    finally:
        dialog.close()
        wait(lambda: not isValid(dialog))
        retireEditor(editor)
