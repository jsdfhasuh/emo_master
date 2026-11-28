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


def testRestartRecoversPreviouslyRegisteredImageAfterOldWriterExits(tmp_path: Path) -> None:
    import subprocess
    import sys
    from uuid import uuid4

    dbPath = tmp_path / "runtime.db"
    store = SqliteStore(dbPath)
    store.initialize()
    store.insertJob(jobId="legacy", projectId="project", workflowId="main", status="RUNNING")
    records = tmp_path / "jobs" / "legacy" / ".background-image-files"
    records.mkdir(parents=True)
    output = tmp_path / "images"
    output.mkdir()
    key = uuid4().hex
    staged = output / f".emo-image-{key}.tmp"
    staged.write_bytes(b"interrupted image")
    unrelated = output / f".emo-image-{uuid4().hex}.tmp"
    unrelated.write_bytes(b"other writer")
    registration = records / key
    registration.write_text(json.dumps(str(output.resolve())), encoding="utf-8")
    with subprocess.Popen([sys.executable, "-c", "import sys; sys.stdin.read()"], stdin=subprocess.PIPE) as old:
        try:
            store.updateJobStatus(jobId="legacy", status="RUNNING", pid=old.pid)
            service = RuntimeService(dbPath=dbPath)
            try:
                assert staged.exists() and registration.exists()
            finally:
                service.close()
        finally:
            old.communicate(timeout=10)
    # The old format stored each target directory as a JSON string. Keep that
    # record when the volume is temporarily unavailable at startup.
    offline = output.rename(tmp_path / "offline")
    service = RuntimeService(dbPath=dbPath)
    try:
        assert registration.exists()
        assert any(event.code == "E_IMAGE_STAGING_CLEANUP_DEFERRED"
                   for event in service.eventStore.readMerged("__runtime__"))
    finally:
        service.close()
    offline.rename(output)
    service = RuntimeService(dbPath=dbPath)
    try:
        assert not staged.exists() and not registration.exists()
        assert unrelated.read_bytes() == b"other writer"
    finally:
        service.close()


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
    replay = service.StreamJobEvents(runtime_pb2.StreamJobEventsRequest(job_id='A', after_sequence=1), None)
    assert [(event.sequence, event.event_type) for event in replay] == [(2, 'process.heartbeat'), (3, 'node.log')]
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
    latest = query(job_id='A', latest_first=True, limit=2)
    assert (latest.upper_event_id, latest.next_event_id, latest.has_more) == (5, 3, True)
    assert [row.event_id for row in latest.events] == [5, 3]
    store.append('A', 'node.log', 'after the recent window', timestampMs=600)
    earlier = query(job_id='A', latest_first=True, before_event_id=latest.next_event_id,
                    upper_event_id=latest.upper_event_id)
    assert (earlier.upper_event_id, earlier.next_event_id, earlier.has_more) == (5, 1, False)
    assert [row.event_id for row in earlier.events] == [1]
    empty = query(latest_first=True, before_event_id=earlier.next_event_id,
                  upper_event_id=earlier.upper_event_id)
    assert (empty.upper_event_id, empty.next_event_id, empty.has_more, len(empty.events)) == (5, 0, False, 0)
    assert query(latest_first=True, after_event_id=1).code == 'INVALID_ARGUMENT'
    assert query(before_event_id=1).code == 'INVALID_ARGUMENT'
    assert query(latest_first=True, before_event_id=0).code == 'INVALID_ARGUMENT'


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
    latest = query(latest_first=True)
    assert latest.ok and latest.ByteSize() <= 1800 and latest.has_more and latest.next_event_id == 2
    assert [row.event_id for row in latest.events] == [3, 2]
    earlier = query(latest_first=True, before_event_id=2, upper_event_id=latest.upper_event_id)
    assert earlier.ok and not earlier.has_more and earlier.next_event_id == 1
    assert [row.event_id for row in earlier.events] == [1]
    service.eventStore.append('A', 'node.log', 'oversize' * 300)
    large = query(after_event_id=3)
    assert not large.ok and large.code == 'EVENT_TOO_LARGE' and large.next_event_id == 3
    largeLatest = query(latest_first=True)
    assert not largeLatest.ok and largeLatest.code == 'EVENT_TOO_LARGE'
    assert largeLatest.next_event_id == 0 and not largeLatest.events
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
        capabilities = stub.GetRuntimeInfo(runtime_pb2.RuntimeInfoRequest(), timeout=5).capabilities
        assert {'runtime_logs_query_v1', 'runtime_logs_recent_v1', 'job_history_query_v1'} <= set(capabilities)
    finally:
        channel.close()
        server.stop(0).wait()
        service.close()


