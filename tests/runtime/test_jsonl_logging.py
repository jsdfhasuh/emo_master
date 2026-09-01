from __future__ import annotations

import json
import os
from pathlib import Path
import time

from emo_master.apps.runtime.events.jsonl_writer import RuntimeJsonlLogWriter
from emo_master.apps.runtime.events.models import RuntimeEvent
from emo_master.apps.runtime.grpc_server.generated import runtime_pb2
from emo_master.apps.runtime.grpc_server.service import RuntimeService


def _event(
    sequence: int,
    eventType: str = "node.log",
    *,
    level: str = "INFO",
    message: str = "消息",
    payload: dict[str, object] | None = None,
) -> RuntimeEvent:
    return RuntimeEvent(
        jobId="job-1",
        eventType=eventType,
        message=message,
        level=level,
        nodeId="node-1",
        code="E_SAMPLE" if level == "ERROR" else "",
        payloadJson=json.dumps(payload or {}, ensure_ascii=False),
        sequence=sequence,
        timestampMs=1_800_000_000_000 + sequence,
        projectId="project-1",
        workflowId="main",
        workflowRunId="workflow-run-1",
        parentWorkflowRunId="parent-run",
        nodeRunId="node-run-1",
        iterationPathJson="[2,3]",
    )


def _records(directory: Path) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    for path in sorted(directory.glob("runtime-*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            records.append(json.loads(line))
    return records


def testJsonlWriterPersistsUnicodeContextAndSequenceInEnqueueOrder(
    tmp_path: Path,
) -> None:
    writer = RuntimeJsonlLogWriter(tmp_path, flushIntervalSeconds=0.05)
    assert writer.enqueue(_event(1, message="中文坐标")) is True
    assert writer.enqueue(
        _event(2, eventType="node.failed", level="ERROR", message="失败")
    ) is True
    assert writer.close() is None

    records = _records(tmp_path)
    assert [record["sequence"] for record in records] == [1, 2]
    assert records[0] == {
        "schemaVersion": "1.0",
        "timestamp": records[0]["timestamp"],
        "timestampMs": 1_800_000_000_001,
        "sequence": 1,
        "level": "INFO",
        "eventType": "node.log",
        "jobId": "job-1",
        "projectId": "project-1",
        "workflowId": "main",
        "workflowRunId": "workflow-run-1",
        "parentWorkflowRunId": "parent-run",
        "nodeId": "node-1",
        "nodeRunId": "node-run-1",
        "iterationPath": [2, 3],
        "code": "",
        "message": "中文坐标",
        "payload": {},
    }


def testCompletedEventProjectionRemovesOutputsButKeepsOperationalSummary(
    tmp_path: Path,
) -> None:
    writer = RuntimeJsonlLogWriter(tmp_path)
    writer.enqueue(
        _event(
            1,
            eventType="node.completed",
            payload={
                "status": "COMPLETED",
                "outputs": {"image": "x" * 10000},
                "metrics": {"latencyMs": 3},
                "diagnostics": {"warning": False},
                "branch": "true",
                "unrelated": "drop",
            },
        )
    )
    assert writer.close() is None

    assert _records(tmp_path)[0]["payload"] == {
        "status": "COMPLETED",
        "metrics": {"latencyMs": 3},
        "diagnostics": {"warning": False},
        "branch": "true",
    }


def testJsonlWriterRotatesAtConfiguredSizeAndUsesStableFileNames(
    tmp_path: Path,
) -> None:
    writer = RuntimeJsonlLogWriter(tmp_path, maximumFileBytes=1024)
    for sequence in range(1, 5):
        assert writer.enqueue(_event(sequence, message="x" * 700)) is True
    assert writer.close() is None

    paths = sorted(tmp_path.glob("runtime-*.jsonl"))
    assert len(paths) >= 2
    assert all(path.name.count("-") == 4 for path in paths)
    assert [record["sequence"] for record in _records(tmp_path)] == [1, 2, 3, 4]


def testJsonlWriterDeletesExpiredAndOldestFilesWithoutDeletingActiveFile(
    tmp_path: Path,
) -> None:
    now = time.time()
    expired = tmp_path / "runtime-20200101-000000-1-0001.jsonl"
    oldest = tmp_path / "runtime-20260101-000000-1-0002.jsonl"
    newer = tmp_path / "runtime-20260102-000000-1-0003.jsonl"
    expired.write_bytes(b"x" * 900)
    oldest.write_bytes(b"x" * 900)
    newer.write_bytes(b"x" * 900)
    os.utime(expired, (now - 20 * 86400, now - 20 * 86400))
    os.utime(oldest, (now - 200, now - 200))
    os.utime(newer, (now - 100, now - 100))

    writer = RuntimeJsonlLogWriter(
        tmp_path,
        maximumFileBytes=1024,
        maximumTotalBytes=1024,
        retentionDays=14,
        wallClock=lambda: now,
    )
    assert not expired.exists()
    assert not oldest.exists()
    assert newer.exists()
    assert writer.enqueue(_event(1)) is True

    deadline = time.monotonic() + 2.0
    while writer.activePath is None and time.monotonic() < deadline:
        time.sleep(0.01)
    active = writer.activePath
    assert active is not None and active.exists()
    writer._cleanupClosedFiles()
    assert active.exists()
    assert writer.close() is None


def testJsonlFailureIsReportedWithoutRaisingOrCreatingRecursiveRecords(
    tmp_path: Path,
) -> None:
    invalidDirectory = tmp_path / "not-a-directory"
    invalidDirectory.write_text("file", encoding="utf-8")
    failures: list[tuple[RuntimeEvent | None, str]] = []
    writer = RuntimeJsonlLogWriter(
        invalidDirectory,
        failureCallback=lambda event, message: failures.append((event, message)),
    )

    assert writer.enqueue(_event(1)) is False
    assert writer.close() is None
    assert len(failures) == 1
    assert failures[0][0] is None
    assert "cannot initialize runtime log directory" in failures[0][1]


def testJsonlFsyncFailureIsDeduplicatedAndKeepsSourceContext(
    tmp_path: Path,
    monkeypatch,
) -> None:
    failures: list[tuple[RuntimeEvent | None, str]] = []
    monkeypatch.setattr(
        "emo_master.apps.runtime.events.jsonl_writer.os.fsync",
        lambda _fileDescriptor: (_ for _ in ()).throw(OSError("fsync failed")),
    )
    writer = RuntimeJsonlLogWriter(
        tmp_path,
        failureCallback=lambda event, message: failures.append((event, message)),
    )
    assert writer.enqueue(_event(1, eventType="node.failed", level="ERROR"))
    assert writer.enqueue(_event(2, eventType="job.failed", level="ERROR"))
    assert writer.close() is None

    assert len(failures) == 1
    assert failures[0][0] is not None
    assert failures[0][0].jobId == "job-1"
    assert failures[0][1] == "runtime JSONL flush failed: fsync failed"


def testJsonlWriterSkipsNonOperationalAndFailureNotificationEvents(
    tmp_path: Path,
) -> None:
    writer = RuntimeJsonlLogWriter(tmp_path)
    assert writer.enqueue(_event(1, eventType="preview.snapshot.failed")) is False
    assert writer.enqueue(_event(2, eventType="runtime.logfile.failed")) is False
    assert writer.close() is None
    assert list(tmp_path.glob("runtime-*.jsonl")) == []


def testRuntimeServiceSinkKeepsSqliteAndJsonlSequencesAligned(
    tmp_path: Path,
) -> None:
    logDirectory = tmp_path / "logs"
    service = RuntimeService(
        dbPath=tmp_path / "runtime.db",
        workspaceRoot=tmp_path / "jobs",
        logDirectory=logDirectory,
    )
    try:
        first = service.eventStore.append(
            "job-service",
            "node.log",
            "operator message",
            nodeId="node",
            projectId="project",
            workflowId="main",
            payload={
                "operatorId": "test.operator",
                "phase": "execute",
                "data": {"count": 1},
            },
        )
        second = service.eventStore.append(
            "job-service",
            "job.completed",
            "job completed",
            payload={"status": "COMPLETED"},
        )
        assert (first.sequence, second.sequence) == (1, 2)
    finally:
        service.close()

    persisted = service.sqliteStore.listJobEventsAfter("job-service")
    records = _records(logDirectory)
    assert [event.sequence for event in persisted] == [1, 2]
    assert [record["sequence"] for record in records] == [1, 2]
    assert [record["eventType"] for record in records] == [
        "node.log",
        "job.completed",
    ]


def testRuntimeServiceFollowStreamReplaysLogFailureCreatedAfterTerminal(
    tmp_path: Path,
) -> None:
    service = RuntimeService(
        dbPath=tmp_path / "runtime.db",
        workspaceRoot=tmp_path / "jobs",
        logDirectory=tmp_path / "logs",
    )
    try:
        terminal = service.eventStore.append(
            "job-late-log-failure",
            "job.completed",
            "done",
            projectId="project",
            workflowId="main",
        )

        def waitUntilProcessed(
            jobId: str,
            sequence: int,
            timeoutSeconds: float = 3.0,
        ) -> bool:
            _ = timeoutSeconds
            assert jobId == terminal.jobId
            assert sequence == terminal.sequence
            service._recordLogFileFailure(
                terminal,
                "late runtime JSONL failure",
            )
            return True

        service.operationalLogWriter.waitUntilProcessed = waitUntilProcessed  # type: ignore[method-assign]
        events = list(
            service.StreamJobEvents(
                runtime_pb2.StreamJobEventsRequest(
                    job_id=terminal.jobId,
                    follow=True,
                ),
                None,
            )
        )

        assert [event.event_type for event in events] == [
            "job.completed",
            "runtime.logfile.failed",
        ]
        assert events[-1].sequence == terminal.sequence + 1
    finally:
        service.close()


def testRuntimeServiceTerminalResumeStillWaitsForLateLogFailure(
    tmp_path: Path,
) -> None:
    service = RuntimeService(
        dbPath=tmp_path / "runtime.db",
        workspaceRoot=tmp_path / "jobs",
        logDirectory=tmp_path / "logs",
    )
    try:
        terminal = service.eventStore.append(
            "job-terminal-resume",
            "job.completed",
            "done",
            projectId="project",
            workflowId="main",
        )

        def waitUntilProcessed(
            jobId: str,
            sequence: int,
            timeoutSeconds: float = 3.0,
        ) -> bool:
            _ = timeoutSeconds
            assert jobId == terminal.jobId
            assert sequence == terminal.sequence
            service._recordLogFileFailure(
                terminal,
                "late runtime JSONL failure after terminal resume",
            )
            return True

        service.operationalLogWriter.waitUntilProcessed = waitUntilProcessed  # type: ignore[method-assign]
        events = list(
            service.StreamJobEvents(
                runtime_pb2.StreamJobEventsRequest(
                    job_id=terminal.jobId,
                    after_sequence=terminal.sequence,
                    follow=True,
                ),
                None,
            )
        )

        assert [event.event_type for event in events] == [
            "runtime.logfile.failed",
        ]
        assert events[0].sequence == terminal.sequence + 1
    finally:
        service.close()


def testRuntimeServiceRecordsLogfileInitializationFailureOnlyInSqlite(
    tmp_path: Path,
) -> None:
    invalidDirectory = tmp_path / "logs-file"
    invalidDirectory.write_text("not a directory", encoding="utf-8")
    service = RuntimeService(
        dbPath=tmp_path / "runtime.db",
        workspaceRoot=tmp_path / "jobs",
        logDirectory=invalidDirectory,
    )
    try:
        events = service.sqliteStore.listJobEventsAfter("__runtime__")
        assert len(events) == 1
        assert events[0].eventType == "runtime.logfile.failed"
        assert events[0].level == "ERROR"
    finally:
        service.close()

    assert invalidDirectory.is_file()
