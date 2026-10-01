"""Diagnostic observers preserve delivery, failures, ownership and bounded output."""
import json
import multiprocessing
import threading
from types import SimpleNamespace

import pytest

from scripts.r3_job_diagnostics import (
    DiagnosticQueue, FIELD, JobDiagnostics, SqliteKeeper, WORKER_FIELDS,
    diagnosticWorker, eventDetails, stackSnapshot,
)


def spawnTarget(_spec, _cancel, queue):
    queue.put({"eventType": "job.completed"})
    queue.close()
    queue.join_thread()


def testQueueAdapterPreservesArgumentsValuesErrorsAndMissingMethods():
    calls = []
    marker = object()
    failure = RuntimeError("unchanged failure")

    def put(*args, **kwargs):
        calls.append((args, kwargs))
        if kwargs.get("block") is False:
            raise failure
        return marker

    original = SimpleNamespace(put=put, sentinel=marker)
    shared = [0] * len(WORKER_FIELDS)
    queue = DiagnosticQueue(original, shared)
    event = {"eventType": "job.completed", "payload": marker}
    assert queue.put(event, True, timeout=3) is marker
    assert calls == [((event, True), {"timeout": 3})]
    assert calls[0][0][0] is event
    assert queue.sentinel is marker
    assert getattr(queue, "close", None) is None
    assert getattr(queue, "join_thread", None) is None
    with pytest.raises(RuntimeError) as raised:
        queue.put(event, block=False)
    assert raised.value is failure
    assert shared[FIELD["main_put_entered"]] == 2
    assert shared[FIELD["main_put_returned"]] == 1
    assert shared[FIELD["terminal_put_return_ns"]] > 0


def testWorkerAdapterIsSpawnPicklableAndReallyFlushesItsQueue():
    context = multiprocessing.get_context("spawn")
    shared = context.Array("q", len(WORKER_FIELDS), lock=False)
    queue = context.Queue()
    process = context.Process(target=diagnosticWorker, args=(None, None, queue),
                              kwargs={"target": spawnTarget, "shared": shared})
    try:
        process.start()
        assert queue.get(timeout=10) == {"eventType": "job.completed"}
        process.join(timeout=10)
        assert not process.is_alive() and process.exitcode == 0
        assert 0 < shared[FIELD["target_enter_ns"]] <= shared[FIELD["terminal_put_return_ns"]]
        assert shared[FIELD["close_enter_ns"]] <= shared[FIELD["close_return_ns"]]
        assert shared[FIELD["join_enter_ns"]] <= shared[FIELD["join_return_ns"]] <= shared[FIELD["target_return_ns"]]
        assert shared[FIELD["target_error"]] == 0
        assert shared[FIELD["main_put_entered"]] == shared[FIELD["main_put_returned"]] == 1
    finally:
        if process.pid is not None:
            if process.is_alive():
                process.kill()
            process.join(timeout=10)
            assert not process.is_alive()
            process.close()
        queue.close()
        queue.join_thread()


def testFeederJoinInProgressIsVisibleWithoutPretendingTargetReturned():
    entered, release = threading.Event(), threading.Event()
    shared = [0] * len(WORKER_FIELDS)

    def join():
        entered.set()
        assert release.wait(3)

    original = SimpleNamespace(put=lambda event: None, close=lambda: None, join_thread=join)
    thread = threading.Thread(target=diagnosticWorker, args=(None, None, original),
                              kwargs={"target": spawnTarget, "shared": shared})
    thread.start()
    try:
        assert entered.wait(2)
        assert shared[FIELD["terminal_put_return_ns"]] > 0
        assert shared[FIELD["join_enter_ns"]] > 0
        assert shared[FIELD["join_return_ns"]] == shared[FIELD["target_return_ns"]] == 0
    finally:
        release.set()
        thread.join(timeout=3)
        assert not thread.is_alive()
    assert shared[FIELD["join_return_ns"]] > 0


def testWorkerFailurePreservesExceptionAndNeverClaimsNormalReturn():
    failure = ValueError("same exception")
    shared = [0] * len(WORKER_FIELDS)

    def target(*_args):
        raise failure

    with pytest.raises(ValueError) as raised:
        diagnosticWorker(None, None, object(), target=target, shared=shared)
    assert raised.value is failure
    assert shared[FIELD["target_error"]] == 1
    assert shared[FIELD["target_return_ns"]] == 0


