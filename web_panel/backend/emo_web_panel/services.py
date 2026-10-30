import asyncio
import copy
import hashlib
import json
import secrets
import sqlite3
import time

class Operations:
    """Durable request correlation only; Runtime remains authoritative."""
    def __init__(self, path):
        self.path = path
        self.lock = asyncio.Lock()
        with sqlite3.connect(path) as db:
            db.execute('CREATE TABLE IF NOT EXISTS operations (id TEXT PRIMARY KEY, owner TEXT, fingerprint TEXT, result TEXT)')

    def get(self, op_id, owner):
        with sqlite3.connect(self.path) as db:
            row = db.execute('SELECT result FROM operations WHERE id=? AND owner=?', (op_id, owner)).fetchone()
            return json.loads(row[0]) if row else None

    def by_request(self, owner, request_id):
        return self.get(hashlib.sha256((owner+'\0'+request_id).encode()).hexdigest(), owner)

    def _reserve(self, op_id, owner, fingerprint, result):
        with sqlite3.connect(self.path) as db:
            row = db.execute('SELECT fingerprint,result FROM operations WHERE id=?', (op_id,)).fetchone()
            if row:
                if row[0] != fingerprint:
                    raise ValueError('requestId reused for a different operation')
                return json.loads(row[1])
            db.execute('INSERT INTO operations VALUES (?,?,?,?)', (op_id, owner, fingerprint, json.dumps(result)))
        return None

    def _finish(self, op_id, result):
        with sqlite3.connect(self.path) as db:
            db.execute('UPDATE operations SET result=? WHERE id=?', (json.dumps(result), op_id))

    async def execute(self, owner, request_id, action, target, body, runtime):
        if not isinstance(request_id, str) or not 8 <= len(request_id) <= 128:
            raise ValueError('stable requestId of 8..128 characters required')
        op_id = hashlib.sha256((owner+'\0'+request_id).encode()).hexdigest()
        fingerprint = hashlib.sha256(json.dumps([action,target,body], sort_keys=True).encode()).hexdigest()
        async with self.lock:
            result = {'operationId':op_id, 'state':'unknown', 'message':'Submitted intent; query before retry. Never automatically replayed.'}
            existing = await asyncio.to_thread(self._reserve, op_id, owner, fingerprint, result)
            if existing is not None:
                return existing
            try:
                result.update(await runtime.control(action, target, body))
            except Exception:
                result.update(state='unknown', message='Runtime outcome is not confirmed; do not automatically replay.')
            await asyncio.to_thread(self._finish, op_id, result)
            return result

