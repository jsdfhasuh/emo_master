from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import queue
import time
from types import SimpleNamespace

import pytest

from emo_master.apps.runtime.operator_debug.contracts import DebugError, MAX_REQUEST_BYTES, encode, parse
from emo_master.apps.runtime.operator_debug.manager import OperatorDebugManager, Session
from tests.runtime.operator_debug_fixture import admitted, project


def waitFor(predicate, seconds=15):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(.02)
    raise AssertionError("debug state did not settle")


@pytest.fixture
def manager():
    owner = OperatorDebugManager("runtime", admitted())
    try:
        yield owner
    finally:
        owner.close()
        assert not owner.ownsResources()
        assert not owner.monitor.is_alive()


def opening(**changes):
    return dict(runtimeInstanceId="runtime", openRequestId="open", projectJson=json.dumps(project()),
                projectId="draft", workflowId="main", nodeId="node", operatorId="test.probe", **changes)


def ready(manager):
    reply = manager.call("open", opening())
    request = dict(runtimeInstanceId="runtime", sessionId=reply["sessionId"], generation=1)
    waitFor(lambda: manager.call("get", request)["state"] == "READY")
    return request


def prepare(manager, identity):
    return manager.call("prepare", dict(identity, requestId="inputs", inputs={}))['inputSetId']


def execute(manager, identity, inputSetId, requestId="execute", mode="ok", **changes):
    return manager.call("execute", dict(identity, requestId=requestId, inputSetId=inputSetId,
                                         paramsJson=json.dumps({"mode": mode}), **changes))


def result(manager, identity, executionId):
    def terminal():
        value = manager.call("execution", dict(identity, executionId=executionId))
        return value if value["status"] != "RUNNING" else None
    return waitFor(terminal)


def testSpawnLifecycleDedupResetAndGenerationFence(manager):
    identity = ready(manager)
    inputs = prepare(manager, identity)
    first = execute(manager, identity, inputs)
    assert result(manager, identity, first["executionId"])["outputs"] == {"value": 1}
    assert execute(manager, identity, inputs) == first
    with pytest.raises(DebugError, match="different content"):
        execute(manager, identity, inputs, mode="error")
    second = execute(manager, identity, inputs, requestId="second")
    assert result(manager, identity, second["executionId"])["outputs"] == {"value": 2}
    resetRequest = dict(identity, requestId="reset")
    reset = manager.call("reset", resetRequest)
    assert reset["resourcesHeld"] and reset["generation"] == 1
    waitFor(lambda: manager.call("get", identity)["generation"] == 2)
    assert manager.call("reset", resetRequest) == reset
    with pytest.raises(DebugError) as error:
        manager.call("renew", identity)
    assert error.value.code == "E_DEBUG_STALE_SESSION"
    identity["generation"] = 2
    newInputs = manager.call("prepare", dict(identity, requestId="inputs2", inputs={}))['inputSetId']
    third = execute(manager, identity, newInputs, requestId="third")
    assert result(manager, identity, third["executionId"])["outputs"] == {"value": 1}
    events = manager.call("events", dict(identity, afterSequence=0))
    assert any(e["type"] == "node.log" for e in events["events"])


def testConcurrentReplayCallsInvokeOnlyOnce(manager):
    identity = ready(manager)
    inputs = prepare(manager, identity)
    with ThreadPoolExecutor(8) as pool:
        replies = list(pool.map(lambda _: execute(manager, identity, inputs), range(16)))
    assert len({reply["executionId"] for reply in replies}) == 1
    assert result(manager, identity, replies[0]["executionId"])["outputs"] == {"value": 1}


@pytest.mark.parametrize("mode,status", [("ok", "SUCCEEDED"), ("error", "FAILED"), ("large", "FAILED"), ("crash", "UNKNOWN")])
def testRealWorkerResultsAndCrash(manager, mode, status):
    identity = ready(manager)
    reply = execute(manager, identity, prepare(manager, identity), mode=mode)
    outcome = result(manager, identity, reply["executionId"])
    assert outcome["status"] == status
    if mode == "large":
        assert outcome["code"] == "E_DEBUG_LIMIT"
    if mode == "crash":
        waitFor(lambda: not manager.ownsResources())
        assert manager.call("get", identity)["state"] == "FAULTED"


