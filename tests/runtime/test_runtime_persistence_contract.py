from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import multiprocessing
from pathlib import Path
import threading
import time
import json
import sqlite3

import grpc
import pytest

from emo_master.apps.runtime.context.sqlite_store import SqliteStore
from emo_master.apps.runtime.context.runtime_lock import RuntimeDataLock
from emo_master.apps.runtime.events.event_store import EventStore
from emo_master.apps.runtime.grpc_server.generated import runtime_pb2
from emo_master.apps.runtime.grpc_server.generated import runtime_pb2_grpc
from emo_master.apps.runtime.grpc_server.service import (
    RuntimeService,
    _defaultDbPath,
    _defaultWorkspaceRoot,
)


def _tryAcquireRuntimeDataLock(path: str, ready, result) -> None:
    ready.set()
    lock = RuntimeDataLock(Path(path))
    try:
        result.put(lock.acquire())
    except RuntimeError:
        result.put(False)
    finally:
        lock.release()


def testEventReplayMergesSqliteHistoryAfterMemoryRetention(tmp_path: Path) -> None:
    persistence = SqliteStore(tmp_path / "events.db")
    persistence.initialize()
    store = EventStore(persistence, retentionPerJob=2)
    for index in range(4):
        store.append("job-history", f"event.{index}", str(index))

    assert [event.sequence for event in store.read("job-history")] == [3, 4]
    assert [event.sequence for event in store.readMerged("job-history")] == [1, 2, 3, 4]
    restarted = EventStore(persistence, retentionPerJob=2)
    assert [event.sequence for event in restarted.readMerged("job-history")] == [1, 2, 3, 4]


def testEventStoreRestoresTerminalSequenceFromSqlite(tmp_path: Path) -> None:
    persistence = SqliteStore(tmp_path / "terminal-events.db")
    persistence.initialize()
    original = EventStore(persistence)
    terminal = original.append("job-terminal", "job.completed", "done")

    restarted = EventStore(persistence)

    assert persistence.getTerminalJobEventSequence("job-terminal") == terminal.sequence
    assert restarted.terminalSequence("job-terminal") == terminal.sequence
    assert list(
        restarted.follow("job-terminal", afterSequence=terminal.sequence)
    ) == []


class _InactiveGrpcContext:
    def is_active(self) -> bool:
        return False


def testEventFollowStopsWhenGrpcContextIsCancelled() -> None:
    store = EventStore()

    assert list(
        store.follow("job-cancelled", cancellation=_InactiveGrpcContext())
    ) == []


def testEventStoreAllocatesSequencesAtomicallyAcrossInstances(tmp_path: Path) -> None:
    dbPath = tmp_path / "concurrent-events.db"
    persistence = SqliteStore(dbPath)
    persistence.initialize()
    stores = [EventStore(SqliteStore(dbPath)), EventStore(SqliteStore(dbPath))]
    barrier = threading.Barrier(2)

    def append(store: EventStore, index: int) -> int:
        barrier.wait()
        return store.append("shared-job", f"event.{index}", str(index)).sequence

    with ThreadPoolExecutor(max_workers=2) as executor:
        sequences = list(executor.map(append, stores, range(2)))

    assert sorted(sequences) == [1, 2]
    assert [event.sequence for event in persistence.listJobEventsAfter("shared-job")] == [1, 2]


def testEventStoreDispatchesSinksInAssignedSequenceOrder() -> None:
    store = EventStore()
    received: list[int] = []
    store.addSink(lambda event: received.append(event.sequence))

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(
            executor.map(
                lambda index: store.append(
                    "sink-order-job", f"node.{index}", str(index)
                ),
                range(100),
            )
        )

    assert received == list(range(1, 101))


