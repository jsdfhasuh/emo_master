from copy import deepcopy
import json
import threading
import time

import pytest

import emo_master  # noqa: F401
from PySide2.QtCore import Qt
from PySide2.QtTest import QTest
from shiboken2 import isValid

from emo_master.apps.designer.services.runtime_client import RuntimeClient
from emo_master.apps.designer.ui.workflow_debug_window import WorkflowDebugWindow
from tests.runtime.test_operator_debug_rpc import runtime as runtime
from tests.runtime.test_workflow_debug_control import nestedProject
from tests.designer.test_operator_debug_window import wait


def processEventsFor(seconds):
    until = time.monotonic() + seconds
    wait(lambda: time.monotonic() >= until, seconds + 1)


def observeWorkflowCalls(dialog, monkeypatch):
    controls, polls = [], []
    call = dialog.connection.call

    def observedCall(method, **fields):
        if method == "ControlWorkflowDebug":
            controls.append(fields)
        elif method == "GetWorkflowDebugSession":
            polls.append(method)
        return call(method, **fields)

    monkeypatch.setattr(dialog.connection, "call", observedCall)
    return controls, polls


@pytest.mark.parametrize("action", ["continue", "into", "over", "out"])
def testWorkflowControlPointerHeldAcrossNaturalPollSendsOnce(runtime, monkeypatch, action):
    dialog = WorkflowDebugWindow(RuntimeClient(runtime), nestedProject(), "main")
    dialog.show()
    try:
        wait(lambda: dialog.startButton.isEnabled())
        QTest.mouseClick(dialog.startButton, Qt.LeftButton)
        wait(lambda: dialog.current is not None and dialog.buttons["into"].isEnabled())
        if action == "continue":
            row = next(row for row in dialog.breakpointRows if row[:2] == ("child", "number"))
            row[2].setChecked(True)
            QTest.mouseClick(dialog.applyBreakpoints, Qt.LeftButton)
            wait(lambda: dialog.buttons[action].isEnabled())
        elif action == "out":
            QTest.mouseClick(dialog.buttons["into"], Qt.LeftButton)
            wait(lambda: dialog.current is not None and dialog.current["nodeId"] == "number"
                 and dialog.buttons[action].isEnabled())
        calls, polls = observeWorkflowCalls(dialog, monkeypatch)
        sequence = dialog.pauseSequence
        button = dialog.buttons[action]
        QTest.mousePress(button, Qt.LeftButton)
        processEventsFor(.35)
        assert polls, "The press must span the unchanged natural 250 ms polling timer"
        assert button.isDown() and button.isEnabled()
        QTest.mouseRelease(button, Qt.LeftButton)
        wait(lambda: dialog.pauseSequence == sequence + 1 and dialog.current is not None
             and dialog.buttons[action].isEnabled())
        processEventsFor(.35)
        assert len(calls) == 1
        assert json.loads(calls[0]["control_json"]) == {"action": action, "pauseSequence": sequence}
        assert dialog.pauseSequence == sequence + 1
        assert dialog.current["pauseSequence"] == sequence + 1
        assert not dialog.session["flow"].get("pendingCommand")
        assert "'status': 'CONFIRMED'" in dialog.logs.toPlainText()
    finally:
        dialog.close()
        wait(lambda: not isValid(dialog))
        assert not runtime.operatorDebugManager.ownsResources()


