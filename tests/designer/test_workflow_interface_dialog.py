from __future__ import annotations

from copy import deepcopy

import emo_master  # noqa: F401 - preload Windows dependencies before Qt
import pytest
from PySide2.QtCore import QCoreApplication, QEvent, QEventLoop, Qt, QTimer
from PySide2.QtTest import QTest
from PySide2.QtWidgets import QApplication, QDialog, QGraphicsSimpleTextItem

from emo_master.apps.designer.ui import main_window as mainWindowModule
from emo_master.apps.designer.ui.main_window import MainWindow
from emo_master.apps.designer.ui.workflow_interface_dialog import WorkflowInterfaceDialog
from emo_master.apps.runtime.workflow.runner import _missingKeys
from emo_master.core.contracts.port_types import matchesPortSpec


class _RuntimeClientStub:
    def listOperators(self):
        return []

    def loadProject(self, projectPath):
        return type("Reply", (), {"ok": True, "message": "ok"})()


def _selectType(combo, typeName):
    index = combo.findData(typeName)
    if index < 0:
        combo.setEditText(typeName)
    else:
        combo.setCurrentIndex(index)


def _dialog(inputs=None, outputs=None, **kwargs):
    return WorkflowInterfaceDialog("示例工作流", inputs or {}, outputs or {}, **kwargs)


def _editProperties(table, edit):
    """Propagate assertions outside Qt callbacks and always dismiss the modal."""
    calls = []
    errors = []
    timer = QTimer()
    timer.setSingleShot(True)

    def finish():
        dialog = QApplication.activeModalWidget()
        calls.append(True)
        try:
            edit(dialog)
        except Exception as error:
            errors.append(error)
        finally:
            if dialog is not None and dialog.isVisible():
                dialog.reject()

    timer.timeout.connect(finish)
    timer.start(0)
    try:
        table._table.cellWidget(0, 2).click()
    finally:
        timer.stop()
    if errors:
        raise errors[0]
    assert calls == [True]


def testEmptyInterfacesCanBeAcceptedAndHaveAddActions(designerApplication):
    dialog = _dialog()
    assert dialog.getInterfaces() == ({}, {})
    assert dialog._saveButton.isEnabled()
    dialog.show()
    designerApplication.processEvents()
    assert dialog._inputTable._emptyLabel.isVisible()
    QTest.mouseClick(dialog._inputTable._addButton, Qt.LeftButton)
    table = dialog._inputTable
    assert len(table._rows) == 1
    assert not table._emptyLabel.isVisible()
    assert not dialog._saveButton.isEnabled()
    assert "请填写接口名称" in dialog._errorLabel.text()
    name, combo, _ = table._rows[0]
    name.setText("hasNext")
    _selectType(combo, "boolean")
    assert dialog.getInterfaces() == ({"hasNext": "boolean"}, {})
    assert dialog._saveButton.isEnabled()
    assert dialog._tabs.tabText(0) == "输入接口 (1)"
    QTest.mouseClick(table._table.cellWidget(0, 3), Qt.LeftButton)
    assert dialog.getInterfaces() == ({}, {})
    assert table._emptyLabel.isVisible()
    assert dialog._saveButton.isEnabled()


@pytest.mark.parametrize("name, message", [
    ("", "请填写接口名称"),
    ("   ", "请填写接口名称"),
    ("value", "重复"),
    (" value", "首尾不能有空格"),
    ("value ", "首尾不能有空格"),
])
def testInvalidNamesPreventConfirmWithoutLosingRows(designerApplication, name, message):
    dialog = _dialog({"value": "integer"})
    dialog._inputTable.addPort(name)
    assert not dialog._saveButton.isEnabled()
    assert message in dialog._errorLabel.text()
    dialog.accept()
    assert dialog.result() != QDialog.Accepted
    assert len(dialog._inputTable._rows) == 2
    dialog._inputTable._rows[1][0].setText("新接口")
    assert dialog._saveButton.isEnabled()
    assert dialog._errorLabel.text() == ""