def testRuntimeDataLockRejectsAnotherProcess(tmp_path: Path) -> None:
    lock = RuntimeDataLock(tmp_path / "runtime.lock")
    assert lock.acquire() is True
    context = multiprocessing.get_context("spawn")
    ready = context.Event()
    result = context.Queue()
    process = context.Process(
        target=_tryAcquireRuntimeDataLock,
        args=(str(lock.path), ready, result),
    )
    process.start()
    try:
        assert ready.wait(5)
        assert result.get(timeout=5) is False
        process.join(timeout=5)
        assert process.exitcode == 0
    finally:
        if process.is_alive():
            process.terminate()
            process.join(timeout=5)
        lock.release()


def testRuntimeRestartMarksOrphanedJobAndPersistsFailureEvent(tmp_path: Path) -> None:
    persistence = SqliteStore(tmp_path / "restart.db")
    persistence.initialize()
    persistence.insertJob(
        jobId="orphan-job",
        projectId="project",
        workflowId="main",
        status="RUNNING",
    )
    persistence.appendJobEvent(
        jobId="orphan-job",
        nodeId="",
        eventType="job.started",
        level="INFO",
        code="",
        message="started",
        payloadJson="{}",
        projectId="project",
        workflowId="main",
    )
    service = RuntimeService(dbPath=tmp_path / "restart.db")
    try:
        status = service.GetJobStatus(
            runtime_pb2.GetJobStatusRequest(job_id="orphan-job"), None
        )
        assert status.status == "FAILED"
        events = list(
            service.StreamJobEvents(
                runtime_pb2.StreamJobEventsRequest(job_id="orphan-job"), None
            )
        )
        assert events[-1].code == "E_RUNTIME_RESTARTED"
        assert events[-1].sequence == 2
    finally:
        service.close()


def testSecondRuntimeServiceDoesNotReapLiveJobInSameProcess(tmp_path: Path) -> None:
    dbPath = tmp_path / "shared-runtime.db"
    workspaceRoot = tmp_path / "jobs"
    first = RuntimeService(dbPath=dbPath, workspaceRoot=workspaceRoot)
    second = None
    try:
        first.sqliteStore.insertJob(
            jobId="live-job",
            projectId="project",
            workflowId="main",
            status="RUNNING",
        )
        second = RuntimeService(dbPath=dbPath, workspaceRoot=workspaceRoot)
        status = second.GetJobStatus(
            runtime_pb2.GetJobStatusRequest(job_id="live-job"), None
        )
        assert status.status == "RUNNING"
    finally:
        if second is not None:
            second.close()
        first.close()


def testDefaultRuntimeDataDirectoryAndDbPathAreStable(monkeypatch, tmp_path: Path) -> None:
    dataDir = tmp_path / "runtime-data"
    monkeypatch.setenv("EMO_RUNTIME_DATA_DIR", str(dataDir))
    monkeypatch.delenv("EMO_RUNTIME_DB_PATH", raising=False)
    monkeypatch.delenv("EMO_MASTER_RUNTIME_DB_PATH", raising=False)

    assert _defaultDbPath() == dataDir / "emo_master.db"
    assert _defaultWorkspaceRoot() == dataDir / "jobs"
    assert _defaultDbPath() == _defaultDbPath()


