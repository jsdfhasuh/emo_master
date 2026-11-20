"""Durable ownership of prepared files, separate from configuration authority.

Unpublished preparations can be removed after an exclusive Runtime restart.
Published generations require proven owner retirement; a crash is not that proof.
"""
from hashlib import sha256
from pathlib import Path
import re

from emo_master.apps.runtime.context.sqlite_transactions import beginImmediate
from .store import ConfigurationError


class ResourceJournal:
    def __init__(self, sqlite, root, instanceId, *, consumerLeases=False):
        self.sqlite, self.root, self.instanceId = sqlite, Path(root).resolve(), instanceId
        self.lease = None
        self.leasePath = None
        if consumerLeases:
            from .resource_lease import ResourceLease
            folder = self.root / '.owners'
            folder.mkdir(parents=True, exist_ok=True)
            self.leasePath = str(self._path(folder / sha256(instanceId.encode()).hexdigest()))
            self.lease = ResourceLease(self.leasePath, create=True)
        with sqlite.connection() as connection:
            beginImmediate(connection)
            connection.execute("""CREATE TABLE IF NOT EXISTS configurationResourceOwners (
                instanceId TEXT PRIMARY KEY, retired INTEGER NOT NULL DEFAULT 0)""")
            connection.execute("""CREATE TABLE IF NOT EXISTS configurationResourceFiles (
                path TEXT PRIMARY KEY, instanceId TEXT NOT NULL,
                exposed INTEGER NOT NULL DEFAULT 0)""")
            if 'leasePath' not in {row[1] for row in connection.execute('PRAGMA table_info(configurationResourceOwners)')}:
                connection.execute('ALTER TABLE configurationResourceOwners ADD COLUMN leasePath TEXT')
            connection.execute("INSERT INTO configurationResourceOwners(instanceId, leasePath) VALUES (?,?)", (instanceId, self.leasePath))

    def _path(self, path):
        target = Path(path)
        if (not target.is_absolute() or target.parent.parent != self.root or target != target.resolve()
                or target.parent.is_symlink() or target.is_symlink()):
            raise ConfigurationError("E_CONFIG_RESOURCE_PATH", "resource ownership path escaped its managed directory")
        return target

    def _markerPath(self, owner, recorded):
        # A copied journal still identifies its old root through this deterministic marker.
        # Validate that identity lexically before touching the current cache.
        marker = Path(recorded)
        name = sha256(owner.encode()).hexdigest()
        root = marker.parent.parent
        if (not root.is_absolute() or '..' in root.parts or Path(root.name) != Path(self.root.name)
                or marker != root / '.owners' / name):
            raise ConfigurationError("E_CONFIG_RESOURCE_PATH", "invalid resource owner lease path")
        return self._path(self.root / '.owners' / name)

    def _filePath(self, path, recordedRoot):
        target = Path(path)
        if (target.parent.parent != recordedRoot or target.parent == recordedRoot / '.owners'
                or (recordedRoot != self.root and not re.fullmatch(r'[0-9a-f]{32}', target.parent.name))):
            raise ConfigurationError("E_CONFIG_RESOURCE_PATH", "resource ownership path escaped its managed directory")
        return self._path(self.root / target.relative_to(recordedRoot))

    def reserve(self, path):
        target = self._path(path)
        if target.exists():
            raise ConfigurationError("E_CONFIG_RESOURCE_PATH", "cannot claim an existing unowned resource file")
        with self.sqlite.connection() as connection:
            beginImmediate(connection)
            connection.execute("INSERT INTO configurationResourceFiles(path, instanceId) VALUES (?,?)",
                               (str(target), self.instanceId))

    def expose(self, paths):
        # Durable before handing a path to adapters, workers, or UI consumers.
        if not paths:
            return
        with self.sqlite.connection() as connection:
            beginImmediate(connection)
            for path in paths:
                connection.execute("UPDATE configurationResourceFiles SET exposed=1 WHERE path=? AND instanceId=?",
                                   (str(self._path(path)), self.instanceId))

    def forget(self, path):
        target = self._path(path)
        if target.exists():
            raise ConfigurationError("E_CONFIG_RESOURCE_BUSY", "cannot forget a file before actual removal")
        with self.sqlite.connection() as connection:
            beginImmediate(connection)
            connection.execute("DELETE FROM configurationResourceFiles WHERE path=? AND instanceId=?",
                               (str(target), self.instanceId))

    def retire(self):
        if self.lease is not None:
            from .resource_lease import retirementLease
            self.lease.close()
            with retirementLease(self.leasePath) as proven:
                if not proven:
                    return
                with self.sqlite.connection() as connection:
                    connection.execute("UPDATE configurationResourceOwners SET retired=1 WHERE instanceId=?", (self.instanceId,))
            return
        with self.sqlite.connection() as connection:
            connection.execute("UPDATE configurationResourceOwners SET retired=1 WHERE instanceId=?", (self.instanceId,))

    def recover(self):
        """Caller must hold the primary Runtime lock; unknown files stay untouched."""
        from .resource_lease import retirementLease
        with self.sqlite.connection() as connection:
            owners = connection.execute("SELECT instanceId, leasePath, retired FROM configurationResourceOwners WHERE instanceId<>? AND leasePath IS NOT NULL",
                                        (self.instanceId,)).fetchall()
        blocked = set()
        recordedRoots = {}
        for owner, path, retired in owners:
            marker = self._markerPath(owner, path)
            recordedRoots[owner] = Path(path).parent.parent
            if retired and not marker.exists():
                continue
            with retirementLease(marker) as proven:
                if proven:
                    with self.sqlite.connection() as connection:
                        connection.execute("UPDATE configurationResourceOwners SET retired=1 WHERE instanceId=?", (owner,))
                else:
                    blocked.add(owner)
        with self.sqlite.connection() as connection:
            rows = connection.execute("""SELECT f.path, f.instanceId FROM configurationResourceFiles f
                JOIN configurationResourceOwners o ON f.instanceId=o.instanceId
                WHERE f.instanceId<>? AND (f.exposed=0 OR o.retired=1)""", (self.instanceId,)).fetchall()
        for path, owner in rows:
            if owner in blocked:
                continue
            target = self._filePath(path, recordedRoots.get(owner, self.root))
            if target != Path(path):
                # Another generation might already own this path in the destination.
                with self.sqlite.connection() as connection:
                    if connection.execute("SELECT 1 FROM configurationResourceFiles WHERE path=?",
                                          (str(target),)).fetchone():
                        continue
            target.unlink(missing_ok=True)
            if target.parent.exists() and not any(target.parent.iterdir()):
                target.parent.rmdir()
            # Delete only after actual cleanup, so a failed unlink retries on restart.
            with self.sqlite.connection() as connection:
                connection.execute("DELETE FROM configurationResourceFiles WHERE path=?", (path,))
        with self.sqlite.connection() as connection:
            connection.execute("""DELETE FROM configurationResourceOwners WHERE instanceId<>?
                AND (retired=1 OR leasePath IS NULL)
                AND instanceId NOT IN (SELECT instanceId FROM configurationResourceFiles)""", (self.instanceId,))

    def retainedAfterCrash(self):
        with self.sqlite.connection() as connection:
            rows = connection.execute("""SELECT f.path, o.retired, o.leasePath, o.instanceId FROM configurationResourceFiles f
                JOIN configurationResourceOwners o ON f.instanceId=o.instanceId
                WHERE f.instanceId<>? AND f.exposed=1""", (self.instanceId,)).fetchall()
        retained = []
        for path, retired, leasePath, owner in rows:
            marker = self._markerPath(owner, leasePath) if leasePath else None
            if not retired or (marker is not None and marker.exists()):
                root = Path(leasePath).parent.parent if leasePath else self.root
                retained.append(str(self._filePath(path, root)))
        return retained