def testBothDirectionsMayUseSameNameAndCustomTypes(designerApplication):
    dialog = _dialog({"state": "my_custom_type"}, {"state": "list<integer>"},
                     availableTypes=["my_plugin_result", "integer"])
    assert dialog.getInterfaces() == ({"state": "my_custom_type"}, {"state": "list<integer>"})
    combo = dialog._inputTable._rows[0][1]
    assert combo.findData("my_plugin_result") >= 0
    assert combo.findData("boolean") >= 0
    assert combo.itemText(combo.findData("boolean")) == "布尔 / 是非 (boolean)"
    _selectType(combo, "my_plugin_result")
    assert dialog.getInterfaces()[0] == {"state": "my_plugin_result"}
    _selectType(combo, "list<integer>")
    assert dialog.getInterfaces()[0] == {"state": "list<integer>"}
    combo.setEditText("")
    assert not dialog._saveButton.isEnabled()


def testLegacyAliasesAndStructuredDescriptorsRoundTripWithoutMutation(designerApplication):
    inputs = {
        "hasNext": "bool",
        "detections": {"type": "detectionCollection", "required": False, "nullable": True, "schemaVersion": "1.x"},
    }
    outputs = {"continue": {"type": "boolean", "required": True, "nullable": False}}
    before = deepcopy((inputs, outputs))
    dialog = _dialog(inputs, outputs)
    assert dialog.getInterfaces() == before
    dialog._inputTable._rows[1][0].setText("objects")
    updated = dialog.getInterfaces()[0]
    assert updated["objects"] == inputs["detections"]
    updated["objects"]["required"] = True
    assert (inputs, outputs) == before
    assert dialog.getInterfaces()[0]["objects"]["required"] is False
    _selectType(dialog._outputTable._rows[0][1], "integer")
    assert dialog.getInterfaces()[1] == {
        "continue": {"type": "integer", "required": True, "nullable": False},
    }


def testSemanticVersionIsNotSilentlyDroppedOnInvalidTypeChange(designerApplication):
    spec = {"type": "detectionCollection", "schemaVersion": "1.x"}
    dialog = _dialog({"objects": spec})
    _selectType(dialog._inputTable._rows[0][1], "string")
    assert not dialog._saveButton.isEnabled()
    assert "schemaVersion" in dialog._errorLabel.text()
    _selectType(dialog._inputTable._rows[0][1], "detectionCollection")
    assert dialog._saveButton.isEnabled()
    assert dialog.getInterfaces()[0] == {"objects": spec}


def testDeletingMiddleRowKeepsOrderAndRemoveButtonsCorrect(designerApplication):
    dialog = _dialog({"first": "image", "second": "integer", "third": "boolean"})
    table = dialog._inputTable
    table._table.cellWidget(1, 3).click()
    assert list(dialog.getInterfaces()[0]) == ["first", "third"]
    table._table.cellWidget(1, 3).click()
    assert list(dialog.getInterfaces()[0]) == ["first"]


@pytest.mark.parametrize("kind, expectedTab", [("workflow_input", "inputs"), ("workflow_output", "outputs")])
def testBoundaryDoubleClickOpensCorrectEditorNotOperatorEditor(designerApplication, monkeypatch, kind, expectedTab):
    window = MainWindow(_RuntimeClientStub())
    window.editWorkflowInterface({"hasNext": "boolean"}, {"continue": "boolean"})
    captured = []

    class DialogStub:
        def __init__(self, workflowName, inputs, outputs, **kwargs):
            captured.append((workflowName, deepcopy(inputs), deepcopy(outputs), kwargs["initialTab"]))

        def execInterface(self):
            return None

    monkeypatch.setattr(mainWindowModule, "WorkflowInterfaceDialog", DialogStub)
    node = next(node for node in window.flowModel.nodes.values() if node.kind == kind)
    before = deepcopy(window.flowModel.toProjectGraph())
    window.flowScene.handleNodeDoubleClick(node.nodeId)
    assert captured == [("Main", {"hasNext": "boolean"}, {"continue": "boolean"}, expectedTab)]
    assert window.nodeParamDialog is None
    assert window.activeParamNodeId is None
    assert window.flowModel.toProjectGraph() == before
    assert "双击配置" in window.flowScene._nodeItems[node.nodeId].toolTip()


