"""Explicit Runtime WAL ownership, without shared writes or durability changes."""
from concurrent.futures import ThreadPoolExecutor
import sqlite3
import threading

import pytest

from emo_master.apps.runtime.context.sqlite_store import SqliteStore
from emo_master.apps.runtime.grpc_server.service import RuntimeService


def testExplicitIdleOwnerKeepsIndependentDurableWritesAndClosesAcrossThreads(tmp_path, monkeypatch):
    store = SqliteStore(tmp_path / "events.db")
    store.initialize()
    assert store._idleConnection is None
    original = sqlite3.connect
    calls = []

    def connect(*args, **kwargs):
        calls.append(kwargs.get("check_same_thread", True))
        return original(*args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", connect)
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda _: store.retainIdleConnection(), range(8)))
    keeper = store._idleConnection
    assert keeper is not None and calls == [False]
    assert not keeper.in_transaction
    assert {name: keeper.execute("PRAGMA " + name).fetchone()[0]
            for name in ("journal_mode", "synchronous", "wal_autocheckpoint", "page_size")} == {
                "journal_mode": "wal", "synchronous": 2, "wal_autocheckpoint": 1000, "page_size": 4096}
    for expected in (1, 2):
        assert store.appendJobEvent("job", "", "node.completed", "INFO", "", "", "{}") == expected
        observer = original(store.dbPath)
        try:
            assert observer.execute("SELECT count(*) FROM jobEvents").fetchone()[0] == expected
        finally:
            observer.close()
    assert calls == [False, True, True]  # Each write still acquired its own connection.
    assert store._idleConnection is keeper and not keeper.in_transaction
    with ThreadPoolExecutor(max_workers=1) as pool:
        pool.submit(store.releaseIdleConnection).result(timeout=2)
    assert store._idleConnection is None
    store.releaseIdleConnection()
    with pytest.raises(sqlite3.ProgrammingError):
        keeper.execute("SELECT 1")


@pytest.mark.parametrize("failureAt", ("PRAGMA", "SELECT"))
def testIdleAcquisitionFailureClosesUnpublishedConnection(tmp_path, monkeypatch, failureAt):
    store = SqliteStore(tmp_path / "events.db")
    failure = RuntimeError("idle schema read failed")

    class Connection:
        closed = 0

        def execute(self, sql):
            if sql.startswith(failureAt):
                raise failure

        def close(self):
            self.closed += 1

    connection = Connection()
    monkeypatch.setattr(sqlite3, "connect", lambda *_args, **_kwargs: connection)
    with pytest.raises(RuntimeError) as caught:
        store.retainIdleConnection()
    assert caught.value is failure
    assert connection.closed == 1 and store._idleConnection is None


@pytest.mark.parametrize("retry", ("release", "retain"))
def testIdleConfigurationCleanupFailureKeepsOwnerAndOriginalCause(tmp_path, monkeypatch, retry):
    store = SqliteStore(tmp_path / "events.db")
    failure = RuntimeError("pragma failed")

    class Connection:
        closed = 0
        ready = False

        def execute(self, _sql):
            if not self.ready:
                raise failure
            class Cursor:
                def fetchone(self):
                    return (0,)

                def close(self):
                    pass
            return Cursor()

        def close(self):
            self.closed += 1
            if self.closed == 1:
                raise OSError("injected native close failure")

    connection = Connection()
    monkeypatch.setattr(sqlite3, "connect", lambda *_args, **_kwargs: connection)
    with pytest.raises(RuntimeError) as caught:
        store.retainIdleConnection()
    assert caught.value.__cause__ is failure and connection.closed == 1
    assert store._idleConnection is connection and not store._idleConnectionReady
    if retry == "retain":
        connection.ready = True
        store.retainIdleConnection()
        assert store._idleConnection is connection and store._idleConnectionReady
    store.releaseIdleConnection()
    assert store._idleConnection is None and not store._idleConnectionReady


def testRuntimeIdleOwnerCanRetireOnDifferentThread(tmp_path):
    runtime = RuntimeService(dbPath=tmp_path / "runtime.db")
    keeper = runtime.sqliteStore._idleConnection
    errors = []

    def close():
        try:
            runtime.close()
        except BaseException as error:
            errors.append(error)

    thread = threading.Thread(target=close)
    try:
        thread.start()
        thread.join(5)
        assert not thread.is_alive() and not errors
        assert runtime._closed and runtime.sqliteStore._idleConnection is None
        assert not runtime._runtimeDataLock._acquired
        with pytest.raises(sqlite3.ProgrammingError):
            keeper.execute("SELECT 1")
    finally:
        runtime.close()


@pytest.mark.parametrize("blocked", ("writer", "maintenance", "native_close"))
def testIncompleteRuntimeShutdownRetainsIdleOwnerAndDataLockForRetry(tmp_path, monkeypatch, blocked):
    runtime = RuntimeService(dbPath=tmp_path / "runtime.db")
    keeper = runtime.sqliteStore._idleConnection
    maintenance = runtime._maintenanceThread
    writerClose = runtime.operationalLogWriter.close
    try:
        if blocked == "writer":
            monkeypatch.setattr(runtime.operationalLogWriter, "close", lambda **_kwargs: "writer still running")
        elif blocked == "maintenance":
            class PendingMaintenance:
                def is_alive(self):
                    return True

                def join(self, **_kwargs):
                    pass
            runtime._maintenanceThread = PendingMaintenance()
        else:
            class FailedNativeClose:
                def close(self):
                    raise RuntimeError("native idle close failed")
            runtime.sqliteStore._idleConnection = FailedNativeClose()
        with pytest.raises(RuntimeError):
            runtime.close()
        assert not runtime._closed and runtime._runtimeDataLock._acquired
        assert runtime.sqliteStore._idleConnection is not None
        assert keeper.execute("SELECT 1").fetchone() == (1,)
    finally:
        runtime._maintenanceThread = maintenance
        monkeypatch.setattr(runtime.operationalLogWriter, "close", writerClose)
        runtime.sqliteStore._idleConnection = keeper
        runtime.close()
    assert runtime._closed and runtime.sqliteStore._idleConnection is None
    assert not runtime._runtimeDataLock._acquired
    assert not maintenance.is_alive() and not runtime.operationalLogWriter._thread.is_alive()
    with pytest.raises(sqlite3.ProgrammingError):
        keeper.execute("SELECT 1")


def testRuntimeInitializationFailureRetiresPreviouslyInitializedOwners(tmp_path, monkeypatch):
    failure = RuntimeError("idle owner initialization failed")
    retired = []
    originalClose = RuntimeService.close

    def fail(_store):
        raise failure

    def close(runtime):
        originalClose(runtime)
        retired.append(runtime)

    monkeypatch.setattr(SqliteStore, "retainIdleConnection", fail)
    monkeypatch.setattr(RuntimeService, "close", close)
    with pytest.raises(RuntimeError) as caught:
        RuntimeService(dbPath=tmp_path / "runtime.db")
    assert caught.value is failure and len(retired) == 1
    runtime = retired[0]
    assert runtime._closed and not runtime._runtimeDataLock._acquired
    assert not runtime._maintenanceThread.is_alive()
    assert not runtime.operationalLogWriter._thread.is_alive()
    assert runtime.sqliteStore._idleConnection is None
