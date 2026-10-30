import asyncio
import copy
import hashlib
import json
import secrets
import sqlite3
import time

class Operations:
    def __init__(self, path):
        self.path = path
        self.lock = asyncio.Lock()
        with sqlite3.connect(path) as db:
            db.execute('CREATE TABLE IF NOT EXISTS operations (id TEXT PRIMARY KEY, owner TEXT, fingerprint TEXT, result TEXT)')
    def get(self, op_id, owner):
        with sqlite3.connect(self.path) as db:
            row = db.execute('SELECT result FROM operations WHERE id=? AND owner=?',(op_id,owner)).fetchone()
            return json.loads(row[0]) if row else None
    def by_request(self, owner, request_id):
        return self.get(hashlib.sha256((owner+"\0"+request_id).encode()).hexdigest(),owner)
    async def execute(self, owner, request_id, action, target, body, runtime):
        if not isinstance(request_id,str) or not 8 <= len(request_id) <= 128: raise ValueError('stable requestId of 8..128 characters required')
        op_id = hashlib.sha256((owner+'\0'+request_id).encode()).hexdigest()
        fp = hashlib.sha256(json.dumps([action,target,body],sort_keys=True).encode()).hexdigest()
        async with self.lock:
            with sqlite3.connect(self.path) as db:
                row=db.execute('SELECT fingerprint,result FROM operations WHERE id=?',(op_id,)).fetchone()
                if row:
                    if row[0]!=fp: raise ValueError('requestId reused for a different operation')
                    return json.loads(row[1])
                result={'operationId':op_id,'state':'unknown','message':'Submitted intent; query before retry. Never automatically replayed.'}
                db.execute('INSERT INTO operations VALUES (?,?,?,?)',(op_id,owner,fp,json.dumps(result)))
            try:
                result.update(await runtime.control(action,target,body))
            except Exception:
                # Transport failure can be ambiguous: never fabricate rejected or retry.
                result.update(state='unknown',message='Runtime outcome is not confirmed; do not automatically replay.')
            with sqlite3.connect(self.path) as db:
                db.execute('UPDATE operations SET result=? WHERE id=?',(json.dumps(result),op_id))
            return result

class Views:
    def __init__(self,runtime,max_views=32,freeze_ttl=120):
        self.runtime,self.max_views,self.freeze_ttl=runtime,max_views,freeze_ttl
        self.views={}
        self.streams={}
    def create(self, owner,panel_id,run_id,revision="draft-1"):
        for key,v in list(self.views.items()):
            if not v["connected"] and time.monotonic()-v["created"]>60:self.views.pop(key)
        if len(self.views)>=self.max_views: raise ValueError('view capacity reached')
        vid=secrets.token_urlsafe(24)
        self.views[vid]={'id':vid,'owner':owner,'panelId':panel_id,'revision':revision,'runId':run_id,'frozen':False,'freezeUntil':0,'last':None,'recent':{},'queue':asyncio.Queue(maxsize=2),'epoch':secrets.token_hex(8),'connected':False,'created':time.monotonic()}
        return self.views[vid]
    def get(self,vid,owner):
        view=self.views.get(vid)
        if not view or view['owner']!=owner: raise KeyError('view not found')
        return view
    async def attach(self,view):
        if view['connected']: raise ValueError('view already connected')
        view['connected']=True
        rid=view['runId']
        if rid not in self.streams:
            self.streams[rid]={'viewers':set(),'task':None}
            self.streams[rid]['task']=asyncio.create_task(self._pump(rid))
        self.streams[rid]['viewers'].add(view['id'])
    async def _pump(self,rid):
        try:
            while True:
                result=await self.runtime.snapshot(rid)
                for vid in list(self.streams[rid]['viewers']):
                    v=self.views.get(vid)
                    if not v: continue
                    if v['frozen'] and time.monotonic()>v['freezeUntil']:
                        v['frozen']=False
                    if not v['frozen']:
                        v['last']=copy.deepcopy(result)
                        v['recent'][result['resultKey']]=copy.deepcopy(result)
                        while len(v['recent'])>4: v['recent'].pop(next(iter(v['recent'])))
                    msg={'protocolVersion':'draft-1','type':'result','sessionEpoch':v['epoch'],'targetId':rid,'pagePackageRevision':v['revision'],'result':v['last'],'frozen':v['frozen'],'state':{'connected':True,'mode':'mock' if hasattr(self.runtime,'running') else 'grpc'}}
                    q=v['queue']
                    if q.full(): q.get_nowait()
                    q.put_nowait(msg)
                await asyncio.sleep(1)
        except asyncio.CancelledError: raise
        except Exception:
            for vid in list(self.streams.get(rid,{}).get('viewers',())):
                v=self.views.get(vid)
                if v:
                    if v['queue'].full():v['queue'].get_nowait()
                    v['queue'].put_nowait({'type':'reset_required','reason':'Runtime connection unavailable','sessionEpoch':v['epoch']})
    async def remove(self,vid):
        v=self.views.pop(vid,None)
        if not v:return
        stream=self.streams.get(v['runId'])
        if stream:
            stream['viewers'].discard(vid)
            if not stream['viewers']:
                stream['task'].cancel()
                await asyncio.gather(stream['task'],return_exceptions=True)
                self.streams.pop(v['runId'],None)
    async def close(self):
        for vid in list(self.views): await self.remove(vid)
