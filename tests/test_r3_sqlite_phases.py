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
