"""Real Runner regressions for ignored inserts and cancellation after commit."""
import sqlite3

import pytest

from emo_master.apps.designer.state.node_run_inspection import NodeRunInspection
from emo_master.apps.runtime.workflow.cancellation import CancellationRequested, CancellationToken
from emo_master.apps.runtime.workflow.context import RunContext
from emo_master.apps.runtime.workflow.runner import WorkflowExecutionError, WorkflowRunner
from emo_master.core.contracts.run_inspection import encodedBytes
from emo_master.core.contracts.sqlite_writer import OPERATOR_ID
from emo_master.core.workflow.compiler import WorkflowCompiler
from emo_master.plugins.builtins.sqlite_writer.operator import SqliteWriterOperator
from tests.sqlite_writer.test_backend import actualRun, makeDb
from tests.sqlite_writer.test_dependencies import config, project
from tests.designer.test_node_run_inspection import event


@pytest.mark.parametrize("ignoredBy", ["trigger", "unique"])
@pytest.mark.parametrize("policy", ["stop", "continue"])
def testIgnoredInsertFailsWithoutInventingRecordOrCommittingTriggerSideEffects(tmp_path, ignoredBy, policy):
    path = makeDb(tmp_path / "business.sqlite3")
    with sqlite3.connect(path) as connection:
        if ignoredBy == "trigger":
            connection.execute("CREATE TABLE audit (message TEXT)")
            connection.execute("CREATE TRIGGER ignore_record BEFORE INSERT ON records BEGIN "
                               "INSERT INTO audit VALUES ('must roll back'); SELECT RAISE(IGNORE); END")
        else:
            connection.execute("DROP TABLE records")
            connection.execute("CREATE TABLE records (id INTEGER PRIMARY KEY, write_id TEXT NOT NULL UNIQUE, "
                               "value TEXT UNIQUE ON CONFLICT IGNORE)")
    if ignoredBy == "unique":
        first, _, _ = actualRun(path, "same")
        assert first["status"] == "COMMITTED"
    if policy == "stop":
        with pytest.raises(WorkflowExecutionError) as failed:
            actualRun(path, "same", policy=policy)
        receipt = failed.value.diagnostics["sqliteReceipt"]
    else:
        receipt, _, events = actualRun(path, "same", policy=policy)
        assert any(row["eventType"] == "node.log" and row["level"] == "ERROR" for row in events)
    assert receipt["status"] == "FAILED" and receipt["rowsAffected"] == 0
    assert receipt["primaryKey"] is None and receipt["error"]["code"] == "E_SQLITE_NO_INSERT"
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM records").fetchone()[0] == (ignoredBy == "unique")
        assert connection.execute("SELECT COUNT(*) FROM records WHERE write_id=?", (receipt["writeId"],)).fetchone()[0] == 0
        if ignoredBy == "trigger":
            assert connection.execute("SELECT COUNT(*) FROM audit").fetchone()[0] == 0


@pytest.mark.parametrize("policy", ["stop", "continue"])
def testCancelAfterConfirmedCommitKeepsSameExecutionReceiptInFailedNode(tmp_path, policy):
    path = makeDb(tmp_path / "cancel.sqlite3")
    operators = {OPERATOR_ID: SqliteWriterOperator}
    payload = project()
    payload["workflows"]["main"]["nodes"][0]["params"] = config(path, failure=policy)
    token = CancellationToken()
    events = []

    def publish(**row):
        events.append(row)
        if row["eventType"] == "sqlite.write.finished" and row["payload"]["receipt"]["status"] == "COMMITTED":
            token.cancel()

    runner = WorkflowRunner(WorkflowCompiler(operators).compile(payload), operators, eventPublisher=publish)
    with pytest.raises(CancellationRequested):
        runner.run("main", {"value": 7}, RunContext.root("cancel-job", "main"), token)
    committed = next(row["payload"]["receipt"] for row in events if row["eventType"] == "sqlite.write.finished")
    failed = next(row for row in events if row["eventType"] == "node.failed")
    assert failed["payload"]["code"] == "E_CANCELLED"
    assert failed["payload"]["diagnostics"]["sqliteReceipt"] == committed
    inspection = NodeRunInspection()
    for sequence, row in enumerate(events, 1):
        context = row["context"]
        inspection.applyEvent({"eventType": row["eventType"], "sequence": sequence,
            "jobId": context.jobId, "workflowId": context.workflowId, "nodeId": context.callerNodeId,
            "workflowRunId": context.workflowRunId, "nodeRunId": context.nodeRunId,
            "payload": row.get("payload", {}), "code": row.get("code", "")})
    record = inspection.get("main", "writer", "cancel-job")
    assert record["status"] == "FAILED" and record["io"]["outputs"] is None
    assert record["sqliteReceipt"]["status"] == "COMMITTED" and record["sqliteReceipt"]["rowsAffected"] == 1
    assert encodedBytes(record) <= 8192
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT write_id, value FROM records").fetchall() == [(committed["writeId"], "7")]
    # A later failed invocation cannot borrow the cancelled invocation's commit.
    sequence = len(events) + 1
    inspection.applyEvent(event("node.started", sequence, job="cancel-job", node="writer", execution="later"))
    inspection.applyEvent(event("node.failed", sequence + 1, job="cancel-job", node="writer", execution="later"))
    assert "sqliteReceipt" not in inspection.get("main", "writer", "cancel-job")
