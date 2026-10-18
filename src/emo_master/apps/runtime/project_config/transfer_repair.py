"""Single authoritative object per manifest; repairs never overwrite readers."""
import json

from emo_master.apps.runtime.context.sqlite_transactions import beginImmediate
from emo_master.core.project.resources import Resource
from .resources import fileDigest
from .store import ConfigurationError


class ResourceRepair:
    def _initializeRepair(self, connection):
        columns = {row[1] for row in connection.execute('PRAGMA table_info(projectResourceTransfers)')}
        migrate = 'active' not in columns
        for name, sql in [('active', 'INTEGER NOT NULL DEFAULT 0'),
                          ('replacementOf', "TEXT NOT NULL DEFAULT ''"),
                          ('expectedRevision', 'INTEGER NOT NULL DEFAULT 0')]:
            if name not in columns:
                connection.execute(f'ALTER TABLE projectResourceTransfers ADD COLUMN {name} {sql}')
        pins = {row[1] for row in connection.execute('PRAGMA table_info(projectResourcePins)')}
        if 'transferId' not in pins:
            connection.execute("ALTER TABLE projectResourcePins ADD COLUMN transferId TEXT NOT NULL DEFAULT ''")
        if migrate:
            groups = connection.execute("SELECT DISTINCT projectId, manifest FROM projectResourceTransfers WHERE state='READY'").fetchall()
            for project, manifest in groups:
                rows = connection.execute("SELECT transferId, filename FROM projectResourceTransfers WHERE projectId=? AND manifest=? AND state='READY' ORDER BY transferId",
                                          (project, manifest)).fetchall()
                item = Resource.model_validate_json(manifest)
                valid = [row for row in rows if self._healthy(row[1], item)]
                chosen = (valid or rows)[0][0]
                connection.execute("UPDATE projectResourceTransfers SET state='SUPERSEDED' WHERE projectId=? AND manifest=? AND state='READY'",
                                   (project, manifest))
                connection.execute('UPDATE projectResourceTransfers SET active=1, state=? WHERE transferId=?',
                                   ('READY' if valid else 'CORRUPT', chosen))
        connection.execute('CREATE UNIQUE INDEX IF NOT EXISTS resourceActiveManifest ON projectResourceTransfers(projectId,manifest) WHERE active=1')

    def _healthy(self, filename, item):
        path = self._path(filename)
        try:
            return path.stat().st_size == item.size and path.is_file() and fileDigest(path) == item.sha256
        except FileNotFoundError:
            return False

    @staticmethod
    def _authority(connection, project, manifest):
        return connection.execute('SELECT transferId, filename, state FROM projectResourceTransfers WHERE projectId=? AND manifest=? AND active=1',
                                  (project, manifest)).fetchone()

    def _lookup(self, project, item):
        with self.sqlite.connection() as connection:
            row = self._authority(connection, project, item.model_dump_json())
            if row is None:
                return dict(state='MISSING')
            state = 'READY' if self._healthy(row[1], item) else 'CORRUPT'
            connection.execute('UPDATE projectResourceTransfers SET state=? WHERE transferId=?', (state, row[0]))
            return dict(transferId=row[0], state=state, offset=item.size if state == 'READY' else 0,
                        size=item.size, sha256=item.sha256)

    def _checkRepairFence(self, connection, project, expectedRevision, instanceId):
        current = connection.execute('SELECT documentRevision, payload FROM onlineProject WHERE singleton=1').fetchone()
        if (instanceId != self.instanceId or not current or current[0] != expectedRevision
                or json.loads(current[1])['project']['projectId'] != project):
            raise ConfigurationError('E_CONFIG_CONFLICT', 'resource repair authority changed; compare again')

    def _promote(self, transferId, row, item):
        with self.sqlite.connection() as connection:
            beginImmediate(connection)
            replacement, revision, instance = connection.execute(
                'SELECT replacementOf, expectedRevision, instanceId FROM projectResourceTransfers WHERE transferId=?', (transferId,)).fetchone()
            active = self._authority(connection, row[0], row[1])
            if active and active[0] == transferId:
                return
            if replacement:
                self._checkRepairFence(connection, row[0], revision, instance)
                if active is None or (active[0] != replacement and not self._healthy(active[1], item)):
                    raise ConfigurationError('E_RESOURCE_REPAIR_CONFLICT', 'repair target is no longer authoritative')
            if active and self._healthy(active[1], item):
                connection.execute("UPDATE projectResourceTransfers SET state='REUSED' WHERE transferId=?", (transferId,))
            else:
                if active:
                    if not replacement:
                        raise ConfigurationError('E_RESOURCE_REPAIR_CONFIRM', 'confirm repair of the corrupt authoritative object')
                    connection.execute("UPDATE projectResourceTransfers SET active=0,state='SUPERSEDED' WHERE transferId=?", (active[0],))
                connection.execute("UPDATE projectResourceTransfers SET active=1,state='READY' WHERE transferId=?", (transferId,))
        self._reclaimSuperseded()

    def _reclaimSuperseded(self):
        with self.sqlite.connection() as connection:
            beginImmediate(connection)
            rows = connection.execute("SELECT transferId, filename, projectId, manifest FROM projectResourceTransfers WHERE active=0 AND state IN ('SUPERSEDED','REUSED')").fetchall()
            for transferId, filename, project, manifest in rows:
                pinned = connection.execute("SELECT 1 FROM projectResourcePins WHERE transferId=? OR (transferId='' AND projectId=? AND manifest=?) LIMIT 1",
                                            (transferId, project, manifest)).fetchone()
                if not pinned:
                    self._path(filename).unlink(missing_ok=True)
            connection.execute("""DELETE FROM projectResourceTransfers WHERE active=0 AND state IN ('SUPERSEDED','REUSED')
                AND transferId NOT IN (SELECT transferId FROM projectResourcePins)
                AND NOT EXISTS (SELECT 1 FROM projectResourcePins p WHERE p.transferId='' AND p.projectId=projectResourceTransfers.projectId AND p.manifest=projectResourceTransfers.manifest)
                AND rowid NOT IN (SELECT rowid FROM projectResourceTransfers WHERE state IN ('SUPERSEDED','REUSED') ORDER BY rowid DESC LIMIT 256)""")
