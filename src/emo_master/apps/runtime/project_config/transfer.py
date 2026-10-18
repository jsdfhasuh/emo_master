"""Explicit bounded resource transfer; never installs code or changes a revision."""
import json
from pathlib import Path
import re
from threading import RLock
from uuid import uuid4
from weakref import WeakValueDictionary
from typing import Any

from emo_master.core.project.resources import Resource
from emo_master.apps.runtime.context.sqlite_transactions import beginImmediate
from .resources import fileDigest
from .store import ConfigurationError


MAX_TRANSFER_BYTES = 4 * 1024 * 1024 * 1024
MAX_CHUNK_BYTES = 512 * 1024


class ResourceTransfers:
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
        if row[3] == "READY":
            raise ConfigurationError("E_RESOURCE_TRANSFER_READY", "completed upload may be referenced by online configuration")
        self._path(row[2]).unlink(missing_ok=True)
        with self.sqlite.connection() as connection:
            connection.execute("UPDATE projectResourceTransfers SET state='CANCELLED', offset=0 WHERE transferId=?", (transferId,))

    def recover(self, *, onlyInstance=None):
        with self.lock, self.sqlite.connection() as connection:
            ids = [row[0] for row in connection.execute("""SELECT transferId FROM projectResourceTransfers
                WHERE state='PENDING' AND (? IS NULL OR instanceId=?)""", (onlyInstance, onlyInstance))]
            for transferId in ids:
                self._cancel(transferId, self._read(transferId))

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
                with self.sqlite.connection() as connection:
                    ready = connection.execute("""SELECT transferId, filename FROM projectResourceTransfers
                        WHERE projectId=? AND manifest=? AND state='READY' LIMIT 1""",
                        (request.project_id, item.model_dump_json())).fetchone()
                if ready is None:
                    return dict(state="MISSING")
                path = self._path(ready[1])
                if not path.is_file() or path.stat().st_size != item.size or fileDigest(path) != item.sha256:
                    raise ConfigurationError("E_RESOURCE_TRANSFER_DIGEST", "completed upload is missing or corrupt")
                return dict(transferId=ready[0], state="READY", offset=item.size, size=item.size, sha256=item.sha256)
            if request.action == "BEGIN":
                item = Resource.model_validate(json.loads(request.resource_json))
                manifest = item.model_dump_json()
                with self.sqlite.connection() as connection:
                    beginImmediate(connection)
                    existing = connection.execute("SELECT projectId, manifest FROM projectResourceTransfers WHERE transferId=?", (transferId,)).fetchone()
                    if existing:
                        if existing != (request.project_id, manifest):
                            raise ConfigurationError("E_RESOURCE_TRANSFER_CONFLICT", "transfer ID content changed")
                    else:
                        rows = connection.execute("SELECT manifest, state FROM projectResourceTransfers").fetchall()
                        reserved = sum(json.loads(value)["size"] for value, state in rows if state != "CANCELLED")
                        disk = sum(path.stat().st_size for path in self.root.iterdir() if path.is_file())
                        if len(rows) >= 4096 or max(reserved, disk) + item.size > MAX_TRANSFER_BYTES:
                            raise ConfigurationError("E_RESOURCE_TRANSFER_CAPACITY", "resource upload capacity exhausted")
                        suffix = Path(item.path).suffix.lower()
                        if suffix and not re.fullmatch(r"\.[a-z0-9]{1,16}", suffix):
                            raise ConfigurationError("E_RESOURCE_PATH", "resource extension is unsupported")
                        name = uuid4().hex + suffix
                        connection.execute("INSERT INTO projectResourceTransfers VALUES (?,?,?,?, 'PENDING',0,?)",
                                           (transferId, request.project_id, manifest, name, self.instanceId))
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
                            connection.execute("UPDATE projectResourceTransfers SET offset=? WHERE transferId=?",
                                               (row[4] + len(request.data), transferId))
            elif request.action == "COMMIT":
                if row[3] not in {"PENDING", "READY"} or row[4] != item.size:
                    raise ConfigurationError("E_RESOURCE_TRANSFER_INCOMPLETE", "resource transfer is incomplete")
                if not path.is_file() or path.stat().st_size != item.size or fileDigest(path) != item.sha256:
                    raise ConfigurationError("E_RESOURCE_TRANSFER_DIGEST", "uploaded resource size or digest mismatch")
                with self.sqlite.connection() as connection:
                    connection.execute("UPDATE projectResourceTransfers SET state='READY' WHERE transferId=?", (transferId,))
            elif request.action == "CANCEL":
                self._cancel(transferId, row)
            elif request.action not in {"BEGIN", "STATUS"}:
                raise ConfigurationError("E_RESOURCE_TRANSFER_ACTION", "unknown resource transfer action")
            row = self._read(transferId)
            return dict(transferId=transferId, state=row[3], offset=row[4], size=item.size, sha256=item.sha256)

    def resolve(self, document):
        if document.resources is None:
            return {}
        with self.lock, self.sqlite.connection() as connection:
            available = {manifest: name for manifest, name in connection.execute(
                "SELECT manifest, filename FROM projectResourceTransfers WHERE projectId=? AND state='READY'",
                (document.project.projectId,))}
            return {key: self._path(available[item.model_dump_json()]) for key, item in document.resources.items.items()
                    if item.model_dump_json() in available}

    def close(self):
        with self.lock:
            self.closed = True
            self.recover(onlyInstance=self.instanceId)
