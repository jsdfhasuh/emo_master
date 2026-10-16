from copy import deepcopy
from threading import Event
from types import SimpleNamespace

import pytest

from emo_master.apps.designer.operator_editors.controller_protocol import EditorContext, EditorKey
from emo_master.apps.designer.operator_editors.workspace_window import OperatorWorkspaceWindow
from emo_master.apps.designer.ui.global_variables_dialog import GlobalVariablesDialog
from emo_master.core.project.global_variables import VariableError
from tests.designer.test_global_counters_dialog import _waitUntil
from tests.runtime.test_global_variables import variable

from PySide2.QtCore import QTimer
from PySide2.QtWidgets import QCheckBox, QWidget


class SlowClient:
    def __init__(self, *, conflict=False, refreshFailure=False):
        self.definition = variable(True, "boolean", name="runEnabled")
        self.started, self.release = Event(), Event()
        self.block = False
        self.calls = []
        self.conflict, self.refreshFailure = conflict, refreshFailure
        self.value = True

    def globalVariableState(self, project, job):
        if self.block:
            self.started.set()
            assert self.release.wait(3)
        if self.calls and self.refreshFailure:
            raise RuntimeError("refresh offline")
        return {"variables": [{"variableId": "v", **self.definition, "state": "current",
                               "value": self.value, "revision": 1, "updatedAtMs": 1}], "jobs": []}

    def setGlobalVariable(self, project, key, value, revision, job):
        self.calls.append((project, key, value, revision, job))
        if self.conflict:
            raise VariableError("E_VARIABLE_CONFLICT", "value changed; refresh")
        self.value = value


def _dialog(app, client):
    dialog = GlobalVariablesDialog(client, lambda: {"v": client.definition}, lambda values: None, lambda: False, lambda: "")
    dialog.showForProject("project")
    _waitUntil(app, lambda: dialog._worker is None)
    dialog.timer.stop()
    dialog.table.selectRow(0)
    return dialog


@pytest.mark.parametrize("conflict,refreshFailure", [(False, False), (True, False), (False, True)])
def testConfirmValueDuringRefreshQueuesExactlyOneVersionedWrite(designerApplication, conflict, refreshFailure):
    app = designerApplication
    client = SlowClient(conflict=conflict, refreshFailure=refreshFailure)
    dialog = _dialog(app, client)
    client.block = True

    def confirmDuringRefresh():
        dialog.refreshVariables()
        # A real worker is still reading when the real modal value editor closes.
        assert client.started.wait(2)
        modal = app.activeModalWidget()
        modal.findChild(QCheckBox).setChecked(False)
        modal.accept()

    try:
        QTimer.singleShot(0, confirmDuringRefresh)
        dialog.setValue()
        assert len(dialog._pendingOperations) == 1
        assert client.calls == []
        client.release.set()
        _waitUntil(app, lambda: dialog._worker is None and not dialog._pendingOperations)
        assert client.calls == [("project", "v", False, 1, "")]
        if conflict:
            assert client.value is True
            assert "设值失败" in dialog.status.text()
        elif refreshFailure:
            assert client.value is False
            assert "设值已完成，但刷新失败" in dialog.status.text()
        else:
            assert client.value is False
            assert "设值成功" in dialog.status.text()
    finally:
        client.release.set()
        _waitUntil(app, lambda: dialog._worker is None)
        dialog.close()


@pytest.mark.parametrize("switch", ["project", "job", "close"])
def testQueuedWritesDoNotCrossContextOrClosing(designerApplication, switch):
    client = SlowClient()
    dialog = _dialog(designerApplication, client)
    client.block = True
    try:
        dialog.refreshVariables()
        assert client.started.wait(2)
        dialog._request(lambda p, j: client.setGlobalVariable(p, "v", False, 1, j))
        assert dialog._pendingOperations
        if switch == "project":
            dialog.bindProject("other")
        elif switch == "job":
            dialog.jobs.addItem("other job", "other-job")
            dialog.jobs.setCurrentIndex(1)
        else:
            dialog.close()
        assert not dialog._pendingOperations
        client.release.set()
        _waitUntil(designerApplication, lambda: dialog._worker is None)
        assert client.calls == []
    finally:
        client.release.set()
        _waitUntil(designerApplication, lambda: dialog._worker is None)
        dialog.close()