@pytest.mark.parametrize("mode,timeout,status", [("wait", False, "CANCELLED"), ("wait", True, "TIMED_OUT"),
                                               ("ignore", False, "UNKNOWN"), ("ignore", True, "UNKNOWN")])
def testCancellationAndDeadlinesRetireUncooperativeWorkers(manager, mode, timeout, status):
    identity = ready(manager)
    reply = execute(manager, identity, prepare(manager, identity), mode=mode, timeoutMs=100 if timeout else 30000)
    if not timeout:
        waitFor(lambda: any(e["type"] == "node.log" and e["event"]["message"] == "invoked"
                           for e in manager.call("events", identity)["events"]))
        manager.call("cancel", dict(identity, requestId="cancel", executionId=reply["executionId"]))
    assert result(manager, identity, reply["executionId"])["status"] == status
    if mode == "ignore":
        waitFor(lambda: not manager.ownsResources())


@pytest.mark.parametrize("mode", ["dispose_error", "dispose_hang"])
def testCleanupFailureDoesNotPretendToBeCleanClose(manager, mode):
    identity = ready(manager)
    reply = execute(manager, identity, prepare(manager, identity), mode=mode)
    result(manager, identity, reply["executionId"])
    request = dict(identity, requestId="close")
    accepted = manager.call("close", request)
    assert accepted["resourcesHeld"]
    waitFor(lambda: not manager.ownsResources())
    assert manager.call("get", identity)["state"] == "FAULTED"
    assert manager.call("close", request) == accepted


def testLeaseExpiryWithoutAnySubsequentClientRequest(manager):
    identity = ready(manager)
    with manager.lock:
        manager.sessions[identity["sessionId"]].expiresAt = time.monotonic() + .1
    waitFor(lambda: not manager.ownsResources())
    view = manager.call("get", identity)
    assert view["code"] == "E_DEBUG_SESSION_EXPIRED", view
    with pytest.raises(DebugError):
        manager.call("renew", identity)


def testRenewalIndependentOfLongExecution(manager):
    identity = ready(manager)
    manager.leaseSeconds = .2
    reply = execute(manager, identity, prepare(manager, identity), mode="wait")
    for _ in range(5):
        time.sleep(.08)
        assert manager.call("renew", identity)["resourcesHeld"]
    manager.call("cancel", dict(identity, requestId="cancel", executionId=reply["executionId"]))
    assert result(manager, identity, reply["executionId"])["status"] == "CANCELLED"


def testHeartbeatContentionKeepsLastSuccessfulSample(manager, monkeypatch):
    from emo_master.apps.runtime.jobs.heartbeat import monotonicMs
    identity = ready(manager)
    with manager.lock:
        session = manager.sessions[identity["sessionId"]]
        worker = session.worker
        worker.started = monotonicMs()-60000
        sample = monotonicMs()
        monkeypatch.setattr(worker.heartbeat, "read", lambda: sample)
        manager._tick(session)
        monkeypatch.setattr(worker.heartbeat, "read", lambda: None)
        manager._tick(session)
        assert session.state == "READY" and worker.lastHeartbeatMs == sample
        worker.lastHeartbeatMs = monotonicMs()-31000
        manager._tick(session)
        assert session.code == "E_DEBUG_HEARTBEAT_TIMEOUT"


def testResultEvictionNeverReplaysRequest(manager):
    manager.maxResults = 1
    identity = ready(manager)
    inputs = prepare(manager, identity)
    first = execute(manager, identity, inputs)
    result(manager, identity, first["executionId"])
    second = execute(manager, identity, inputs, requestId="second")
    result(manager, identity, second["executionId"])
    assert execute(manager, identity, inputs) == first
    with pytest.raises(DebugError) as error:
        manager.call("execution", dict(identity, executionId=first["executionId"]))
    assert error.value.code == "E_DEBUG_RESULT_EXPIRED"


