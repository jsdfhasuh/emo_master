from __future__ import annotations

from pathlib import Path

from emo_master.apps.runtime.context.sqlite_store import SqliteStore
from emo_master.apps.runtime.events.event_store import EventStore
from emo_master.apps.runtime.grpc_server.generated import runtime_pb2
from emo_master.apps.runtime.grpc_server.service import (
    RuntimeService,
    _defaultDbPath,
    _defaultWorkspaceRoot,
)


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


class _InactiveGrpcContext:
    def is_active(self) -> bool:
        return False


def testEventFollowStopsWhenGrpcContextIsCancelled() -> None:
    store = EventStore()

    assert list(
        store.follow("job-cancelled", cancellation=_InactiveGrpcContext())
    ) == []


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