def testWorkflowCommandQueuesBehindPollAndLocksUntilPostAckState(runtime, monkeypatch):
    dialog = WorkflowDebugWindow(RuntimeClient(runtime), nestedProject(), "main")
    dialog.show()
    entered, release, refreshed, releaseRefresh = (threading.Event() for _ in range(4))
    try:
        wait(lambda: dialog.startButton.isEnabled())
        QTest.mouseClick(dialog.startButton, Qt.LeftButton)
        wait(lambda: dialog.current is not None and dialog.buttons["into"].isEnabled())
        sequence = dialog.pauseSequence
        snapshot = dialog.connection.flowSnapshot

        def heldSnapshot(sequence=0):
            value = snapshot(sequence)
            if not entered.is_set():
                entered.set()
                assert release.wait(5)
            elif not refreshed.is_set():
                refreshed.set()
                assert releaseRefresh.wait(5)
            return value

        monkeypatch.setattr(dialog.connection, "flowSnapshot", heldSnapshot)
        calls, _ = observeWorkflowCalls(dialog, monkeypatch)
        wait(entered.is_set)
        assert dialog.buttons["into"].isEnabled()
        for _ in range(4):
            QTest.mouseClick(dialog.buttons["into"], Qt.LeftButton)
        assert dialog.busy and not dialog.buttons["into"].isEnabled()
        assert not calls, "A user command must remain serialized behind the in-flight read"
        release.set()
        wait(refreshed.is_set)
        assert not dialog.busy and dialog.refreshPending
        assert dialog.pauseSequence == sequence and not dialog.buttons["into"].isEnabled()
        QTest.mouseClick(dialog.buttons["into"], Qt.LeftButton)
        releaseRefresh.set()
        wait(lambda: dialog.pauseSequence == sequence + 1 and dialog.current is not None
             and dialog.buttons["into"].isEnabled())
        processEventsFor(.35)
        assert len(calls) == 1
        assert json.loads(calls[0]["control_json"]) == {"action": "into", "pauseSequence": sequence}
        assert dialog.pauseSequence == sequence + 1
        assert not dialog.session["flow"].get("pendingCommand")
    finally:
        release.set()
        releaseRefresh.set()
        dialog.close()
        wait(lambda: not isValid(dialog))
        assert not runtime.operatorDebugManager.ownsResources()


def testWorkflowFastCommandCompletionCannotOvertakeQueuedPoll(runtime, monkeypatch):
    dialog = WorkflowDebugWindow(RuntimeClient(runtime), nestedProject(), "main")
    dialog.show()
    releaseOld, enteredFresh, releaseFresh = (threading.Event() for _ in range(3))
    try:
        wait(lambda: dialog.startButton.isEnabled())
        QTest.mouseClick(dialog.startButton, Qt.LeftButton)
        wait(lambda: dialog.current is not None and dialog.buttons["into"].isEnabled())
        dialog.timer.stop()
        wait(lambda: not dialog.polling)
        sequence = dialog.pauseSequence
        snapshot, submit = dialog.connection.flowSnapshot, dialog.pool.submit

        def oldRead():
            assert releaseOld.wait(5)
            return snapshot(dialog.sequence)

        assert dialog.submit("poll", oldRead)
        releaseOld.set()
        # The sentinel runs after the old poll's callback has queued its Qt signal.
        # Do not process GUI events until the next real command has completed.
        submit(lambda: None).result(timeout=5)
        assert dialog.polling and not dialog.busy
        fast = [True]

        def completeBeforeCallbackRegistration(work):
            future = submit(work)
            if fast[0]:
                fast[0] = False
                future.result(timeout=5)
            return future

        def heldFreshSnapshot(sequence=0):
            value = snapshot(sequence)
            enteredFresh.set()
            assert releaseFresh.wait(5)
            return value

        monkeypatch.setattr(dialog.pool, "submit", completeBeforeCallbackRegistration)
        monkeypatch.setattr(dialog.connection, "flowSnapshot", heldFreshSnapshot)
        refresh, refreshStates = dialog.refresh, []

        def observedRefresh():
            refreshStates.append((dialog.busy, dialog.polling, dialog.refreshPending, dialog.pauseSequence))
            refresh()

        monkeypatch.setattr(dialog, "refresh", observedRefresh)
        calls, _ = observeWorkflowCalls(dialog, monkeypatch)
        QTest.mouseClick(dialog.buttons["into"], Qt.LeftButton)
        # Even an already-finished Future must deliver completion through the queue.
        assert dialog.busy and dialog.polling and dialog.refreshPending
        wait(enteredFresh.is_set)
        assert (True, False, True, sequence) in refreshStates, "The older poll must observe the queued command's busy lock"
        assert not dialog.busy and dialog.refreshPending
        assert dialog.pauseSequence == sequence and not dialog.buttons["into"].isEnabled()
        releaseFresh.set()
        dialog.timer.start(250)
        wait(lambda: dialog.pauseSequence == sequence + 1 and dialog.current is not None
             and dialog.buttons["into"].isEnabled())
        assert len(calls) == 1
        assert json.loads(calls[0]["control_json"]) == {"action": "into", "pauseSequence": sequence}
    finally:
        releaseOld.set()
        releaseFresh.set()
        dialog.close()
        wait(lambda: not isValid(dialog))
        assert not runtime.operatorDebugManager.ownsResources()