@pytest.mark.parametrize("raw", ['{"x":1,"x":2}', '{"x":NaN}', '{"x":1e999}', '['*34+'0'+']'*34])
def testStrictJsonRejectsAmbiguousOrUnboundedValues(raw):
    with pytest.raises(DebugError):
        parse(raw)


def testAdmissionFailureDoesNotStartAWorker(manager):
    request = opening()
    request["operatorId"] = "vision.io.sqlite_writer"
    request["projectJson"] = json.dumps(project(request["operatorId"]))
    with pytest.raises(DebugError) as error:
        manager.call("open", request)
    assert error.value.code == "E_DEBUG_UNSUPPORTED"
    assert not manager.sessions


def testOpenConflictCapacityAndRetiredTombstone(manager):
    identity = ready(manager)
    assert manager.call("open", opening())["sessionId"] == identity["sessionId"]
    changed = opening()
    changed["nodeId"] = "other"
    with pytest.raises(DebugError) as error:
        manager.call("open", changed)
    assert error.value.code == "E_DEBUG_REQUEST_CONFLICT"
    another = dict(opening(), openRequestId="another")
    with pytest.raises(DebugError) as error:
        manager.call("open", another)
    assert error.value.code == "E_RESOURCE_BUSY"
    manager.call("close", dict(identity, requestId="close"))
    waitFor(lambda: not manager.ownsResources())
    assert not manager.call("open", opening())["resourcesHeld"]
    manager.maxOpens = 1
    with pytest.raises(DebugError) as error:
        manager.call("open", another)
    assert error.value.code == "E_DEBUG_LIMIT"


def testFullRequestLedgerStillAllowsCancelAndClose(manager):
    manager.maxRequests = 2
    identity = ready(manager)
    reply = execute(manager, identity, prepare(manager, identity), mode="wait")
    with pytest.raises(DebugError) as error:
        manager.call("prepare", dict(identity, requestId="more-inputs", inputs={}))
    assert error.value.code == "E_DEBUG_LIMIT"
    manager.call("cancel", dict(identity, requestId="cancel", executionId=reply["executionId"]))
    manager.call("close", dict(identity, requestId="close"))
    waitFor(lambda: not manager.ownsResources())


def testResetWhileBusyIsRejectedWithoutAdvancingGeneration(manager):
    identity = ready(manager)
    execute(manager, identity, prepare(manager, identity), mode="wait")
    with pytest.raises(DebugError) as error:
        manager.call("reset", dict(identity, requestId="reset"))
    assert error.value.code == "E_RESOURCE_BUSY"
    assert manager.call("get", identity)["generation"] == 1


def testEventsReportEvictionGap(manager):
    identity = ready(manager)
    with manager.lock:
        session = manager.sessions[identity["sessionId"]]
        for index in range(1100):
            manager._event(session, "test", index=index)
    response = manager.call("events", dict(identity, afterSequence=0, limit=500))
    assert response["gap"] and len(response["events"]) == 100
    assert response["nextSequence"] == response["events"][-1]["sequence"]


@pytest.mark.parametrize("message", ["x" * (60 * 1024), "\u56fe" * (10 * 1024)], ids=["ascii", "escaped-unicode"])
def testLargeEventPagesStayBoundedAndAlwaysAdvance(manager, message):
    identity = ready(manager)
    with manager.lock:
        session = manager.sessions[identity["sessionId"]]
        cursor = session.sequence
        for _ in range(20):
            manager._event(session, "node.log", event={"message": message})
        last = session.sequence
    received = []
    while cursor < last:
        response = manager.call("events", dict(identity, afterSequence=cursor))
        assert response["events"] and response["nextSequence"] > cursor
        assert len(encode(response["events"], MAX_REQUEST_BYTES)) <= MAX_REQUEST_BYTES // 2
        encode(response, MAX_REQUEST_BYTES)
        received.extend(response["events"])
        cursor = response["nextSequence"]
    assert len(received) == 20
    assert all(event["event"]["message"] == message for event in received)
    assert [event["sequence"] for event in received] == list(range(last - 19, last + 1))