def testCatalogDescriptorTypesAreAvailableInDropdown(designerApplication, monkeypatch):
    window = MainWindow(_RuntimeClientStub())
    window.operatorCatalog = [{
        "inputPorts": {"image": "image"},
        "outputPorts": {"boxes": {"type": "detectionCollection", "schemaVersion": "1.x"}},
    }]
    captured = []

    class DialogStub:
        def __init__(self, *args, **kwargs):
            captured.append(kwargs["availableTypes"])

        def execInterface(self):
            return None

    monkeypatch.setattr(mainWindowModule, "WorkflowInterfaceDialog", DialogStub)
    window.editWorkflowInterfaceFor("main")
    assert captured == [{"image", "detectionCollection"}]


@pytest.mark.parametrize("kind, expectedIndex", [("workflow_input", 0), ("workflow_output", 1)])
@pytest.mark.parametrize("confirm", [False, True, "unchanged"])
@pytest.mark.parametrize("target", ["title", "body"])
def testRealCanvasDoubleClickAndModalConfirmAreSafe(designerApplication, kind, expectedIndex, confirm, target):
    window = MainWindow(_RuntimeClientStub())
    window.editWorkflowInterface({"hasNext": "boolean"}, {"continue": "boolean"})
    before = deepcopy(window.flowModel.toProjectGraph())
    window.show()
    designerApplication.processEvents()
    window.flowView.setZoomFactor(1.0)
    node = next(node for node in window.flowModel.nodes.values() if node.kind == kind)
    item = window.flowScene._nodeItems[node.nodeId]
    item.setPos(70, 110)
    otherNode = next(node for node in window.flowModel.nodes.values() if node.kind != kind)
    window.flowScene._nodeItems[otherNode.nodeId].setPos(380, 40)
    window.flowView.centerOn(window.flowScene.itemsBoundingRect().center())
    # Persist test placements before the gesture; an ordinary subsequent
    # click should not be blamed for checkpointing unsaved fixture movement.
    window.pageCoordinator.sync()
    window.pageCoordinator.session.markSaved()
    positions = window.flowScene.getNodePositions()
    signature = window.pageCoordinator.session._signature()
    title = next(child for child in item.childItems() if isinstance(child, QGraphicsSimpleTextItem))
    scenePoint = title.sceneBoundingRect().center() if target == "title" else item.mapToScene(item.rect().center())
    point = window.flowView.mapFromScene(scenePoint)
    captured = []
    loop = QEventLoop()
    poll = QTimer()

    def finishDialog():
        dialog = QApplication.activeModalWidget()
        if not isinstance(dialog, WorkflowInterfaceDialog):
            return
        poll.stop()
        captured.append(dialog._tabs.currentIndex())
        # The native second release can arrive at the modal, not the canvas.
        QTest.mouseRelease(dialog, Qt.LeftButton, pos=dialog.rect().center())
        table = dialog._inputTable if expectedIndex == 0 else dialog._outputTable
        if confirm != "unchanged":
            table._rows[0][0].setText("updated")
        (dialog.accept if confirm else dialog.reject)()
        QTimer.singleShot(0, loop.quit)

    poll.timeout.connect(finishDialog)
    poll.start(10)
    # A safety timeout must also dismiss a modal if the event path regresses.
    def timeout():
        modal = QApplication.activeModalWidget()
        if modal is not None:
            modal.reject()
        loop.quit()

    safety = QTimer()
    safety.setSingleShot(True)
    safety.timeout.connect(timeout)
    safety.start(3000)
    QTest.mouseDClick(window.flowView.viewport(), Qt.LeftButton, pos=point)
    loop.exec_()
    safety.stop()
    poll.stop()
    assert captured == [expectedIndex]
    workflow = window.workflowStore.get()
    edited = workflow.inputs if expectedIndex == 0 else workflow.outputs
    other = workflow.outputs if expectedIndex == 0 else workflow.inputs
    expected = {"updated": "boolean"} if confirm is True else ({"hasNext": "boolean"} if expectedIndex == 0 else {"continue": "boolean"})
    assert edited == expected
    assert other == ({"continue": "boolean"} if expectedIndex == 0 else {"hasNext": "boolean"})
    otherItem = window.flowScene._nodeItems[otherNode.nodeId]
    otherPoint = window.flowView.mapFromScene(otherItem.sceneBoundingRect().center())
    QTest.mouseMove(window.flowView.viewport(), otherPoint)
    QTest.mouseClick(window.flowView.viewport(), Qt.LeftButton, pos=otherPoint)
    designerApplication.processEvents()
    assert window.flowScene.mouseGrabberItem() is None
    assert window.flowScene.getNodePositions() == positions
    if confirm is not True:
        assert window.flowModel.toProjectGraph() == before
        assert window.pageCoordinator.session._signature() == signature
    assert window.nodeParamDialog is None


