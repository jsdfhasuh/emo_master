"""Local administrator-managed Runtime directory. No SDK or network operations.

Application must supply its authenticated session as actor, never browser role data.
Target ids, not arbitrary addresses/RPCs, are the public selection mechanism.
"""
import ipaddress
from contextlib import contextmanager
import re
import sqlite3
from pathlib import Path


class RegistryError(ValueError):
    pass


class RegistryDenied(PermissionError):
    pass


def _identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,79}', value):
        raise RegistryError('Runtime id must be 1..80 safe characters')
    return value


def _username(value):
    if not isinstance(value, str) or not 1 <= len(value) <= 80 or any(ord(c) < 32 for c in value):
        raise RegistryError('Invalid username')
    return value


def _configuration(alias, mode, address):
    if not isinstance(alias, str) or not alias.strip() or len(alias) > 120 or any(ord(c) < 32 for c in alias):
        raise RegistryError('Alias must be 1..120 printable characters')
    if mode not in ('mock', 'grpc') or not isinstance(address, str):
        raise RegistryError('Mode must be mock or grpc')
    if mode == 'mock':
        if address:
            raise RegistryError('Mock targets must not specify an address')
        return alias.strip(), mode, ''
    # Deliberately reject grpc URI schemes, userinfo, paths, query and fragments.
    if len(address) > 260 or any(c in address for c in '/@?#\\'):
        raise RegistryError('Expected host:port, not a URL, credential or path')
    match = re.fullmatch(r'(\[[0-9A-Fa-f:]+\]|[A-Za-z0-9.-]+):([0-9]{1,5})', address)
    if not match or not 1 <= int(match[2]) <= 65535:
        raise RegistryError('Invalid Runtime host:port')
    host = match[1]
    if host.startswith('['):
        try:
            host = '[' + str(ipaddress.IPv6Address(host[1:-1])) + ']'
        except ValueError as exc:
            raise RegistryError('Invalid IPv6 host') from exc
    else:
        if len(host) > 253 or any(not re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?', label) for label in host.split('.')):
            raise RegistryError('Invalid host name')
        if re.fullmatch(r'[0-9.]+', host):
            try:
                ipaddress.IPv4Address(host)
            except ValueError as exc:
                raise RegistryError('Invalid IPv4 host') from exc
        host = host.lower()
    return alias.strip(), mode, f'{host}:{int(match[2])}'


class RuntimeRegistry:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.db() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS runtimes (
                    id TEXT PRIMARY KEY, alias TEXT NOT NULL,
                    mode TEXT NOT NULL CHECK(mode IN ('mock','grpc')), address TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS runtime_acl (
                    runtime_id TEXT NOT NULL REFERENCES runtimes(id) ON DELETE CASCADE,
                    username TEXT NOT NULL, can_view INTEGER NOT NULL CHECK(can_view IN (0,1)),
                    can_control INTEGER NOT NULL CHECK(can_control IN (0,1) AND can_control<=can_view),
                    PRIMARY KEY(runtime_id,username));
            ''')

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def _actor(actor, admin=False):
        if not isinstance(actor, dict) or actor.get('role') not in ('admin', 'operator', 'viewer'):
            raise RegistryDenied('Authenticated session required')
        _username(actor.get('username'))
        if admin and actor['role'] != 'admin':
            raise RegistryDenied('Administrator required')

    def _permissions(self, db, actor, runtime_id):
        if actor['role'] == 'admin':
            return ['view', 'control']
        row = db.execute('SELECT can_view,can_control FROM runtime_acl WHERE runtime_id=? AND username=?', (runtime_id, actor['username'])).fetchone()
        if not row or not row['can_view']:
            return []
        return ['view', 'control'] if actor['role'] == 'operator' and row['can_control'] else ['view']

    def _public(self, db, actor, row):
        value = dict(row)
        value['permissions'] = self._permissions(db, actor, row['id'])
        if actor['role'] != 'admin':
            value.pop('address')
        return value

    def list(self, actor):
        self._actor(actor)
        with self.db() as db:
            return [self._public(db, actor, row) for row in db.execute('SELECT * FROM runtimes ORDER BY id') if self._permissions(db, actor, row['id'])]

    def get(self, actor, runtime_id):
        return self.require(actor, runtime_id, 'view')

    def require(self, actor, runtime_id, permission='view'):
        self._actor(actor)
        _identifier(runtime_id)
        if permission not in ('view', 'control'):
            raise RegistryError('Unknown target permission')
        with self.db() as db:
            row = db.execute('SELECT * FROM runtimes WHERE id=?', (runtime_id,)).fetchone()
            # Same failure for unknown and unauthorized targets avoids enumeration.
            if row is None or permission not in self._permissions(db, actor, runtime_id):
                raise RegistryDenied('Target unavailable or unauthorized')
            return self._public(db, actor, row)

    def create(self, actor, runtime_id, alias, mode, address=''):
        self._actor(actor, admin=True)
        _identifier(runtime_id)
        config = _configuration(alias, mode, address)
        try:
            with self.db() as db:
                db.execute('INSERT INTO runtimes VALUES (?,?,?,?)', (runtime_id, *config))
        except sqlite3.IntegrityError as exc:
            raise RegistryError('Runtime id already exists') from exc
        return self.get(actor, runtime_id)

    def update(self, actor, runtime_id, *, alias=None, mode=None, address=None):
        self._actor(actor, admin=True)
        _identifier(runtime_id)
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM runtimes WHERE id=?', (runtime_id,)).fetchone()
            if row is None:
                raise RegistryError('Runtime not found')
            config = _configuration(row['alias'] if alias is None else alias, row['mode'] if mode is None else mode, row['address'] if address is None else address)
            db.execute('UPDATE runtimes SET alias=?,mode=?,address=? WHERE id=?', (*config, runtime_id))
        return self.get(actor, runtime_id)

    def set_acl(self, actor, runtime_id, username, view, control=False):
        self._actor(actor, admin=True)
        _identifier(runtime_id)
        _username(username)
        if type(view) is not bool or type(control) is not bool or (control and not view):
            raise RegistryError('Boolean ACL required; control requires view')
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            if not db.execute('SELECT 1 FROM runtimes WHERE id=?', (runtime_id,)).fetchone():
                raise RegistryError('Runtime not found')
            if not view:
                db.execute('DELETE FROM runtime_acl WHERE runtime_id=? AND username=?', (runtime_id, username))
            else:
                db.execute('INSERT INTO runtime_acl VALUES (?,?,?,?) ON CONFLICT(runtime_id,username) DO UPDATE SET can_view=excluded.can_view,can_control=excluded.can_control', (runtime_id, username, int(view), int(control)))
        return {'runtimeId': runtime_id, 'username': username, 'view': view, 'control': control}

    def list_acl(self, actor, runtime_id):
        self._actor(actor, admin=True)
        self.get(actor, runtime_id)
        with self.db() as db:
            return [{'username': row['username'], 'view': bool(row['can_view']), 'control': bool(row['can_control'])} for row in db.execute('SELECT * FROM runtime_acl WHERE runtime_id=? ORDER BY username', (runtime_id,))]

    def revoke_user(self, actor, username):
        """Call when deleting user, before allowing that username to be reused."""
        self._actor(actor, admin=True)
        _username(username)
        with self.db() as db:
            db.execute('DELETE FROM runtime_acl WHERE username=?', (username,))

    def delete(self, actor, runtime_id, *, revoke_acl=False, in_use=False):
        """Caller supplies trusted active-reference check; browser cannot set in_use.

        Runtime connections/views must first be closed by application coordination.
        """
        self._actor(actor, admin=True)
        _identifier(runtime_id)
        if type(revoke_acl) is not bool or type(in_use) is not bool:
            raise RegistryError('Boolean deletion flags required')
        if in_use:
            raise RegistryError('Runtime has active references; close them before deletion')
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            if not db.execute('SELECT 1 FROM runtimes WHERE id=?', (runtime_id,)).fetchone():
                raise RegistryError('Runtime not found')
            if not revoke_acl and db.execute('SELECT 1 FROM runtime_acl WHERE runtime_id=? LIMIT 1', (runtime_id,)).fetchone():
                raise RegistryError('Runtime has ACL references; explicit revoke_acl required')
            db.execute('DELETE FROM runtimes WHERE id=?', (runtime_id,))