def testExplicitRuntimeDbPathOverridesDataDirectory(monkeypatch, tmp_path: Path) -> None:
    explicit = tmp_path / "explicit.db"
    monkeypatch.setenv("EMO_RUNTIME_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("EMO_RUNTIME_DB_PATH", str(explicit))

    assert _defaultDbPath() == explicit


def testSqliteRetentionDeletesOnlyOldUnprotectedTerminalJobEvents(
    tmp_path: Path,
) -> None:
    store = SqliteStore(tmp_path / "retention.db")
    store.initialize()
    nowMs = int(time.time() * 1000.0)

    def addJob(
        jobId: str,
        projectId: str,
        *,
        ageDays: int,
        status: str = "COMPLETED",
    ) -> None:
        store.insertJob(
            jobId=jobId,
            projectId=projectId,
            workflowId="main",
            status=status,
        )
        if status in {"COMPLETED", "FAILED", "ABORTED"}:
            store.updateJobStatus(
                jobId,
                status,
                endedAtMs=nowMs - ageDays * 86400000,
            )
        store.appendJobEvent(
            jobId=jobId,
            nodeId="",
            eventType=("job.completed" if status == "COMPLETED" else "job.started"),
            level="INFO",
            code="",
            message=jobId,
            payloadJson="{}",
            projectId=projectId,
            workflowId="main",
            timestamp=nowMs - ageDays * 86400000,
        )

    # The two newest terminal jobs are protected for project-a even though both
    # are older than 30 days.  Only its third-oldest terminal job is pruned.
    addJob("project-a-newest", "project-a", ageDays=35)
    addJob("project-a-second", "project-a", ageDays=40)
    addJob("project-a-pruned", "project-a", ageDays=50)
    addJob("project-b-only", "project-b", ageDays=60)
    addJob("project-a-running", "project-a", ageDays=90, status="RUNNING")

    deleted = store.pruneTerminalJobEvents(
        retentionDays=30,
        minimumJobsPerProject=2,
        currentTimestampMs=nowMs,
    )

    assert deleted == 1
    assert store.listJobEventsAfter("project-a-pruned") == []
    for jobId in (
        "project-a-newest",
        "project-a-second",
        "project-b-only",
        "project-a-running",
    ):
        assert len(store.listJobEventsAfter(jobId)) == 1

    store.checkpointWal()
    assert isinstance(store.vacuumIfNeeded(0.0), bool)


def testLoggingMetadataMigrationFromV5PreservesHistoryAndReopens(tmp_path):
    from emo_master.apps.runtime.context.global_variables import migrateVariables
    import emo_master.apps.runtime.context.sqlite_store as module
    db = tmp_path / 'old.db'
    migrations = Path(module.__file__).parent / 'migrations'
    with sqlite3.connect(db) as connection:
        for name in ('001_init.sql', '002_runtime_workflow.sql', '003_runtime_timestamps.sql', '004_global_counters.sql'):
            connection.executescript((migrations / name).read_text(encoding='utf-8'))
        migrateVariables(connection)
        connection.execute('CREATE TABLE IF NOT EXISTS schemaMigrations(version INTEGER PRIMARY KEY, appliedAt TEXT NOT NULL)')
        connection.executemany("INSERT OR REPLACE INTO schemaMigrations VALUES (?, 'old')", [(i,) for i in range(1, 6)])
        for index, eventType in enumerate(('node.log', 'node.completed', 'device.connect.failed',
                                            'presentation.source.unavailable', 'runtime.logfile.failed', 'unknown',
                                            'dispatch.invocation.failed', 'dispatch.invocation.completed')):
            connection.execute("""INSERT INTO jobEvents(jobId,nodeId,eventType,level,code,message,payloadJson,
                timestamp,sequence,timestampMs) VALUES ('old-job','node',?,'INFO','','old',?,'old',?,123)""",
                (eventType, json.dumps({'original': index}), index + 1))
    store = SqliteStore(db)
    store.initialize()
    store.initialize()
    rows = SqliteStore(db).listJobEventsAfter('old-job')
    assert [event.component for event in rows] == ['operator', 'execution', 'device', 'presentation', 'storage',
                                                  'application', 'execution', 'execution']
    assert all(event.originApp == 'runtime' and event.runtimeInstanceId == '' for event in rows)
    assert [event.sequence for event in rows] == list(range(1, 9))
    assert [json.loads(event.payloadJson) for event in rows] == [{'original': i} for i in range(8)]
    assert [eventId for eventId, _ in store.queryRuntimeLogs()[1]] == list(range(1, 9))
    events = EventStore(store, runtimeInstanceId='current-instance')
    for eventType in ('dispatch.invocation.failed', 'dispatch.invocation.completed'):
        event = events.append('current-job', eventType, 'dispatch result')
        assert event.component == 'execution'
    current = SqliteStore(db).listJobEventsAfter('current-job')
    assert [event.component for event in current] == ['execution', 'execution']
    assert all(event.runtimeInstanceId == 'current-instance' for event in current)


@pytest.fixture
def queryRpc(tmp_path):
    service = RuntimeService(dbPath=tmp_path / 'query.db', runtimeInstanceId='connected-instance')
    server = grpc.server(ThreadPoolExecutor(max_workers=2), options=[('grpc.max_send_message_length', 9 * 1024 * 1024)])
    runtime_pb2_grpc.add_RuntimeServiceServicer_to_server(service, server)
    port = server.add_insecure_port('127.0.0.1:0')
    server.start()
    channel = grpc.insecure_channel(f'127.0.0.1:{port}', options=[('grpc.max_receive_message_length', 9 * 1024 * 1024)])
    stub = runtime_pb2_grpc.RuntimeServiceStub(channel)
    try:
        yield service, lambda **fields: stub.QueryRuntimeLogs(runtime_pb2.QueryRuntimeLogsRequest(**fields), timeout=5)
    finally:
        channel.close()
        server.stop(0).wait()
        service.close()


def testQueryRuntimeLogsRpcFixedUpperIdentityAssociationsAndTime(queryRpc):
    service, query = queryRpc
    store = service.eventStore
    store.append('A', 'node.log', 'one', projectId='old-project', workflowId='flow', nodeId='node', timestampMs=100,
                 payload={'deviceBindings': {'camera': {'deviceId': 'camera'}, 'plc': {'deviceId': 'plc'}},
                          'diagnostics': {'deviceSlot': 'camera'}})
    store.append('B', 'node.log', 'two', projectId='other-project', timestampMs=200, runtimeInstanceId='earlier-instance',
                 payload={'deviceBindings': {'camera': {'deviceId': 'camera'}}})
    store.append('__runtime__', 'device.action.failed', 'failed', projectId='old-project', timestampMs=300,
                 payload={'jobId': 'A', 'deviceId': 'camera'})
    store.append('A', 'process.heartbeat', 'pulse', timestampMs=400)
    first = query(limit=1)
    assert first.ok and first.code == 'OK' and first.runtime_instance_id == 'connected-instance'
    assert (first.upper_event_id, first.next_event_id, first.has_more) == (4, 1, True)
    assert first.events[0].event.origin_app == 'runtime'
    assert first.events[0].event.component == 'operator'
    assert first.events[0].event.runtime_instance_id == 'connected-instance'
    store.append('A', 'node.log', 'new', timestampMs=500)
    second = query(after_event_id=first.next_event_id, upper_event_id=first.upper_event_id,
                   runtime_instance_id=first.runtime_instance_id)
    assert [row.event_id for row in second.events] == [2, 3]
    assert second.events[0].event.runtime_instance_id == 'earlier-instance'
    assert second.runtime_instance_id == 'connected-instance'
    assert not second.has_more and second.next_event_id == 4
    assert [row.event_id for row in query(after_event_id=4).events] == [5]
    assert [row.event_id for row in query(job_id='A').events] == [1, 3, 5]
    assert [row.event_id for row in query(device_id='camera').events] == [1, 2, 3]
    assert not query(device_id='plc').events
    assert [row.event_id for row in query(project_id='old-project').events] == [1, 3]
    assert [row.event_id for row in query(component='device', start_timestamp_ms=300, end_timestamp_ms=400).events] == [3]
    assert [row.event_id for row in query(workflow_id='flow', node_id='node', levels=['INFO']).events] == [1]
    assert not query(upper_event_id=0).events
    reset = query(runtime_instance_id='previous', after_event_id=3)
    assert not reset.ok and reset.code == 'RESET_REQUIRED' and reset.runtime_instance_id == 'connected-instance'
    assert reset.next_event_id == 3
    assert query(after_event_id=-1).code == 'INVALID_ARGUMENT'
    assert query(limit=1001).code == 'INVALID_ARGUMENT'


@pytest.mark.parametrize("completedType", ("node.completed", "dispatch.invocation.completed"))
def testQueryRuntimeLogsRpcByteBudgetAndProjectionKeepCursor(queryRpc, monkeypatch, completedType):
    import emo_master.apps.runtime.grpc_server.service as module
    service, query = queryRpc
    for index in range(3):
        service.eventStore.append('A', 'node.log', 'x' * 700, payload={'data': index})
    monkeypatch.setattr(module, 'LOG_QUERY_REPLY_BYTES', 1800)
    page = query()
    assert page.ok and page.ByteSize() <= 1800 and page.has_more
    assert [row.event_id for row in page.events] == [1, 2]
    assert page.next_event_id == 2
    following = query(after_event_id=page.next_event_id, upper_event_id=page.upper_event_id)
    assert [row.event_id for row in following.events] == [3]
    service.eventStore.append('A', 'node.log', 'oversize' * 300)
    large = query(after_event_id=3)
    assert not large.ok and large.code == 'EVENT_TOO_LARGE' and large.next_event_id == 3
    context = dict(runGroupId='group-1', dispatchId='dispatch-1', deliveryId='delivery-1',
                   invocationId='invocation-1', targetSlotId='slot-1', documentRevision=7)
    original = dict(context, outputs={'huge': 'x' * 5000}, status='OK')
    service.eventStore.append('B', completedType, 'done', payload=original)
    compact = query(job_id='B')
    assert compact.ok and json.loads(compact.events[0].event.payload_json) == dict(context, status='OK')
    assert json.loads(service.sqliteStore.listJobEventsAfter('B')[0].payloadJson) == original


def testRuntimeLogsQueryThroughProductionAioAdapter(tmp_path):
    from emo_master.apps.runtime.main import createRuntimeServer
    service = RuntimeService(dbPath=tmp_path / 'aio.db')
    service.eventStore.append('job', 'node.log', 'aio query')
    server, port, _ = createRuntimeServer(port=0, runtimeService=service)
    server.start()
    channel = grpc.insecure_channel(f'127.0.0.1:{port}')
    try:
        stub = runtime_pb2_grpc.RuntimeServiceStub(channel)
        page = stub.QueryRuntimeLogs(runtime_pb2.QueryRuntimeLogsRequest(), timeout=5)
        assert page.ok and page.events[0].event.message == 'aio query'
        assert page.events[0].event.runtime_instance_id == service.runtimeInstanceId
        assert 'runtime_logs_query_v1' in stub.GetRuntimeInfo(runtime_pb2.RuntimeInfoRequest(), timeout=5).capabilities
    finally:
        channel.close()
        server.stop(0).wait()
        service.close()


def testWorkerSourceTimestampIsPreservedAndHeartbeatUsesReceiptTime(monkeypatch):
    from queue import Queue
    from emo_master.apps.runtime.jobs.worker_main import _put, heartbeatLoop
    from tests.runtime.test_job_supervisor_contracts import _supervisor
    from threading import Event
    import emo_master.apps.runtime.jobs.supervisor as module
    queue = Queue()
    _put(queue, {'eventType': 'node.log'})
    assert abs(queue.get()['timestampMs'] - int(time.time() * 1000)) < 1000
    stop = Event()
    stop.set()
    heartbeatLoop('job-stop', 'project', 'flow', queue, stop, 100)
    pulse = queue.get()
    assert pulse['timestampMs'] > 0
    supervisor, _, store, _ = _supervisor()
    supervisor.consumeWorkerEvents('job-stop', [{'eventType': 'node.log', 'timestampMs': 123}])
    assert store.read('job-stop')[-1].timestampMs == 123
    monkeypatch.setattr(module, 'nowMs', lambda: 456)
    supervisor.consumeWorkerEvent('job-stop', dict(pulse, timestampMs=124))
    assert store.read('job-stop')[-1].timestampMs == 124
    assert supervisor._heartbeat['job-stop'] == 456
