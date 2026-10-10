"""Startup must confirm the visible breakpoint configuration before execution."""
import json
import threading

import pytest

import emo_master  # noqa: F401
from PySide2.QtCore import Qt
from PySide2.QtTest import QTest
from shiboken2 import isValid

from emo_master.apps.designer.services.runtime_client import RuntimeClient
from emo_master.apps.designer.ui.workflow_debug_window import WorkflowDebugWindow
from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb
from tests.designer.test_operator_debug_window import wait
from tests.designer.test_workflow_debug_window import processEventsFor
from tests.runtime.test_operator_debug_rpc import runtime as runtime
from tests.runtime.test_workflow_debug_control import nestedProject


def closeWindow(dialog, runtime):
    if isValid(dialog):
        dialog.close()
        wait(lambda: not isValid(dialog))
    assert not runtime.operatorDebugManager.ownsResources()


def breakpointRow(dialog, workflow="child", node="number"):
    return next(row for row in dialog.breakpointRows if row[:2] == (workflow, node))


@pytest.mark.parametrize("condition,hitCount,expectedHits", [("", 1, 1), ("", 2, 2), ("hits == 2", 1, 2)])
def testCheckedBreakpointsApplyBeforeStart(runtime, condition, hitCount, expectedHits):
    dialog = WorkflowDebugWindow(RuntimeClient(runtime), nestedProject(), "main")
    dialog.show()
    try:
        wait(lambda: dialog.startButton.isEnabled())
        row = breakpointRow(dialog)
        row[2].setChecked(True)
        row[3].setText(condition)
        row[4].setValue(hitCount)
        # The very first executable node must also be counted, not just later nodes.
        breakpointRow(dialog, "main", "first")[2].setChecked(True)
        QTest.mouseClick(dialog.startButton, Qt.LeftButton)
        wait(lambda: dialog.current is not None and dialog.buttons["continue"].isEnabled())
        assert dialog.current["hits"]["main/first"] == 1
        assert dialog.current["reason"] == "breakpoint"
        assert '已由 Worker 确认应用' in dialog.breakpointStatus.text()
        row[4].setValue(hitCount + 1)
        assert '未提交修改' in dialog.breakpointStatus.text()
        row[4].setValue(hitCount)
        assert '已由 Worker 确认应用' in dialog.breakpointStatus.text()
        initial = dialog.pauseSequence
        QTest.mouseClick(dialog.buttons["continue"], Qt.LeftButton)
        wait(lambda: dialog.state == "SUCCEEDED" or (
            dialog.pauseSequence > initial and dialog.current is not None
            and dialog.buttons["continue"].isEnabled()))
        assert dialog.state == "PAUSED"
        assert dialog.current["nodeId"] == "number"
        assert dialog.current["reason"] == "breakpoint"
        assert dialog.current["hits"]["child/number"] == expectedHits
        assert runtime.loadedDocument is None and runtime.jobRepository.all() == []
    finally:
        closeWindow(dialog, runtime)


def testRejectedStartupBreakpointsDoNotStartAndCanBeCorrected(runtime, monkeypatch):
    dialog = WorkflowDebugWindow(RuntimeClient(runtime), nestedProject(), "main")
    dialog.show()
    methods = []
    call = dialog.connection.call

    def record(method, **fields):
        methods.append(method)
        return call(method, **fields)

    monkeypatch.setattr(dialog.connection, "call", record)
    try:
        wait(lambda: dialog.startButton.isEnabled())
        row = breakpointRow(dialog)
        row[2].setChecked(True)
        row[3].setText("forbidden()")
        QTest.mouseClick(dialog.startButton, Qt.LeftButton)
        wait(lambda: not dialog.busy and not dialog.refreshPending and dialog.session)
        assert "StartWorkflowDebug" not in methods
        assert dialog.state == "READY" and dialog.startButton.isEnabled()
        assert "E_DEBUG_CONDITION" in dialog.logs.toPlainText()
        assert not dialog.session["flow"].get("started")
        assert not dialog.session["snapshots"]
        row[3].setText("")
        QTest.mouseClick(dialog.startButton, Qt.LeftButton)
        wait(lambda: dialog.current is not None and dialog.buttons["continue"].isEnabled())
        assert methods.count("StartWorkflowDebug") == 1
    finally:
        closeWindow(dialog, runtime)


