"""Online resource writes share event writers' bounded SQLite admission."""
from functools import partial
from threading import Event, Thread
from types import SimpleNamespace
import time

import pytest

from emo_master.apps.runtime.context import sqlite_transactions
from emo_master.apps.runtime.context.sqlite_store import SqliteStore
from emo_master.apps.runtime.project_config.resource_journal import ResourceJournal
from emo_master.apps.runtime.project_config.transfer import ResourceTransfers
from emo_master.core.project.models import ProjectDocument
from emo_master.core.project.resources import Resource
from tests.runtime.test_device_runtime import simulatedProject
from tests.runtime.test_project_resource_transfer import chunk, manifest, request


def contendWithWriter(store, operation, monkeypatch):
    """A real competing SQLite writer retires after bounded admission yields.

    Native sqlite busy backoff cannot signal this handoff. No scheduling race or
    fast-disk deadline determines whether the shared admission path was used.
    """
    retried, release = Event(), Event()
    values, failures = [], []

    def pause(_seconds):
        retried.set()
        assert release.wait(5), "competing writer was not released"

    monkeypatch.setattr(sqlite_transactions, "time",
                        SimpleNamespace(monotonic=time.monotonic, sleep=pause))

    def run():
        try:
            values.append(operation())
        except BaseException as error:
            failures.append(error)

    worker = Thread(target=run, name="resource-write-admission")
    try:
        with store.connection() as writer:
            writer.execute("BEGIN IMMEDIATE")
            worker.start()
            try:
                assert retried.wait(2), "resource write bypassed bounded SQLite admission"
            finally:
                writer.rollback()
                release.set()
    finally:
        worker.join(5)
    assert not worker.is_alive()
    assert not failures, failures
    return values[0]


@pytest.mark.parametrize("action", ["reserve", "expose", "forget"])
def testPreparedResourceJournalUsesBoundedWriteAdmission(tmp_path, monkeypatch, action):
    store = SqliteStore(tmp_path / "runtime.db")
    owner = ResourceJournal(store, tmp_path / "resources", "owner")
    path = owner.root / "generation" / "input.bin"
    path.parent.mkdir(parents=True)
    if action != "reserve":
        owner.reserve(path)
    if action == "expose":
        path.write_bytes(b"prepared")
    operation = partial(owner.expose, [path]) if action == "expose" else partial(getattr(owner, action), path)
    contendWithWriter(store, operation, monkeypatch)
    with store.connection() as connection:
        rows = connection.execute("SELECT path, exposed FROM configurationResourceFiles").fetchall()
    assert rows == ([] if action == "forget" else [(str(path), int(action == "expose"))])


def testEmptyPreparationDoesNotRequestSqliteWriter(tmp_path, monkeypatch):
    store = SqliteStore(tmp_path / "runtime.db")
    owner = ResourceJournal(store, tmp_path / "resources", "owner")
    native = store._connect

    def connect():
        connection = native()
        connection.execute("PRAGMA busy_timeout=0")
        return connection

    monkeypatch.setattr(store, "_connect", connect)
    with store.connection() as writer:
        writer.execute("BEGIN IMMEDIATE")
        try:
            owner.expose([])
        finally:
            writer.rollback()


@pytest.fixture
def transfers(tmp_path):
    return ResourceTransfers(SqliteStore(tmp_path / "runtime.db"), tmp_path / "uploads")


def readyTransfer(transfers):
    transfers.execute(request("BEGIN"))
    transfers.execute(chunk(b"model data"))
    transfers.execute(request("COMMIT"))


def resourceDocument(root):
    document = ProjectDocument.model_validate(simulatedProject())
    document.resources.items["model"] = Resource(**manifest())
    document.project.projectId = "project"
    source = root / "models" / "input.bin"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"model data")
    return document


