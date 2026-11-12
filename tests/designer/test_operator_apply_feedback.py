import os
from pathlib import Path

import pytest

from emo_master.apps.designer.operator_editors import OperatorEditorManager
from tests.designer.qt_wait import waitUntil
from tests.designer.test_operator_editor_manager import _Runtime, _Settings


@pytest.fixture
def applyEditor(tmp_path):
    manager = OperatorEditorManager(
        runtimeClient=_Runtime(), settingsStore=_Settings(),
        applyParams=lambda *_: True, appendLog=lambda *_: None, cacheRoot=tmp_path,
    )
    window = manager.open(
        projectId="p", workflowId="main", nodeId="number",
        operatorId="vision.value.number", displayName="Number",
        schema={"properties": {"value": {"type": "integer", "default": 1}}},
        values={"value": 1},
    )
    yield window
    manager.closeAll()


@pytest.mark.parametrize("width", [460, 620])
def testApplyShowsSuccessAndRestoresButtonWithoutResizing(applyEditor, designerApplication, width):
    window = applyEditor
    window.resize(width, 720)
    designerApplication.processEvents()
    buttonWidth = window._applyButton.width()
    window._applyButton.click()
    designerApplication.processEvents()
    assert window._statusLabel.text() == "参数已应用"
    assert "background-color: #dcfce7" in window._statusLabel.styleSheet()
    assert window._applyButton.text() == "已应用"
    assert "#15803d" in window._applyButton.styleSheet()
    assert window._applyButton.isEnabled() and window._applyFeedbackTimer.isActive()
    assert window._applyButton.width() == buttonWidth
    assert window.width() == width
    assert window.rect().contains(window._statusLabel.geometry())
    assert window.rect().contains(window._applyButton.geometry())
    screenshotDir = os.environ.get("EMO_APPLY_FEEDBACK_SCREEN_DIR")
    if screenshotDir:
        target = Path(screenshotDir)
        target.mkdir(parents=True, exist_ok=True)
        assert window.grab().save(str(target / f"apply-success-{width}.png"))
    window._applyFeedbackTimer.setInterval(100)
    waitUntil(lambda: window._applyFeedbackTimer.remainingTime() < 50)
    window._applyButton.click()
    assert window._applyFeedbackTimer.remainingTime() > 60
    waitUntil(lambda: not window._applyFeedbackTimer.isActive())
    assert not window._applyFeedbackTimer.isActive()
    assert window._applyButton.text() == "应用"
    assert window._applyButton.styleSheet() == ""
    assert window._statusLabel.text() == "参数已应用"
    assert window._applyButton.width() == buttonWidth


@pytest.mark.parametrize("failure", ["rejected", "exception", "validation"])
def testFailedApplyClearsPreviousSuccess(applyEditor, monkeypatch, failure):
    window = applyEditor
    assert window.applyChanges()
    if failure == "validation":
        monkeypatch.setattr(window._schemaForm, "validationMessage", lambda: "Invalid value")
    else:
        def apply(_params):
            if failure == "exception":
                raise RuntimeError("Apply failed")
            return False
        monkeypatch.setattr(window.context, "applyParams", apply)
    assert not window.applyChanges()
    assert window._statusLabel.text() != "参数已应用"
    assert window._statusLabel.styleSheet() == "color: #d45b5b;"
    assert window._applyButton.text() == "应用"
    assert not window._applyFeedbackTimer.isActive()


def testControllerEditClearsTemporarySuccessButton(applyEditor):
    assert applyEditor.applyChanges()
    applyEditor.markDirty()
    assert applyEditor.isDirty()
    assert applyEditor._applyButton.text() == "应用"
    assert not applyEditor._applyFeedbackTimer.isActive()
