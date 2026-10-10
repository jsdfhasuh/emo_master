from __future__ import annotations

import json
from pathlib import Path

import emo_master  # noqa: F401 - preload Windows dependencies before Qt
import pytest
from PySide2.QtCore import QEventLoop, Qt, QTimer
from PySide2.QtTest import QTest
from PySide2.QtWidgets import QApplication, QStyle, QStyleOptionComboBox

from emo_master.apps.designer.ui.main_window import MainWindow
from emo_master.apps.designer.ui.param_form import SchemaParamForm


class _RuntimeClientStub:
    def listOperators(self):
        return []


def _window():
    window = MainWindow(_RuntimeClientStub())
    example = Path(__file__).resolve().parents[2] / "examples/image_batch_while_portable/image-batch-while.emoproj"
    window.workflowController.loadPayload(json.loads(example.read_text(encoding="utf-8")), preserveEdges=True)
    window.activeWorkflowId = "main"
    return window


def testLoopEditorDisplaysNamesAndAppliesIds(designerApplication, tmp_path):
    window = _window()
    window.openNodeParamDialog("while")
    dialog = window.nodeParamDialog
    form = dialog._schemaForm
    body = form._controls["bodyWorkflowId"]
    condition = form._controls["conditionWorkflowId"]
    assert body.currentText() == "Load And Process One Image"
    assert condition.currentText() == "Continue While Images Remain"
    assert body.currentData() == "body"
    assert condition.currentData() == "condition"
    assert form._layout.labelForField(body).text() == "循环体工作流 *"
    assert form._layout.labelForField(condition).text() == "布尔条件来源工作流（兼容） *"
    assert "工作流 ID：body" in body.itemData(body.currentIndex(), Qt.ToolTipRole)
    assert window.applyNodeParams("while", form.getValues())
    loop = window.flowModel.nodes["while"].loop
    assert loop["bodyWorkflowId"] == "body"
    assert loop["conditionWorkflowId"] == "condition"
    window.workflowController.captureActiveWorkflow()
    saved = window.workflowStore.toPayload()
    storedLoop = next(node for node in saved["workflows"]["main"]["nodes"] if node["kind"] == "loop")["loop"]
    assert storedLoop["bodyWorkflowId"] == "body"
    assert storedLoop["conditionWorkflowId"] == "condition"
    for _ in range(3):
        designerApplication.processEvents()
    screenshot = tmp_path / "workflow-name-selector.png"
    assert dialog.grab().save(str(screenshot))
    print(f"Workflow selector screenshot: {screenshot}")


def testSubflowSelectorReturnsIdWhenSelectionChanges():
    window = _window()
    nodeId = window.addSubflowNode("condition")
    node = window.flowModel.nodes[nodeId]
    form = SchemaParamForm()
    form.setSchema(window._nodeEditorSchema(node), {"targetWorkflowId": "condition"})
    combo = form._controls["targetWorkflowId"]
    assert combo.currentText() == "Continue While Images Remain"
    combo.setCurrentIndex(combo.findData("body"))
    assert combo.currentText() == "Load And Process One Image"
    assert form.getValues() == {"targetWorkflowId": "body"}
    assert window.applyNodeParams(nodeId, form.getValues())
    assert window.flowModel.nodes[nodeId].targetWorkflowId == "body"


def testRenamedAndDuplicateNamesKeepStableIds():
    window = _window()
    window.renameWorkflow("body", "同名工作流")
    window.renameWorkflow("condition", "同名工作流")
    node = window.flowModel.nodes["while"]
    form = SchemaParamForm()
    form.setSchema(window._nodeEditorSchema(node), node.loop)
    assert form._controls["bodyWorkflowId"].currentText() == "同名工作流 (body)"
    assert form._controls["conditionWorkflowId"].currentText() == "同名工作流 (condition)"
    window.renameWorkflow("body", "读取并处理单张图片")
    form.setSchema(window._nodeEditorSchema(node), form.getValues())
    assert form._controls["bodyWorkflowId"].currentText() == "读取并处理单张图片"
    assert form.getValues()["bodyWorkflowId"] == "body"


def testMissingReferenceKeepsOriginalId():
    window = _window()
    node = window.flowModel.nodes["while"]
    form = SchemaParamForm()
    form.setSchema(window._nodeEditorSchema(node), {**node.loop, "bodyWorkflowId": "removed-id"})
    assert form._controls["bodyWorkflowId"].currentText() == "未找到工作流 (removed-id)"
    assert form.getValues()["bodyWorkflowId"] == "removed-id"


