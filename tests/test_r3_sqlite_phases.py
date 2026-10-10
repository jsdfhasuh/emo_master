"""SQL observations forward native outcomes and own no native connection."""
import gc
import json
import sqlite3
import threading
import time
from types import ModuleType, SimpleNamespace
import weakref

import pytest

from scripts.r3_sqlite_phases import SqlitePhases, _Connection


def testConnectionDetailsAreOffByDefaultAndDoNotReadGetters(monkeypatch):
    from scripts import r3_sqlite_phases as module
    monkeypatch.setattr(module, "_transactionReading", lambda *_: pytest.fail("default getter"))
    monkeypatch.setattr(module, "_perfReading", lambda: pytest.fail("default QPC"))
    probe = SqlitePhases()
    probe.SLOW_NS = 0
    native = sqlite3.connect(":memory:")
    try:
        _Connection(native, probe).commit()
        row = probe.snapshot()
        assert "connection_detail_limitations" not in row
        assert "connection_operation_token" not in row["slow_calls"][0]
        assert "call_perf_ns" not in row["stages"]["commit"]
    finally:
        native.close()


def testDetailedConnectionAnchorsRawAutocommitAndIndependentClocks(monkeypatch):
    from scripts import r3_sqlite_phases as module
    native = sqlite3.connect(":memory:")
    probe = SqlitePhases(details=True)
    probe.SLOW_NS = 0
    proxy = _Connection(native, probe)
    statements = []
    native.set_trace_callback(statements.append)
    try:
        proxy.execute("BEGIN IMMEDIATE")
        anchor = probe.snapshot()["slow_calls"][-1]["call_id"]
        mono, cpu, perf = iter((100, 130)), iter((2, 9)), iter((1000, 1020))
        with monkeypatch.context() as patch:
            patch.setattr(module.time, "monotonic_ns", lambda: next(mono))
            patch.setattr(module.time, "thread_time_ns", lambda: next(cpu))
            patch.setattr(module.time, "perf_counter_ns", lambda: next(perf))
            proxy.commit()
        row = probe.snapshot()["slow_calls"][-1]
        assert row["successful_begin_call_id_at_entry"] == anchor
        assert row["in_transaction_before"] == {"status": "OBSERVED", "value": True}
        assert row["in_transaction_after"] == {"status": "OBSERVED", "value": False}
        assert (row["wall_ns"], row["thread_cpu_ns"], row["call_perf_wall_ns"]) == (30, 7, 20)
        assert (row["call_perf_start_ns"], row["call_perf_end_ns"]) == (1000, 1020)
        assert proxy.beginAnchor is None
        proxy.executescript("BEGIN; SELECT 1;")
        assert proxy.beginAnchor is None and native.in_transaction
        proxy.rollback()
        assert statements == ["BEGIN IMMEDIATE", "COMMIT", "BEGIN;", " SELECT 1;", "ROLLBACK"]
        other = sqlite3.connect(":memory:")
        try:
            assert _Connection(other, probe).operationToken > proxy.operationToken
        finally:
            other.close()
    finally:
        native.close()


def testDetailedGetterFailuresPreserveOriginalReturnAndException(monkeypatch):
    from scripts import r3_sqlite_phases as module
    probe = SqlitePhases(details=True)
    probe.SLOW_NS = 0
    native = sqlite3.connect(":memory:")
    proxy = _Connection(native, probe)
    error = ValueError("original operation")
    calls = []
    def unavailable(*_args):
        raise RuntimeError("getter only")
    def operation():
        calls.append(1)
        raise error
    monkeypatch.setattr(module, "_transactionReading", unavailable)
    try:
        assert proxy.commit() is None
        with pytest.raises(ValueError) as caught:
            proxy._call("commit", operation)
        assert caught.value is error and calls == [1]
        rows = probe.snapshot()["slow_calls"]
        assert len(rows) == 2 and probe.enabled
        assert all(row["in_transaction_before"]["status"] == "UNAVAILABLE"
                   and row["in_transaction_after"]["status"] == "UNAVAILABLE" for row in rows)
        assert rows[-1]["failed"] and rows[-1]["error_type"] == "ValueError"
    finally:
        native.close()


