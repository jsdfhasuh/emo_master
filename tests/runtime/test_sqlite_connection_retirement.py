"""Independent transactions close deterministically, not when CPython collects cycles."""
import sqlite3

import pytest

from emo_master.apps.runtime.context.global_variables import ProjectGlobalVariables
from emo_master.apps.runtime.context.sqlite_store import SqliteStore
from emo_master.core.project.global_variables import VariableError
from tests.runtime.test_global_variables import variable


def trackedStore(tmp_path, monkeypatch):
    store = SqliteStore(tmp_path / "state.db")
    store.initialize()
    store.retainIdleConnection()
    native = store._connect
    connections = []
    def track():
        connection = native()
        connections.append(connection)  # Strong refs make implicit GC insufficient.
        return connection
    monkeypatch.setattr(store, "_connect", track)
    return store, connections


def assertClosed(connections):
    assert connections
    for connection in connections:
        with pytest.raises(sqlite3.ProgrammingError, match="closed"):
            connection.execute("SELECT 1")


def testEventAndReadConnectionsRetireWhileExplicitIdleOwnerRemains(tmp_path, monkeypatch):
    store, connections = trackedStore(tmp_path, monkeypatch)
    try:
        for sequence in range(1, 9):
            assert store.appendJobEvent("job", "", "node.completed", "INFO", "", "", "{}") == sequence
            assert store.getLastJobEventSequence("job") == sequence
            assertClosed(connections)
            observer = sqlite3.connect(store.dbPath)
            try:
                assert observer.execute("SELECT count(*) FROM jobEvents").fetchone()[0] == sequence
            finally:
                observer.close()
        assert not store._idleConnection.in_transaction
    finally:
        for connection in connections:
            connection.close()
        store.releaseIdleConnection()


def testVariableTransactionsCloseOnSuccessAndValidationRollback(tmp_path, monkeypatch):
    store, connections = trackedStore(tmp_path, monkeypatch)
    try:
        values = ProjectGlobalVariables(store, "project", {"v": variable()})
        values.synchronize()
        first = values.records()["v"]
        values.set("v", 17, first.revision)
        with pytest.raises(VariableError):
            values.set("v", 99, first.revision)
        assert values.get("v") == 17
        assertClosed(connections)
        assert not store._idleConnection.in_transaction
    finally:
        for connection in connections:
            connection.close()
        store.releaseIdleConnection()


def testConnectionConfigurationFailureClosesHandleAndKeepsOriginalError(tmp_path, monkeypatch):
    from emo_master.apps.runtime.context import sqlite_store as module
    store = SqliteStore(tmp_path / "state.db")
    native = sqlite3.connect
    connections = []
    failure = sqlite3.OperationalError("injected pragma failure")
    def connect(*args, **kwargs):
        connection = native(*args, **kwargs)
        connections.append(connection)
        return connection
    def configure(_connection):
        raise failure
    monkeypatch.setattr(sqlite3, "connect", connect)
    monkeypatch.setattr(module, "_configureConnection", configure)
    try:
        with pytest.raises(sqlite3.OperationalError) as caught:
            store._connect()
        assert caught.value is failure
        assertClosed(connections)
    finally:
        for connection in connections:
            connection.close()
