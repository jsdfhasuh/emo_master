from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import multiprocessing
from pathlib import Path
import threading
import time

from emo_master.apps.runtime.context.sqlite_store import SqliteStore
from emo_master.apps.runtime.context.runtime_lock import RuntimeDataLock
from emo_master.apps.runtime.events.event_store import EventStore
from emo_master.apps.runtime.grpc_server.generated import runtime_pb2
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