def testValueModalCannotSubmitIntoChangedProject(designerApplication):
    client = SlowClient()
    dialog = _dialog(designerApplication, client)
    def switchAndConfirm():
        modal = designerApplication.activeModalWidget()
        modal.findChild(QCheckBox).setChecked(False)
        dialog.bindProject("other")
        modal.accept()
    try:
        QTimer.singleShot(0, switchAndConfirm)
        dialog.setValue()
        assert client.calls == []
        assert "设值未发送" in dialog.status.text()
    finally:
        dialog.close()


def testReadFailureAfterSuccessfulWriteRemainsVisible(designerApplication):
    client = SlowClient()
    dialog = _dialog(designerApplication, client)
    try:
        dialog._request(lambda p, j: client.setGlobalVariable(p, "v", False, 1, j))
        _waitUntil(designerApplication, lambda: dialog._worker is None)
        assert "设值成功" in dialog.status.text()
        client.refreshFailure = True
        dialog.refreshVariables()
        _waitUntil(designerApplication, lambda: dialog._worker is None)
        assert "刷新失败" in dialog.status.text()
        assert "refresh offline" in dialog.status.text()
        assert not dialog.buttons["set"].isEnabled()
    finally:
        dialog.close()


def testQueuedWriteRejectsTaskRemovedByRefresh(designerApplication):
    client = SlowClient()
    dialog = _dialog(designerApplication, client)
    client.block = True
    try:
        dialog.jobs.blockSignals(True)
        dialog.jobs.addItem("job", "job")
        dialog.jobs.setCurrentIndex(1)
        dialog.jobs.blockSignals(False)
        dialog.refreshVariables()
        assert client.started.wait(2)
        dialog._request(lambda p, j: client.setGlobalVariable(p, "v", False, 1, j))
        client.release.set()
        _waitUntil(designerApplication, lambda: dialog._worker is None and not dialog._pendingOperations)
        # The refresh no longer lists that task. Do not retarget the write to
        # the newly selected empty task, even though signals were blocked.
        assert client.calls == []
        assert "设值未发送" in dialog.status.text()
    finally:
        client.release.set()
        _waitUntil(designerApplication, lambda: dialog._worker is None)
        dialog.close()


def _editor(variables, *, custom=False, schema=None, bindings=None):
    schema = schema or {"type": "object", "properties": {"value": {"type": "number"}}}
    context = EditorContext(key=EditorKey("p", "w", "n"), operatorId="test", version="1", previewMode="none",
        paramSchema=schema, runtimeClient=None, applyParams=lambda *args: True, appendLog=lambda *args: None,
        variableDefinitions=lambda: variables, variableBindings=bindings,
        applyConfiguration=lambda *args: True)
    controller = SimpleNamespace(bind=lambda *args: None, loadParams=lambda p: None,
        collectParams=lambda: {"value": 99}, validate=lambda: None, onClose=lambda: None, dispose=lambda: None)
    return OperatorWorkspaceWindow(key=context.key, title="test", context=context, schema=schema, values={"value": 99},
        customRoot=QWidget() if custom else None, controller=controller if custom else None)


