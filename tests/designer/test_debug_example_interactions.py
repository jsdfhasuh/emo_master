"""Exercise shipped examples through native operator and workflow controls."""
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path

import emo_master  # noqa: F401
import cv2
import numpy as np
import pytest
from PySide2.QtCore import Qt, QTimer
from PySide2.QtTest import QTest
from PySide2.QtWidgets import QApplication, QDialog, QDialogButtonBox, QFileDialog
from shiboken2 import isValid

from emo_master.apps.designer.services.runtime_client import RuntimeClient
from emo_master.apps.designer.ui.param_form import SchemaParamForm
from emo_master.apps.runtime.operator_debug.assets import decode
from tests.designer.qt_wait import waitForCatalog
from tests.designer.test_operator_debug_window import wait
from tests.runtime.test_operator_debug_rpc import runtime as runtime


EXAMPLES = Path(__file__).resolve().parents[2] / "examples/workflow_debugger"


@contextmanager
def exampleWindow(runtime, ownedDesignerWindow, filename):
    path = EXAMPLES / filename
    original = path.read_bytes()
    window = ownedDesignerWindow(RuntimeClient(runtime))
    waitForCatalog(window)
    assert window.loadProjectDirectory(str(path))
    for workflowId in window.pageCoordinator.session.payload()["workflows"]:
        window.activateWorkflow(workflowId)
    window.activateWorkflow("main")
    session = window.pageCoordinator.session
    before, undo, loaded = deepcopy(session.payload()), len(session._undo), runtime.loadedDocument
    dirty = session.dirty
    try:
        yield window
        assert session.payload() == before and len(session._undo) == undo
        assert session.dirty == dirty and path.read_bytes() == original
        assert runtime.loadedDocument is loaded and not runtime.jobRepository.all()
    finally:
        dialog = getattr(window, "_workflowDebugWindow", None)
        if dialog is not None and isValid(dialog):
            dialog.close()
            wait(lambda: not isValid(dialog))
        window.operatorEditorManager.closeAll()
        wait(lambda: not runtime.operatorDebugManager.ownsResources())


def paused(dialog, nodeId, workflowId, *, after=0, phase="node.before"):
    wait(lambda: dialog.current is not None and dialog.pauseSequence > after
         and dialog.current["nodeId"] == nodeId
         and dialog.current["identity"]["workflowId"] == workflowId
         and dialog.current["phase"] == phase and dialog.buttons["continue"].isEnabled())
    return deepcopy(dialog.current)


def step(dialog, action, nodeId, workflowId, phase="node.before"):
    sequence = dialog.pauseSequence
    assert dialog.buttons[action].isEnabled()
    QTest.mouseClick(dialog.buttons[action], Qt.LeftButton)
    current = paused(dialog, nodeId, workflowId, after=sequence, phase=phase)
    assert current["pauseSequence"] == sequence + 1
    return current


def startFlow(window, monkeypatch=None, inputFile=None, port=None):
    window.designerActions.invoke("workflow_debug")
    dialog = window._workflowDebugWindow
    wait(lambda: dialog.startButton.isEnabled())
    if inputFile:
        monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *args: (str(EXAMPLES / inputFile), ""))
        dialog.file(port)
        wait(lambda: dialog.startButton.isEnabled() and dialog.rows[port].wire() is not None)
    QTest.mouseClick(dialog.startButton, Qt.LeftButton)
    wait(lambda: dialog.current is not None and dialog.buttons["continue"].isEnabled())
    return dialog


def finish(dialog, expected):
    QTest.mouseClick(dialog.buttons["continue"], Qt.LeftButton)
    wait(lambda: dialog.state == "SUCCEEDED" and dialog.displayed is not None
         and dialog.displayed.get("phase") == "workflow.result")
    assert dialog.displayed["outputs"] == expected