@pytest.mark.parametrize("direction", ["inputs", "outputs"])
@pytest.mark.parametrize("active", [False, True])
def testOrderOnlyChangeUpdatesCanvasHistoryAndSavedProject(
    designerApplication, monkeypatch, tmp_path, direction, active
):
    window = MainWindow(_RuntimeClientStub())
    workflowId = "main" if active else window.createWorkflow("Body")
    window.editWorkflowInterfaceFor(workflowId, {"a": "integer", "b": "integer"},
                                   {"a": "integer", "b": "integer"})
    if not active:
        window.activateWorkflow("main")
        subflowId = window.addSubflowNode(workflowId)
    session = window.pageCoordinator.session
    session.markSaved()
    historyLength = len(session._undo)

    class DialogStub:
        def __init__(self, workflowName, inputs, outputs, **kwargs):
            dialog = _dialog(inputs, outputs)
            table = dialog._inputTable if direction == "inputs" else dialog._outputTable
            table.removePort(table._rows[0][0])
            table.addPort("a", "integer")
            self.result = dialog.getInterfaces()

        def execInterface(self):
            return self.result

    monkeypatch.setattr(mainWindowModule, "WorkflowInterfaceDialog", DialogStub)
    window.editWorkflowInterfaceFor(workflowId)
    workflow = window.workflowStore.get(workflowId)
    assert list(getattr(workflow, direction)) == ["b", "a"]
    assert session.dirty
    assert len(session._undo) == historyLength + 1
    if active:
        kind = "workflow_input" if direction == "inputs" else "workflow_output"
        node = next(node for node in window.flowModel.nodes.values() if node.kind == kind)
        ports = node.outputPorts if direction == "inputs" else node.inputPorts
    else:
        node = window.flowModel.nodes[subflowId]
        ports = node.inputPorts if direction == "inputs" else node.outputPorts
    assert list(ports) == ["b", "a"]
    window.pageCoordinator.history()
    assert list(getattr(window.workflowStore.get(workflowId), direction)) == ["a", "b"]
    assert not session.dirty
    window.pageCoordinator.history(redo=True)
    assert list(getattr(window.workflowStore.get(workflowId), direction)) == ["b", "a"]
    workflow = window.workflowStore.get(workflowId)
    signature = session._signature()
    historyLength = len(session._undo)
    window.editWorkflowInterfaceFor(workflowId, workflow.inputs, workflow.outputs)
    assert session._signature() == signature
    assert len(session._undo) == historyLength
    project = tmp_path / "ordered-interfaces"
    assert window.saveProjectToDirectory(str(project))
    assert not session.dirty
    reloaded = MainWindow(_RuntimeClientStub())
    assert reloaded.loadProjectDirectory(str(project))
    assert list(getattr(reloaded.workflowStore.get(workflowId), direction)) == ["b", "a"]