@pytest.mark.parametrize("action", ["chunk", "ingest", "pin", "unpin", "reclaim"])
def testResourceTransferUsesBoundedWriteAdmission(tmp_path, transfers, monkeypatch, action):
    document = resourceDocument(tmp_path)
    if action == "chunk":
        transfers.execute(request("BEGIN"))
        operation = partial(transfers.execute, chunk(b"model data"))
    elif action == "ingest":
        operation = partial(transfers.pin, document, tmp_path)
    elif action == "pin":
        readyTransfer(transfers)
        operation = partial(transfers.pin, document, tmp_path)
    elif action == "unpin":
        readyTransfer(transfers)
        token = transfers.pin(document, tmp_path)
        operation = partial(transfers.unpin, token)
    else:
        with transfers.sqlite.connection() as connection:
            connection.execute("INSERT INTO projectResourceIngestions VALUES ('missing-transfer')")
        operation = transfers._reclaimIngestions
    result = contendWithWriter(transfers.sqlite, operation, monkeypatch)
    with transfers.sqlite.connection() as connection:
        pins = connection.execute("SELECT token, transferId FROM projectResourcePins").fetchall()
        ingestions = connection.execute("SELECT transferId FROM projectResourceIngestions").fetchall()
    if action == "chunk":
        assert result["offset"] == 10
        assert transfers._path(transfers._read("transfer")[2]).read_bytes() == b"model data"
    elif action in {"ingest", "pin"}:
        assert len(pins) == 1 and pins[0][0] == result
        assert transfers._read(pins[0][1])[3] == "READY"
        assert transfers._path(transfers._read(pins[0][1])[2]).read_bytes() == b"model data"
    else:
        assert not pins and not ingestions


def testHealthyResourceLookupRemainsWalReaderDuringEventWrite(transfers, monkeypatch):
    readyTransfer(transfers)
    native = transfers.sqlite._connect

    def connect():
        connection = native()
        connection.execute("PRAGMA busy_timeout=0")
        return connection

    monkeypatch.setattr(transfers.sqlite, "_connect", connect)
    with transfers.sqlite.connection() as writer:
        writer.execute("BEGIN IMMEDIATE")
        try:
            assert transfers.execute(request("LOOKUP"))["state"] == "READY"
        finally:
            writer.rollback()


@pytest.mark.parametrize("restored", [False, True])
def testResourceHealthTransitionUsesBoundedWriteAdmission(transfers, monkeypatch, restored):
    readyTransfer(transfers)
    path = transfers._path(transfers._read("transfer")[2])
    path.write_bytes(b"corrupt")
    if restored:
        assert transfers.execute(request("LOOKUP"))["state"] == "CORRUPT"
        path.write_bytes(b"model data")
    result = contendWithWriter(transfers.sqlite, lambda: transfers.execute(request("LOOKUP")), monkeypatch)
    expected = "READY" if restored else "CORRUPT"
    assert result["state"] == expected
    assert transfers._read("transfer")[3] == expected


def testOnlineResourceLifecycleHasNoImplicitWriteAdmission(tmp_path, transfers, monkeypatch):
    owner = ResourceJournal(transfers.sqlite, tmp_path / "resources", "owner")
    statements = []
    native = transfers.sqlite._connect

    def connect():
        connection = native()
        connection.set_trace_callback(statements.append)
        return connection

    monkeypatch.setattr(transfers.sqlite, "_connect", connect)
    token = transfers.pin(resourceDocument(tmp_path), tmp_path)
    path = owner.root / "generation" / "input.bin"
    path.parent.mkdir(parents=True)
    owner.reserve(path)
    path.write_bytes(b"prepared")
    owner.expose([path])
    path.unlink()
    owner.forget(path)
    transfers.unpin(token)
    begins = [sql for sql in statements if sql.startswith("BEGIN")]
    assert begins and all(sql == "BEGIN IMMEDIATE" for sql in begins), begins
    with transfers.sqlite.connection() as connection:
        assert connection.execute("SELECT count(*) FROM projectResourcePins").fetchone() == (0,)
        assert connection.execute("SELECT count(*) FROM projectResourceIngestions").fetchone() == (0,)
        assert connection.execute("SELECT count(*) FROM configurationResourceFiles").fetchone() == (0,)