def testBrokenDiagnosticCellsDoNotPreventQueueOperationsOrChangeResults():
    class BrokenCells:
        def __getitem__(self, _key):
            raise RuntimeError("diagnostic read failed")

        def __setitem__(self, _key, _value):
            raise RuntimeError("diagnostic write failed")

    calls = []
    marker = object()
    original = SimpleNamespace(put=lambda event: calls.append(event) or marker,
                               close=lambda: calls.append("close"),
                               join_thread=lambda: calls.append("join"))
    queue = DiagnosticQueue(original, BrokenCells())
    event = {"eventType": "job.completed"}
    assert queue.put(event) is marker
    queue.close()
    queue.join_thread()
    assert calls == [event, "close", "join"]


@pytest.mark.parametrize("fail_original", [False, True])
def testDiagnosticCallbackAndCounterFailuresPreserveExactlyOneOriginalCall(tmp_path, fail_original):
    probe = JobDiagnostics(tmp_path / "diagnostic.json", source={}, plannedSeconds=5)
    calls = []
    result, failure = object(), RuntimeError("original failure")

    def operation():
        calls.append("original")
        if fail_original:
            raise failure
        return result

    def bad_details():
        raise ValueError("diagnostic details failed")

    def bad_returned(*_args):
        raise LookupError("diagnostic returned callback failed")

    owner = SimpleNamespace(operation=operation)
    probe.wrap(owner, "operation", "formal.sqlite_append", details=bad_details, returned=bad_returned)
    # Break only diagnostic accounting, leaving the original operation intact.
    del probe.stages["formal.sqlite_append"]
    try:
        if fail_original:
            with pytest.raises(RuntimeError) as raised:
                owner.operation()
            assert raised.value is failure
        else:
            assert owner.operation() is result
            assert "LookupError" in probe.errors
        assert calls == ["original"]
        assert "ValueError" in probe.errors and "KeyError" in probe.errors
    finally:
        probe.close()


def testStageCountersExposeInFlightWaitAndPreserveFailureAndReturn(tmp_path):
    probe = JobDiagnostics(tmp_path / "diagnostic.json", source={"head": "test"}, plannedSeconds=5)
    entered, release = threading.Event(), threading.Event()
    result = object()

    def operation():
        entered.set()
        assert release.wait(3)
        return result

    owner = SimpleNamespace(operation=operation)
    probe.wrap(owner, "operation", "terminal.callback")
    returned = []
    thread = threading.Thread(target=lambda: returned.append(owner.operation()))
    thread.start()
    try:
        assert entered.wait(2)
        probe.capture("before_cleanup:TimeoutError", stacks=False)
        current = json.loads(probe.path.read_text())["snapshots"][-1]
        assert current["active"][0]["stage"] == "terminal.callback"
        assert current["active"][0]["elapsed_ns"] >= 0
        assert current["stages"]["terminal.callback"]["returned"] == 0
        release.set()
        thread.join(timeout=3)
        assert not thread.is_alive() and returned == [result]
        failure = RuntimeError("keep exact failure")

        def fail():
            raise failure

        owner.fail = fail
        probe.wrap(owner, "fail", "formal.sqlite_append")
        with pytest.raises(RuntimeError) as raised:
            owner.fail()
        assert raised.value is failure
        probe.capture("after_operation", stacks=False)
        current = json.loads(probe.path.read_text())["snapshots"][-1]
        row = current["stages"]["terminal.callback"]
        assert row["entered"] == row["returned"] == 1 and row["failed"] == 0
        assert row["wall_ns"] >= row["thread_cpu_ns"] >= 0
        assert current["stages"]["formal.sqlite_append"]["failed"] == 1
        assert not current["active"]
    finally:
        release.set()
        thread.join(timeout=3)
        probe.close()
    assert owner.operation is operation
    assert json.loads(probe.path.read_text())["observer_retired"]