@pytest.mark.parametrize("closeDuringAck", [False, True])
def testStartupWaitsForBreakpointAckAndCannotBeRepeated(runtime, monkeypatch, closeDuringAck):
    dialog = WorkflowDebugWindow(RuntimeClient(runtime), nestedProject(), "main")
    dialog.show()
    entered, release = threading.Event(), threading.Event()
    mutations, pointId = [], []
    call = dialog.connection.call

    def holdAck(method, **fields):
        if method in {"ControlWorkflowDebug", "StartWorkflowDebug"}:
            mutations.append((method, fields))
        if method == "ControlWorkflowDebug":
            pointId.append(fields["request_id"])
        if method == "GetWorkflowDebugCommand" and fields["request_id"] in pointId:
            entered.set()
            assert release.wait(5)
        return call(method, **fields)

    try:
        wait(lambda: dialog.startButton.isEnabled())
        monkeypatch.setattr(dialog.connection, "call", holdAck)
        row = breakpointRow(dialog)
        row[2].setChecked(True)
        QTest.mouseClick(dialog.startButton, Qt.LeftButton)
        wait(entered.is_set)
        assert not dialog.startButton.isEnabled()
        assert not dialog.breakpoints.isEnabled()
        assert not any(button.isEnabled() for button in dialog.buttons.values())
        QTest.mouseClick(dialog.startButton, Qt.LeftButton)
        assert len(mutations) == 1
        if closeDuringAck:
            closeWindow(dialog, runtime)
            release.set()
            dialog.pool.shutdown(wait=True)
            assert len(mutations) == 1
            return
        release.set()
        wait(lambda: dialog.current is not None and dialog.buttons["continue"].isEnabled())
        assert [method for method, _ in mutations] == ["ControlWorkflowDebug", "StartWorkflowDebug"]
        sent = json.loads(mutations[0][1]["control_json"])
        assert sent["breakpoints"] == [dict(workflowId="child", nodeId="number", enabled=True,
                                            condition="", hitCount=1)]
    finally:
        release.set()
        closeWindow(dialog, runtime)


def testLostStartupBreakpointAckDoesNotStartOrRetry(runtime, monkeypatch):
    dialog = WorkflowDebugWindow(RuntimeClient(runtime), nestedProject(), "main")
    dialog.show()
    mutations = []
    call = dialog.connection.call

    def lostAck(method, **fields):
        if method in {"ControlWorkflowDebug", "StartWorkflowDebug"}:
            mutations.append(method)
        if method == "GetWorkflowDebugCommand":
            raise RuntimeError("startup ACK unavailable")
        return call(method, **fields)

    try:
        wait(lambda: dialog.startButton.isEnabled())
        monkeypatch.setattr(dialog.connection, "call", lostAck)
        breakpointRow(dialog)[2].setChecked(True)
        QTest.mouseClick(dialog.startButton, Qt.LeftButton)
        wait(lambda: dialog.connection.uncertain and not dialog.busy and not dialog.refreshPending)
        processEventsFor(.35)
        assert mutations == ["ControlWorkflowDebug"]
        assert dialog.state == "READY" and not dialog.session["flow"].get("started")
        assert not dialog.startButton.isEnabled()
        assert not dialog.breakpoints.isEnabled()
        assert "E_DEBUG_UNCERTAIN" in dialog.logs.toPlainText()
    finally:
        closeWindow(dialog, runtime)


@pytest.mark.parametrize("presetBreakpoint", [False, True])
def testLegacyRuntimeCanStartWithoutBreakpointsButRejectsUnsupportedPresets(runtime, presetBreakpoint):
    class LegacyRuntime:
        def __init__(self):
            self.starts = 0
            self.readyControls = 0

        def __getattr__(self, name):
            return getattr(runtime, name)

        def GetWorkflowDebugCapabilities(self, request, context):
            result = runtime.GetWorkflowDebugCapabilities(request, context)
            capability = json.loads(result.snapshot_json)
            capability.pop("preStartBreakpoints")
            result.snapshot_json = json.dumps(capability)
            return result

        def ControlWorkflowDebug(self, request, context):
            state = runtime.GetWorkflowDebugSession(request, context)
            if state.state == "READY":
                self.readyControls += 1
                return pb.OperatorDebugReply(ok=False, code="E_DEBUG_STALE_SESSION", message="workflow is not active")
            return runtime.ControlWorkflowDebug(request, context)

        def StartWorkflowDebug(self, request, context):
            self.starts += 1
            return runtime.StartWorkflowDebug(request, context)

    transport = LegacyRuntime()
    dialog = WorkflowDebugWindow(RuntimeClient(transport), nestedProject(), "main")
    dialog.show()
    try:
        wait(lambda: dialog.startButton.isEnabled())
        row = breakpointRow(dialog)
        row[2].setChecked(presetBreakpoint)
        QTest.mouseClick(dialog.startButton, Qt.LeftButton)
        if presetBreakpoint:
            wait(lambda: not dialog.busy and not dialog.refreshPending and dialog.session)
            assert transport.starts == 0 and dialog.state == "READY"
            assert "升级 Runtime" in dialog.logs.toPlainText()
            assert not dialog.connection.uncertain and dialog.startButton.isEnabled()
            row[2].setChecked(False)
            QTest.mouseClick(dialog.startButton, Qt.LeftButton)
        wait(lambda: dialog.current is not None and dialog.buttons["continue"].isEnabled())
        assert transport.starts == 1 and transport.readyControls == 0
        assert not dialog.connection.preStartBreakpoints
    finally:
        closeWindow(dialog, runtime)
