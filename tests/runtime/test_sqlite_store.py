from pathlib import Path
import sqlite3

from emo_master.apps.runtime.context.sqlite_store import SqliteStore


def testSqliteStoreCreatesTables(tmp_path: Path) -> None:
  dbPath = tmp_path / "emo_master.db"
  store = SqliteStore(dbPath)
  store.initialize()

  assert dbPath.exists()

  connection = sqlite3.connect(dbPath)
  cursor = connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
  tableNames = {row[0] for row in cursor.fetchall()}
  expected = {
    "projects",
    "projectReleases",
    "jobs",
    "jobEvents",
    "pluginDiagnostics",
    "deviceBindings"
  }
  assert expected.issubset(tableNames)


def testSqliteStoreCanPersistJobAndEvent(tmp_path: Path) -> None:
  dbPath = tmp_path / "emo_master.db"
  store = SqliteStore(dbPath)
  store.initialize()

  store.insertJob(jobId="job-1", projectId="project-1", releaseId="release-1", status="READY")
  store.updateJobStatus(jobId="job-1", status="RUNNING", errorCode=None)
  store.appendJobEvent(
    jobId="job-1",
    nodeId="node-a",
    eventType="node.status.changed",
    level="INFO",
    code="",
    message="running",
    payloadJson="{}"
  )

  connection = sqlite3.connect(dbPath)
  jobRow = connection.execute("SELECT status FROM jobs WHERE jobId='job-1'").fetchone()
  eventRow = connection.execute("SELECT eventType FROM jobEvents WHERE jobId='job-1'").fetchone()
  assert jobRow is not None and jobRow[0] == "RUNNING"
  assert eventRow is not None and eventRow[0] == "node.status.changed"
