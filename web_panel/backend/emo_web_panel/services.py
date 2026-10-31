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
            result = {'operationId':op_id, 'intent':{'action':action,'target':target,'runGroupId':body.get('runGroupId'),'requestId':request_id,'runtimeId':body.get('runtimeId','configured'),'runtimeInstanceId':body.get('runtimeInstanceId'),'projectId':body.get('projectId'),'address':getattr(runtime,'address',None)}, 'state':'unknown', 'message':'Submitted intent; query before retry. Never automatically replayed.'}
            existing = await asyncio.to_thread(self._reserve, op_id, owner, fingerprint, result)
            if existing is not None:
                return existing
            try:
                result.update(await runtime.control(action, target, body))
            except ValueError as error:
                result.update(state='rejected',outcome='REJECTED',message=str(error))
            except Exception:
                result.update(state='unknown',outcome='UNKNOWN', message='Runtime outcome is not confirmed; do not automatically replay.')
            if action=='start' and result.get('state')=='accepted' and not result.get('runId'):
                result.update(state='unknown',outcome='UNKNOWN',message='Start admission has no confirmed Job identity; query the original request')
            result['commandConfirmed']=result.get('state') in {'accepted','rejected','succeeded','failed','cancelled'}
            await asyncio.to_thread(self._finish, op_id, result)
            return result

    async def reconcile(self, result, runtime):
        intent=result.get('intent',{})
        action=intent.get('action')
        # Accepted Start settles the command, not the new Job's lifetime.
        # An accepted Stop can still be observed without resending that command.
        if result.get('state')!='unknown' and not (action=='stop' and result.get('state')=='accepted'):return result
        if intent.get('address')!=getattr(runtime,'address',None):return result
        info=await runtime.info()
        if (info['runtimeInstanceId'],info['projectId'])!=(intent.get('runtimeInstanceId'),intent.get('projectId')):return result
        if action=='start' and hasattr(runtime,'lookup_start'):
            update=await runtime.lookup_start(intent['requestId'],intent['runtimeInstanceId'])
            if update.get('state')=='accepted' and not update.get('runId'):
                update={**update,'state':'unknown','outcome':'UNKNOWN',
                        'message':'Start admission has no confirmed Job identity; query the original request'}
            update['commandConfirmed']=update.get('state') in {'accepted','rejected','succeeded','failed','cancelled'}
        elif action=='stop' and hasattr(runtime,'run_state') and intent.get('target'):
            run=await runtime.run_state(intent['target'])
            if (run.get('runtimeInstanceId'),run.get('projectId'),run.get('jobId'),run.get('runGroupId')) != (
                    intent['runtimeInstanceId'],intent['projectId'],intent['target'],intent.get('runGroupId')):return result
            status=run.get('status','').upper()
            terminal=run.get('active') is False and status in {'COMPLETED','FAILED','CANCELLED','ABORTED'}
            update={'run':run,'jobTerminal':terminal}
            if terminal:
                # Job termination is observable even if the Stop reply was lost;
                # commandConfirmed closes command tracking; outcome preserves whether
                # acknowledgement was observed. Never infer resource release.
                update.update(state='succeeded',commandConfirmed=True,runId=intent['target'],status=status,
                    message='Selected Job is authoritatively '+status+'; resource state is reported separately. No Stop was replayed.')
            elif result.get('state')=='accepted':
                update['message']='Stop accepted; selected Job is still stopping. Resource state is reported separately.'
        else:return result
        result={**result,**update}
        await asyncio.to_thread(self._finish,result['operationId'],result)
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
                self._cancel_freeze_timer(view)
                self.views.pop(key)
        if len(self.views) >= self.max_views:
            raise ValueError('view capacity reached')
        vid = secrets.token_urlsafe(24)
        view = {'id': vid, 'owner': owner, 'panelId': panel_id,
                'revision': revision, 'runtimeId': runtime_id, 'runId': run_id,
                'runtime': runtime or self.runtime, 'frozen': False,
                'freezeUntil': 0, 'freezeTimer': None, 'freezeExpired': False, 'generation': 0, 'last': None, 'recent': {},
                'scopes': {}, 'latest': {}, 'scopeRecent': {}, 'frozenScopes': {},
                'queue': asyncio.Queue(maxsize=self.max_scopes * 2),
                'notices': {}, 'scopeNotices': {}, 'scopeStatus': {}, 'wake': asyncio.Event(),
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
    def _key(view, run_id=None):
        return view.get('instanceId') or view['runtimeId'], view.get('projectId',''), run_id or view['runId']

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
            if view['scopeNotices']:
                job=next(iter(view['scopeNotices']))
                return {**view['scopeNotices'].pop(job),'frozenScopes':list(view['frozenScopes'])}
            if not view['queue'].empty():
                return view['queue'].get_nowait()
            view['wake'].clear()
            await view['wake'].wait()

    def _envelope(self, view, result=None, **extra):
        if result and result.get('closedResult') and not result.get('gatewayTransformed') and not extra:
            return {'contractMessage':{'event':'result','runtimeInstanceId':result['runtimeInstanceId'],'projectId':result['projectId'],'cursor':result.get('cursor','0'),'result':result['closedResult']}}
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

    @staticmethod
    def _cancel_freeze_timer(view):
        timer=view.get('freezeTimer')
        if timer:timer.cancel()
        view['freezeTimer']=None

    def _expire_freeze(self, view):
        if self.views.get(view['id']) is not view or not view['frozen'] or view.get('freezeExpired'):return
        self._cancel_freeze_timer(view)
        self._invalidate_pending(view)
        view['freezeUntil']=0
        view['freezeExpired']=True
        for frozen in view['frozenScopes'].values():
            frozen['sources']={sid:{'state':'unavailable','reasonCode':'FREEZE_EXPIRED',
                'reason':'Frozen result expired; resume explicitly'} for sid in frozen.get('sources',{})}
            frozen['status']='expired'
            frozen['gatewayTransformed']=True
            frozen.pop('closedResult',None)
        self.publish_notice(view,self._envelope(view,type='freeze_expired',reason='Frozen view TTL expired; resume explicitly'))

    def refresh_frozen(self, view):
        """Publish gateway-local availability changes without forging Runtime DTOs."""
        if view.get('freezeExpired'):return
        self._invalidate_pending(view)
        for result in view['scopes'].values():
            self._queue(view,self._envelope(view,result))

    def advertised_result(self, view, scope, result_key):
        frozen=view['frozenScopes'].get(scope)
        if frozen and frozen['resultKey']==result_key:
            if view.get('freezeExpired') or not view['freezeUntil'] or time.monotonic()>=view['freezeUntil']:
                self._expire_freeze(view)
                raise KeyError('Exact frozen result expired')
            return frozen,True
        result=view['scopeRecent'].get(scope,{}).get(result_key)
        if result is None:raise KeyError('Advertised result retired')
        return result,False

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
            view['freezeExpired']=False
            view['freezeUntil'] = time.monotonic() + self.freeze_ttl
            self._cancel_freeze_timer(view)
            view['freezeTimer']=asyncio.get_running_loop().call_later(self.freeze_ttl,self._expire_freeze,view)
        for result in view['scopes'].values():
            self._queue(view, self._envelope(view, result))
        return {'frozen': True, 'viewGeneration': str(view['generation']), 'resultKeys': {s: r['resultKey'] for s, r in combined.items()},
                'resultKey': view['last']['resultKey']}

    def resume(self, view):
        self._cancel_freeze_timer(view)
        view['freezeExpired']=False
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
        selection=None
        if hasattr(view['runtime'],'run_state'):
            state=await view['runtime'].run_state(view['runId'])
            if (state['runtimeInstanceId'],state['projectId'],state['jobId']) != self._key(view):
                raise ValueError('Selected Job identity changed')
            selection=self.formal_message({'event':'job_changed','runtimeInstanceId':state['runtimeInstanceId'],
                'projectId':state['projectId'],'previousJobId':None,'jobId':state['jobId'],'runGroupId':state['runGroupId']})
        view['connected'] = True
        if selection:self._queue(view,{'contractMessage':selection})
        view['streamKeys']=[]
        for run_id in view.get('runIds') or [view['runId']]:
            key = self._key(view,run_id)
            view['streamKeys'].append(key)
            if key not in self.streams:
                stream = {'viewers': set(), 'runtime': view['runtime'], 'runId': run_id, 'task': None, 'stateTask': None, 'latest': {}, 'scopeStatus': None}
                self.streams[key] = stream
                stream['task'] = asyncio.create_task(self._pump(key))
                if hasattr(view['runtime'],'run_state'):stream['stateTask']=asyncio.create_task(self._states(key))
            self.streams[key]['viewers'].add(view['id'])
            if self.streams[key].get('scopeStatus'):self._scope_status(view,self.streams[key]['scopeStatus'])
            for result in self.streams[key]['latest'].values():self._receive(view,result)

    @staticmethod
    def _expired(result, expired):
        ordinal=result.get('resultOrdinal')
        threshold=expired.get(result.get('resultScopeId'))
        return ordinal is not None and threshold is not None and int(ordinal)<=int(threshold)

    def _scope_status(self, view, message):
        """Runtime scope retirement is independent of receiving another result."""
        job=message['jobId']
        previous=view['scopeStatus'].get(job,{})
        merged={**message}
        for field in ('latestStartedOrdinals','expiredScopeOrdinals'):
            merged[field]=dict(previous.get(field,{}))
            for scope,ordinal in message.get(field,{}).items():
                merged[field][scope]=str(max(int(ordinal),int(merged[field].get(scope,'0'))))
        view['scopeStatus'][job]=merged
        expired=merged['expiredScopeOrdinals']
        for field in ('latest','scopes'):
            for scope,result in list(view[field].items()):
                if field=='scopes' and scope in view['frozenScopes']:continue
                if result.get('jobId')==job and self._expired(result,expired):view[field].pop(scope,None)
        for scope,recent in view['scopeRecent'].items():
            for key,result in list(recent.items()):
                if result.get('jobId')==job and self._expired(result,expired):recent.pop(key,None)
        for key,result in list(view['recent'].items()):
            if result.get('jobId')==job and self._expired(result,expired):view['recent'].pop(key,None)
        last=view.get('last')
        if last and last.get('jobId')==job and last.get('resultScopeId') not in view['frozenScopes'] and self._expired(last,expired):view['last']=None
        kept=[]
        while not view['queue'].empty():
            pending=view['queue'].get_nowait()
            raw=(pending.get('contractMessage') or {}).get('result') or pending.get('result') or {}
            identity=raw.get('identity',raw)
            if (identity.get('jobId')==job and identity.get('resultScopeId') not in view['frozenScopes']
                    and self._expired(identity,expired)):continue
            kept.append(pending)
        for pending in kept:view['queue'].put_nowait(pending)
        view['scopeNotices'][job]={**merged,'type':'scope_status','protocolVersion':'1',
                                  'frozenScopes':list(view['frozenScopes'])}
        view['wake'].set()

    def _receive(self, view, result):
        if self._expired(result,view['scopeStatus'].get(result.get('jobId'),{}).get('expiredScopeOrdinals',{})):return
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
            self._cancel_freeze_timer(view)
            view['scopeStatus'].clear();view['scopeNotices'].clear()
            view['freezeExpired']=False
            view['last'], view['frozen'] = None, False
            while not view['queue'].empty():
                view['queue'].get_nowait()
            self.publish_notice(view, self._envelope(view, type='reset_required', reason='Runtime instance changed'))
        view['instanceId'] = instance
        view['runtimeRunState']=result.get('runState','unknown')
        value = copy.deepcopy(result)
        for source_id,problem in view.get('sourceProblems',{}).items():
            if source_id in value.get('sources',{}):
                value['sources'][source_id]={'state':'unavailable','reason':problem,'reasonCode':problem.split(' ')[0]}
                value['gatewayTransformed']=True
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
            self._expire_freeze(view)
        # Compatibility for existing consumers that set the old single flag.
        legacy_frozen = view['frozen'] and not view['frozenScopes']
        if scope not in view['frozenScopes'] and not legacy_frozen:
            view['scopes'][scope] = value
        displayed = view['frozenScopes'].get(scope) or view['scopes'].get(scope)
        if displayed is None and legacy_frozen:
            displayed = view['last']
        if displayed is not None:
            view['last'] = displayed
            if view.get('freezeExpired') and scope in view['frozenScopes']:return
            self._queue(view, self._envelope(view, displayed))

    def _deliver(self,key,result):
        stream=self.streams.get(key)
        matching=(result.get('jobId')==key[2] and (not key[1] or (result.get('runtimeInstanceId'),result.get('projectId'))==key[:2]))
        scope_status=result.get('type')=='scope_status'
        if stream and matching:
            if scope_status:
                previous=stream.get('scopeStatus') or {}
                merged={**result}
                for field in ('latestStartedOrdinals','expiredScopeOrdinals'):
                    merged[field]=dict(previous.get(field,{}))
                    for scope,ordinal in result.get(field,{}).items():
                        merged[field][scope]=str(max(int(ordinal),int(merged[field].get(scope,'0'))))
                stream['scopeStatus']=result=merged
                for scope,item in list(stream['latest'].items()):
                    if self._expired(item,merged['expiredScopeOrdinals']):stream['latest'].pop(scope,None)
            elif result.get('resultScopeId') and not self._expired(result,(stream.get('scopeStatus') or {}).get('expiredScopeOrdinals',{})):
                stream['latest'][result['resultScopeId']]=result
        for vid in list((stream or {}).get('viewers',())):
            view=self.views.get(vid)
            if not view:continue
            if view.get('instanceId') and (result.get('runtimeInstanceId'),result.get('projectId',view.get('projectId'))) != (view['instanceId'],view.get('projectId')):
                self.publish_notice(view,{'type':'reset_required','reason':'Runtime identity changed; select a run again'})
                continue
            if result.get('jobId')!=key[2]:continue
            if scope_status:self._scope_status(view,result)
            else:self._receive(view,result)

    @staticmethod
    def formal_message(message):
        from emo_panel_protocol.messages import BrowserMessage
        return BrowserMessage.model_validate(message).model_dump(mode='json')

    def _contract_notice(self,key,message):
        message=self.formal_message(message)
        for vid in list(self.streams.get(key,{}).get('viewers',())):
            view=self.views.get(vid)
            if view:self._queue(view,{'contractMessage':message})

    async def _states(self,key):
        while True:
            try:
                stream=self.streams[key];state=await stream['runtime'].run_state(stream['runId'])
                if (state['runtimeInstanceId'],state['projectId']) != key[:2]:raise ValueError('Run identity changed')
                fields={k:state[k] for k in ('runtimeInstanceId','projectId','jobId','runGroupId','status','active','ownsResources','displayResourcesReleased','allowedActions')}
                self._contract_notice(key,{'event':'job_state',**fields})
            except asyncio.CancelledError:raise
            except Exception:pass # subscription path emits connection reset; never infer terminal
            await asyncio.sleep(1)

    async def _pump(self, key):
        disconnected=False
        while True:
            try:
                stream=self.streams[key]
                if hasattr(stream['runtime'],'subscribe'):
                    async for result in stream['runtime'].subscribe(stream['runId']):
                        self._deliver(key,result)
                    # A closed subscription is not permission to restart a Job.
                else:
                    results=await stream['runtime'].snapshot(stream['runId'])
                    for result in results if isinstance(results,list) else [results]:self._deliver(key,result)
                disconnected=False
            except asyncio.CancelledError:raise
            except Exception as exc:
                if getattr(exc,'code','')=='CAPTURE_UNAVAILABLE':
                    self._contract_notice(key,{'event':'capture_unavailable','runtimeInstanceId':key[0],'projectId':key[1],'jobId':self.streams[key]['runId'],'reason':str(exc)})
                elif not disconnected:
                    for vid in list(self.streams.get(key,{}).get('viewers',())):
                        view=self.views.get(vid)
                        if view:
                            view['epoch']=secrets.token_hex(8)
                            while not view['queue'].empty():view['queue'].get_nowait()
                            self.publish_notice(view,self._envelope(view,type='reset_required',reason=str(exc) or 'Runtime connection unavailable',state={'connected':False}))
                disconnected=True
            await asyncio.sleep(max(self.poll_interval,.1))

    async def remove(self, vid):
        view = self.views.pop(vid, None)
        if not view:
            return
        self._cancel_freeze_timer(view)
        for key in view.get('streamKeys',[view.get('streamKey',self._key(view))]):
            stream = self.streams.get(key)
            if stream:
                stream['viewers'].discard(vid)
                if not stream['viewers']:
                    tasks=[task for task in (stream['task'],stream.get('stateTask')) if task]
                    for task in tasks:task.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)
                    self.streams.pop(key, None)
        for field in ('scopes', 'latest', 'recent', 'scopeRecent', 'frozenScopes','scopeStatus','scopeNotices'):
            view[field].clear()
        while not view['queue'].empty():
            view['queue'].get_nowait()

    async def close(self):
        for vid in list(self.views):
            await self.remove(vid)
