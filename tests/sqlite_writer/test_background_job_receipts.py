from contextlib import closing
import json
from pathlib import Path
import sqlite3
from threading import Event, get_ident

import pytest

from emo_master import __version__
from emo_master.apps.runtime.business_sqlite import backend
from emo_master.apps.runtime.execution.background_tasks import BackgroundTaskService
from emo_master.apps.runtime.execution.errors import WorkflowExecutionError
from emo_master.apps.runtime.workflow.cancellation import CancellationToken
from emo_master.apps.runtime.workflow.context import RunContext
from emo_master.apps.runtime.workflow.runner import WorkflowRunner
from emo_master.core.plugin.registry import PluginRegistry
from emo_master.core.workflow.compiler import WorkflowCompiler
from tests.sqlite_writer.test_backend import makeDb
from tests.sqlite_writer.test_dependencies import config, project


def setupRun(path, policy, service, events):
    plugins = Path("src/emo_master/plugins/builtins/sqlite_writer")
    registry = PluginRegistry(__version__).scanRoots([plugins]).activeOperators
    payload = project()
    payload["workflows"]["main"]["nodes"][0]["params"] = dict(config(path, failure=policy), writeMode="background")
    owner = get_ident()

    def publish(**event):
        assert get_ident() == owner
        events.append(event)

    return WorkflowRunner(WorkflowCompiler(registry).compile(payload), registry, backgroundTasks=service,
                          eventPublisher=publish)


def matching(events, eventType, *, node="writer"):
    return [row for row in events if row["eventType"] == eventType and row["context"].callerNodeId == node]


@pytest.mark.parametrize("outcome", ["commit", "continue", "stop"])
def testPollingProceedsBeforeSqliteCompletesAndRetainsOriginalIdentity(tmp_path, monkeypatch, outcome):
    path = makeDb(tmp_path / "history.db")
    if outcome != "commit":
        with closing(sqlite3.connect(path)) as conn:
            conn.execute("CREATE TRIGGER reject BEFORE INSERT ON records BEGIN SELECT RAISE(ABORT,'blocked'); END")
    release, writing = Event(), Event()
    insert = backend.insert

    def gated(*args):
        writing.set()
        assert release.wait(5)
        return insert(*args)

    monkeypatch.setattr(backend, "insert", gated)
    token = CancellationToken()
    service = BackgroundTaskService(token)
    events = []
    runner = setupRun(path, "stop" if outcome == "stop" else "continue", service, events)
    context = RunContext.root("production", "main")
    try:
        runner.run("main", {"value": 3}, context, token)
        assert writing.wait(5)
        [submitted] = matching(events, "node.completed")
        [started] = matching(events, "sqlite.write.started")
        assert started["context"] == submitted["context"]
        queued = submitted["payload"]["diagnostics"]["sqliteReceipt"]
        assert queued["status"] == "QUEUED" and queued["rowsAffected"] is None
        runner.pollBackgroundTasks()
        assert not matching(events, "sqlite.write.finished")
        with closing(sqlite3.connect(path)) as conn:
            assert conn.execute("SELECT COUNT(*) FROM records").fetchone() == (0,)
    finally:
        release.set()
        service.close()
    if outcome == "stop":
        with pytest.raises(WorkflowExecutionError) as failed:
            runner.pollBackgroundTasks()
        assert failed.value.diagnostics["backgroundTask"]["nodeRunId"] == submitted["context"].nodeRunId
    runner.pollBackgroundTasks()
    [finished] = matching(events, "sqlite.write.finished")
    assert finished["context"] == submitted["context"] and finished["payload"]["background"]
    final = finished["payload"]["receipt"]
    assert final["execution"] == queued["execution"] and final["writeId"] == queued["writeId"]
    with closing(sqlite3.connect(path)) as conn:
        rows = conn.execute("SELECT id,write_id,value FROM records").fetchall()
    if outcome == "commit":
        assert rows == [(final["primaryKey"], queued["writeId"], "3")]
        assert finished["level"] == "INFO" and final["status"] == "COMMITTED" and final["rowsAffected"] == 1
    else:
        assert rows == []
        assert finished["level"] == "ERROR" and final["status"] == "FAILED" and final["rowsAffected"] == 0


