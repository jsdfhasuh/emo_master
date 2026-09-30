from types import SimpleNamespace
import json
import threading
import pytest

from tests.runtime.runtime_test_utils import jobFailureDetails, waitForTerminal


def testJobFailureDiagnosticIsBoundedAndDoesNotReadPayloadOrCallRpc():
    events = [SimpleNamespace(sequence=index, eventType="node.failed", code="E_NODE",
        message="failure " * 200, payloadJson="PRIVATE_PAYLOAD_MUST_NOT_BE_READ") for index in range(100)]
    runtime = SimpleNamespace(jobRepository=SimpleNamespace(_jobs={"job": SimpleNamespace(
        status="FAILED", errorCode="E_HEARTBEAT_TIMEOUT", message="missed heartbeat", pid=42)}),
        eventStore=SimpleNamespace(_events={"job": events}),
        jobSupervisor=SimpleNamespace(_heartbeatSeen={"job"}, _heartbeat={"job": 123},
            _handles={}, _bridges={}, presentationErrors=["bounded"]),
        GetJobStatus=lambda *_: (_ for _ in ()).throw(AssertionError("must not query RPC")))
    text = jobFailureDetails(runtime, "job")
    assert len(text) <= 16384 and "PRIVATE_PAYLOAD" not in text
    value = json.loads(text)
    assert value["record"]["errorCode"] == "E_HEARTBEAT_TIMEOUT"
    assert value["heartbeat_seen"] is True
    assert [event["sequence"] for event in value["event_tail"]] == list(range(68, 100))


def testZeroTimeoutKeepsDeadlineAndExplainsMissingTerminal():
    import pytest
    runtime = SimpleNamespace(GetJobStatus=lambda *_: (_ for _ in ()).throw(AssertionError("no poll after deadline")))
    with pytest.raises(AssertionError, match="job did not reach a terminal state: job;"):
        waitForTerminal(runtime, "job", timeoutSeconds=0)


def testUnicodeDiagnosticBudgetKeepsValidJsonAndEventDenominator():
    events = [SimpleNamespace(sequence=index, eventType="类型" * 100, code="错误" * 100,
                              message="故障" * 200) for index in range(40)]
    value = jobFailureDetails(SimpleNamespace(eventStore=SimpleNamespace(_events={"job": events})), "job")
    assert len(value) <= 16384
    assert len(json.loads(value)["event_tail"]) == 32


def testFailureCapturesOwnerCallSitesWithoutLocalsOrFullPaths():
    ready, stop = threading.Event(), threading.Event()

    def blockedOwner():
        privatePayload = "PRIVATE_LOCAL_MUST_NOT_BE_READ"
        ready.set()
        stop.wait()
        assert privatePayload

    thread = threading.Thread(target=blockedOwner, name="diagnostic-owned-bridge")
    thread.start()
    try:
        assert ready.wait(2)
        runtime = SimpleNamespace(jobSupervisor=SimpleNamespace(_bridges={"job": thread}))
        text = jobFailureDetails(runtime, "job")
        assert len(text) <= 16384 and "PRIVATE_LOCAL" not in text
        frames = json.loads(text)["owner_stacks"]["bridge"]["frames"]
        assert 0 < len(frames) <= 12
        assert any(row["function"] == "blockedOwner" for row in frames)
        assert all(set(row) == {"file", "function", "line"} for row in frames)
        assert all("/" not in row["file"] and "\\" not in row["file"] for row in frames)
    finally:
        stop.set()
        thread.join(2)
        assert not thread.is_alive()


@pytest.mark.parametrize("staleRegistry", (False, True))
def testDeadOwnerCannotClaimReusedLiveThreadIdentity(monkeypatch, staleRegistry):
    old = threading.Thread(target=lambda: None)
    old.start()
    old.join(2)
    assert not old.is_alive()
    # Simulate the OS/Python reusing the old ID for this unrelated live thread.
    monkeypatch.setattr(old, "_ident", threading.get_ident())
    if staleRegistry:
        # Also exercise an owner exiting after the registry snapshot was taken.
        monkeypatch.setattr(threading, "enumerate", lambda: [old])
    runtime = SimpleNamespace(jobSupervisor=SimpleNamespace(_bridges={"job": old}),
                              _maintenanceThread=old, operationalLogWriter=SimpleNamespace(_thread=old))
    details = json.loads(jobFailureDetails(runtime, "job"))
    assert not details["owner_stacks"]