class Views:
    """Single-worker draft view state; never owns Runtime task lifetimes.

    RuntimePort.snapshot may return one result or a list of independent scope
    results. This is an internal port, not a claim about the unpublished SDK.
    """
    def __init__(self, runtime, max_views=32, freeze_ttl=120,
                 byte_budget=8 * 1024 * 1024, poll_interval=1, max_scopes=16):
        self.runtime, self.max_views, self.freeze_ttl = runtime, max_views, freeze_ttl
        self.byte_budget, self.poll_interval, self.max_scopes = byte_budget, poll_interval, max_scopes
        self.views, self.streams = {}, {}

    def create(self, owner, panel_id, run_id, revision="draft-1",
               runtime_id="configured", runtime=None):
        for key, view in list(self.views.items()):
            if not view['connected'] and time.monotonic() - view['created'] > 60:
                self.views.pop(key)
        if len(self.views) >= self.max_views:
            raise ValueError('view capacity reached')
        vid = secrets.token_urlsafe(24)
        view = {'id': vid, 'owner': owner, 'panelId': panel_id,
                'revision': revision, 'runtimeId': runtime_id, 'runId': run_id,
                'runtime': runtime or self.runtime, 'frozen': False,
                'freezeUntil': 0, 'generation': 0, 'last': None, 'recent': {},
                'scopes': {}, 'latest': {}, 'scopeRecent': {}, 'frozenScopes': {},
                'queue': asyncio.Queue(maxsize=self.max_scopes * 2),
                'notices': {}, 'wake': asyncio.Event(),
                'epoch': secrets.token_hex(8), 'connected': False,
                'created': time.monotonic(), 'instanceId': None}
        self.views[vid] = view
        return view

    def get(self, vid, owner):
        view = self.views.get(vid)
        if not view or view['owner'] != owner:
            raise KeyError('view not found')
        return view

    @staticmethod
    def _size(value):
        return len(json.dumps(value, separators=(',', ':')).encode())

    @staticmethod
    def _key(view):
        return view['runtimeId'], view['runId']

    def _queue(self, view, message):
        queue = view['queue']
        if queue.full():
            queue.get_nowait()
        queue.put_nowait(message)
        view['wake'].set()

    def publish_notice(self, view, message):
        # Reliable latest control notice per type; snapshots cannot evict it.
        kind = message.get('type')
        if kind not in {'package_updated', 'reset_required', 'freeze_expired', 'permission_changed'}:
            raise ValueError('Unsupported view notice')
        view['notices'][kind] = message
        view['wake'].set()

    async def next_message(self, view):
        while True:
            if view['notices']:
                kind = next(iter(view['notices']))
                return view['notices'].pop(kind)
            if not view['queue'].empty():
                return view['queue'].get_nowait()
            view['wake'].clear()
            await view['wake'].wait()

    def _envelope(self, view, result=None, **extra):
        return {'protocolVersion': 'draft-1', 'type': 'result',
                'sessionEpoch': view['epoch'], 'targetId': view['runId'],
                'runtimeId': view['runtimeId'], 'viewGeneration': str(view['generation']),
                'pagePackageRevision': view['revision'], 'result': result,
                'frozen': view['frozen'], 'frozenScopes': list(view['frozenScopes']),
                'state': {'connected': True, 'runState':view.get('runtimeRunState','unknown'),
                          'mode': 'mock' if hasattr(view['runtime'], 'running') else 'grpc'},
                **extra}

    @staticmethod
    def _invalidate_pending(view):
        view['generation'] += 1
        while not view['queue'].empty():
            view['queue'].get_nowait()
        view['notices'].pop('freeze_expired', None)

    def freeze(self, view, result_keys):
        if view['frozen'] and (not view['freezeUntil'] or time.monotonic() >= view['freezeUntil']):
            raise ValueError('Frozen result expired; resume before freezing again')
        if isinstance(result_keys, str):
            matches = [(scope, result_keys) for scope, recent in view['scopeRecent'].items()
                       if result_keys in recent]
            if len(matches) != 1:
                raise ValueError('Displayed result expired or ambiguous; supply scope resultKeys')
            result_keys = dict(matches)
        if not isinstance(result_keys, dict) or not result_keys or len(result_keys) > self.max_scopes:
            raise ValueError('Displayed scope resultKeys required')
        selected = {}
        for scope, key in result_keys.items():
            if not isinstance(scope, str) or not isinstance(key, str):
                raise ValueError('Invalid displayed result identity')
            result = view['scopeRecent'].get(scope, {}).get(key)
            if result is None:
                raise ValueError('Displayed result expired; refresh before freezing')
            selected[scope] = copy.deepcopy(result)
        combined = {**view['frozenScopes'], **selected}
        if self._size(combined) > self.byte_budget:
            raise ValueError('Frozen result byte budget exceeded')
        was_frozen = view['frozen']
        self._invalidate_pending(view)
        view['frozenScopes'] = combined
        view['scopes'].update(selected)
        view['last'] = next(reversed(selected.values()))
        view['frozen'] = True
        if not was_frozen:
            view['freezeUntil'] = time.monotonic() + self.freeze_ttl
        for result in view['scopes'].values():
            self._queue(view, self._envelope(view, result))
        return {'frozen': True, 'viewGeneration': str(view['generation']), 'resultKeys': {s: r['resultKey'] for s, r in combined.items()},
                'resultKey': view['last']['resultKey']}

    def resume(self, view):
        self._invalidate_pending(view)
        view['frozenScopes'].clear()
        view['frozen'] = False
        view['freezeUntil'] = 0
        view['scopes'] = copy.deepcopy(view['latest'])
        for result in view['scopes'].values():
            view['last'] = result
            self._queue(view, self._envelope(view, result))
        return {'frozen': False, 'viewGeneration': str(view['generation']), 'resultKeys': {s: r['resultKey'] for s, r in view['scopes'].items()},
                'resultKey': (view['last'] or {}).get('resultKey')}

    async def attach(self, view):
        if view['connected']:
            raise ValueError('view already connected')
        view['connected'] = True
        key = self._key(view)
        if key not in self.streams:
            stream = {'viewers': set(), 'runtime': view['runtime'], 'task': None}
            self.streams[key] = stream
            stream['task'] = asyncio.create_task(self._pump(key))
        self.streams[key]['viewers'].add(view['id'])

    def _receive(self, view, result):
        scope, key = result.get('resultScopeId'), result.get('resultKey')
        if not isinstance(scope, str) or not isinstance(key, str):
            raise ValueError('Result scope and key required')
        if scope not in view['latest'] and len(view['latest']) >= self.max_scopes:
            raise ValueError('Result scope budget exceeded')
        if self._size(result) > self.byte_budget // (self.max_scopes * 5):
            raise ValueError('Result metadata byte budget exceeded')
        instance = result.get('runtimeInstanceId')
        if view['instanceId'] is not None and instance != view['instanceId']:
            view['epoch'] = secrets.token_hex(8)
            for field in ('scopes', 'latest', 'recent', 'scopeRecent', 'frozenScopes'):
                view[field].clear()
            view['last'], view['frozen'] = None, False
            while not view['queue'].empty():
                view['queue'].get_nowait()
            self.publish_notice(view, self._envelope(view, type='reset_required', reason='Runtime instance changed'))
        view['instanceId'] = instance
        view['runtimeRunState']=result.get('runState','unknown')
        value = copy.deepcopy(result)
        view['latest'][scope] = value
        recent = view['scopeRecent'].setdefault(scope, {})
        recent[key] = value
        while len(recent) > 4:
            recent.pop(next(iter(recent)))
        # Compatibility index for old single-scope consumers; scope-aware callers
        # must use scopeRecent because different scopes can reuse result keys.
        view['recent'][key] = value
        while len(view['recent']) > self.max_scopes * 4:
            view['recent'].pop(next(iter(view['recent'])))
        if view['frozen'] and view['freezeUntil'] and time.monotonic() >= view['freezeUntil']:
            self._invalidate_pending(view)
            view['freezeUntil'] = 0
            for frozen in view['frozenScopes'].values():
                frozen['sources'] = {sid: {'state': 'unavailable', 'reasonCode': 'FREEZE_EXPIRED',
                                          'reason': 'Frozen result expired; resume explicitly'}
                                     for sid in frozen.get('sources', {})}
                frozen['status'] = 'expired'
            self.publish_notice(view, self._envelope(view, type='freeze_expired', reason='Frozen view TTL expired; resume explicitly'))
        # Compatibility for existing consumers that set the old single flag.
        legacy_frozen = view['frozen'] and not view['frozenScopes']
        if scope not in view['frozenScopes'] and not legacy_frozen:
            view['scopes'][scope] = value
        displayed = view['frozenScopes'].get(scope) or view['scopes'].get(scope)
        if displayed is None and legacy_frozen:
            displayed = view['last']
        if displayed is not None:
            view['last'] = displayed
            self._queue(view, self._envelope(view, displayed))

    async def _pump(self, key):
        disconnected = False
        while True:
            try:
                stream = self.streams[key]
                results = await stream['runtime'].snapshot(key[1])
                results = results if isinstance(results, list) else [results]
                for result in results:
                    for vid in list(stream['viewers']):
                        view = self.views.get(vid)
                        if view:
                            self._receive(view, result)
                disconnected = False
            except asyncio.CancelledError:
                raise
            except Exception:
                if not disconnected:
                    for vid in list(self.streams.get(key, {}).get('viewers', ())):
                        view = self.views.get(vid)
                        if view:
                            view['epoch'] = secrets.token_hex(8)
                            while not view['queue'].empty():
                                view['queue'].get_nowait()
                            self.publish_notice(view, self._envelope(
                                view, type='reset_required', reason='Runtime connection unavailable',
                                state={'connected': False}))
                disconnected = True
            # Retry only the read-only full snapshot; never replay controls. Real
            # cursor continuation belongs to the eventual official SDK adapter.
            await asyncio.sleep(max(self.poll_interval, .05) if disconnected else self.poll_interval)

    async def remove(self, vid):
        view = self.views.pop(vid, None)
        if not view:
            return
        key = self._key(view)
        stream = self.streams.get(key)
        if stream:
            stream['viewers'].discard(vid)
            if not stream['viewers']:
                stream['task'].cancel()
                await asyncio.gather(stream['task'], return_exceptions=True)
                self.streams.pop(key, None)
        for field in ('scopes', 'latest', 'recent', 'scopeRecent', 'frozenScopes'):
            view[field].clear()
        while not view['queue'].empty():
            view['queue'].get_nowait()

    async def close(self):
        for vid in list(self.views):
            await self.remove(vid)