def testSnapshotUsesCachedFrontiersWithoutProductionLocksOrQueries(tmp_path):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("production lock or query was called")

    probe = JobDiagnostics(tmp_path / "diagnostic.json", source={}, plannedSeconds=5)
    probe.jobId = "job"
    supervisor = SimpleNamespace(_handles={}, _bridges={}, _terminalEvents=set(),
        _heartbeat={"job": 10}, _heartbeatMonotonic={"job": 20},
        _heartbeatCells={"job": SimpleNamespace(read=lambda: 30)}, _lock=SimpleNamespace(acquire=forbidden),
        getProcess=forbidden, ownsJobResources=forbidden)
    probe.runtime = SimpleNamespace(jobSupervisor=supervisor,
        jobRepository=SimpleNamespace(_jobs={"job": SimpleNamespace(status="RUNNING", errorCode="", endedAtMs=0)}, get=forbidden),
        eventStore=SimpleNamespace(_sequences={"job": 99}, _terminalSequences={}, read=forbidden),
        sqliteStore=SimpleNamespace(getJob=forbidden, listJobEventsAfter=forbidden))
    try:
        probe.capture("before_cleanup:TimeoutError", stacks=False)
        row = json.loads(probe.path.read_text())["snapshots"][-1]
        assert row["state"]["job_status"] == "RUNNING"
        assert row["state"]["event_store_sequence"] == 99
        assert row["state"]["heartbeat_cell_ms"] == 30
        assert not probe.errors
    finally:
        probe.close()


def testDiagnosticRetirementDoesNotRestoreAnAlreadyRetiredCallback(tmp_path):
    probe = JobDiagnostics(tmp_path / "diagnostic.json", source={}, plannedSeconds=5)
    owner = SimpleNamespace(callback=lambda: "original")
    probe.wrap(owner, "callback", "terminal.callback")
    def retired():
        return "retired"
    owner.callback = retired
    probe.close()
    assert owner.callback is retired


def testSnapshotFailureCannotReplaceTheOriginalRuntimeFailure(tmp_path):
    probe = JobDiagnostics(tmp_path / "diagnostic.json", source={}, plannedSeconds=5)
    failure = RuntimeError("original runtime failure")

    def broken_snapshot():
        raise ValueError("diagnostic snapshot failed")

    probe._state = broken_snapshot
    try:
        with pytest.raises(RuntimeError) as raised:
            try:
                raise failure
            except BaseException:
                probe.capture("before_cleanup:RuntimeError")
                raise
        assert raised.value is failure
        assert "ValueError" in probe.errors
    finally:
        probe.close()


def testBoundedSnapshotsExcludePayloadsAndFileContents(tmp_path):
    secret = "PAYLOAD-MUST-NOT-BE-RECORDED"
    event = {"eventType": "node.completed", "message": secret, "payload": {"text": secret},
             "iterationPath": list(range(100)), "nodeId": "n" * 1000}
    details = eventDetails(event)
    assert len(details["nodeId"]) == 80 and len(details["iterationPath"]) == 8
    assert secret not in json.dumps(details)
    stacks = stackSnapshot()
    assert len(stacks["threads"]) <= 16
    assert all(len(thread["frames"]) <= 16 for thread in stacks["threads"])
    assert secret not in json.dumps(stacks)
    assert all(set(frame) == {"file", "function", "line"}
               for thread in stacks["threads"] for frame in thread["frames"])
    probe = JobDiagnostics(tmp_path / "diagnostic.json", source={}, plannedSeconds=5)
    probe.MAX_BYTES = 7000
    try:
        probe._frontier("consume_enter", details)
        for _ in range(probe.MAX_SNAPSHOTS + 5):
            probe.capture("periodic", stacks=False)
        saved = json.loads(probe.path.read_text())
        assert len(probe.rows) == probe.MAX_SNAPSHOTS
        assert saved["evicted_snapshots"] == 5
        assert saved["byte_budget_omitted_snapshots"] > 0
        assert 0 < probe.path.stat().st_size <= probe.MAX_BYTES
        assert secret not in probe.path.read_text()
        assert not probe.errors
    finally:
        probe.close()


