from datetime import datetime, timezone
from pathlib import Path
import sqlite3


class SqliteStore:
  def __init__(self, dbPath: Path) -> None:
    self.dbPath = dbPath
    self.dbPath.parent.mkdir(parents=True, exist_ok=True)

  def initialize(self) -> None:
    connection = sqlite3.connect(self.dbPath)
    try:
      connection.execute("PRAGMA journal_mode=WAL")
      connection.execute("PRAGMA busy_timeout=5000")
      migrationPath = Path(__file__).parent / "migrations" / "001_init.sql"
      migrationSql = migrationPath.read_text(encoding="utf-8")
      connection.executescript(migrationSql)
      connection.commit()
    finally:
      connection.close()

  def insertJob(self, jobId: str, projectId: str, releaseId: str, status: str) -> None:
    now = _utcNow()
    with sqlite3.connect(self.dbPath) as connection:
      connection.execute("PRAGMA busy_timeout=5000")
      connection.execute(
        """
        INSERT INTO jobs(jobId, projectId, releaseId, status, startAt, endAt, durationMs, errorCode)
        VALUES (?, ?, ?, ?, ?, NULL, NULL, NULL)
        """,
        (jobId, projectId, releaseId, status, now)
      )
      connection.commit()

  def updateJobStatus(self, jobId: str, status: str, errorCode: str | None) -> None:
    with sqlite3.connect(self.dbPath) as connection:
      connection.execute("PRAGMA busy_timeout=5000")
      connection.execute(
        "UPDATE jobs SET status = ?, errorCode = ? WHERE jobId = ?",
        (status, errorCode, jobId)
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
    payloadJson: str
  ) -> None:
    with sqlite3.connect(self.dbPath) as connection:
      connection.execute("PRAGMA busy_timeout=5000")
      connection.execute(
        """
        INSERT INTO jobEvents(jobId, nodeId, eventType, level, code, message, payloadJson, timestamp)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (jobId, nodeId, eventType, level, code, message, payloadJson, _utcNow())
      )
      connection.commit()

  def upsertPluginDiagnostic(
    self,
    operatorId: str,
    version: str,
    status: str,
    reasonCode: str,
    reasonMessage: str
  ) -> None:
    with sqlite3.connect(self.dbPath) as connection:
      connection.execute("PRAGMA busy_timeout=5000")
      connection.execute(
        """
        INSERT INTO pluginDiagnostics(operatorId, version, status, reasonCode, reasonMessage, timestamp)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (operatorId, version, status, reasonCode, reasonMessage, _utcNow())
      )
      connection.commit()


def _utcNow() -> str:
  return datetime.now(timezone.utc).isoformat()
