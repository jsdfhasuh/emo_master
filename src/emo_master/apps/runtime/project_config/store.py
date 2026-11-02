"""Configuration and committed receipt share one SQLite commit boundary."""
import json
from pathlib import Path
from threading import Lock

from emo_master.core.project.configuration import canonical, executionDigest
from emo_master.apps.runtime.context.sqlite_transactions import beginImmediate

RECEIPT_WINDOW = 4096


class ConfigurationError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


class ConfigurationStore:
    def __init__(self, sqliteStore, *, commitState=None, exportPath=None):
        self.sqlite = sqliteStore
        self.commitState = commitState
        self.exportPath = exportPath
        self.exportError = ''
        self.exportLock = Lock()
        with self.sqlite.connection() as connection:
            connection.execute("""CREATE TABLE IF NOT EXISTS onlineProject (
                singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                documentRevision INTEGER NOT NULL, executionRevision INTEGER NOT NULL,
                payload TEXT NOT NULL, sourcePath TEXT NOT NULL)""")
            connection.execute("""CREATE TABLE IF NOT EXISTS projectWriteReceipts (
                requestId TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, receipt TEXT NOT NULL)""")
            connection.execute("""CREATE TABLE IF NOT EXISTS onlineProjectHistory (
                documentRevision INTEGER PRIMARY KEY, executionRevision INTEGER NOT NULL,
                payload TEXT NOT NULL, sourcePath TEXT NOT NULL)""")
            connection.execute("""INSERT OR IGNORE INTO onlineProjectHistory
                SELECT documentRevision, executionRevision, payload, sourcePath FROM onlineProject""")
        self.exportCopy()

    def exportCopy(self):
        if self.exportPath is None:
            return
        from emo_master.core.project.delivery_store import atomicJson
        with self.exportLock:
            try:
                snapshot = self.snapshot()
                if snapshot is not None:
                    atomicJson(self.exportPath, snapshot)
                self.exportError = ''
            except Exception as error:
                # This is a reconstructible copy, never a second authority or
                # a reason to report an already committed write as failed.
                self.exportError = str(error)

    @staticmethod
    def _snapshot(row):
        if row is None:
            return None
        return dict(documentRevision=row[0], executionRevision=row[1],
                    payload=json.loads(row[2]), sourcePath=row[3])

    def snapshot(self):
        with self.sqlite.connection() as connection:
            return self._snapshot(connection.execute(
                "SELECT documentRevision, executionRevision, payload, sourcePath FROM onlineProject WHERE singleton=1"
            ).fetchone())

    def receipt(self, requestId):
        with self.sqlite.connection() as connection:
            row = connection.execute("SELECT fingerprint, receipt FROM projectWriteReceipts WHERE requestId=?",
                                     (requestId,)).fetchone()
            return (row[0], json.loads(row[1])) if row else None

    def historicalSnapshot(self, revision):
        with self.sqlite.connection() as connection:
            result = self._snapshot(connection.execute("""SELECT documentRevision, executionRevision, payload, sourcePath
                FROM onlineProjectHistory WHERE documentRevision=?""", (revision,)).fetchone())
            if result is None:
                raise ConfigurationError("E_CONFIG_HISTORY_UNAVAILABLE", "historical configuration is unavailable; current draft is not a substitute")
            return result

    def recordTransition(self, status):
        """Append effect observations without changing the atomic commit receipt."""
        with self.sqlite.connection() as connection:
            beginImmediate(connection)
            row = connection.execute("SELECT receipt FROM projectWriteReceipts WHERE requestId=?",
                                     (status["requestId"],)).fetchone()
            if row is None:
                return
            receipt = json.loads(row[0])
            for field in ("affectedJobs", "connections", "connectionError", "transitionMessage", "publicationError"):
                if field in status:
                    receipt[field] = status[field]
            connection.execute("UPDATE projectWriteReceipts SET receipt=? WHERE requestId=?",
                               (canonical(receipt), status["requestId"]))

    def history(self, projectId=None):
        with self.sqlite.connection() as connection:
            return [dict(documentRevision=row[0], executionRevision=row[1]) for row in connection.execute(
                "SELECT documentRevision, executionRevision, payload FROM onlineProjectHistory ORDER BY documentRevision DESC")
                if projectId is None or json.loads(row[2])["project"]["projectId"] == projectId]

    def initialize(self, payload, sourcePath, *, receipt=None, fingerprint=''):
        with self.sqlite.connection() as connection:
            beginImmediate(connection)
            if connection.execute("SELECT 1 FROM onlineProject").fetchone():
                raise ConfigurationError("E_CONFIG_ACTIVE", "an online configuration already exists; compare before writing")
            if self.commitState is not None:
                self.commitState(connection, None, payload)
            connection.execute("INSERT INTO onlineProject VALUES (1,1,1,?,?)", (canonical(payload), sourcePath))
            connection.execute("INSERT INTO onlineProjectHistory VALUES (1,1,?,?)", (canonical(payload), sourcePath))
            if receipt is not None:
                connection.execute("INSERT INTO projectWriteReceipts VALUES (?,?,?)",
                                   (receipt['requestId'], fingerprint, canonical(receipt)))
        self.exportCopy()
        return self.snapshot()

    def advanceRequestFence(self, expectedRevision):
        """Expire terminal attempts without changing an execution plan."""
        with self.sqlite.connection() as connection:
            beginImmediate(connection)
            row = connection.execute("SELECT documentRevision, executionRevision, payload, sourcePath FROM onlineProject WHERE singleton=1").fetchone()
            if row is None or row[0] != expectedRevision:
                raise ConfigurationError("E_CONFIG_CONFLICT", "online revision changed; compare again")
            connection.execute("UPDATE onlineProject SET documentRevision=? WHERE singleton=1", (expectedRevision + 1,))
            connection.execute("INSERT INTO onlineProjectHistory VALUES (?,?,?,?)", (expectedRevision + 1, *row[1:]))
        self.exportCopy()
        return self.snapshot()

    def commit(self, expectedRevision, payload, requestId, fingerprint, runtimeInstanceId, *, sourcePath=None):
        with self.sqlite.connection() as connection:
            beginImmediate(connection)
            saved = connection.execute("SELECT fingerprint, receipt FROM projectWriteReceipts WHERE requestId=?",
                                       (requestId,)).fetchone()
            if saved:
                if saved[0] != fingerprint:
                    raise ConfigurationError("E_CONFIG_REQUEST_MISMATCH", "request ID was used for different content")
                return json.loads(saved[1])
            row = connection.execute(
                "SELECT documentRevision, executionRevision, payload, sourcePath FROM onlineProject WHERE singleton=1"
            ).fetchone()
            current = self._snapshot(row)
            if current is None or current["documentRevision"] != expectedRevision:
                raise ConfigurationError("E_CONFIG_CONFLICT", "online revision changed; compare again")
            if self.commitState is not None:
                self.commitState(connection, current["payload"], payload)
            revision = expectedRevision + 1
            sourcePath = sourcePath or current["sourcePath"]
            execution = current["executionRevision"] + int(
                executionDigest(payload) != executionDigest(current["payload"])
                or Path(sourcePath).resolve() != Path(current["sourcePath"]).resolve())
            receipt = dict(state="COMMITTED", requestId=requestId, runtimeInstanceId=runtimeInstanceId,
                           documentRevision=revision, executionRevision=execution, code="", message="configuration committed")
            connection.execute("UPDATE onlineProject SET documentRevision=?, executionRevision=?, payload=?, sourcePath=? WHERE singleton=1",
                               (revision, execution, canonical(payload), sourcePath))
            connection.execute("INSERT INTO projectWriteReceipts VALUES (?,?,?)",
                               (requestId, fingerprint, canonical(receipt)))
            # The revision fence is committed with eviction. An exact replay
            # outside the receipt window fails CAS, never repeats preparation.
            connection.execute("""DELETE FROM projectWriteReceipts WHERE rowid NOT IN
                (SELECT rowid FROM projectWriteReceipts ORDER BY rowid DESC LIMIT ?)""", (RECEIPT_WINDOW,))
            connection.execute("INSERT INTO onlineProjectHistory VALUES (?,?,?,?)",
                               (revision, execution, canonical(payload), sourcePath))
        self.exportCopy()
        if self.exportError:
            receipt = dict(receipt, exportError=self.exportError)
        return receipt