def testLegacyWorkflowOptionsStillReturnIds():
    form = SchemaParamForm()
    form.setWorkflowOptions(["first", "second"])
    form.setSchema({"type": "object", "properties": {
        "target": {"type": "string", "xWidget": "workflow-select"},
    }}, {"target": "second"})
    assert form._controls["target"].currentText() == "second"
    assert form.getValues() == {"target": "second"}


def _comboPoint(combo, subControl):
    option = QStyleOptionComboBox()
    combo.initStyleOption(option)
    return combo.style().subControlRect(QStyle.CC_ComboBox, option, subControl, combo).center()


def _doubleClickName(combo):
    point = _comboPoint(combo, QStyle.SC_ComboBoxEditField)
    QTest.mouseClick(combo, Qt.LeftButton, pos=point)
    QTest.mouseDClick(combo, Qt.LeftButton, pos=point)
    QTest.mouseRelease(combo, Qt.LeftButton, pos=point)


@pytest.mark.parametrize("field,target", [("bodyWorkflowId", "body"), ("conditionWorkflowId", "condition")])
def testDoubleClickOpensWorkflowWithoutApplyingDraft(designerApplication, field, target):
    window = _window()
    window.show()
    window.openNodeParamDialog("while")
    editor = window.nodeParamDialog
    form = editor._schemaForm
    form._controls["maxIterations"].setValue(321)
    designerApplication.processEvents()
    _doubleClickName(form._controls[field])
    designerApplication.processEvents()
    assert window.activeWorkflowId == target
    assert not editor.isVisible()
    assert editor.collectParams()["maxIterations"] == 321
    storedLoop = next(n["loop"] for n in window.workflowStore.get("main").nodes if n["kind"] == "loop")
    assert storedLoop["maxIterations"] == 10000
    assert not form._controls[field]._popupTimer.isActive()
    window.activateWorkflow("main")
    window.openNodeParamDialog("while")
    assert window.nodeParamDialog is editor
    assert editor.collectParams()["maxIterations"] == 321
    # Leave no dirty test dialog for the fixture to prompt about on shutdown.
    form._controls["maxIterations"].setValue(10000)


def testDoubleClickSubflowNameNavigatesOnceWhenEditorReused(designerApplication, monkeypatch):
    window = _window()
    nodeId = window.addSubflowNode("body")
    window.openNodeParamDialog(nodeId)
    window.openNodeParamDialog(nodeId)
    designerApplication.processEvents()
    activations = []
    activate = window.activateWorkflow

    def record(workflowId):
        activations.append(workflowId)
        activate(workflowId)

    monkeypatch.setattr(window, "activateWorkflow", record)
    _doubleClickName(window.nodeParamDialog._schemaForm._controls["targetWorkflowId"])
    assert activations == ["body"]
    assert window.activeWorkflowId == "body"


def testSingleClickAndArrowKeepSelectingWithoutNavigation(designerApplication):
    window = _window()
    window.openNodeParamDialog("while")
    combo = window.nodeParamDialog._schemaForm._controls["bodyWorkflowId"]
    designerApplication.processEvents()
    QTest.mouseClick(combo, Qt.LeftButton, pos=_comboPoint(combo, QStyle.SC_ComboBoxEditField))
    wait = QEventLoop()
    QTimer.singleShot(QApplication.doubleClickInterval() + 80, wait.quit)
    wait.exec_()
    assert combo.view().isVisible()
    assert window.activeWorkflowId == "main"
    combo.hidePopup()
    QTest.mouseClick(combo, Qt.LeftButton, pos=_comboPoint(combo, QStyle.SC_ComboBoxArrow))
    designerApplication.processEvents()
    assert combo.view().isVisible()
    assert window.activeWorkflowId == "main"
    combo.hidePopup()


def testMissingTargetDoesNotNavigateOrDiscardDraft(designerApplication):
    window = _window()
    window.openNodeParamDialog("while")
    editor = window.nodeParamDialog
    form = editor._schemaForm
    form.setSchema(window._nodeEditorSchema(window.flowModel.nodes["while"]), {
        **editor.collectParams(), "bodyWorkflowId": "removed-id",
    })
    designerApplication.processEvents()
    _doubleClickName(form._controls["bodyWorkflowId"])
    assert window.activeWorkflowId == "main"
    assert editor.isVisible()
    assert "工作流不存在" in editor._statusLabel.text()
    assert editor.collectParams()["bodyWorkflowId"] == "removed-id"
    form._controls["bodyWorkflowId"].setCurrentIndex(form._controls["bodyWorkflowId"].findData("body"))