@pytest.mark.parametrize("direction", ["inputs", "outputs"])
def testPropertiesButtonCanCreateOptionalNullablePorts(designerApplication, direction):
    from emo_master.apps.designer.ui import workflow_interface_dialog as interfaceModule
    dialog = _dialog({"value": "integer"}, {"value": "integer"}, initialTab=direction)
    table = dialog._inputTable if direction == "inputs" else dialog._outputTable
    assert table._table.columnCount() == 4
    captured = []

    def editProperties(properties):
        assert isinstance(properties, interfaceModule._PortPropertiesDialog)
        captured.append(True)
        properties._requiredCombo.setCurrentIndex(properties._requiredCombo.findData(False))
        properties._nullableCombo.setCurrentIndex(properties._nullableCombo.findData(True))
        properties.accept()

    dialog.show()
    designerApplication.processEvents()
    _editProperties(table, editProperties)
    assert captured == [True]
    ports = dialog.getInterfaces()[0 if direction == "inputs" else 1]
    assert ports == {"value": {"type": "integer", "required": False, "nullable": True}}
    assert _missingKeys(ports, {}) == []
    assert matchesPortSpec(None, ports["value"])


def testIncompatibleVersionCanBeExplicitlyRemovedFromProperties(designerApplication):
    from emo_master.apps.designer.ui import workflow_interface_dialog as interfaceModule
    spec = {"type": "detectionCollection", "required": False, "nullable": True, "schemaVersion": "1.x"}
    dialog = _dialog({"objects": spec})
    table = dialog._inputTable
    _selectType(table._rows[0][1], "string")
    assert not dialog._saveButton.isEnabled()
    assert table._table.columnCount() == 4

    def editProperties(properties):
        assert isinstance(properties, interfaceModule._PortPropertiesDialog)
        assert properties._versionCheck.isChecked()
        assert not properties._saveButton.isEnabled()
        properties._versionCheck.setChecked(False)
        assert properties._saveButton.isEnabled()
        properties.accept()

    _editProperties(table, editProperties)
    assert dialog._saveButton.isEnabled()
    assert dialog.getInterfaces()[0] == {"objects": {"type": "string", "required": False, "nullable": True}}
    assert spec["schemaVersion"] == "1.x"


@pytest.mark.parametrize("spec", [
    "bool", "list<int>", {"type": "int"},
    {"type": "integer", "required": False},
    {"type": "boolean", "required": True, "nullable": False},
    {"type": "list<detectionCollection>", "nullable": True, "schemaVersion": "1.x"},
])
def testUnchangedPropertiesPreserveRepresentationAndFieldPresence(designerApplication, spec):
    from emo_master.apps.designer.ui.workflow_interface_dialog import _PortPropertiesDialog
    from emo_master.core.contracts.port_types import normalizePortType
    before = deepcopy(spec)
    dialog = _PortPropertiesDialog("输入", "value", normalizePortType(spec), spec)
    assert dialog.getPortSpec() == spec
    assert isinstance(dialog.getPortSpec(), type(spec))
    dialog.accept()
    assert dialog.result() == QDialog.Accepted
    assert spec == before


