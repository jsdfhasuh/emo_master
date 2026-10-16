from __future__ import annotations

import json
from pathlib import Path

import emo_master  # noqa: F401 - preload Windows dependencies before Qt
import pytest
from PySide2.QtCore import QEventLoop, Qt, QTimer
from PySide2.QtTest import QTest
from PySide2.QtWidgets import QApplication, QComboBox, QStyle, QStyleOptionComboBox

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


@pytest.mark.parametrize("field", ["targetWorkflowId", "bodyWorkflowId", "conditionWorkflowId"])
def testDoubleClickNameOnlyEditsParametersWithoutNavigating(designerApplication, field):
    window = _window()
    nodeId = window.addSubflowNode('body') if field == 'targetWorkflowId' else 'while'
    window.show()
    window.openNodeParamDialog(nodeId)
    editor = window.nodeParamDialog
    form = editor._schemaForm
    combo = form._controls[field]
    assert type(combo) is QComboBox
    assert '双击' not in combo.toolTip()
    storedValue = combo.currentData()
    draftValue = 'condition' if storedValue == 'body' else 'body'
    combo.setCurrentIndex(combo.findData(draftValue))
    if field != 'targetWorkflowId':
        form._controls["maxIterations"].setValue(321)
    history = list(window.workflowNavigation.history.entries), window.workflowNavigation.history.index
    designerApplication.processEvents()
    _doubleClickName(combo)
    designerApplication.processEvents()
    assert window.activeWorkflowId == 'main'
    assert editor.isVisible()
    assert editor.collectParams()[field] == draftValue
    assert (window.workflowNavigation.history.entries, window.workflowNavigation.history.index) == history
    node = window.flowModel.nodes[nodeId]
    assert (node.targetWorkflowId if field == 'targetWorkflowId' else node.loop[field]) == storedValue
    if field != 'targetWorkflowId':
        assert editor.collectParams()['maxIterations'] == 321
        assert node.loop['maxIterations'] == 10000
    combo.hidePopup()
    editor.forceClose()


@pytest.mark.parametrize('subControl', [QStyle.SC_ComboBoxEditField, QStyle.SC_ComboBoxArrow])
def testSingleClickOpensImmediatelyAndOnlySelectsWithoutNavigation(designerApplication, subControl):
    window = _window()
    window.openNodeParamDialog("while")
    editor = window.nodeParamDialog
    combo = editor._schemaForm._controls["bodyWorkflowId"]
    designerApplication.processEvents()
    QTest.mouseClick(combo, Qt.LeftButton, pos=_comboPoint(combo, subControl))
    # No double-click timer: the first click opens a normal combo immediately.
    assert combo.view().isVisible()
    assert window.activeWorkflowId == "main"
    designerApplication.processEvents()
    target = combo.model().index(combo.findData('condition'), combo.modelColumn())
    point = combo.view().visualRect(target).center()
    QTest.mouseMove(combo.view().viewport(), point)
    # Qt guards the opening mouse release briefly; model a subsequent choice,
    # not two complete mouse clicks sent within the same event-loop tick.
    wait = QEventLoop()
    QTimer.singleShot(QApplication.doubleClickInterval() + 80, wait.quit)
    wait.exec_()
    # Deferred editor layout may move the popup while events are processed.
    point = combo.view().visualRect(target).center()
    assert combo.view().indexAt(point) == target
    QTest.mouseClick(combo.view().viewport(), Qt.LeftButton, pos=point)
    designerApplication.processEvents()
    assert not combo.view().isVisible()
    assert combo.currentData() == 'condition'
    assert window.activeWorkflowId == "main"
    assert editor.isVisible() and window.flowModel.nodes['while'].loop['bodyWorkflowId'] == 'body'
    editor.forceClose()


def testDoubleClickExpandedCandidateDoesNotNavigateOrApply(designerApplication):
    window = _window()
    nodeId = window.addSubflowNode('body')
    window.openNodeParamDialog(nodeId)
    editor = window.nodeParamDialog
    combo = editor._schemaForm._controls['targetWorkflowId']
    combo.showPopup()
    designerApplication.processEvents()
    target = combo.model().index(combo.findData('condition'), combo.modelColumn())
    point = combo.view().visualRect(target).center()
    QTest.mouseDClick(combo.view().viewport(), Qt.LeftButton, pos=point)
    QTest.mouseRelease(combo.view().viewport(), Qt.LeftButton, pos=point)
    designerApplication.processEvents()
    assert window.activeWorkflowId == 'main'
    assert editor.isVisible() and window.flowModel.nodes[nodeId].targetWorkflowId == 'body'
    combo.hidePopup()
    editor.forceClose()


def testMissingTargetStaysEditableWithoutNavigation(designerApplication):
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
    assert editor.collectParams()["bodyWorkflowId"] == "removed-id"
    assert form._controls['bodyWorkflowId'].currentText() == '未找到工作流 (removed-id)'
    form._controls['bodyWorkflowId'].hidePopup()
    form._controls["bodyWorkflowId"].setCurrentIndex(form._controls["bodyWorkflowId"].findData("body"))
