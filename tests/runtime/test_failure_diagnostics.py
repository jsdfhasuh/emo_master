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


def testOversizedPresentationDiagnosticsKeepFailedResultAndReason():
    identity = dict(jobId="job", resultKey="failed-result", resultOrdinal=3,
                    invocationId="failed-invocation", resultScopeId="scope")
    source = SimpleNamespace(sourceId="image", state="UNAVAILABLE",
                             reasonCode="EXPORT_TIMEOUT", reason="snapshot wait expired")
    result = SimpleNamespace(status="INCOMPLETE", executionTerminal="COMPLETED",
        identity=SimpleNamespace(model_dump=lambda: identity), expectedSourceIds=("image",), sources=(source,))
    # Repeated closure/export records can exceed the output budget even when
    # every retained record is bounded; keep the failed result, not just job ID.
    exports = [{"jobId": "job", "sourceId": "image", "stage": "snapshot_wait",
                **{name: 1234567890 for name in (
                    "captureAcquiredNs", "submittedNs", "ownerStartedNs", "dispatchNs",
                    "snapshotwaitStartedNs", "snapshotwaitEndedNs", "snapshotReplyNs",
                    "copiedNs", "encodeStartedNs", "encodedNs", "stagedNs", "replyNs",
                    "adoptionStartedNs", "adoptionEndedNs", "retirementEndedNs")}}
               for _ in range(16)]
    owner = SimpleNamespace(exporter=SimpleNamespace(diagnostics=exports),
        diagnostics=[{"identity": identity, "exports": exports} for _ in range(8)])
    runtime = SimpleNamespace(_presentationOwner=owner, jobRepository=SimpleNamespace(
        _jobs={"job": SimpleNamespace(status="RUNNING", errorCode="", message="")}
    ))
    text = jobFailureDetails(runtime, "job", result=result)
    assert len(text) <= 16384
    details = json.loads(text)
    assert details["record"]["status"] == "RUNNING"
    assert details["result"]["status"] == "INCOMPLETE"
    assert details["result"]["identity"] == identity
    assert details["result"]["sources"] == [dict(id="image", state="UNAVAILABLE",
        reason="EXPORT_TIMEOUT", detail="snapshot wait expired")]
    assert "budget" in details["diagnostic_error"]


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
