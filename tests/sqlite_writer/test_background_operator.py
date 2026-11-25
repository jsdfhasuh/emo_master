"""The writer snapshots one row at admission and reports its actual transaction outcome."""
from contextlib import closing
from datetime import datetime, timezone
import sqlite3
from threading import Event

import pytest

from emo_master.apps.runtime.business_sqlite import backend
from emo_master.apps.runtime.execution.background_tasks import BackgroundTaskService
from emo_master.apps.runtime.workflow.context import RunContext
from emo_master.apps.runtime.workflow.cancellation import CancellationRequested
from emo_master.core.contracts.sqlite_writer import (
    parseConfig, receiptSummary, SqliteCommittedCleanupError, SqliteWriterError,
)
from emo_master.plugins.builtins.sqlite_writer.operator import SqliteWriterOperator
from tests.sqlite_writer.test_backend import makeDb
from tests.sqlite_writer.test_dependencies import config


def testQueuedNodeSnapshotsConfirmationBeforeActualCommit(tmp_path):
    path = makeDb(tmp_path / "ack.db", columns=[{"name": "confirmed_at", "storageType": "UTC_TIME"}])
    params = config(path, rows=[{"column": "confirmed_at", "storageType": "UTC_TIME",
                                "source": {"kind": "context", "key": "timestampUtc"}}], failure="continue")
    assert "writeMode" not in parseConfig(params)
    params["writeMode"] = "background"
    entered, resume = Event(), Event()
    admissions, events = [], []
    owner = RunContext.root("job", "plc").forNode("archive_ack")
    tasks = BackgroundTaskService()
    client = tasks.client(owner, "archive_ack")

    def delayedInsert(path, config, values, writeId, cancelled):
        entered.set()
        assert resume.wait(5)
        return backend.insert(path, config, values, writeId, cancelled)

    context = {"jobId": owner.jobId, "workflowRunId": owner.workflowRunId, "nodeRunId": owner.nodeRunId,
               "backgroundTasks": client, "sqliteInsert": delayedInsert,
               "publishSqliteWrite": lambda name, receipt, *, background: events.append((name, receipt["writeId"], background)),
               "registerBackgroundSqlite": lambda *admission: admissions.append(admission)}
    try:
        result = SqliteWriterOperator().executeNode({}, params, context)
        queued = result["outputs"]["receipt"]
        assert result["diagnostics"]["mappingSources"] == {"context": 1}
        assert result["diagnostics"]["sqliteReceipt"] == queued
        assert entered.wait(2) and queued["status"] == "QUEUED" and queued["rowsAffected"] is None
        assert receiptSummary(queued, queued["execution"])["status"] == "QUEUED"
        (taskId, registered, policy), = admissions
        assert registered == queued and policy == "continue"
        assert events == [("sqlite.write.started", queued["writeId"], True)]
        with closing(sqlite3.connect(path)) as connection:
            assert connection.execute("SELECT count(*) FROM records").fetchone() == (0,)
        beforeCommit = datetime.now(timezone.utc)
        params["table"] = "different_table"
        context["nodeRunId"] = "subsequent-node-run"
        resume.set()
        final = client.wait(taskId)
        assert queued["status"] == "QUEUED" and final["status"] == "COMMITTED"
        assert final["writeId"] == queued["writeId"] and final["execution"]["nodeRunId"] == owner.nodeRunId
        with closing(sqlite3.connect(path)) as connection:
            (row,) = connection.execute("SELECT id,write_id,confirmed_at FROM records").fetchall()
        assert row[:2] == (final["primaryKey"], final["writeId"])
        assert datetime.fromisoformat(row[2].replace("Z", "+00:00")) <= beforeCommit
    finally:
        resume.set()
        tasks.close()


@pytest.mark.parametrize(("error", "status", "rows"), [
    (sqlite3.IntegrityError("constraint"), "FAILED", 0),
    (SqliteWriterError("E_SQLITE_UNKNOWN", "commit acknowledgement lost"), "UNKNOWN", None),
    (SqliteCommittedCleanupError(17, "close"), "COMMITTED", 1),
    (CancellationRequested("cancelled before commit"), "CANCELLED", 0),
])
def testBackgroundInsertReturnsActualOutcomeWithOriginalIdentity(tmp_path, error, status, rows):
    params = dict(config(tmp_path / "ack.db", rows=[
        {"column": "value", "storageType": "INTEGER", "source": {"kind": "constant", "value": 3}}
    ]), writeMode="background")
    tasks = BackgroundTaskService()
    owner = RunContext.root("job", "plc").forNode("archive_ack")
    client = tasks.client(owner, "archive_ack")
    admissions = []

    def insert(*args):
        raise error

    try:
        result = SqliteWriterOperator().executeNode({}, params, {
            "nodeRunId": owner.nodeRunId, "sqliteInsert": insert, "backgroundTasks": client,
            "registerBackgroundSqlite": lambda *admission: admissions.append(admission),
        })
        queued = result["outputs"]["receipt"]
        (taskId, _, _), = admissions
        final = client.wait(taskId)
        assert final["writeId"] == queued["writeId"] and final["execution"] == queued["execution"]
        assert final["status"] == status and final["rowsAffected"] == rows
        assert receiptSummary(final, final["execution"])["status"] == status
        if status == "COMMITTED":
            assert final["error"] is None and final["cleanupError"]["code"] == "E_SQLITE_CLEANUP"
            assert final["primaryKey"] == 17
    finally:
        tasks.close()
