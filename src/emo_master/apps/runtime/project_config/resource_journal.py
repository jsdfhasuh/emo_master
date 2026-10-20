"""Durable ownership of prepared files, separate from configuration authority.

Unpublished preparations can be removed after an exclusive Runtime restart.
Published generations require proven owner retirement; a crash is not that proof.
"""
from pathlib import Path

from emo_master.apps.runtime.context.sqlite_transactions import beginImmediate
from .store import ConfigurationError


class ResourceJournal:
    def __init__(self, sqlite, root, instanceId, *, consumerLeases=False):
        self.sqlite, self.root, self.instanceId = sqlite, Path(root).resolve(), instanceId
        self.lease = None
        self.leasePath = None
        if consumerLeases:
            import hashlib
            from .resource_lease import ResourceLease
            folder = self.root / '.owners'
            folder.mkdir(parents=True, exist_ok=True)
            self.leasePath = str(self._path(folder / hashlib.sha256(instanceId.encode()).hexdigest()))
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
        resolved = target.resolve()
        if (not target.is_absolute() or resolved.parent.parent != self.root
                or target != resolved or target.parent.is_symlink() or target.is_symlink()):
            raise ConfigurationError("E_CONFIG_RESOURCE_PATH", "resource ownership path escaped its managed directory")
        return target

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
        for owner, path, retired in owners:
            marker = self._path(path)
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
            target = self._path(path)
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
            rows = connection.execute("""SELECT f.path, o.retired, o.leasePath FROM configurationResourceFiles f
                JOIN configurationResourceOwners o ON f.instanceId=o.instanceId
                WHERE f.instanceId<>? AND f.exposed=1""", (self.instanceId,)).fetchall()
        return [path for path, retired, leasePath in rows if not retired or
                (leasePath is not None and self._path(leasePath).exists())]