def testJobHistoryUsesPersistedTerminalRowsAndKeysetAfterRestart(tmp_path):
    from emo_master.apps.runtime.main import createRuntimeServer
    from emo_master.apps.designer.services.runtime_client import RuntimeClient

    path = tmp_path / 'job-history.db'
    sameEnd = 1_700_000_000_100
    first = RuntimeService(dbPath=path, runtimeInstanceId='old-runtime')
    try:
        for jobId, projectId, status, ended in (
            ('z', 'project', 'FAILED', sameEnd),
            ('a', 'project', 'ABORTED', sameEnd),
            ('older', 'project', 'COMPLETED', sameEnd - 1),
            ('other-project', 'elsewhere', 'COMPLETED', sameEnd + 1),
        ):
            first.sqliteStore.insertJob(jobId, projectId, projectRevision=7, workflowId='flow', acceptedAtMs=sameEnd - 10)
            first.sqliteStore.updateJobStatus(jobId, status, startedAtMs=sameEnd - 3, endedAtMs=ended,
                errorCode='E_SOFTWARE' if status == 'FAILED' else None,
                errorMessage='failed in software' if status == 'FAILED' else None,
                stopMode='graceful' if status == 'ABORTED' else None)
        with first.sqliteStore.connection() as connection:
            connection.execute(
                "INSERT INTO jobs (jobId, projectId, releaseId, status, startAt) VALUES "
                "('legacy', 'project', '', 'COMPLETED', '2023-11-14T22:13:19+00:00')"
            )
            connection.execute("UPDATE jobs SET endAt = '2023-11-14T22:13:20.100900+00:00' "
                               "WHERE jobId = 'other-project'")
        first.eventStore.append('z', 'node.log', 'old-runtime event', projectId='project')
    finally:
        first.close()

    service = RuntimeService(dbPath=path, runtimeInstanceId='new-runtime')
    service.sqliteStore.insertJob('still-active', 'project', status='RUNNING')
    server, port, _ = createRuntimeServer(port=0, runtimeService=service)
    server.start()
    channel = grpc.insecure_channel(f'127.0.0.1:{port}')
    client = RuntimeClient(runtime_pb2_grpc.RuntimeServiceStub(channel), ownedChannel=channel)
    try:
        assert not client.listRuns('new-runtime', 'project', includeTerminal=True).runs
        page = client.listJobHistory('new-runtime', 'project', limit=1)
        assert page.ok and page.has_more and page.next_cursor and page.runtime_instance_id == 'new-runtime'
        row, = page.jobs
        assert (row.job_id, row.project_revision, row.workflow_id, row.status) == ('z', 7, 'flow', 'FAILED')
        assert (row.accepted_at_ms, row.started_at_ms, row.ended_at_ms) == (sameEnd - 10, sameEnd - 3, sameEnd)
        assert (row.error_code, row.message) == ('E_SOFTWARE', 'failed in software')
        following = client.listJobHistory('new-runtime', 'project', limit=3, cursor=page.next_cursor)
        assert [row.job_id for row in following.jobs] == ['a', 'older', 'legacy']
        assert following.jobs[0].stop_mode == 'graceful'
        assert not following.has_more and not following.next_cursor
        legacy = following.jobs[-1]
        assert (legacy.accepted_at_ms, legacy.started_at_ms, legacy.ended_at_ms) == (1_699_999_999_000, 0, 0)
        timed = client.listJobHistory('new-runtime', 'project', startEndedAtMs=sameEnd, endEndedAtMs=sameEnd + 1)
        assert [row.job_id for row in timed.jobs] == ['z', 'a']
        archived = client.listJobHistory('new-runtime', 'elsewhere', startEndedAtMs=sameEnd, endEndedAtMs=sameEnd + 1)
        assert [row.job_id for row in archived.jobs] == ['other-project']
        onlyStopped = client.listJobHistory('new-runtime', 'project', statuses=['ABORTED'])
        assert [row.job_id for row in onlyStopped.jobs] == ['a']
        empty = client.listJobHistory('new-runtime', 'project', startEndedAtMs=sameEnd + 1)
        assert empty.ok and not empty.jobs and not empty.has_more and not empty.next_cursor
        assert client.listJobHistory('old-runtime', 'project').code == 'RESET_REQUIRED'
        assert client.listJobHistory('new-runtime', 'project', statuses=['RUNNING']).code == 'INVALID_ARGUMENT'
        assert client.listJobHistory('new-runtime', 'project', cursor='invalid').code == 'INVALID_ARGUMENT'
        events = client.queryRuntimeLogs(runtimeInstanceId='new-runtime', jobId='z', latestFirst=True)
        assert events['ok'] and events['runtimeInstanceId'] == 'new-runtime'
        assert [row['event']['runtimeInstanceId'] for row in events['events']] == ['old-runtime']
    finally:
        client.close()
        server.stop(0).wait()
        service.close()


