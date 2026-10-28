"""Designer reads and cleanup follow a reset without replaying execution."""
from types import SimpleNamespace

import pytest

from emo_master.apps.designer.operator_editors import EditorKey
from emo_master.apps.designer.services.operator_debug import DebugConnection
from emo_master.apps.designer.services.runtime_client import RuntimeClient, RuntimeClientError
from tests.runtime.operator_debug_fixture import project
from tests.runtime.test_operator_debug_rpc import runtime as runtime
from tests.runtime.test_operator_debug_sessions import waitFor


def opened(runtime):
    connection = DebugConnection(RuntimeClient(runtime))
    connection.open(project("vision.value.number"), EditorKey("draft", "main", "node"), "vision.value.number")
    waitFor(lambda: connection.snapshot()["session"]["state"] == "READY")
    return connection


def resetAndWait(connection, call):
    connection.mutation("ResetOperatorDebugSession")
    return waitFor(lambda: (state if (state := call("GetOperatorDebugSession"))["generation"] == 2 else None))


def testSnapshotRefreshesWhenResetFinishesBetweenSessionAndEvents(runtime, monkeypatch):
    connection = opened(runtime)
    call = connection.call
    calls, reset = [], False

    def resetBetweenReads(method, **fields):
        nonlocal reset
        calls.append(method)
        reply = call(method, **fields)
        if method == "GetOperatorDebugSession" and not reset:
            reset = True
            resetAndWait(connection, call)
        return reply

    monkeypatch.setattr(connection, "call", resetBetweenReads)
    try:
        snapshot = connection.snapshot()
        assert snapshot["session"]["generation"] == connection.identity["generation"] == 2
        assert snapshot["session"]["state"] == "READY"
        assert snapshot["result"] is None
        assert snapshot["events"]["events"][-1]["generation"] == 2
        assert calls == ["GetOperatorDebugSession", "ResetOperatorDebugSession", "ReadOperatorDebugEvents",
                         "GetOperatorDebugSession", "ReadOperatorDebugEvents"]
    finally:
        # Keep the pre-fix failure about the raced read, not a second teardown error.
        monkeypatch.setattr(connection, "call", call)
        state = call("GetOperatorDebugSession")
        connection.identity["generation"] = state["generation"]
        connection.close()
    assert not runtime.operatorDebugManager.ownsResources()


def testCloseRefreshesCompletedResetWithoutAnInterveningPoll(runtime):
    connection = opened(runtime)
    try:
        resetAndWait(connection, connection.call)
        assert connection.identity["generation"] == 1
        closed = connection.close()
        assert not closed["resourcesHeld"]
        assert closed["generation"] == connection.identity["generation"] == 2
        assert not runtime.operatorDebugManager.ownsResources()
    finally:
        # Also retire the pre-fix stale connection so the regression has no leak.
        state = connection.call("GetOperatorDebugSession")
        connection.identity["generation"] = state["generation"]
        connection.close()


@pytest.mark.parametrize("operation", ["snapshot", "close"])
@pytest.mark.parametrize("failure", ["expired", "transport", "stale", "runtime_changed"])
def testRefreshIsBoundedAndDoesNotReplayOtherFailures(operation, failure, monkeypatch):
    connection = DebugConnection(SimpleNamespace(runtimeService=object()))
    identity = dict(runtime_instance_id="pinned-runtime", session_id="pinned-session", generation=1)
    connection.identity = dict(identity)
    calls = []
    error = (RuntimeError("reply lost") if failure == "transport" else
             RuntimeClientError("E_DEBUG_RESULT_EXPIRED" if failure == "expired" else
                                "E_DEBUG_STALE_SESSION", failure))

    def failing(method, **fields):
        calls.append((method, fields))
        if method == "GetOperatorDebugSession" and failure != "runtime_changed":
            return dict(generation=1)
        raise error

    monkeypatch.setattr(connection, "call", failing)
    with pytest.raises(type(error)) as caught:
        getattr(connection, operation)()
    assert caught.value is error
    attempts = 2 if failure == "stale" else 1
    target = "ReadOperatorDebugEvents" if operation == "snapshot" else "CloseOperatorDebugSession"
    if failure == "runtime_changed" and operation == "snapshot":
        attempts = 0
    requests = [fields for method, fields in calls if method == target]
    assert len(requests) == attempts
    if operation == "close":
        assert len({request["request_id"] for request in requests}) == 1
    assert connection.identity == identity
    assert all(method in {"GetOperatorDebugSession", target} for method, _ in calls)