def trial(dialog, params):
    errors = []
    def acceptParams():
        modal = QApplication.activeModalWidget()
        try:
            assert isinstance(modal, QDialog)
            form = modal.findChild(SchemaParamForm)
            form.setSchema(dialog.trialOperator()["paramSchema"], params)
            buttons = modal.findChild(QDialogButtonBox)
            QTest.mouseClick(buttons.button(QDialogButtonBox.Ok), Qt.LeftButton)
        except BaseException as error:
            errors.append(error)
            if isinstance(modal, QDialog):
                modal.reject()
    before = deepcopy(dialog.current)
    QTimer.singleShot(0, acceptParams)
    QTest.mouseClick(dialog.buttons["trial"], Qt.LeftButton)
    assert not errors, errors
    wait(lambda: dialog.displayed is not None and dialog.displayed["snapshotKind"] == "trial"
         and dialog.buttons["continue"].isEnabled())
    assert dialog.displayed["status"] == "SUCCEEDED"
    assert dialog.current == before and dialog.pauseSequence == before["pauseSequence"]
    return deepcopy(dialog.displayed)


@pytest.mark.parametrize("filename,workflowId,nodeId", [
    ("01-nested-calls.emoproj", "child", "number"),
    ("03-image.emoproj", "process", "threshold"),
])
def testStandaloneNodeUsesOnlyExplicitInputs(runtime, ownedDesignerWindow, monkeypatch, filename, workflowId, nodeId):
    with exampleWindow(runtime, ownedDesignerWindow, filename) as window:
        window.activateWorkflow(workflowId)
        window.flowScene.setNodeSelected(nodeId)
        window.onNodeSelectionChanged()
        menu = window.nodeContextMenu.buildMenu(nodeId)
        action = next(item for item in menu.actions() if item.data() == "operator_debug")
        assert action.isEnabled()
        action.trigger()
        editor = window.nodeParamDialog
        dialog = editor._debugWindow
        wait(lambda: dialog.run.isEnabled())
        if nodeId == "number":
            assert dialog.rows == {}
            editor._schemaForm.setSchema(editor.context.paramSchema, {"value": 99})
        else:
            assert dialog.rows["image"].wire() is None
            monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *args: (str(EXAMPLES / "sample.png"), ""))
            dialog.selectFile("image")
            wait(lambda: dialog.run.isEnabled() and dialog.rows["image"].wire() is not None)
        QTest.mouseClick(dialog.run, Qt.LeftButton)
        wait(lambda: len(dialog.records) == 1 and dialog.run.isEnabled())
        record = dialog.records[0]
        assert record["status"] == "SUCCEEDED"
        if nodeId == "number":
            assert record["outputs"] == {"value": 99}
        else:
            raw, _ = decode((EXAMPLES / "sample.png").read_bytes(), "image/png")
            expected = cv2.threshold(raw, 127, 255, cv2.THRESH_BINARY)[1]
            upstream = cv2.threshold(cv2.GaussianBlur(raw, (3, 3), 0), 127, 255, cv2.THRESH_BINARY)[1]
            assert not np.array_equal(expected, upstream)
            downloaded = dialog.connection.download(record["outputAssets"]["mask"]["assetId"])
            actual, _ = decode(downloaded["content"], "image/png")
            np.testing.assert_array_equal(actual, expected)
            assert record["outputs"]["threshold"] == 127


def testFlowSingleStepIntoOutOverAndTrialIsolation(runtime, ownedDesignerWindow):
    with exampleWindow(runtime, ownedDesignerWindow, "01-nested-calls.emoproj") as window:
        dialog = startFlow(window)
        paused(dialog, "seed", "main")
        step(dialog, "into", "first", "main")
        child = step(dialog, "into", "number", "child")
        assert trial(dialog, {"value": 99})["outputs"] == {"value": 99}
        returned = step(dialog, "out", "first", "main", "call.return")
        assert returned["outputs"] == {"value": 7}
        assert returned["identity"]["workflowRunId"] != child["identity"]["workflowRunId"]
        step(dialog, "into", "second", "main")
        returned = step(dialog, "over", "second", "main", "call.return")
        assert returned["outputs"] == {"value": 7}
        finish(dialog, {"value": 7})