def testPropertyValidationCanEditVersionAndRemoveExplicitFlags(designerApplication):
    from emo_master.apps.designer.ui.workflow_interface_dialog import _PortPropertiesDialog
    spec = {"type": "list<detectionCollection>", "required": False, "nullable": False, "schemaVersion": "1.x"}
    dialog = _PortPropertiesDialog("输出", "objects", "list<detectionCollection>", spec)
    dialog._versionEdit.setText("9.x")
    assert not dialog._saveButton.isEnabled()
    dialog.accept()
    assert dialog.result() != QDialog.Accepted
    dialog._versionEdit.setText("1.1")
    dialog._requiredCombo.setCurrentIndex(dialog._requiredCombo.findData(None))
    dialog._nullableCombo.setCurrentIndex(dialog._nullableCombo.findData(None))
    assert dialog.getPortSpec() == {"type": "list<detectionCollection>", "schemaVersion": "1.1"}
    dialog._versionCheck.setChecked(False)
    assert dialog.getPortSpec() == {"type": "list<detectionCollection>"}
    assert spec["required"] is False


@pytest.mark.parametrize("confirm", [False, True])
def testCancelledOrUnchangedPropertiesDoNotModifyInterface(designerApplication, confirm):
    dialog = _dialog({"value": "int"})
    before = deepcopy(dialog.getInterfaces())
    captured = []

    def finishProperties(properties):
        captured.append(True)
        if not confirm:
            properties._requiredCombo.setCurrentIndex(properties._requiredCombo.findData(False))
        (properties.accept if confirm else properties.reject)()

    _editProperties(dialog._inputTable, finishProperties)
    assert captured == [True]
    assert dialog.getInterfaces() == before


@pytest.mark.parametrize("disappear", ["switch", "close"])
def testQueuedInterfaceOpenIsDiscardedWhenNativeItemDisappears(designerApplication, monkeypatch, disappear):
    window = MainWindow(_RuntimeClientStub())
    bodyId = window.createWorkflow("Body")
    window.activateWorkflow("main")
    opened = []

    class DialogStub:
        def __init__(self, *args, **kwargs):
            opened.append(True)

        def execInterface(self):
            return None

    monkeypatch.setattr(mainWindowModule, "WorkflowInterfaceDialog", DialogStub)
    window.show()
    designerApplication.processEvents()
    node = next(node for node in window.flowModel.nodes.values() if node.kind == "workflow_input")
    item = window.flowScene._nodeItems[node.nodeId]
    window.flowView.centerOn(item)
    point = window.flowView.mapFromScene(item.sceneBoundingRect().center())
    QTest.mouseDClick(window.flowView.viewport(), Qt.LeftButton, pos=point)
    if disappear == "switch":
        window.activateWorkflow(bodyId)
    else:
        window.close()
        window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    designerApplication.processEvents()
    assert opened == []


def testCancelAndUnchangedConfirmDoNotCaptureGraphOrDirtyDraft(designerApplication, monkeypatch):
    window = MainWindow(_RuntimeClientStub())
    window.flowModel.addNode("vision.unsaved", "Unsaved", {}, {})
    before = deepcopy(window.workflowStore.workflows)
    signature = window.pageCoordinator.session._signature()

    class DialogStub:
        result = None

        def __init__(self, *args, **kwargs):
            pass

        def execInterface(self):
            return self.result

    monkeypatch.setattr(mainWindowModule, "WorkflowInterfaceDialog", DialogStub)
    window.editWorkflowInterfaceFor("main")
    assert window.workflowStore.workflows == before
    assert window.pageCoordinator.session._signature() == signature
    DialogStub.result = ({}, {})
    window.editWorkflowInterfaceFor("main")
    assert window.workflowStore.workflows == before
    assert window.pageCoordinator.session._signature() == signature