def testDetailedCloseAndCrossThreadGetterAreUnavailable():
    from concurrent.futures import ThreadPoolExecutor
    probe = SqlitePhases(details=True)
    probe.SLOW_NS = 0
    native = sqlite3.connect(":memory:", check_same_thread=False)
    proxy = _Connection(native, probe)
    try:
        with ThreadPoolExecutor(1) as executor:
            def differentThreadWithReusedNumericIdentity():
                proxy.operationThreadId = threading.get_ident()
                return proxy.commit()
            assert executor.submit(differentThreadWithReusedNumericIdentity).result(2) is None
        row = probe.snapshot()["slow_calls"][-1]
        assert row["connection_operation_thread_id"] == row["thread_id"]
        assert row["creating_thread_matches"] is False
        assert row["in_transaction_before"]["reason"] == "different_operation_thread"
        assert row["in_transaction_after"]["reason"] == "different_operation_thread"
        proxy.close()
        assert probe.snapshot()["slow_calls"][-1]["in_transaction_after"]["reason"] == "close_operation"
        with pytest.raises(sqlite3.ProgrammingError):
            proxy.commit()
        assert probe.snapshot()["slow_calls"][-1]["in_transaction_before"]["status"] == "UNAVAILABLE"
    finally:
        native.close()


def testDetailedFalseAutocommitValueDoesNotWrapOrConsumeActiveReader():
    probe = SqlitePhases(details=True)
    probe.SLOW_NS = 0
    native = sqlite3.connect(":memory:")
    try:
        cursor = _Connection(native, probe).execute("SELECT 1 UNION ALL SELECT 2")
        assert type(cursor) is sqlite3.Cursor and cursor.connection is native
        row = probe.snapshot()["slow_calls"][-1]
        assert row["in_transaction_after"] == {"status": "OBSERVED", "value": False}
        assert cursor.fetchone() == (1,)  # Observation did not drain the reader.
        assert cursor.fetchone() == (2,)
        cursor.close()
    finally:
        native.close()


def testDetailedObserverKeepsNoNativeProxyOrThreadOwnerAfterReturn():
    class Native:
        def commit(self):
            return None
    probe = SqlitePhases(details=True)
    probe.SLOW_NS = 0
    native = Native()
    proxy = _Connection(native, probe)
    refs = weakref.ref(native), weakref.ref(proxy)
    wasEnabled = gc.isenabled()
    gc.disable()
    try:
        proxy.commit()
        del native, proxy
        assert [ref() for ref in refs] == [None, None]
        assert not probe.active
        json.dumps(probe.snapshot())
    finally:
        if wasEnabled:
            gc.enable()