def testWorkflowQueuedCommandKeepsExpiredPauseAndDoesNotRetry(runtime, monkeypatch):
    dialog = WorkflowDebugWindow(RuntimeClient(runtime), nestedProject(), "main")
    dialog.show()
    entered, release = threading.Event(), threading.Event()
    try:
        wait(lambda: dialog.startButton.isEnabled())
        QTest.mouseClick(dialog.startButton, Qt.LeftButton)
        wait(lambda: dialog.current is not None and dialog.buttons["into"].isEnabled())
        sequence = dialog.pauseSequence
        snapshot = dialog.connection.flowSnapshot

        def heldSnapshot(sequence=0):
            value = snapshot(sequence)
            if not entered.is_set():
                entered.set()
                assert release.wait(5)
            return value

        monkeypatch.setattr(dialog.connection, "flowSnapshot", heldSnapshot)
        wait(entered.is_set)
        # Move Runtime through the real protocol while its older read is in flight.
        dialog.connection.control("into", sequence)
        wait(lambda: snapshot()["session"]["flow"]["pauseSequence"] == sequence + 1)
        calls, _ = observeWorkflowCalls(dialog, monkeypatch)
        QTest.mouseClick(dialog.buttons["into"], Qt.LeftButton)
        release.set()
        wait(lambda: "E_DEBUG_STALE_PAUSE" in dialog.logs.toPlainText()
             and dialog.current is not None and dialog.buttons["into"].isEnabled())
        processEventsFor(.35)
        assert len(calls) == 1
        assert json.loads(calls[0]["control_json"])["pauseSequence"] == sequence
        assert dialog.pauseSequence == sequence + 1
        assert dialog.current["pauseSequence"] == sequence + 1
    finally:
        release.set()
        dialog.close()
        wait(lambda: not isValid(dialog))
        assert not runtime.operatorDebugManager.ownsResources()


def testWorkflowHistorySelectionDoesNotRetargetPausedControl(runtime, monkeypatch):
    dialog = WorkflowDebugWindow(RuntimeClient(runtime), nestedProject(), "main")
    dialog.show()
    try:
        wait(lambda: dialog.startButton.isEnabled())
        QTest.mouseClick(dialog.startButton, Qt.LeftButton)
        wait(lambda: dialog.current is not None and dialog.buttons["into"].isEnabled())
        previous = dialog.current
        QTest.mouseClick(dialog.buttons["into"], Qt.LeftButton)
        wait(lambda: dialog.current is not None and dialog.current["nodeId"] == "number"
             and dialog.buttons["out"].isEnabled())
        sequence = dialog.pauseSequence
        dialog.history.setCurrentIndex(dialog.history.findData(previous["snapshotId"]))
        wait(lambda: dialog.displayed["snapshotId"] == previous["snapshotId"]
             and dialog.buttons["out"].isEnabled())
        assert dialog.displayed["pauseSequence"] < sequence
        assert dialog.current["pauseSequence"] == sequence
        calls, _ = observeWorkflowCalls(dialog, monkeypatch)
        QTest.mouseClick(dialog.buttons["out"], Qt.LeftButton)
        wait(lambda: dialog.pauseSequence == sequence + 1 and dialog.current is not None
             and dialog.buttons["out"].isEnabled())
        assert len(calls) == 1
        assert json.loads(calls[0]["control_json"])["pauseSequence"] == sequence
        assert dialog.current["phase"] == "call.return"
    finally:
        dialog.close()
        wait(lambda: not isValid(dialog))
        assert not runtime.operatorDebugManager.ownsResources()