@pytest.mark.parametrize("exitDuringFrameRead", (False, True))
def testStackSnapshotCannotAttributeReusedIdToRetiredOwner(monkeypatch, exitDuringFrameRead):
    import sys
    release = threading.Event()
    owner = threading.Thread(target=release.wait, name="runtime-event-bridge-retired")
    owner.start()
    originalFrames = sys._current_frames
    if not exitDuringFrameRead:
        release.set()
        owner.join(2)
        monkeypatch.setattr(owner, "_ident", threading.get_ident())

    def frames():
        if exitDuringFrameRead:
            release.set()
            owner.join(2)
            monkeypatch.setattr(owner, "_ident", threading.get_ident())
        return originalFrames()

    monkeypatch.setattr(threading, "enumerate", lambda: [owner])
    monkeypatch.setattr(sys, "_current_frames", frames)
    try:
        assert stackSnapshot()["threads"] == []
    finally:
        release.set()
        owner.join(2)
        assert not owner.is_alive()


def testMemoryOnlyCaptureTimesActiveRowsUnderItsOwnLock(monkeypatch, tmp_path):
    from scripts import r3_job_diagnostics as module
    probe = JobDiagnostics(tmp_path / "diagnostic.json", source={}, plannedSeconds=1, persistPeriodic=False)
    clock = [100]

    class InterleavingLock:
        def __enter__(self):
            probe.active[1] = {"stage": "formal.sqlite_append", "start_ns": 150, "thread_id": 1}
            clock[0] = 200
        def __exit__(self, *_args):
            return False

    def forbiddenSave():
        raise AssertionError("memory-only capture must not wait for disk I/O")

    try:
        with monkeypatch.context() as patch:
            patch.setattr(module.time, "monotonic_ns", lambda: clock[0])
            patch.setattr(probe, "lock", InterleavingLock())
            patch.setattr(probe, "_save", forbiddenSave)
            probe.capture("terminal_wait_enter", stacks=False, save=False)
            row = probe.rows[-1]
            assert row["observation_started_ns"] == 100 and row["monotonic_ns"] == 200
            assert row["active"][0]["elapsed_ns"] == 50
            assert probe.captureCost["calls"] == 1 and probe.captureCost["disk_save_requests"] == 0
            assert not probe.errors
    finally:
        probe.close()


def testExplicitMemoryOnlyPeriodicPolicyDoesNotChangeDefault(tmp_path):
    for persist in (True, False):
        probe = JobDiagnostics(tmp_path / (str(persist) + ".json"), source={}, plannedSeconds=1,
                               persistPeriodic=persist)
        calls = []
        def capture(reason, **kwargs):
            calls.append((reason, kwargs))
            probe.stop.set()
        probe.capture = capture
        probe._observe()
        assert calls == [("periodic", {"save": persist})]
        probe.close()


def testKeeperPreservesConfiguredDurabilityAndClosesExplicitly(tmp_path):
    from emo_master.apps.runtime.context.sqlite_store import SqliteStore
    store = SqliteStore(tmp_path / "runtime.db")
    store.initialize()
    keeper = SqliteKeeper(store)
    connection = keeper.connection
    try:
        state = keeper.report()
        assert state["pragmas"] == {"journal_mode": "wal", "synchronous": 2,
                                    "wal_autocheckpoint": 1000, "page_size": 4096}
        assert not state["in_transaction"] and not state["closed"]
        sequence = store.appendJobEvent("job", "", "job.completed", "INFO", "", "", "{}")
        assert sequence == 1
    finally:
        keeper.close()
    state = keeper.report()
    assert state["closed"] and state["closed_ns"] >= state["opened_ns"]
    assert state["close_wall_ns"] >= 0
    assert keeper.connection is None
    import sqlite3
    with pytest.raises(sqlite3.ProgrammingError):
        connection.execute("SELECT 1")
    keeper.close()
    assert keeper.report() == state


def testDiagnosticOwnersRetireAfterRuntimeAndStillAttemptIndependentOwners():
    from scripts.r3_soak import retireOwned
    calls = []

    def fail():
        calls.append("runtime")
        raise RuntimeError("runtime ownership retained")

    diagnostic = SimpleNamespace(capture=lambda reason: calls.append(reason), close=lambda: calls.append("diagnostic"))
    errors = retireOwned([], [], SimpleNamespace(close=fail), None, "",
        diagnostics=diagnostic, keeper=SimpleNamespace(close=lambda: calls.append("keeper")),
        sampler=SimpleNamespace(close=lambda: calls.append("sampler")))
    assert calls == ["runtime", "keeper", "after_cleanup", "diagnostic", "sampler"]
    assert errors[0]["owner"] == "runtime"
