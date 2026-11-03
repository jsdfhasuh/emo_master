from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import json
import sqlite3
import threading
import time

from emo_master.apps.runtime.context.global_counters import (
    E_COUNTER_BUSY,
    GlobalCounterError,
    GlobalCounterRecord,
    validateGlobalCounterValue,
)
from emo_master.apps.runtime.events.models import RuntimeEvent, MAX_WORKER_EVENT_BATCH, batchableWorkerEvent
from emo_master.apps.runtime.context.sqlite_transactions import beginImmediate
from emo_master.core.contracts.diagnostic_logging import componentForEvent


class SqliteStore:
    def __init__(self, dbPath: Path) -> None:
        self.dbPath = dbPath
        self.jobEventRetention: int | None = None
        self.dbPath.parent.mkdir(parents=True, exist_ok=True)
        self._idleConnection: sqlite3.Connection | None = None
        self._idleConnectionReady = False
        self._idleConnectionLock = threading.Lock()

    def retainIdleConnection(self) -> None:
        """Keep WAL open for one explicit Runtime owner, without a transaction.

        Writers still use their own connections and durable per-event commits.
        Standalone stores do not acquire this resource implicitly.
        """
        with self._idleConnectionLock:
            if self._idleConnection is not None:
                if self._idleConnectionReady:
                    return
                # A previous failed acquisition could not close its handle.
                # Retire that owner before allocating another connection.
                self._idleConnection.close()
                self._idleConnection = None
            connection = sqlite3.connect(self.dbPath, timeout=5.0, check_same_thread=False)
            self._idleConnection = connection
            try:
                _configureConnection(connection)
                cursor = connection.execute("SELECT count(*) FROM sqlite_master")
                try:
                    cursor.fetchone()
                finally:
                    cursor.close()
            except BaseException as error:
                try:
                    connection.close()
                except BaseException as closeError:
                    raise RuntimeError(
                        "Idle connection initialization failed and cleanup is incomplete "
                        f"({type(closeError).__name__}); release or retry the retained owner"
                    ) from error
                self._idleConnection = None
                raise
            self._idleConnectionReady = True

    def releaseIdleConnection(self) -> None:
        """Release only after Runtime producers retire; failed closes can retry."""
        with self._idleConnectionLock:
            if self._idleConnection is not None:
                self._idleConnection.close()
                self._idleConnection = None
                self._idleConnectionReady = False

    def initialize(self) -> None:
        connection = self._connect()
        try:
            with connection:
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
                        (migrationDir / "002_runtime_workflow.sql").read_text(
                            encoding="utf-8"
                        )
                    )
                    connection.execute(
                        "INSERT INTO schemaMigrations(version, appliedAt) VALUES (2, ?)",
                        (_utcNow(),),
                    )
                if 3 not in applied:
                    connection.executescript(
                        (migrationDir / "003_runtime_timestamps.sql").read_text(
                            encoding="utf-8"
                        )
                    )
                    connection.execute(
                        "INSERT INTO schemaMigrations(version, appliedAt) VALUES (3, ?)",
                        (_utcNow(),),
                    )
                if 4 not in applied:
                    connection.executescript(
                        (migrationDir / "004_global_counters.sql").read_text(
                            encoding="utf-8"
                        )
                    )
                    connection.execute(
                        "INSERT INTO schemaMigrations(version, appliedAt) VALUES (4, ?)",
                        (_utcNow(),),
                    )
                connection.commit()
                if 5 not in applied:
                    from emo_master.apps.runtime.context.global_variables import migrateVariables
                    beginImmediate(connection)
                    migrateVariables(connection)
                    connection.execute(
                        "INSERT INTO schemaMigrations(version, appliedAt) VALUES (5, ?)", (_utcNow(),)
                    )
                    connection.commit()
                if 6 not in applied:
                    connection.executescript(
                        (migrationDir / "006_logging_metadata.sql").read_text(encoding="utf-8")
                    )
                    connection.execute(
                        "INSERT INTO schemaMigrations(version, appliedAt) VALUES (6, ?)", (_utcNow(),)
                    )
                    connection.commit()
        finally:
            connection.close()

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
        with self.connection() as connection:
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
        with self.connection() as connection:
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
        originApp: str = "",
        component: str = "",
        runtimeInstanceId: str = "",
    ) -> int:
        timestampMs = timestamp if isinstance(timestamp, int) else _timestampMs()
        timestampText = _utcNow() if not isinstance(timestamp, str) else timestamp
        with self.connection() as connection:
            beginImmediate(connection)
            if sequence is None:
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
                  parentWorkflowRunId, nodeRunId, iterationPathJson, timestampMs,
                  originApp, component, runtimeInstanceId
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    originApp or "runtime", component or componentForEvent(eventType), runtimeInstanceId,
                ),
            )
            limit = self.jobEventRetention
            if limit and (sequence % min(100, limit) == 0
                          or eventType in {"job.completed", "job.failed", "job.aborted"}):
                # Production diagnostics are a rolling window, not the product
                # result archive. Keep the high cursor so sequence never resets.
                connection.execute("DELETE FROM jobEvents WHERE jobId = ? AND sequence <= ?",
                                   (jobId, sequence - limit))
            connection.commit()
        return sequence

    def appendJobEvents(self, events: list[RuntimeEvent]) -> list[int]:
        """One bounded FULL transaction; nothing is acknowledged before commit.

        Only already queued ordinary events of one Job are eligible. BEGIN may
        retry admission; the body and uncertain commit are never replayed.
        """
        if not events or len(events) > MAX_WORKER_EVENT_BATCH:
            raise ValueError("invalid worker event batch size")
        jobId = events[0].jobId
        if any(event.jobId != jobId or not batchableWorkerEvent(event.eventType) for event in events):
            raise ValueError("worker event batch crosses a Job or event barrier")
        with self.connection() as connection:
            beginImmediate(connection)
            row = connection.execute(
                "SELECT COALESCE(MAX(sequence), 0) + 1 FROM jobEvents WHERE jobId = ?", (jobId,)
            ).fetchone()
            first = int(row[0]) if row is not None else 1
            sequences = list(range(first, first + len(events)))
            for event, sequence in zip(events, sequences):
                connection.execute(
                    """INSERT INTO jobEvents(
                      jobId, nodeId, eventType, level, code, message, payloadJson,
                      timestamp, sequence, projectId, workflowId, workflowRunId,
                      parentWorkflowRunId, nodeRunId, iterationPathJson, timestampMs,
                      originApp, component, runtimeInstanceId
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (jobId, event.nodeId, event.eventType, event.level, event.code,
                     event.message, event.payloadJson, _utcNow(), sequence, event.projectId,
                     event.workflowId, event.workflowRunId, event.parentWorkflowRunId,
                     event.nodeRunId, event.iterationPathJson, event.timestampMs,
                     event.originApp or "runtime", event.component or componentForEvent(event.eventType),
                     event.runtimeInstanceId))
                limit = self.jobEventRetention
                if limit and sequence % min(100, limit) == 0:
                    connection.execute("DELETE FROM jobEvents WHERE jobId = ? AND sequence <= ?",
                                       (jobId, sequence - limit))
            connection.commit()
        return sequences

    def getLastJobEventSequence(self, jobId: str) -> int:
        with self.connection() as connection:
            row = connection.execute(
                "SELECT COALESCE(MAX(sequence), 0) FROM jobEvents WHERE jobId = ?",
                (jobId,),
            ).fetchone()
        return int(row[0]) if row is not None else 0

    def getTerminalJobEventSequence(self, jobId: str) -> int:
        with self.connection() as connection:
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
        with self.connection() as connection:
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

    def getJobSnapshotOptions(self, jobId: str) -> dict[str, str]:
        """Read one immutable accepted event, without materializing event history.

        Events can be pruned independently of jobs. Missing evidence is UNKNOWN,
        while an existing pre-policy accepted event represents the old ALL path.
        """
        with self.connection() as connection:
            row = connection.execute(
                "SELECT payloadJson FROM jobEvents WHERE jobId = ? AND eventType = 'job.accepted' ORDER BY sequence LIMIT 1",
                (jobId,),
            ).fetchone()
        unknown = {"legacySnapshotPolicy": "UNKNOWN", "previewProjectKey": ""}
        if row is None:
            return unknown
        try:
            payload = json.loads(row[0])
        except (TypeError, ValueError):
            return unknown
        if not isinstance(payload, dict):
            return unknown
        policy = payload.get("legacySnapshotPolicy", "ALL")
        return {"legacySnapshotPolicy": policy if isinstance(policy, str) and policy in {"ALL", "NONE"} else "UNKNOWN",
                "previewProjectKey": str(payload.get("previewProjectKey", ""))}

    def listJobEventsAfter(self, jobId: str, afterSequence: int = 0) -> list[RuntimeEvent]:
        with self.connection() as connection:
            rows = connection.execute(
                """
                SELECT jobId, eventType, message, level, nodeId, code, payloadJson,
                  sequence, timestampMs, projectId, workflowId, workflowRunId,
                  parentWorkflowRunId, nodeRunId, iterationPathJson, originApp, component, runtimeInstanceId
                FROM jobEvents WHERE jobId = ? AND sequence > ? ORDER BY sequence
                """,
                (jobId, afterSequence),
            ).fetchall()
        return [RuntimeEvent(*row) for row in rows]

    def queryRuntimeLogs(self, *, afterEventId: int = 0, upperEventId: int | None = None,
                         limit: int = 200, projectId: str = "", jobId: str = "",
                         deviceId: str = "", component: str = "", levels: tuple[str, ...] = (),
                         workflowId: str = "", nodeId: str = "",
                         startTimestampMs: int | None = None,
                         endTimestampMs: int | None = None) -> tuple[int, list[tuple[int, RuntimeEvent]]]:
        """Read matching rows before pagination, including one look-ahead row.

        The upper bound belongs to this query, independently of each row's
        originating Runtime instance or Job-local sequence.
        """
        with self.connection() as connection:
            if upperEventId is None:
                upperEventId = int(connection.execute(
                    "SELECT COALESCE(MAX(eventId), 0) FROM jobEvents").fetchone()[0])
            clauses = ["eventId > ?", "eventId <= ?", "eventType != 'process.heartbeat'"]
            values: list[object] = [afterEventId, upperEventId]
            for field, value in (("projectId", projectId), ("component", component),
                                 ("workflowId", workflowId), ("nodeId", nodeId)):
                if value:
                    clauses.append(f"{field} = ?")
                    values.append(value)
            if jobId:
                clauses.append("(jobId = ? OR (jobId = '__runtime__' AND json_extract(payloadJson, '$.jobId') = ?))")
                values.extend((jobId, jobId))
            if deviceId:
                clauses.append("""(json_extract(payloadJson, '$.deviceId') = ? OR EXISTS (
                    SELECT 1 FROM json_each(payloadJson, '$.deviceBindings') AS binding
                    WHERE json_extract(binding.value, '$.deviceId') = ?
                      AND (COALESCE(json_extract(payloadJson, '$.diagnostics.deviceSlot'), '') = ''
                        OR binding.key = json_extract(payloadJson, '$.diagnostics.deviceSlot'))))""")
                values.extend((deviceId, deviceId))
            if levels:
                clauses.append("level IN (" + ",".join("?" for _ in levels) + ")")
                values.extend(levels)
            if startTimestampMs is not None:
                clauses.append("timestampMs >= ?")
                values.append(startTimestampMs)
            if endTimestampMs is not None:
                clauses.append("timestampMs < ?")
                values.append(endTimestampMs)
            rows = connection.execute(
                "SELECT eventId, jobId, eventType, message, level, nodeId, code, payloadJson, "
                "sequence, timestampMs, projectId, workflowId, workflowRunId, parentWorkflowRunId, "
                "nodeRunId, iterationPathJson, originApp, component, runtimeInstanceId "
                "FROM jobEvents WHERE " + " AND ".join(clauses) + " ORDER BY eventId LIMIT ?",
                (*values, limit + 1)).fetchall()
        return upperEventId, [(int(row[0]), RuntimeEvent(*row[1:])) for row in rows]

    def markOrphanedJobsFailed(self, runtimeInstanceId: str = "") -> int:
        with self.connection() as connection:
            beginImmediate(connection)
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
                      parentWorkflowRunId, nodeRunId, iterationPathJson, timestampMs,
                      originApp, component, runtimeInstanceId
                    ) VALUES (?, '', 'job.failed', 'ERROR', 'E_RUNTIME_RESTARTED', ?, '{}',
                              ?, ?, ?, ?, '', '', '', '', ?, 'runtime', 'execution', ?)
                    """,
                    (
                        jobId,
                        "runtime restarted while job was active",
                        now,
                        sequence,
                        projectId,
                        workflowId,
                        nowMs,
                        runtimeInstanceId,
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
        with self.connection() as connection:
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
            beginImmediate(connection)
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
        with self.connection() as connection:
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchall()

    def applyGlobalCounter(
        self,
        projectId: str,
        name: str,
        *,
        increment: bool = False,
        reset: bool = False,
    ) -> GlobalCounterRecord:
        from emo_master.apps.runtime.context.global_variables import counterOperation
        return counterOperation(self, projectId, name, increment=increment, reset=reset)

    def getGlobalCounter(self, projectId: str, name: str) -> GlobalCounterRecord:
        return self.applyGlobalCounter(projectId, name)

    def listGlobalCounters(self, projectId: str) -> list[GlobalCounterRecord]:
        try:
            with self.connection() as connection:
                rows = connection.execute(
                    "SELECT name, value, updatedAtMs FROM globalCounters "
                    "WHERE projectId = ? ORDER BY name COLLATE BINARY",
                    (projectId,),
                ).fetchall()
        except sqlite3.OperationalError as err:
            raise _mapCounterOperationalError(err) from err
        return [
            GlobalCounterRecord(str(name), int(value), int(updatedAtMs))
            for name, value, updatedAtMs in rows
        ]

    def setGlobalCounter(
        self,
        projectId: str,
        name: str,
        value: int,
    ) -> GlobalCounterRecord:
        from emo_master.apps.runtime.context.global_variables import counterOperation
        validateGlobalCounterValue(value)
        return counterOperation(self, projectId, name, value=value)

    def resetGlobalCounter(self, projectId: str, name: str) -> GlobalCounterRecord:
        return self.setGlobalCounter(projectId, name, 0)

    def vacuumIfNeeded(self, minimumFreeRatio: float = 0.25) -> bool:
        with self.connection() as connection:
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
        with self.connection() as connection:
            connection.execute(
                """
                INSERT INTO pluginDiagnostics(operatorId, version, status, reasonCode, reasonMessage, timestamp)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (operatorId, version, status, reasonCode, reasonMessage, _utcNow()),
            )
            connection.commit()

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        """One independent transaction with deterministic handle retirement.

        sqlite3.Connection.__exit__ commits/rolls back but DOES NOT close.
        The explicit Runtime idle owner, not garbage-collected read/write
        connections, is responsible for retaining the WAL lifetime.
        """
        connection = self._connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.dbPath, timeout=5.0)
        try:
            _configureConnection(connection)
        except BaseException:
            connection.close()
            raise
        return connection


def _configureConnection(connection: sqlite3.Connection) -> None:
    connection.execute("PRAGMA busy_timeout=5000")
    connection.execute("PRAGMA journal_mode=WAL")


def _utcNow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _timestampMs() -> int:
    return int(time.time() * 1000)


def _rollbackQuietly(connection: sqlite3.Connection | None) -> None:
    if connection is None:
        return
    try:
        connection.rollback()
    except sqlite3.Error:
        pass


def _mapCounterOperationalError(error: sqlite3.OperationalError) -> GlobalCounterError:
    message = str(error)
    if "locked" in message.casefold() or "busy" in message.casefold():
        return GlobalCounterError(E_COUNTER_BUSY, "global counter database is busy")
    return GlobalCounterError(
        "E_RUNTIME_STATE_UNAVAILABLE",
        f"global counter state is unavailable: {message}",
    )
