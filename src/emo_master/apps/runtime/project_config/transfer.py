"""Explicit bounded resource transfer; never installs code or changes a revision."""
import json
from pathlib import Path
import re
from threading import RLock
from uuid import uuid4
from weakref import WeakValueDictionary
from typing import Any
from types import SimpleNamespace

from emo_master.core.project.resources import Resource
from emo_master.apps.runtime.context.sqlite_transactions import beginImmediate
from .resources import fileDigest
from .store import ConfigurationError
from .transfer_repair import ResourceRepair


MAX_TRANSFER_BYTES = 4 * 1024 * 1024 * 1024
MAX_CHUNK_BYTES = 512 * 1024


class ResourceTransfers(ResourceRepair):
    _registryLock = RLock()
    _locks: WeakValueDictionary[tuple[Path, Path], Any] = WeakValueDictionary()

    def __init__(self, sqlite, root, instanceId=None):
        self.sqlite, self.root = sqlite, Path(root).resolve()
        self.instanceId = instanceId or uuid4().hex
        self.root.mkdir(parents=True, exist_ok=True)
        with self._registryLock:
            key = (sqlite.dbPath.resolve(), self.root)
            self.lock = self._locks.setdefault(key, RLock())
        self.closed = False
        with self.lock, sqlite.connection() as connection:
            beginImmediate(connection)
            connection.execute("""CREATE TABLE IF NOT EXISTS projectResourceTransfers (
                transferId TEXT PRIMARY KEY, projectId TEXT NOT NULL, manifest TEXT NOT NULL,
                filename TEXT NOT NULL UNIQUE, state TEXT NOT NULL, offset INTEGER NOT NULL)""")
            columns = {row[1] for row in connection.execute("PRAGMA table_info(projectResourceTransfers)")}
            if "instanceId" not in columns:
                connection.execute("ALTER TABLE projectResourceTransfers ADD COLUMN instanceId TEXT NOT NULL DEFAULT ''")
            connection.execute("""CREATE TABLE IF NOT EXISTS projectResourcePins (
                token TEXT NOT NULL, instanceId TEXT NOT NULL, projectId TEXT NOT NULL, manifest TEXT NOT NULL)""")
            connection.execute("""CREATE TABLE IF NOT EXISTS projectResourceIngestions (
                transferId TEXT PRIMARY KEY)""")
            self._initializeRepair(connection)

    def _path(self, name):
        if not re.fullmatch(r"[0-9a-f]{32}(\.[a-z0-9]{1,16})?", name):
            raise ConfigurationError("E_RESOURCE_PATH", "invalid owned transfer filename")
        path = self.root / name
        if path.is_symlink() or path.resolve().parent != self.root:
            raise ConfigurationError("E_RESOURCE_PATH", "transfer path crosses a link")
        return path

    def _read(self, transferId):
        with self.sqlite.connection() as connection:
            row = connection.execute("SELECT projectId, manifest, filename, state, offset FROM projectResourceTransfers WHERE transferId=?",
                                     (transferId,)).fetchone()
        if row is None:
            raise ConfigurationError("E_RESOURCE_TRANSFER_UNKNOWN", "resource transfer not found")
        return row

    def _cancel(self, transferId, row):
        with self.sqlite.connection() as connection:
            beginImmediate(connection)
            pinned = connection.execute("SELECT 1 FROM projectResourcePins WHERE transferId=? OR (transferId='' AND projectId=? AND manifest=?) LIMIT 1",
                                        (transferId, row[0], row[1])).fetchone()
            if pinned:
                raise ConfigurationError("E_RESOURCE_TRANSFER_READY", "resource is pinned by prepared configuration")
            if row[3] in {"READY", "CORRUPT"}:
                online = None
                if connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='onlineProject'").fetchone():
                    online = connection.execute("SELECT payload FROM onlineProject WHERE singleton=1").fetchone()
                payload = json.loads(online[0]) if online else {}
                referenced = payload.get("project", {}).get("projectId") == row[0] and any(
                    Resource.model_validate(item).model_dump_json() == row[1]
                    for item in (payload.get("resources") or {}).get("items", {}).values())
                if pinned or referenced:
                    raise ConfigurationError("E_RESOURCE_TRANSFER_READY", "resource is referenced by online or prepared configuration")
            self._path(row[2]).unlink(missing_ok=True)
            connection.execute("UPDATE projectResourceTransfers SET state='CANCELLED', active=0, offset=0 WHERE transferId=?", (transferId,))

    def recover(self, *, onlyInstance=None):
        with self.lock, self.sqlite.connection() as connection:
            ids = [row[0] for row in connection.execute("""SELECT transferId FROM projectResourceTransfers
                WHERE state='PENDING' AND (? IS NULL OR instanceId=?)""", (onlyInstance, onlyInstance))]
            for transferId in ids:
                self._cancel(transferId, self._read(transferId))
            connection.execute("DELETE FROM projectResourcePins WHERE (? IS NULL OR instanceId=?)", (onlyInstance, onlyInstance))
        with self.lock:
            self._reclaimIngestions()
            self._reclaimSuperseded()

    def execute(self, request):
        with self.lock:
            if self.closed:
                raise ConfigurationError("E_RUNTIME_CLOSING", "resource transfer owner is closing")
            transferId = request.transfer_id
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,160}", transferId):
                raise ConfigurationError("E_RESOURCE_TRANSFER_ID", "a bounded transfer ID is required")
            if len(request.data) > MAX_CHUNK_BYTES or len(request.resource_json) > 8192:
                raise ConfigurationError("E_RESOURCE_TRANSFER_LIMIT", "resource chunk exceeds budget")
            if request.action == "LOOKUP":
                item = Resource.model_validate(json.loads(request.resource_json))
                return self._lookup(request.project_id, item)
            if request.action == "BEGIN":
                item = Resource.model_validate(json.loads(request.resource_json))
                manifest = item.model_dump_json()
                replacement = getattr(request, 'replacement_of', '')
                revision = getattr(request, 'expected_document_revision', 0)
                with self.sqlite.connection() as connection:
                    beginImmediate(connection)
                    existing = connection.execute("SELECT projectId, manifest, replacementOf, expectedRevision FROM projectResourceTransfers WHERE transferId=?", (transferId,)).fetchone()
                    if existing:
                        if existing != (request.project_id, manifest, replacement, revision):
                            raise ConfigurationError("E_RESOURCE_TRANSFER_CONFLICT", "transfer ID content changed")
                    else:
                        active = self._authority(connection, request.project_id, manifest)
                        if replacement:
                            self._checkRepairFence(connection, request.project_id, revision, self.instanceId)
                            if active is None or active[0] != replacement:
                                raise ConfigurationError('E_RESOURCE_REPAIR_CONFLICT', 'repair target is no longer authoritative')
                        elif active and not self._healthy(active[1], item):
                            raise ConfigurationError('E_RESOURCE_REPAIR_CONFIRM', 'confirm repair of corrupt resource')
                        # Retain a small status window; an expired BEGIN is a new
                        # empty upload, never a replay of publication or bytes.
                        connection.execute("""DELETE FROM projectResourceTransfers WHERE state='CANCELLED' AND rowid NOT IN
                            (SELECT rowid FROM projectResourceTransfers WHERE state='CANCELLED' ORDER BY rowid DESC LIMIT 256)""")
                        rows = connection.execute("SELECT manifest, state FROM projectResourceTransfers WHERE state NOT IN ('SUPERSEDED','REUSED')").fetchall()
                        reserved = sum(json.loads(value)["size"] for value, state in rows if state != "CANCELLED")
                        disk = sum(path.stat().st_size for path in self.root.iterdir() if path.is_file())
                        if sum(state != "CANCELLED" for _, state in rows) >= 4096 or max(reserved, disk) + item.size > MAX_TRANSFER_BYTES:
                            raise ConfigurationError("E_RESOURCE_TRANSFER_CAPACITY", "resource upload capacity exhausted")
                        suffix = Path(item.path).suffix.lower()
                        if suffix and not re.fullmatch(r"\.[a-z0-9]{1,16}", suffix):
                            raise ConfigurationError("E_RESOURCE_PATH", "resource extension is unsupported")
                        name = uuid4().hex + suffix
                        connection.execute("INSERT INTO projectResourceTransfers (transferId,projectId,manifest,filename,state,offset,instanceId,replacementOf,expectedRevision) VALUES (?,?,?,?, 'PENDING',0,?,?,?)",
                                           (transferId, request.project_id, manifest, name, self.instanceId, replacement, revision))
                row = self._read(transferId)
                if not existing:
                    self._path(row[2]).touch(exist_ok=False)
            else:
                row = self._read(transferId)
            if row[0] != request.project_id:
                raise ConfigurationError("E_RESOURCE_PROJECT", "transfer belongs to a different project")
            item = Resource.model_validate_json(row[1])
            path = self._path(row[2])
            if request.action == "CHUNK":
                if row[3] != "PENDING" or not request.data or request.offset > row[4]:
                    raise ConfigurationError("E_RESOURCE_TRANSFER_OFFSET", "transfer is not writable at that offset")
                if request.offset + len(request.data) > item.size:
                    raise ConfigurationError("E_RESOURCE_TRANSFER_LIMIT", "chunk exceeds declared resource size")
                with path.open("r+b") as stream:
                    if request.offset < row[4]:
                        stream.seek(request.offset)
                        if request.offset + len(request.data) > row[4] or stream.read(len(request.data)) != request.data:
                            raise ConfigurationError("E_RESOURCE_TRANSFER_CONFLICT", "replayed chunk differs from accepted bytes")
                    else:
                        stream.truncate(row[4])
                        stream.seek(row[4])
                        stream.write(request.data)
                        stream.flush()
                        import os
                        os.fsync(stream.fileno())
                        with self.sqlite.connection() as connection:
                            beginImmediate(connection)
                            connection.execute("UPDATE projectResourceTransfers SET offset=? WHERE transferId=?",
                                               (row[4] + len(request.data), transferId))
            elif request.action == "COMMIT":
                if row[3] == 'REUSED':
                    return dict(self._lookup(row[0], item), requestTransferId=transferId)
                if row[3] not in {"PENDING", "READY"} or row[4] != item.size:
                    raise ConfigurationError("E_RESOURCE_TRANSFER_INCOMPLETE", "resource transfer is incomplete")
                if not path.is_file() or path.stat().st_size != item.size or fileDigest(path) != item.sha256:
                    raise ConfigurationError("E_RESOURCE_TRANSFER_DIGEST", "uploaded resource size or digest mismatch")
                self._promote(transferId, row, item)
            elif request.action == "CANCEL":
                self._cancel(transferId, row)
            elif request.action not in {"BEGIN", "STATUS"}:
                raise ConfigurationError("E_RESOURCE_TRANSFER_ACTION", "unknown resource transfer action")
            row = self._read(transferId)
            if row[3] == 'REUSED':
                return dict(self._lookup(row[0], item), requestTransferId=transferId)
            return dict(transferId=transferId, state=row[3], offset=row[4], size=item.size, sha256=item.sha256)

    def pin(self, document, root, *, cancelled=None):
        """Ingest declared local inputs and pin verified copies until publication.

        Pins and cancellation share the transfer lock and SQLite authority. A
        successful commit protects the copies before the preparation pin retires.
        """
        token = uuid4().hex
        with self.lock:
            try:
                self._pinDocument(token, document, root, cancelled)
            except BaseException:
                self.unpin(token)
                raise
        return token

    def _pinDocument(self, token, document, root, cancelled):
        from emo_master.core.project.snapshots import verifyResources
        with self.lock:
            if self.closed:
                raise ConfigurationError("E_RUNTIME_CLOSING", "resource transfer owner is closing")
            if document.resources is None:
                return
            available = self.resolve(document)
            sources = verifyResources(document, Path(root), resourcePaths=available)
            for key, item in document.resources.items.items():
                if cancelled is not None and cancelled.is_set():
                    raise ConfigurationError("E_CONFIG_CANCELLED", "resource ingestion cancelled")
                if key in available:
                    continue
                request = SimpleNamespace(action="BEGIN", transfer_id=uuid4().hex,
                    project_id=document.project.projectId, resource_json=item.model_dump_json(), data=b"", offset=0)
                with self.sqlite.connection() as connection:
                    beginImmediate(connection)
                    connection.execute("INSERT INTO projectResourceIngestions VALUES (?)", (request.transfer_id,))
                try:
                    self.execute(request)
                    with Path(sources[key]).open("rb") as source:
                        while data := source.read(MAX_CHUNK_BYTES):
                            if cancelled is not None and cancelled.is_set():
                                raise ConfigurationError("E_CONFIG_CANCELLED", "resource ingestion cancelled")
                            request.action, request.data = "CHUNK", data
                            self.execute(request)
                            request.offset += len(data)
                    request.action, request.data = "COMMIT", b""
                    self.execute(request)
                except BaseException:
                    with self.sqlite.connection() as connection:
                        exists = connection.execute("SELECT 1 FROM projectResourceTransfers WHERE transferId=?", (request.transfer_id,)).fetchone()
                    if exists:
                        self._cancel(request.transfer_id, self._read(request.transfer_id))
                    raise
            with self.sqlite.connection() as connection:
                beginImmediate(connection)
                connection.executemany("INSERT INTO projectResourcePins VALUES (?,?,?,?,?)",
                    [(token, self.instanceId, document.project.projectId, item.model_dump_json(),
                      self._authority(connection, document.project.projectId, item.model_dump_json())[0])
                     for item in document.resources.items.values()])

    def unpin(self, token):
        with self.lock:
            with self.sqlite.connection() as connection:
                beginImmediate(connection)
                connection.execute("DELETE FROM projectResourcePins WHERE token=? AND instanceId=?", (token, self.instanceId))
            self._reclaimIngestions()
            self._reclaimSuperseded()

    def _reclaimIngestions(self):
        # Only automatically ingested copies are implicit cleanup candidates.
        # User uploads remain available until an explicit CANCEL.
        with self.sqlite.connection() as connection:
            ids = [row[0] for row in connection.execute("SELECT transferId FROM projectResourceIngestions")]
        for transferId in ids:
            try:
                self._cancel(transferId, self._read(transferId))
            except ConfigurationError as error:
                if error.code == "E_RESOURCE_TRANSFER_READY":
                    continue
                if error.code != "E_RESOURCE_TRANSFER_UNKNOWN":
                    raise
            with self.sqlite.connection() as connection:
                beginImmediate(connection)
                connection.execute("DELETE FROM projectResourceIngestions WHERE transferId=?", (transferId,))

    def resolve(self, document):
        if document.resources is None:
            return {}
        with self.lock:
            result = {}
            for key, item in document.resources.items.items():
                found = self._lookup(document.project.projectId, item)
                if found['state'] == 'CORRUPT':
                    raise ConfigurationError('E_RESOURCE_TRANSFER_DIGEST', 'completed upload is missing or corrupt')
                if found['state'] == 'READY':
                    result[key] = self._path(self._read(found['transferId'])[2])
            return result

    def close(self):
        with self.lock:
            self.closed = True
            self.recover(onlyInstance=self.instanceId)