def testWorkflowTerminalSelectsResultAlreadyListedBeforeTerminalState(runtime):
    dialog = WorkflowDebugWindow(RuntimeClient(runtime), nestedProject(), "main")
    dialog.show()
    try:
        wait(lambda: dialog.startButton.isEnabled())
        QTest.mouseClick(dialog.startButton, Qt.LeftButton)
        wait(lambda: dialog.current is not None and dialog.buttons["continue"].isEnabled())
        dialog.timer.stop()
        wait(lambda: not dialog.polling and not dialog.busy)
        previous = dialog.current["snapshotId"]
        dialog.connection.control("continue", dialog.pauseSequence)
        wait(lambda: dialog.connection.flowSnapshot()["session"]["state"] == "SUCCEEDED")
        terminal = dialog.connection.flowSnapshot(dialog.sequence)
        result = terminal["session"]["flow"]["lastOutput"]
        # Replay the valid split: the real result snapshot is listed before the
        # following flow_terminal event changes state. No snapshot IDs change then.
        running = deepcopy(terminal)
        running["session"]["state"] = "RUNNING"
        running["session"]["flow"].pop("terminal")
        running["events"]["events"] = [event for event in running["events"]["events"] if event["type"] != "flow_terminal"]
        running["events"]["nextSequence"] = running["events"]["events"][-1]["sequence"]
        running["session"]["lastSequence"] = running["events"]["nextSequence"]
        terminal["events"]["events"] = [event for event in terminal["events"]["events"] if event["type"] == "flow_terminal"]
        dialog.completed("poll", running, None)
        assert dialog.history.findData(result) >= 0
        assert dialog.history.currentData() == previous
        dialog.completed("poll", terminal, None)
        assert dialog.history.currentData() == result
        wait(lambda: dialog.displayed is not None and dialog.displayed["phase"] == "workflow.result")
        assert dialog.displayed["outputs"] == {"value": 7}
        # A later read must preserve a deliberate history selection after success.
        dialog.history.setCurrentIndex(dialog.history.findData(previous))
        wait(lambda: dialog.displayed["snapshotId"] == previous and not dialog.busy)
        dialog.completed("poll", terminal, None)
        assert dialog.history.currentData() == previous
        assert dialog.displayed["snapshotId"] == previous
        assert not dialog.buttons["continue"].isEnabled()
    finally:
        dialog.close()
        wait(lambda: not isValid(dialog))
        assert not runtime.operatorDebugManager.ownsResources()


@pytest.mark.parametrize("stopButton", [False, True])
def testWorkflowCloseRetiresResourcesDuringReadOnlyPoll(runtime, monkeypatch, stopButton):
    dialog = WorkflowDebugWindow(RuntimeClient(runtime), nestedProject(), "main")
    dialog.show()
    entered, release = threading.Event(), threading.Event()
    try:
        wait(lambda: dialog.startButton.isEnabled())
        QTest.mouseClick(dialog.startButton, Qt.LeftButton)
        wait(lambda: dialog.current is not None and dialog.buttons["into"].isEnabled())
        snapshot = dialog.connection.flowSnapshot

        def heldSnapshot(sequence=0):
            value = snapshot(sequence)
            entered.set()
            assert release.wait(5)
            return value

        monkeypatch.setattr(dialog.connection, "flowSnapshot", heldSnapshot)
        wait(entered.is_set)
        if stopButton:
            QTest.mouseClick(dialog.end, Qt.LeftButton)
        else:
            dialog.close()
        wait(lambda: not isValid(dialog))
        assert not runtime.operatorDebugManager.ownsResources()
        assert dialog.connection.stop.is_set()
    finally:
        release.set()
        if isValid(dialog):
            dialog.close()
            wait(lambda: not isValid(dialog))


def testWorkflowLostAckStaysUncertainAfterPollWithoutResend(runtime, monkeypatch):
    dialog = WorkflowDebugWindow(RuntimeClient(runtime), nestedProject(), "main")
    dialog.show()
    try:
        wait(lambda: dialog.startButton.isEnabled())
        QTest.mouseClick(dialog.startButton, Qt.LeftButton)
        wait(lambda: dialog.current is not None and dialog.buttons["into"].isEnabled())
        calls, _ = observeWorkflowCalls(dialog, monkeypatch)
        call = dialog.connection.call

        def unavailableAck(method, **fields):
            if method == "GetWorkflowDebugCommand":
                raise RuntimeError("ACK transport unavailable")
            return call(method, **fields)

        monkeypatch.setattr(dialog.connection, "call", unavailableAck)
        QTest.mouseClick(dialog.buttons["into"], Qt.LeftButton)
        wait(lambda: dialog.connection.uncertain and not dialog.busy and not dialog.refreshPending)
        processEventsFor(.35)
        assert len(calls) == 1
        assert "E_DEBUG_UNCERTAIN" in dialog.logs.toPlainText()
        assert not any(button.isEnabled() for button in dialog.buttons.values())
        QTest.mouseClick(dialog.end, Qt.LeftButton)
        wait(lambda: not isValid(dialog))
        assert not runtime.operatorDebugManager.ownsResources()
    finally:
        if isValid(dialog):
            dialog.close()
            wait(lambda: not isValid(dialog))


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
