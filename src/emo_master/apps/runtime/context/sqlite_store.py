from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sqlite3
import time

from emo_master.apps.runtime.events.models import RuntimeEvent


class SqliteStore:
    def __init__(self, dbPath: Path) -> None:
        self.dbPath = dbPath
        self.dbPath.parent.mkdir(parents=True, exist_ok=True)

    def initialize(self) -> None:
        with self._connect() as connection:
            migrationDir = Path(__file__).parent / "migrations"
            connection.executescript(
                (migrationDir / "001_init.sql").read_text(encoding="utf-8")
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS schemaMigrations (version INTEGER PRIMARY KEY, appliedAt TEXT NOT NULL)"
            )
            applied = {
                int(row[0])
                for row in connection.execute("SELECT version FROM schemaMigrations")
            }
            if 1 not in applied:
                connection.execute(
                    "INSERT INTO schemaMigrations(version, appliedAt) VALUES (1, ?)",
                    (_utcNow(),),
                )
            if 2 not in applied:
                connection.executescript(
                    (migrationDir / "002_runtime_workflow.sql").read_text(encoding="utf-8")
                )
                connection.execute(
                    "INSERT INTO schemaMigrations(version, appliedAt) VALUES (2, ?)",
                    (_utcNow(),),
                )
            if 3 not in applied:
                connection.executescript(
                    (migrationDir / "003_runtime_timestamps.sql").read_text(encoding="utf-8")
                )
                connection.execute(
                    "INSERT INTO schemaMigrations(version, appliedAt) VALUES (3, ?)",
                    (_utcNow(),),
                )
            connection.commit()

    def insertJob(
        self,
        jobId: str,
        projectId: str,
        releaseId: str = "",
        status: str = "ACCEPTED",
        projectRevision: int = 1,
        workflowId: str = "",
        pid: int | None = None,
        acceptedAtMs: int | None = None,
    ) -> None:
        accepted = acceptedAtMs if acceptedAtMs is not None else _timestampMs()
        now = _utcNow()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO jobs(
                  jobId, projectId, releaseId, status, startAt, endAt,
                  durationMs, errorCode, projectRevision, workflowId, pid,
                  acceptedAt, errorMessage, stopMode
                ) VALUES (?, ?, ?, ?, ?, NULL, NULL, NULL, ?, ?, ?, ?, NULL, NULL)
                """,
                (
                    jobId,
                    projectId,
                    releaseId,
                    status,
                    now,
                    projectRevision,
                    workflowId,
                    pid,
                    datetime.fromtimestamp(accepted / 1000.0, timezone.utc).isoformat(),
                ),
            )
            connection.commit()

    def updateJobStatus(
        self,
        jobId: str,
        status: str,
        errorCode: str | None = None,
        errorMessage: str | None = None,
        endedAtMs: int | None = None,
        startedAtMs: int | None = None,
        pid: int | None = None,
        stopMode: str | None = None,
    ) -> None:
        endAt = (
            datetime.fromtimestamp(endedAtMs / 1000.0, timezone.utc).isoformat()
            if endedAtMs is not None
            else None
        )
        startedAt = (
            datetime.fromtimestamp(startedAtMs / 1000.0, timezone.utc).isoformat()
            if startedAtMs is not None
            else None
        )
        with self._connect() as connection:
            current = connection.execute(
                "SELECT startAt FROM jobs WHERE jobId = ?", (jobId,)
            ).fetchone()
            duration: float | None = None
            if endAt is not None and current is not None:
                try:
                    start = datetime.fromisoformat(str(current[0])).timestamp()
                    duration = max(0.0, (endedAtMs or 0) / 1000.0 - start)
                except (TypeError, ValueError, OSError):
                    duration = None
            connection.execute(
                """
                UPDATE jobs SET status = ?, errorCode = COALESCE(?, errorCode),
                  errorMessage = COALESCE(?, errorMessage), endAt = COALESCE(?, endAt),
                  durationMs = COALESCE(?, durationMs), pid = COALESCE(?, pid),
                  stopMode = COALESCE(?, stopMode),
                  startedAt = COALESCE(?, startedAt)
                WHERE jobId = ?
                """,
                (
                    status,
                    errorCode,
                    errorMessage,
                    endAt,
                    duration * 1000.0 if duration is not None else None,
                    pid,
                    stopMode,
                    startedAt,
                    jobId,
                ),
            )
            connection.commit()

    def appendJobEvent(
        self,
        jobId: str,
        nodeId: str,
        eventType: str,
        level: str,
        code: str,
        message: str,
        payloadJson: str,
        sequence: int | None = None,
        projectId: str = "",
        workflowId: str = "",
        workflowRunId: str = "",
        parentWorkflowRunId: str = "",
        nodeRunId: str = "",
        iterationPathJson: str = "[]",
        timestamp: int | str | None = None,
    ) -> int:
        timestampMs = timestamp if isinstance(timestamp, int) else _timestampMs()
        timestampText = _utcNow() if not isinstance(timestamp, str) else timestamp
        with self._connect() as connection:
            if sequence is None:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    "SELECT COALESCE(MAX(sequence), 0) + 1 FROM jobEvents WHERE jobId = ?",
                    (jobId,),
                ).fetchone()
                sequence = int(row[0]) if row is not None else 1
            connection.execute(
                """
                INSERT INTO jobEvents(
                  jobId, nodeId, eventType, level, code, message, payloadJson,
                  timestamp, sequence, projectId, workflowId, workflowRunId,
                  parentWorkflowRunId, nodeRunId, iterationPathJson, timestampMs
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    jobId,
                    nodeId,
                    eventType,
                    level,
                    code,
                    message,
                    payloadJson,
                    timestampText,
                    sequence,
                    projectId,
                    workflowId,
                    workflowRunId,
                    parentWorkflowRunId,
                    nodeRunId,
                    iterationPathJson,
                    timestampMs,
                ),
            )
            connection.commit()
        return sequence

    def getLastJobEventSequence(self, jobId: str) -> int:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT COALESCE(MAX(sequence), 0) FROM jobEvents WHERE jobId = ?",
                (jobId,),
            ).fetchone()
        return int(row[0]) if row is not None else 0

    def getTerminalJobEventSequence(self, jobId: str) -> int:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT COALESCE(MAX(sequence), 0)
                FROM jobEvents
                WHERE jobId = ?
                  AND eventType IN ('job.completed', 'job.failed', 'job.aborted')
                """,
                (jobId,),
            ).fetchone()
        return int(row[0]) if row is not None else 0

    def getJob(self, jobId: str) -> dict[str, object] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT jobId, projectId, projectRevision, workflowId, status, pid, acceptedAt, startAt, startedAt, endAt, durationMs, errorCode, errorMessage, stopMode FROM jobs WHERE jobId = ?",
                (jobId,),
            ).fetchone()
        if row is None:
            return None
        fields = [
            "jobId", "projectId", "projectRevision", "workflowId", "status", "pid",
            "acceptedAt", "startAt", "startedAt", "endAt", "durationMs", "errorCode",
            "errorMessage", "stopMode",
        ]
        return dict(zip(fields, row))

    def listJobEventsAfter(self, jobId: str, afterSequence: int = 0) -> list[RuntimeEvent]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT jobId, eventType, message, level, nodeId, code, payloadJson,
                  sequence, timestampMs, projectId, workflowId, workflowRunId,
                  parentWorkflowRunId, nodeRunId, iterationPathJson
                FROM jobEvents WHERE jobId = ? AND sequence > ? ORDER BY sequence
                """,
                (jobId, afterSequence),
            ).fetchall()
        return [RuntimeEvent(*row) for row in rows]

    def markOrphanedJobsFailed(self) -> int:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                "SELECT jobId, projectId, workflowId, startAt FROM jobs "
                "WHERE status IN ('ACCEPTED', 'STARTING', 'RUNNING', 'STOPPING')"
            ).fetchall()
            now = _utcNow()
            nowMs = _timestampMs()
            for jobId, projectId, workflowId, startAt in rows:
                durationMs = None
                try:
                    started = datetime.fromisoformat(str(startAt)).timestamp() * 1000.0
                    durationMs = max(0.0, nowMs - started)
                except (TypeError, ValueError, OSError):
                    pass
                connection.execute(
                """
                UPDATE jobs SET status = 'FAILED', errorCode = 'E_RUNTIME_RESTARTED',
                  errorMessage = 'runtime restarted while job was active', endAt = ?,
                  durationMs = ?, stopMode = NULL
                WHERE status IN ('ACCEPTED', 'STARTING', 'RUNNING', 'STOPPING')
                  AND jobId = ?
                """,
                (now, durationMs, jobId),
                )
                sequenceRow = connection.execute(
                    "SELECT COALESCE(MAX(sequence), 0) + 1 FROM jobEvents WHERE jobId = ?",
                    (jobId,),
                ).fetchone()
                sequence = int(sequenceRow[0]) if sequenceRow is not None else 1
                connection.execute(
                    """
                    INSERT INTO jobEvents(
                      jobId, nodeId, eventType, level, code, message, payloadJson,
                      timestamp, sequence, projectId, workflowId, workflowRunId,
                      parentWorkflowRunId, nodeRunId, iterationPathJson, timestampMs
                    ) VALUES (?, '', 'job.failed', 'ERROR', 'E_RUNTIME_RESTARTED', ?, '{}',
                              ?, ?, ?, ?, '', '', '', '', ?)
                    """,
                    (
                        jobId,
                        "runtime restarted while job was active",
                        now,
                        sequence,
                        projectId,
                        workflowId,
                        nowMs,
                    ),
                )
            connection.commit()
            return len(rows)

    def pruneTerminalJobEvents(
        self,
        *,
        retentionDays: int = 30,
        minimumJobsPerProject: int = 100,
        currentTimestampMs: int | None = None,
    ) -> int:
        nowMs = _timestampMs() if currentTimestampMs is None else int(currentTimestampMs)
        cutoffMs = nowMs - max(1, retentionDays) * 86400000
        minimum = max(0, int(minimumJobsPerProject))
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT jobId, projectId, endAt, startAt
                FROM jobs
                WHERE status IN ('COMPLETED', 'FAILED', 'ABORTED')
                ORDER BY projectId, COALESCE(endAt, startAt) DESC, jobId DESC
                """
            ).fetchall()
            seenByProject: dict[str, int] = {}
            candidates: list[str] = []
            for jobId, projectId, endAt, startAt in rows:
                projectKey = str(projectId)
                rank = seenByProject.get(projectKey, 0)
                seenByProject[projectKey] = rank + 1
                if rank < minimum:
                    continue
                try:
                    endedMs = int(
                        datetime.fromisoformat(str(endAt or startAt)).timestamp() * 1000.0
                    )
                except (TypeError, ValueError, OSError):
                    continue
                if endedMs < cutoffMs:
                    candidates.append(str(jobId))
            if not candidates:
                return 0
            connection.execute("BEGIN IMMEDIATE")
            deleted = 0
            for offset in range(0, len(candidates), 400):
                batch = candidates[offset : offset + 400]
                placeholders = ",".join("?" for _ in batch)
                cursor = connection.execute(
                    f"DELETE FROM jobEvents WHERE jobId IN ({placeholders})",
                    batch,
                )
                deleted += max(0, int(cursor.rowcount))
            connection.commit()
            return deleted

    def checkpointWal(self) -> None:
        with self._connect() as connection:
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchall()

    def vacuumIfNeeded(self, minimumFreeRatio: float = 0.25) -> bool:
        with self._connect() as connection:
            pageRow = connection.execute("PRAGMA page_count").fetchone()
            freeRow = connection.execute("PRAGMA freelist_count").fetchone()
            pages = int(pageRow[0]) if pageRow is not None else 0
            free = int(freeRow[0]) if freeRow is not None else 0
            if pages <= 0 or free / pages < max(0.0, minimumFreeRatio):
                return False
            connection.execute("VACUUM")
            return True

    def upsertPluginDiagnostic(
        self,
        operatorId: str,
        version: str,
        status: str,
        reasonCode: str,
        reasonMessage: str,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO pluginDiagnostics(operatorId, version, status, reasonCode, reasonMessage, timestamp)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (operatorId, version, status, reasonCode, reasonMessage, _utcNow()),
            )
            connection.commit()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.dbPath, timeout=5.0)
        connection.execute("PRAGMA busy_timeout=5000")
        connection.execute("PRAGMA journal_mode=WAL")
        return connection


def _utcNow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _timestampMs() -> int:
    return int(time.time() * 1000)