def testWorkerSourceTimestampIsPreservedAndHeartbeatUsesReceiptTime(monkeypatch, tmp_path):
    from queue import Queue
    from emo_master.apps.runtime.jobs.worker_main import _put, heartbeatLoop
    from emo_master.apps.runtime.jobs.models import JobRecord, JobStatus
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
    service = RuntimeService(dbPath=tmp_path / 'heartbeat.db')
    try:
        supervisor = service.jobSupervisor
        store = service.eventStore
        jobId = 'job-stop'
        retention = 5
        service.sqliteStore.jobEventRetention = store.retentionPerJob = retention
        service.jobRepository.create(JobRecord(jobId, 'project', 1, 'flow', status=JobStatus.RUNNING.value))
        supervisor.consumeWorkerEvent(jobId, {
            'eventType': 'node.failed', 'message': 'earlier failure', 'level': 'ERROR',
            'code': 'E_SAMPLE', 'timestampMs': 123,
        })
        [confirmed] = service.StreamJobEvents(runtime_pb2.StreamJobEventsRequest(job_id=jobId), None)
        assert (confirmed.sequence, confirmed.timestamp_ms, confirmed.code) == (1, 123, 'E_SAMPLE')

        for receiptTime in range(450, 450 + retention * 2):
            monkeypatch.setattr(module, 'nowMs', lambda: receiptTime)
            supervisor.consumeWorkerEvent(jobId, dict(pulse, timestampMs=124))
        assert supervisor._heartbeat[jobId] == 450 + retention * 2 - 1
        assert jobId in supervisor._heartbeatSeen
        assert [event.sequence for event in store.read(jobId)] == [confirmed.sequence]
        assert [event.sequence for event in service.sqliteStore.listJobEventsAfter(jobId)] == [confirmed.sequence]

        supervisor.consumeWorkerEvent(jobId, {'eventType': 'node.started', 'timestampMs': 125})
        supervisor.consumeWorkerEvents(jobId, [
            {'eventType': 'node.log', 'timestampMs': 126},
            {'eventType': 'node.completed', 'timestampMs': 127},
        ])
        supervisor.consumeWorkerEvent(jobId, {'eventType': 'job.completed', 'timestampMs': 128})
        resumed = service.StreamJobEvents(runtime_pb2.StreamJobEventsRequest(
            job_id=jobId, after_sequence=confirmed.sequence, follow=True), None)
        assert [(event.sequence, event.event_type, event.timestamp_ms) for event in resumed] == [
            (2, 'node.started', 125), (3, 'node.log', 126),
            (4, 'node.completed', 127), (5, 'job.completed', 128),
        ]
        page = service.QueryRuntimeLogs(runtime_pb2.QueryRuntimeLogsRequest(job_id=jobId, levels=['ERROR']), None)
        assert [(row.event.sequence, row.event.message) for row in page.events] == [(1, 'earlier failure')]
    finally:
        service.close()