@pytest.mark.parametrize("condition,hits", [("", 1), ('params["value"] == 7', 2)])
def testFlowSingleBreakpointStopsBeforeExactInvocation(runtime, ownedDesignerWindow, condition, hits):
    with exampleWindow(runtime, ownedDesignerWindow, "01-nested-calls.emoproj") as window:
        dialog = startFlow(window)
        row = next(row for row in dialog.breakpointRows if row[:2] == ("child", "number"))
        row[2].setChecked(True)
        row[3].setText(condition)
        row[4].setValue(hits)
        QTest.mouseClick(dialog.applyBreakpoints, Qt.LeftButton)
        wait(lambda: dialog.buttons["continue"].isEnabled())
        first = step(dialog, "continue", "number", "child")
        assert first["reason"] == "breakpoint" and first["outputs"] == {}
        assert first["hits"]["child/number"] == hits
        if hits == 1:
            second = step(dialog, "continue", "number", "child")
            assert second["reason"] == "breakpoint" and second["hits"]["child/number"] == 2
            assert second["identity"]["nodeRunId"] != first["identity"]["nodeRunId"]
            assert second["identity"]["workflowRunId"] != first["identity"]["workflowRunId"]
        finish(dialog, {"value": 7})


def testLoopConditionalPointUsesCurrentIteration(runtime, ownedDesignerWindow, monkeypatch):
    with exampleWindow(runtime, ownedDesignerWindow, "02-foreach.emoproj") as window:
        dialog = startFlow(window, monkeypatch, "items.json", "items")
        row = next(row for row in dialog.breakpointRows if row[:2] == ("body", "compare"))
        row[2].setChecked(True)
        row[3].setText('inputs["left"] >= 5')
        QTest.mouseClick(dialog.applyBreakpoints, Qt.LeftButton)
        wait(lambda: dialog.buttons["continue"].isEnabled())
        for value, iteration in ((5, 1), (9, 2)):
            current = step(dialog, "continue", "compare", "body")
            assert current["reason"] == "breakpoint" and current["inputs"]["left"] == value
            assert current["identity"]["iterationPath"] == [iteration]
        finish(dialog, {"result": [False, True, True], "index": [0, 1, 2]})


def testImageRunToPointUsesUpstreamPixelsAndTrialKeepsOriginalThreshold(runtime, ownedDesignerWindow, monkeypatch):
    with exampleWindow(runtime, ownedDesignerWindow, "03-image.emoproj") as window:
        dialog = startFlow(window, monkeypatch, "sample.png", "image")
        row = next(index for index, row in enumerate(dialog.breakpointRows) if row[:2] == ("process", "threshold"))
        dialog.breakpoints.selectRow(row)
        sequence = dialog.pauseSequence
        QTest.mouseClick(dialog.runTo, Qt.LeftButton)
        current = paused(dialog, "threshold", "process", after=sequence)
        assert current["reason"] == "run-to" and current["pauseSequence"] == sequence + 1
        raw, _ = decode((EXAMPLES / "sample.png").read_bytes(), "image/png")
        expected = cv2.GaussianBlur(raw, (3, 3), 0)
        downloaded = dialog.connection.download(current["inputsAssets"]["image"]["assetId"])
        actual, _ = decode(downloaded["content"], "image/png")
        np.testing.assert_array_equal(actual, expected)
        assert not np.array_equal(actual, raw)
        trialResult = trial(dialog, dict(current["params"], threshold=200))
        assert trialResult["outputs"]["threshold"] == 200
        downloaded = dialog.connection.download(trialResult["outputsAssets"]["mask"]["assetId"])
        trialMask, _ = decode(downloaded["content"], "image/png")
        np.testing.assert_array_equal(trialMask, cv2.threshold(expected, 200, 255, cv2.THRESH_BINARY)[1])
        finish(dialog, {"threshold": 127})
        downloaded = dialog.connection.download(dialog.displayed["outputsAssets"]["mask"]["assetId"])
        actual, _ = decode(downloaded["content"], "image/png")
        np.testing.assert_array_equal(actual, cv2.threshold(expected, 127, 255, cv2.THRESH_BINARY)[1])
        assert not np.array_equal(actual, trialMask)
