from copy import deepcopy

import emo_master  # noqa: F401
from PySide2.QtCore import Qt
from PySide2.QtTest import QTest
from shiboken2 import isValid

from emo_master.apps.designer.services.runtime_client import RuntimeClient
from emo_master.apps.designer.ui.workflow_debug_window import WorkflowDebugWindow
from tests.runtime.test_operator_debug_rpc import runtime as runtime
from tests.runtime.test_workflow_debug_control import nestedProject
from tests.designer.test_operator_debug_window import wait


def testNativeWorkflowControlsUseActualPausedInvocation(runtime):
    payload = nestedProject()
    before, navigated = deepcopy(payload), []
    dialog = WorkflowDebugWindow(RuntimeClient(runtime), payload, "main", navigate=lambda *args: navigated.append(args))
    dialog.show()
    try:
        wait(lambda: dialog.startButton.isEnabled())
        QTest.mouseClick(dialog.startButton, Qt.LeftButton)
        wait(lambda: dialog.current is not None and dialog.buttons["into"].isEnabled())
        assert dialog.current["nodeId"] == "first"
        QTest.mouseClick(dialog.buttons["into"], Qt.LeftButton)
        wait(lambda: dialog.current is not None and dialog.current["nodeId"] == "number" and dialog.buttons["out"].isEnabled())
        assert dialog.buttons["trial"].isEnabled()
        sequence = dialog.pauseSequence
        dialog.pauseSequence += 1
        dialog.refresh()
        assert not dialog.buttons["trial"].isEnabled() and not dialog.locate.isEnabled()
        dialog.pauseSequence = sequence
        dialog.refresh()
        QTest.mouseClick(dialog.locate, Qt.LeftButton)
        assert navigated == [("child", "number")]
        QTest.mouseClick(dialog.buttons["out"], Qt.LeftButton)
        wait(lambda: dialog.current is not None and dialog.current["phase"] == "call.return" and dialog.buttons["continue"].isEnabled())
        assert dialog.current["outputs"] == {"value": 7}
        QTest.mouseClick(dialog.buttons["continue"], Qt.LeftButton)
        wait(lambda: dialog.state == "SUCCEEDED" and dialog.displayed["phase"] == "workflow.result")
        assert dialog.displayed["outputs"] == {"value": 7}
        assert payload == before and runtime.loadedDocument is None and runtime.jobRepository.all() == []
        QTest.mouseClick(dialog.end, Qt.LeftButton)
        wait(lambda: not isValid(dialog))
        assert not runtime.operatorDebugManager.ownsResources()
    finally:
        if isValid(dialog):
            dialog.close()
            wait(lambda: not isValid(dialog))


def testWorkflowWindowCloseDuringOpenWaitsForResourceRetirement(runtime):
    dialog = WorkflowDebugWindow(RuntimeClient(runtime), nestedProject(), "main")
    dialog.show()
    dialog.close()
    wait(lambda: not isValid(dialog))
    assert runtime.operatorDebugManager.opens
    assert not runtime.operatorDebugManager.ownsResources()


def testWorkflowWindowInvalidationClosesPinnedRuntime(runtime):
    valid = [True]
    dialog = WorkflowDebugWindow(RuntimeClient(runtime), nestedProject(), "main", valid=lambda: valid[0])
    dialog.show()
    wait(lambda: dialog.startButton.isEnabled())
    valid[0] = False
    wait(lambda: not isValid(dialog))
    assert not runtime.operatorDebugManager.ownsResources()


def testCanonicalWorkflowEntryUsesUnsavedDraftAndSurvivesChildNavigation(runtime, ownedDesignerWindow, tmp_path):
    import json
    from tests.designer.qt_wait import waitForCatalog
    root = tmp_path / "flow-debug-project"
    root.mkdir()
    (root / "project.json").write_text(json.dumps(nestedProject()), encoding="utf-8")
    original = (root / "project.json").read_bytes()
    window = ownedDesignerWindow(RuntimeClient(runtime))
    waitForCatalog(window)
    assert window.loadProjectDirectory(str(root))
    # Materialize the same derived node caches that ordinary tab navigation uses.
    window.activateWorkflow("child")
    window.activateWorkflow("main")
    loaded = runtime.loadedDocument
    before = deepcopy(window.pageCoordinator.session.payload())
    undo = len(window.pageCoordinator.session._undo)
    window.designerActions.invoke("workflow_debug")
    dialog = window._workflowDebugWindow
    try:
        wait(lambda: dialog.startButton.isEnabled())
        assert window.hasActiveDebugSession()
        assert not window._syncRuntimeProjectBeforeRun()
        QTest.mouseClick(dialog.startButton, Qt.LeftButton)
        wait(lambda: dialog.current is not None and dialog.buttons["into"].isEnabled())
        QTest.mouseClick(dialog.buttons["into"], Qt.LeftButton)
        wait(lambda: dialog.current is not None and dialog.current["nodeId"] == "number" and dialog.buttons["out"].isEnabled())
        QTest.mouseClick(dialog.locate, Qt.LeftButton)
        assert window.activeWorkflowId == "child" and not dialog.closing
        assert window.flowScene.getSelectedNodeId() == "number"
        assert window.pageCoordinator.session.payload() == before
        assert len(window.pageCoordinator.session._undo) == undo
        assert (root / "project.json").read_bytes() == original
        assert runtime.loadedDocument is loaded and not runtime.jobRepository.all()
    finally:
        dialog.close()
        wait(lambda: not isValid(dialog))