def testDetailedNativeReturnAfterRetirementAddsNoGetterOrClockObservation(monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from scripts import r3_sqlite_phases as module
    probe = SqlitePhases(details=True)
    entered, release = threading.Event(), threading.Event()
    getterCalls, clockCalls = [], []
    result = object()
    def getter(*_args):
        getterCalls.append(1)
        return {"status": "UNAVAILABLE"}
    def perf():
        clockCalls.append(1)
        return 100
    def operation():
        entered.set()
        assert release.wait(2)
        return result
    monkeypatch.setattr(module, "_transactionReading", getter)
    monkeypatch.setattr(module, "_perfReading", perf)
    proxy = _Connection(SimpleNamespace(), probe)
    with ThreadPoolExecutor(1) as executor:
        future = executor.submit(proxy._call, "commit", operation)
        try:
            assert entered.wait(2)
            probe.enabled = False
            release.set()
            assert future.result(2) is result
        finally:
            release.set()
    assert getterCalls == clockCalls == [1]
    assert not probe.slow
    active = next(iter(probe.active.values()))
    assert "call_perf_end_ns" not in active and "in_transaction_after" not in active


def testRetirementRestoresInheritedConnectWithoutGCOrSelfCycle(tmp_path):
    from emo_master.apps.runtime.context.sqlite_store import SqliteStore
    wasEnabled = gc.isenabled()
    gc.disable()
    try:
        plain = SqliteStore(tmp_path / "plain.db")
        plainRef = weakref.ref(plain)
        del plain
        assert plainRef() is None
        observed = SqliteStore(tmp_path / "observed.db")
        observedRef = weakref.ref(observed)
        probe = SqlitePhases()
        assert "_connect" not in vars(observed)
        probe.install(observed)
        probe.close()
        assert "_connect" not in vars(observed)
        assert observed._connect.__func__ is SqliteStore._connect
        del observed
        assert observedRef() is None  # No gc.collect() may rescue this check.
        assert probe.patch is None
    finally:
        if wasEnabled:
            gc.enable()


def testRetirementPreservesOwnOverrideClassDescriptorAndModuleValue():
    def original():
        return "original"

    class Store:
        @classmethod
        def _connect(cls):
            return cls

    module = ModuleType("diagnostic_test_store")
    module._connect = original
    instance = Store()
    instance._connect = original
    for owner in (instance, Store, module):
        raw = vars(owner)["_connect"]
        probe = SqlitePhases()
        probe.install(owner)
        probe.close()
        assert vars(owner)["_connect"] is raw
    assert Store._connect() is Store
    assert instance._connect() == module._connect() == "original"


@pytest.mark.parametrize("remove", (False, True))
def testRetirementDoesNotOverwriteConcurrentConnectReplacementOrRemoval(remove):
    class Store:
        def _connect(self):
            return "inherited"
    store = Store()
    probe = SqlitePhases()
    probe.install(store)
    if remove:
        del store._connect
    else:
        store._connect = lambda: "new owner"
    probe.close()
    assert store._connect() == ("inherited" if remove else "new owner")
    assert ("_connect" in vars(store)) is not remove


@pytest.mark.parametrize("mutateBeforeFailure", (False, True))
def testFailedInstallationRetainsRestorationForCloseRetry(mutateBeforeFailure):
    class Store:
        failDelete = True
        def _connect(self):
            return "original"
        def __setattr__(self, name, value):
            if name == "_connect":
                if mutateBeforeFailure:
                    object.__setattr__(self, name, value)
                raise RuntimeError("installation unavailable")
            object.__setattr__(self, name, value)
        def __delattr__(self, name):
            if name == "_connect" and self.failDelete:
                self.failDelete = False
                raise RuntimeError("restoration unavailable")
            object.__delattr__(self, name)

    store = Store()
    probe = SqlitePhases()
    with pytest.raises(RuntimeError, match="installation unavailable"):
        probe.install(store)
    if mutateBeforeFailure:
        with pytest.raises(RuntimeError, match="restoration unavailable"):
            probe.close()
        assert probe.patch is not None and "_connect" in vars(store)
    probe.close()
    probe.close()
    assert probe.patch is None and "_connect" not in vars(store)
    assert store._connect() == "original"


def testSnapshotTimestampCannotPrecedeNewlyEnteredCall(monkeypatch):
    from scripts import r3_sqlite_phases as module
    probe = SqlitePhases()
    clock = [100]

    class InterleavingLock:
        def __enter__(self):
            probe.active[1] = {"phase": "commit", "start_ns": 150, "call_id": 1, "thread_id": 1}
            clock[0] = 200
        def __exit__(self, *_args):
            return False

    probe.lock = InterleavingLock()
    monkeypatch.setattr(module.time, "monotonic_ns", lambda: clock[0])
    report = probe.snapshot()
    assert report["observed_ns"] == 200
    assert report["active"][0]["elapsed_ns"] == 50


def testConnectionForwardsArgumentsReturnsAndContextWithoutClosing():
    calls = []
    marker = object()

    def call(name, result=marker):
        def operation(*args, **kwargs):
            calls.append((name, args, kwargs))
            return result
        return operation

    native = SimpleNamespace(execute=call("execute"), executescript=call("script"),
        commit=call("commit"), rollback=call("rollback"), close=call("close"),
        __enter__=call("enter"), __exit__=call("exit", False), in_transaction=True)
    probe = SqlitePhases()
    connection = _Connection(native, probe)
    assert connection.in_transaction is True
    parameters = ("PRIVATE-PARAMETER",)
    with connection as entered:
        assert entered is connection
        assert connection.execute("SELECT ?", parameters) is marker
        assert connection.executescript("PRIVATE-SCRIPT") is marker
        assert connection.commit() is marker
    assert [entry[0] for entry in calls] == ["enter", "execute", "script", "commit", "exit"]
    assert calls[1][1] == ("SELECT ?", parameters)
    assert calls[-1][1] == (None, None, None)
    assert connection.rollback() is marker
    assert connection.close() is marker
    assert [entry[0] for entry in calls][-2:] == ["rollback", "close"]
    report = probe.snapshot()
    assert all(row["entered"] == row["returned"] == 1 for row in report["stages"].values())
    assert "PRIVATE" not in json.dumps(report)


@pytest.mark.parametrize("method", ("execute", "commit", "__exit__", "close"))
@pytest.mark.parametrize("breakRecorder", (None, "_begin", "_finish"))
def testFailureObjectAndExactlyOneNativeCallSurviveObserverFailures(monkeypatch, method, breakRecorder):
    failure = sqlite3.OperationalError("PRIVATE ORIGINAL FAILURE")
    calls = []

    def fail(*args):
        calls.append(args)
        raise failure

    probe = SqlitePhases()
    native = SimpleNamespace(**{method: fail})
    if breakRecorder is not None:
        def broken(*_args):
            raise RuntimeError("observer is unavailable")
        monkeypatch.setattr(probe, breakRecorder, broken)
    connection = _Connection(native, probe)
    args = ("INSERT INTO jobEvents VALUES (?)", ("PRIVATE PARAMETER",)) if method == "execute" else ()
    if method == "__exit__":
        args = (ValueError, ValueError("original body failure"), None)
    with pytest.raises(sqlite3.OperationalError) as raised:
        getattr(connection, method)(*args)
    assert raised.value is failure and calls == [args]
    report = probe.snapshot()
    assert "PRIVATE" not in json.dumps(report)
    assert report["enabled"] is (breakRecorder is None)
    if breakRecorder is None:
        assert next(iter(report["stages"].values()))["failed"] == 1
        assert report["slow_calls"][0]["error_type"] == "OperationalError"


def testOverflowDisablesOnlyObservationAndBoundedTailExcludesSql():
    probe = SqlitePhases()
    probe.MAX_STAGES = 1
    marker, calls = object(), []
    assert probe.call("first", lambda: marker) is marker
    assert probe.call("overflow", lambda: calls.append("original")) is None
    assert calls == ["original"] and not probe.snapshot()["enabled"]
    probe = SqlitePhases()
    probe.SLOW_NS = 0
    for _ in range(probe.MAX_SLOW + 3):
        probe.call("commit", lambda: None)
    report = probe.snapshot()
    assert len(report["slow_calls"]) == probe.MAX_SLOW
    assert report["slow_or_failed_call_count"] == probe.MAX_SLOW + 3
    assert report["evicted_slow_or_failed_calls"] == 3
    assert report["stages"]["commit"]["returned"] == probe.MAX_SLOW + 3


@pytest.mark.parametrize("brokenStage", ("_begin", "_finish"))
def testSuccessfulNativeReturnSurvivesRecordingFailure(monkeypatch, brokenStage):
    probe = SqlitePhases()
    marker, calls = object(), []

    def broken(*_args):
        raise MemoryError("observer could not record")

    def native():
        calls.append(True)
        return marker

    monkeypatch.setattr(probe, brokenStage, broken)
    assert probe.call("commit", native) is marker
    assert calls == [True] and probe.snapshot()["enabled"] is False


def testNativeContextStillCommitsRollsBackAndLeavesConnectionOpen(tmp_path):
    native = sqlite3.connect(tmp_path / "native.db")
    probe = SqlitePhases()
    connection = _Connection(native, probe)
    try:
        with connection:
            connection.execute("CREATE TABLE rows(value)")
            connection.execute("INSERT INTO rows VALUES (1)")
        assert native.execute("SELECT * FROM rows").fetchall() == [(1,)]
        failure = RuntimeError("abort transaction")
        with pytest.raises(RuntimeError) as raised:
            with connection:
                connection.execute("INSERT INTO rows VALUES (2)")
                raise failure
        assert raised.value is failure
        assert native.execute("SELECT * FROM rows").fetchall() == [(1,)]
        assert not native.in_transaction
        report = probe.snapshot()
        assert report["stages"]["context_exit"]["returned"] == 2
        assert "explicit_close" not in report["stages"]
    finally:
        native.close()


def testObserverDoesNotKeepNativeConnectionAliveOrCloseIt():
    retired, closed = [], []

    class Native:
        def __enter__(self):
            return self
        def __exit__(self, *_args):
            return False
        def close(self):
            closed.append(True)

    references = []
    def connect():
        native = Native()
        references.append(weakref.ref(native))
        weakref.finalize(native, retired.append, True)
        return native

    store = SimpleNamespace(_connect=connect)
    probe = SqlitePhases()
    probe.install(store)
    connection = store._connect()
    with connection:
        pass
    del connection
    gc.collect()
    assert references[0]() is None and retired == [True] and closed == []
    probe.close()
    assert store._connect is connect


def testRealWriterContentionIsVisibleAtBeginAndPreservesAllDurableRows(tmp_path):
    from emo_master.apps.runtime.context.sqlite_store import SqliteStore
    store = SqliteStore(tmp_path / "runtime.db")
    store.initialize()
    store.retainIdleConnection()
    blocker = sqlite3.connect(store.dbPath)
    blocker.execute("BEGIN IMMEDIATE")
    probe = SqlitePhases()
    probe.install(store)
    result, failures = [], []

    def append():
        try:
            result.append(store.appendJobEvent("job", "node", "node.completed", "INFO", "", "private", "{}"))
        except BaseException as error:
            failures.append(error)

    thread = threading.Thread(target=append)
    thread.start()
    try:
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            report = probe.snapshot()
            if any(row["phase"] == "begin_immediate" for row in report["active"]):
                break
            threading.Event().wait(0.005)
        else:
            pytest.fail("writer never entered measured BEGIN IMMEDIATE")
        assert report["stages"]["begin_immediate"]["returned"] == 0
        assert report["stages"].get("commit", {}).get("entered", 0) == 0
        blocker.rollback()
        thread.join(3)
        assert not thread.is_alive() and result == [1] and not failures
        report = probe.snapshot()
        assert not report["active"] and report["stages"]["commit"]["returned"] == 1
        assert blocker.execute("SELECT sequence FROM jobEvents").fetchall() == [(1,)]
        assert blocker.execute("PRAGMA synchronous").fetchone()[0] == 2
        assert blocker.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    finally:
        blocker.rollback()
        thread.join(6)
        probe.close()
        blocker.close()
        store.releaseIdleConnection()


def testOnePendingCommitHasStableCallIdWhileLaterCommitsAdvance():
    entered, release = threading.Event(), threading.Event()
    probe = SqlitePhases()

    def nativeCommit():
        entered.set()
        assert release.wait(3)

    connection = _Connection(SimpleNamespace(commit=nativeCommit), probe)
    thread = threading.Thread(target=connection.commit)
    thread.start()
    try:
        assert entered.wait(2)
        first = probe.snapshot()
        second = probe.snapshot()
        assert first["active"][0]["call_id"] == second["active"][0]["call_id"]
        assert second["active"][0]["elapsed_ns"] >= first["active"][0]["elapsed_ns"]
        assert second["stages"]["commit"]["returned"] == 0
    finally:
        release.set()
        thread.join(3)
    connection.commit()
    report = probe.snapshot()
    assert not report["active"]
    assert report["stages"]["commit"]["entered"] == report["stages"]["commit"]["returned"] == 2