def testStoppingReportsUnstartedQueuedReceiptWithoutWriting(tmp_path):
    path = makeDb(tmp_path / "history.db")
    token = CancellationToken()
    service = BackgroundTaskService(token)
    events = []
    runner = setupRun(path, "continue", service, events)
    context = RunContext.root("production", "main")
    release, occupied = Event(), Event()

    def occupy(control):
        occupied.set()
        assert release.wait(5)

    try:
        service.client(context, "other").submit(occupy, taskName="other", estimatedBytes=0)
        assert occupied.wait(5)
        runner.run("main", {"value": 7}, context, token)
    finally:
        token.cancel()
        release.set()
        service.close(drain=False, waitForActive=True)
    runner.pollBackgroundTasks()
    [finished] = matching(events, "sqlite.write.finished")
    [submitted] = matching(events, "node.completed")
    assert finished["context"] == submitted["context"]
    assert finished["level"] == "WARN" and finished["payload"]["receipt"]["status"] == "CANCELLED"
    assert finished["payload"]["receipt"]["rowsAffected"] == 0
    with closing(sqlite3.connect(path)) as conn:
        assert conn.execute("SELECT COUNT(*) FROM records").fetchone() == (0,)


def testSpawnedWorkerPublishesFinalBeforeJobCompletion(tmp_path):
    from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb
    from emo_master.apps.runtime.grpc_server.service import RuntimeService
    from tests.runtime.runtime_test_utils import waitForTerminal

    database = makeDb(tmp_path / "history.db")
    payload = project()
    payload["workflows"]["main"]["nodes"][0]["params"].update(databasePath=database.name, writeMode="background")
    (tmp_path / "project.json").write_text(json.dumps(payload), encoding="utf-8")
    runtime = RuntimeService(dbPath=tmp_path / "runtime.db", workspaceRoot=tmp_path / "jobs")
    try:
        loaded = runtime.LoadProject(pb.LoadProjectRequest(project_path=str(tmp_path)), None)
        assert loaded.ok, loaded.message
        job = runtime.StartJob(pb.StartJobRequest(project_id="sqlite-test", inputs_json='{"value":3}'), None)
        assert job.ok, job.message
        assert waitForTerminal(runtime, job.job_id).status == "COMPLETED"
        events = runtime.eventStore.readMerged(job.job_id)
        [submitted] = [e for e in events if e.nodeId == "writer" and e.eventType == "node.completed"]
        [finished] = [e for e in events if e.eventType == "sqlite.write.finished"]
        [terminal] = [e for e in events if e.eventType == "job.completed"]
        assert submitted.sequence < finished.sequence < terminal.sequence
        assert finished.nodeRunId == submitted.nodeRunId and finished.workflowRunId == submitted.workflowRunId
        queued = json.loads(submitted.payloadJson)["diagnostics"]["sqliteReceipt"]
        final = json.loads(finished.payloadJson)["receipt"]
        assert queued["status"] == "QUEUED" and final["status"] == "COMMITTED"
        assert final["writeId"] == queued["writeId"] and not runtime.sqliteOutcomes.pending
        with closing(sqlite3.connect(database)) as conn:
            assert conn.execute("SELECT write_id,value FROM records").fetchall() == [(final["writeId"], "3")]
    finally:
        runtime.close()


def testSingleOperatorDebugShowsFinalInIsolatedDatabase(tmp_path):
    from emo_master.apps.runtime.grpc_server.service import RuntimeService
    from tests.runtime.test_operator_debug_resources import connected, run

    database = makeDb(tmp_path / "history.db")
    params = dict(config(database, rows=[{"column": "value", "storageType": "JSON",
                          "source": {"kind": "constant", "value": 3}}]), writeMode="background")
    runtime = RuntimeService(dbPath=tmp_path / "runtime.db", workspaceRoot=tmp_path / "jobs")
    try:
        with connected(runtime, "vision.io.sqlite_writer") as debug:
            result = run(debug, params)
            assert result["status"] == "SUCCEEDED", result
            accepted = result["outputs"]["receipt"]
            final = result["diagnostics"]["sqliteReceipt"]
            assert accepted["status"] == "QUEUED" and final["status"] == "COMMITTED"
            assert final["writeId"] == accepted["writeId"]
            with closing(sqlite3.connect(result["diagnostics"]["debugDatabasePath"])) as conn:
                assert conn.execute("SELECT write_id,value FROM records").fetchall() == [(final["writeId"], "3")]
        with closing(sqlite3.connect(database)) as conn:
            assert conn.execute("SELECT COUNT(*) FROM records").fetchone() == (0,)
    finally:
        runtime.close()