@pytest.mark.parametrize("custom", [False, True])
def testOpenEditorRefreshPreservesDraftAndUpdatesNamesAndInvalidReferences(designerApplication, custom):
    variables = {}
    window = _editor(variables, custom=custom)
    try:
        schema = window.context.paramSchema
        variables["v"] = variable(.5, "number", name="threshold")
        window.refreshSchema(schema)
        assert not window.isDirty()
        combo = window._bindingPanel.fields[("value",)]
        combo.setCurrentIndex(combo.findData("v"))
        selected = window._bindingPanel.rawBindings()
        variables["v"]["name"] = "renamedThreshold"
        window.refreshSchema(schema)
        assert window._bindingPanel.bindings() == selected
        assert window.collectParams() == {"value": 99}
        assert window.isDirty()
        assert "renamedThreshold" in window._bindingPanel.fields[("value",)].currentText()
        if not custom:
            proxy = window._schemaForm._variableSources["value"]
            assert proxy.currentData() == "v"
            assert "renamedThreshold" in proxy.toolTip()
            assert not window._schemaForm._controls["value"].isEnabled()
        assert window.applyChanges()
        variables["v"]["name"] = "undoRename"
        window.refreshSchema(schema)
        assert not window.isDirty()
        del variables["v"]
        window.refreshSchema(schema)
        assert window._bindingPanel.rawBindings() == selected
        assert "失效" in window._bindingPanel.fields[("value",)].currentText()
        assert not window.applyChanges()
        combo = window._bindingPanel.fields[("value",)]
        combo.setCurrentIndex(combo.findData(""))
        assert window.applyChanges()
    finally:
        window.forceClose()


def testRemovedSchemaFieldRetainsUnappliedBinding(designerApplication):
    variables = {"v": variable(.5, "number")}
    window = _editor(variables)
    try:
        combo = window._bindingPanel.fields[("value",)]
        combo.setCurrentIndex(combo.findData("v"))
        window.refreshSchema({"type": "object", "properties": {"other": {"type": "number"}}})
        assert "失效参数" in window._bindingPanel.fields[("value",)].currentText()
        assert not window._bindingScroll.isHidden()
        assert not window.applyChanges()
    finally:
        window.forceClose()


def testFileFieldsOfferNoVariablesAndLegacyBindingCanBeRemoved(designerApplication):
    variables = {"v": variable("input.png", "string")}
    schema = {"type": "object", "properties": {"value": {"type": "string", "xWidget": "file", "xFileMode": "open"}}}
    window = _editor(variables, schema=schema)
    try:
        assert ("value",) not in window._bindingPanel.fields
    finally:
        window.forceClose()
    window = _editor(variables, schema=deepcopy(schema), bindings=[{"parameterPath": ["value"], "variableId": "v"}])
    try:
        assert "失效参数" in window._bindingPanel.fields[("value",)].currentText()
        assert not window.applyChanges()
        combo = window._bindingPanel.fields[("value",)]
        combo.setCurrentIndex(combo.findData(""))
        assert window._bindingPanel.bindings() == []
    finally:
        window.forceClose()


def testMainWindowDefinitionEditsAndHistoryRefreshOpenEditors(designerApplication):
    from emo_master.apps.designer.ui.main_window import MainWindow
    main = MainWindow(SimpleNamespace(listOperators=lambda: []))
    schema = {"type": "object", "properties": {"value": {"type": "number"}}}
    nodeId = main.flowModel.addNode("test", "Test", {}, {}, schema)
    main.workflowController.captureActiveWorkflow()
    window = main.operatorEditorManager.open(projectId=main._currentProjectId(), workflowId=main.activeWorkflowId,
        nodeId=nodeId, operatorId="test", displayName="Test", schema=schema, values={"value": 99},
        variableDefinitions=main._globalVariableDefinitions, parent=main)
    try:
        main._editGlobalVariableDefinitions({"v": variable(.5, "number", name="first")})
        combo = window._bindingPanel.fields[("value",)]
        assert combo.findData("v") >= 0
        combo.setCurrentIndex(combo.findData("v"))
        main._editGlobalVariableDefinitions({"v": variable(.5, "number", name="second")})
        assert "second" in window._bindingPanel.fields[("value",)].currentText()
        main.pageCoordinator.history()
        _waitUntil(designerApplication, lambda: "first" in window._bindingPanel.fields[("value",)].currentText())
        main.pageCoordinator.history(redo=True)
        _waitUntil(designerApplication, lambda: "second" in window._bindingPanel.fields[("value",)].currentText())
        assert window.collectParams() == {"value": 99}
        assert window.isDirty()  # The user's un-applied binding was not reset by history.
    finally:
        window.forceClose()