def testConfirmedInterfacesPersistAndRefreshSubflowReferences(designerApplication, monkeypatch, tmp_path):
    window = MainWindow(_RuntimeClientStub())
    bodyId = window.createWorkflow("Body")
    window.activateWorkflow("main")
    subflowId = window.addSubflowNode(bodyId)
    descriptor = {"type": "boolean", "required": False, "nullable": False}
    captured = []

    class DialogStub:
        def __init__(self, workflowName, inputs, outputs, **kwargs):
            captured.append(workflowName)

        def execInterface(self):
            return {"hasNext": descriptor}, {"continue": "boolean"}

    monkeypatch.setattr(mainWindowModule, "WorkflowInterfaceDialog", DialogStub)
    signature = window.pageCoordinator.session._signature()
    window.editWorkflowInterfaceFor(bodyId)
    assert captured == ["Body"]
    assert window.activeWorkflowId == "main"
    assert window.pageCoordinator.session._signature() != signature
    assert window.flowModel.nodes[subflowId].inputPorts == {"hasNext": "boolean"}
    assert window.flowModel.nodes[subflowId].outputPorts == {"continue": "boolean"}
    project = tmp_path / "interface-project"
    assert window.saveProjectToDirectory(str(project))
    reloaded = MainWindow(_RuntimeClientStub())
    assert reloaded.loadProjectDirectory(str(project))
    workflow = reloaded.workflowStore.get(bodyId)
    assert workflow.inputs == {"hasNext": descriptor}
    assert workflow.outputs == {"continue": "boolean"}
    assert reloaded.flowModel.nodes[subflowId].inputPorts == {"hasNext": "boolean"}


def testRunningWorkflowDoesNotOpenOrApplyInterfaceEditor(designerApplication, monkeypatch):
    window = MainWindow(_RuntimeClientStub())
    called = []
    monkeypatch.setattr(mainWindowModule, "WorkflowInterfaceDialog", lambda *args, **kwargs: called.append(True))
    window.isJobRunning = True
    inputNode = next(node for node in window.flowModel.nodes.values() if node.kind == "workflow_input")
    window.openNodeParamDialog(inputNode.nodeId)
    window.editWorkflowInterfaceFor("main", {"value": "string"}, {})
    assert called == []
    assert window.workflowStore.get().inputs == {}


def testRuntimeStartingDuringModalPreventsApplyingDraft(designerApplication, monkeypatch):
    window = MainWindow(_RuntimeClientStub())
    before = deepcopy(window.workflowStore.workflows)

    class DialogStub:
        def __init__(self, *args, **kwargs):
            pass

        def execInterface(self):
            window.isJobRunning = True
            return {"unexpected": "integer"}, {}

    monkeypatch.setattr(mainWindowModule, "WorkflowInterfaceDialog", DialogStub)
    window.editWorkflowInterfaceFor("main")
    assert window.workflowStore.workflows == before


def testEscapeCancelsBothDirectionsAtomically(designerApplication):
    inputs = {"hasNext": "boolean"}
    outputs = {"continue": "boolean"}
    dialog = _dialog(inputs, outputs)

    def cancel():
        dialog._inputTable._rows[0][0].setText("newInput")
        dialog._outputTable._rows[0][0].setText("newOutput")
        QTest.keyClick(dialog, Qt.Key_Escape)

    QTimer.singleShot(0, cancel)
    assert dialog.execInterface() is None
    assert inputs == {"hasNext": "boolean"}
    assert outputs == {"continue": "boolean"}


@pytest.mark.parametrize("initialTab", ["inputs", "outputs"])
def testSmallDialogKeepsControlsAccessible(designerApplication, initialTab):
    dialog = _dialog({f"input{index}": "boolean" for index in range(18)},
                     {f"output{index}": "string" for index in range(18)},
                     initialTab=initialTab)
    dialog.resize(640, 430)
    dialog.show()
    designerApplication.processEvents()
    assert dialog.width() <= 640
    assert dialog.height() <= 430
    table = dialog._inputTable if initialTab == "inputs" else dialog._outputTable
    assert table._table.viewport().height() > 80
    assert table._table.verticalScrollBar().maximum() > 0
    for widget in (dialog._saveButton, dialog._cancelButton, table._addButton):
        rect = widget.rect()
        assert dialog.rect().contains(widget.mapTo(dialog, rect.topLeft()))
        assert dialog.rect().contains(widget.mapTo(dialog, rect.bottomRight()))
