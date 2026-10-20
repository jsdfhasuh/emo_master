"""Legacy reads are WAL readers; every intentional mutation commits once."""
import sqlite3

import pytest

from emo_master.apps.runtime.context.global_counters import GlobalCounterError
from emo_master.apps.runtime.context.global_variables import ProjectGlobalVariables
from emo_master.apps.runtime.context.sqlite_store import SqliteStore
from tests.runtime.test_global_variables import variable


def counterStore(tmp_path):
    store = SqliteStore(tmp_path / "runtime.db")
    store.initialize()
    store.setGlobalCounter("project", "parts", 9)
    return store


def traceConnections(store, monkeypatch):
    native = store._connect
    statements = []

    def connect():
        connection = native()
        connection.set_trace_callback(statements.append)
        return connection

    monkeypatch.setattr(store, "_connect", connect)
    return statements


def testExistingCounterReadNeverRequestsTheSqliteWriter(tmp_path, monkeypatch):
    store = counterStore(tmp_path)
    statements = traceConnections(store, monkeypatch)
    before = store.getGlobalCounter("project", "parts")
    assert store.applyGlobalCounter("project", "parts") == before
    assert not [sql for sql in statements if sql.startswith(("BEGIN IMMEDIATE", "INSERT", "UPDATE"))], statements


def testExistingCounterReadUsesCommittedWalValueDuringAnotherWrite(tmp_path, monkeypatch):
    store = counterStore(tmp_path)
    native = store._connect

    def connect():
        connection = native()
        connection.execute("PRAGMA busy_timeout=0")
        return connection

    monkeypatch.setattr(store, "_connect", connect)
    with sqlite3.connect(store.dbPath) as writer:
        writer.execute("BEGIN IMMEDIATE")
        writer.execute("UPDATE variableValues SET valueJson='10' WHERE variableId='counter:parts'")
        try:
            assert store.getGlobalCounter("project", "parts").value == 9
        finally:
            writer.rollback()
    assert store.getGlobalCounter("project", "parts").value == 9


@pytest.mark.parametrize("exists", [False, True])
def testCounterIncrementHasOneWriteAdmissionAndOneRevision(tmp_path, monkeypatch, exists):
    store = counterStore(tmp_path)
    name, expected = ("parts", 10) if exists else ("new", 1)
    statements = traceConnections(store, monkeypatch)
    assert store.applyGlobalCounter("project", name, increment=True).value == expected
    assert statements.count("BEGIN IMMEDIATE") == 1, statements
    with store.connection() as connection:
        assert connection.execute("SELECT revision FROM variableValues WHERE variableId=?",
                                  ("counter:" + name,)).fetchone() == ((3 if exists else 2),)


def testCounterCreationRollsBackIfItsInitialValueCannotBeInserted(tmp_path):
    store = counterStore(tmp_path)
    with store.connection() as connection:
        connection.execute("""CREATE TRIGGER rejectCounter BEFORE INSERT ON variableValues
            WHEN NEW.variableId='counter:rejected'
            BEGIN SELECT RAISE(ABORT, 'injected value initialization failure'); END""")
    with pytest.raises(sqlite3.IntegrityError, match="initialization failure"):
        store.applyGlobalCounter("project", "rejected", increment=True)
    with store.connection() as connection:
        assert connection.execute("SELECT count(*) FROM variableDefinitions WHERE variableId='counter:rejected'").fetchone() == (0,)
        assert connection.execute("SELECT count(*) FROM variableValues WHERE variableId='counter:rejected'").fetchone() == (0,)


def testCounterDoesNotClaimAnExistingTypedVariableName(tmp_path):
    store = counterStore(tmp_path)
    values = ProjectGlobalVariables(store, "project", {"v": variable(name="typed")})
    values.synchronize()
    with pytest.raises(GlobalCounterError) as error:
        store.applyGlobalCounter("project", "typed", increment=True)
    assert error.value.code == "E_VARIABLE_NAME_CONFLICT"
    assert values.get("v") == 0
    with store.connection() as connection:
        assert connection.execute("SELECT count(*) FROM variableDefinitions WHERE variableId='counter:typed'").fetchone() == (0,)