def testAlreadyExitedWorkerIsNotMarkedForcedWhileRetiringIpc(manager):
    now = time.monotonic()
    worker = SimpleNamespace(inbound=queue.Queue(), droppedLogs=0, fault="", started=now*1000,
        heartbeat=SimpleNamespace(read=lambda: now*1000), requestStop=lambda: None,
        terminate=lambda: False, retire=lambda: True)
    session = Session("already-exited", {}, state="CLOSING", worker=worker,
        expiresAt=now+60, retiringAt=now-4, closedAck=True)
    manager._tick(session)
    assert session.worker is None and session.state == "CLOSED"
    assert not session.forced


def testProcessStartFailureRetiresOwnerAndCannotReplay(manager):
    from emo_master.apps.runtime.operator_debug.worker import DebugWorker
    built = []

    def factory(spec):
        worker = DebugWorker(spec)
        def failure():
            raise OSError("synthetic process start failure")
        worker.process.start = failure
        built.append(worker)
        return worker

    manager.workerFactory = factory
    reply = manager.call("open", opening())
    waitFor(lambda: not manager.ownsResources())
    same = manager.call("open", opening())
    assert same["sessionId"] == reply["sessionId"] and len(built) == 1
    assert same["state"] == "FAULTED" and built[0].retired
    assert not Path(built[0].workspace.name).exists()


def testRetirementFailureKeepsAdmissionHeldUntilRetryConfirmsCleanup(manager):
    identity = ready(manager)
    worker = manager.sessions[identity["sessionId"]].worker
    original = worker.retire
    def failure():
        raise OSError("synthetic close handle failure")
    worker.retire = failure
    try:
        manager.call("close", dict(identity, requestId="close"))
        waitFor(lambda: manager.call("get", identity)["code"] == "E_RESOURCE_CLEANUP_FAILED")
        assert manager.ownsResources()
        with pytest.raises(DebugError) as error:
            manager.call("open", dict(opening(), openRequestId="other"))
        assert error.value.code == "E_RESOURCE_BUSY"
    finally:
        worker.retire = original
    waitFor(lambda: not manager.ownsResources())


def testCompletionPastDeadlineCannotWinByArrivingBeforeMonitorTick(manager):
    identity = ready(manager)
    reply = execute(manager, identity, prepare(manager, identity), mode="wait")
    with manager.lock:
        session = manager.sessions[identity["sessionId"]]
        manager._consume(session, dict(kind="result", executionId=reply["executionId"], status="SUCCEEDED",
            finishedAtMs=session.deadline*1000+1, outputs={"value": 1}))
    assert manager.call("execution", dict(identity, executionId=reply["executionId"]))["status"] == "TIMED_OUT"


def testChangedParametersCannotHideCleanupFailure(manager):
    identity = ready(manager)
    inputs = prepare(manager, identity)
    first = execute(manager, identity, inputs, mode="dispose_error")
    result(manager, identity, first["executionId"])
    second = execute(manager, identity, inputs, requestId="second")
    assert result(manager, identity, second["executionId"])["code"] == "E_RESOURCE_CLEANUP_FAILED"
    waitFor(lambda: not manager.ownsResources())
    assert manager.call("get", identity)["state"] == "FAULTED"


def testRepeatedOpenCloseRetiresProcessPipesThreadsAndWorkspace(manager):
    workers = []
    for index in range(5):
        opened = manager.call("open", dict(opening(), openRequestId=f"open-{index}"))
        identity = dict(runtimeInstanceId="runtime", sessionId=opened["sessionId"], generation=1)
        waitFor(lambda: manager.call("get", identity)["state"] == "READY")
        worker = manager.sessions[opened["sessionId"]].worker
        workers.append(worker)
        assert Path(worker.workspace.name).is_dir()
        manager.call("close", dict(identity, requestId="close"))
        waitFor(lambda: not manager.ownsResources())
        assert worker.retired and worker.processClosed
        assert not worker.terminate()
        assert not worker.reader.is_alive() and not worker.writer.is_alive()
        assert worker.commandPipe.closed and worker.eventPipe.closed
        assert not Path(worker.workspace.name).exists()
