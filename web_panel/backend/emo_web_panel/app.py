import asyncio
import hmac
import json
import time
import sqlite3
import hashlib
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from .config import Config
from .auth import AuthStore
from .runtime_client import ContractUnavailable, GrpcRuntimeClient
from .mock_runtime import MockRuntime, demo_panel, IMAGE
from .services import Operations, Views
from .packages import PackageStore
from .registry import RuntimeRegistry, RegistryDenied
from .resource_cache import ResourceCache

COOKIE='emo_panel_session'

def create_app(config: Config, runtime=None):
    config.data_dir.mkdir(parents=True,exist_ok=True)
    auth=AuthStore(config.data_dir/'web.sqlite',config.session_ttl)
    packages=PackageStore(config.data_dir/'packages')
    registry=RuntimeRegistry(config.data_dir/'web.sqlite')
    service_actor={'username':'web-service','role':'admin'}
    if not registry.list(service_actor):
        registry.create(service_actor,'configured','Configured Runtime',config.mode,config.runtime_address or '')
    initial=next((entry for entry in registry.list(service_actor) if entry['id']=='configured'),None)
    rt=runtime or (MockRuntime() if initial is None or initial['mode']=='mock' else GrpcRuntimeClient(initial['address']))
    views=Views(rt,config.max_views,config.freeze_ttl)
    ops=Operations(config.data_dir/'web.sqlite')
    clients={'configured':rt} if initial else {}
    target_lock=asyncio.Lock()
    resources=ResourceCache()
    with sqlite3.connect(config.data_dir/'web.sqlite') as db:
        db.execute('CREATE TABLE IF NOT EXISTS package_targets (package_id TEXT, runtime_id TEXT, PRIMARY KEY(package_id,runtime_id))')
    def package_targets(pid):
        with sqlite3.connect(config.data_dir/'web.sqlite') as db:
            return [r[0] for r in db.execute('SELECT runtime_id FROM package_targets WHERE package_id=?',(pid,))]
    def associate_package(pid,rids):
        with sqlite3.connect(config.data_dir/'web.sqlite') as db:
            db.execute('DELETE FROM package_targets WHERE package_id=?',(pid,))
            for rid in rids:db.execute('INSERT INTO package_targets VALUES (?,?)',(pid,rid))
    def remove_target_associations(rid):
        with sqlite3.connect(config.data_dir/'web.sqlite') as db:db.execute('DELETE FROM package_targets WHERE runtime_id=?',(rid,))
    async def package_access(who,pid,rid=None):
        if who['role']=='admin':return
        allowed={r['id'] for r in await asyncio.to_thread(registry.list,who)}
        if pid=='demo-panel':targets=allowed
        else:targets=set(await asyncio.to_thread(package_targets,pid)) & allowed
        if not targets or rid is not None and rid not in targets:raise HTTPException(403,'Page package not authorized for target')
    client_lock=asyncio.Lock()
    async def client_for(who,rid,permission='view'):
        await asyncio.to_thread(registry.require,who,rid,permission)
        async with client_lock:
            if rid not in clients:
                entry=await asyncio.to_thread(registry.get,service_actor,rid)
                client=MockRuntime(rid) if entry['mode']=='mock' else GrpcRuntimeClient(entry['address'])
                await client.start();clients[rid]=client
            return clients[rid]
    login_failures={}
    @asynccontextmanager
    async def lifespan(app):
        for client in clients.values():await client.start()
        yield
        await views.close()
        await resources.close()
        for client in clients.values():await client.close()
    app=FastAPI(title='emo_master independent Web panel',lifespan=lifespan)
    app.state.auth,app.state.views,app.state.runtime=auth,views,rt
    app.state.registry=registry
    def origin_ok(origin,host,scheme):
        if not origin:return True # non-browser command clients still need session and CSRF
        if origin in config.allowed_origins:return True
        return origin == f'{scheme}://{host}'
    async def identity(request:Request,permission='view',mutating=False):
        me=await asyncio.to_thread(auth.session,request.cookies.get(COOKIE))
        if not me:raise HTTPException(401,'Login required')
        if permission not in me['permissions']:raise HTTPException(403,'Permission denied')
        if mutating:
            if not origin_ok(request.headers.get('origin'),request.headers.get('host'),request.url.scheme): raise HTTPException(403,'Origin denied')
            if not hmac.compare_digest(request.headers.get('x-csrf-token',''),me['csrf']): raise HTTPException(403,'CSRF denied')
        return me
    async def json_object(req,allow_empty=False):
        data=bytearray()
        async for chunk in req.stream():
            data.extend(chunk)
            if len(data)>65536:raise HTTPException(413,'JSON request too large')
        if not data and allow_empty:return {}
        try:body=json.loads(data)
        except (ValueError,UnicodeDecodeError):raise HTTPException(422,'JSON object required')
        if not isinstance(body,dict):raise HTTPException(422,'JSON object required')
        return body
    @app.exception_handler(RegistryDenied)
    async def target_denied(req,exc):return JSONResponse({'detail':str(exc)},status_code=403)
    @app.exception_handler(sqlite3.IntegrityError)
    async def conflict(req,exc):return JSONResponse({'detail':'Record already exists'},status_code=409)
    @app.exception_handler(ContractUnavailable)
    async def contract_error(req,exc): return JSONResponse({'detail':str(exc),'code':'CONTRACT_UNAVAILABLE'},status_code=503)
    @app.exception_handler(ValueError)
    async def value_error(req,exc): return JSONResponse({'detail':str(exc)},status_code=422)
    @app.exception_handler(KeyError)
    async def key_error(req,exc): return JSONResponse({'detail':'Not found'},status_code=404)
    @app.middleware('http')
    async def guard(req,call_next):
        if req.method in {'POST','PUT','PATCH'}:
            try: size=int(req.headers.get('content-length','0'))
            except ValueError:return JSONResponse({'detail':'Invalid Content-Length'},400)
            if size>24*1024*1024:return JSONResponse({'detail':'Request too large'},413)
        response=await call_next(req)
        response.headers['X-Content-Type-Options']='nosniff'
        response.headers['Cache-Control']='no-store'
        response.headers['Content-Security-Policy']="default-src 'self'; img-src 'self' blob:; style-src 'self' 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'"
        return response
    @app.get('/api/health')
    async def health():return {'status':'ok','mode':config.mode,'productionContractReady':False}
    @app.post('/api/session')
    async def login(req:Request):
        if not origin_ok(req.headers.get('origin'),req.headers.get('host'),req.url.scheme):raise HTTPException(403,'Origin denied')
        ip=req.client.host if req.client else 'unknown'; now=time.monotonic()
        prior=[v for v in login_failures.get(ip,[]) if now-v<60]
        if len(prior)>=10:raise HTTPException(429,'Try again later')
        body=await json_object(req)
        if not isinstance(body.get('username'),str) or not isinstance(body.get('password'),str):raise HTTPException(422,'Credentials required')
        if len(body['password'])>1024:raise HTTPException(422,'Password too long')
        token=await asyncio.to_thread(auth.login,body['username'],body['password'])
        if not token:
            login_failures[ip]=prior+[now]
            raise HTTPException(401,'Invalid credentials')
        me=await asyncio.to_thread(auth.session,token)
        response=JSONResponse({**me,'mode':config.mode})
        response.set_cookie(COOKIE,token,httponly=True,samesite='strict',secure=config.secure_cookie,max_age=config.session_ttl)
        return response
    @app.get('/api/me')
    async def me(req:Request):return {**await identity(req),'mode':config.mode}
    @app.post('/api/logout')
    async def logout(req:Request):
        who=await identity(req,mutating=True)
        await asyncio.to_thread(auth.logout,req.cookies.get(COOKIE,''))
        response=JSONResponse({'ok':True}); response.delete_cookie(COOKIE); return response
    @app.get('/api/admin/audit')
    async def audit_records(req:Request,limit:int=100):
        await identity(req,'admin')
        if not 1<=limit<=500:raise HTTPException(422,'Invalid limit')
        return await asyncio.to_thread(auth.audit_records,limit)
    @app.get('/api/admin/users')
    async def users(req:Request):await identity(req,'admin');return await asyncio.to_thread(auth.users)
    @app.post('/api/admin/users')
    async def create_user(req:Request):
        await identity(req,'admin',True);b=await json_object(req)
        await asyncio.to_thread(auth.create_user,b.get('username'),b.get('password'),b.get('role'))
        return {'ok':True}
    @app.patch('/api/admin/users/{username}')
    async def update_user(username:str,req:Request):
        who=await identity(req,'admin',True);b=await json_object(req)
        if username==who['username'] and b.get('role',who['role'])!=who['role']:raise HTTPException(422,'Change your role from another administrator account')
        await asyncio.to_thread(auth.update_user,username,b.get('role'),b.get('password'))
        await asyncio.to_thread(auth.audit,who['username'],'update_user',username,'succeeded')
        return {'ok':True}
    @app.delete('/api/admin/users/{username}')
    async def delete_user(username:str,req:Request):
        who=await identity(req,'admin',True)
        if username==who['username']:raise HTTPException(422,'Cannot delete own active administrator')
        await asyncio.to_thread(registry.revoke_user,who,username)
        await asyncio.to_thread(auth.delete_user,username)
        await asyncio.to_thread(auth.audit,who['username'],'delete_user',username,'succeeded')
        return {'ok':True}
    @app.get('/api/admin/runtimes')
    async def runtimes(req:Request):
        who=await identity(req,'admin');return await asyncio.to_thread(registry.list,who)
    @app.get('/api/runtimes')
    async def allowed_runtimes(req:Request):
        who=await identity(req);return await asyncio.to_thread(registry.list,who)
    @app.post('/api/admin/runtimes')
    async def add_runtime(req:Request):
        who=await identity(req,'admin',True);b=await json_object(req)
        return await asyncio.to_thread(registry.create,who,b.get('id'),b.get('alias'),b.get('mode'),b.get('address',''))
    @app.patch('/api/admin/runtimes/{rid}')
    async def change_runtime(rid:str,req:Request):
        async with target_lock:
            who=await identity(req,'admin',True);b=await json_object(req)
            if any(v.get('runtimeId','configured')==rid for v in views.views.values()):raise HTTPException(409,'Close active views before changing connection')
            result=await asyncio.to_thread(registry.update,who,rid,alias=b.get('alias'),mode=b.get('mode'),address=b.get('address'))
            if rid in clients:await clients.pop(rid).close()
            return result
    @app.delete('/api/admin/runtimes/{rid}')
    async def remove_runtime(rid:str,req:Request,revoke_acl:bool=False):
        async with target_lock:
            who=await identity(req,'admin',True)
            await asyncio.to_thread(registry.delete,who,rid,revoke_acl=revoke_acl,in_use=any(v.get('runtimeId','configured')==rid for v in views.views.values()))
            if rid in clients:await clients.pop(rid).close()
            await asyncio.to_thread(remove_target_associations,rid)
            return {'ok':True}
    @app.get('/api/admin/runtimes/{rid}/grants')
    async def target_grants(rid:str,req:Request):
        who=await identity(req,'admin');return await asyncio.to_thread(registry.list_acl,who,rid)
    @app.put('/api/admin/runtimes/{rid}/grants')
    async def grant_target(rid:str,req:Request):
        who=await identity(req,'admin',True);b=await json_object(req)
        if b.get('username') not in [u['username'] for u in await asyncio.to_thread(auth.users)]:raise HTTPException(404,'User not found')
        return await asyncio.to_thread(registry.set_acl,who,rid,b['username'],b.get('view'),b.get('control',False))
    @app.get('/api/runtimes/{rid}/status')
    async def runtime_status(rid:str,req:Request,after:str='0'):
        who=await identity(req);client=await client_for(who,rid)
        return {'runtime':await client.info(),'counters':await client.counters(),'events':await client.task_events(after)}
    @app.get('/api/runtimes/{rid}/workflows')
    async def workflows(rid:str,req:Request):
        who=await identity(req);client=await client_for(who,rid);info=await client.info()
        return [{**w,'runtimeId':rid,'runtimeInstanceId':info['runtimeInstanceId'],'projectId':info['projectId'],'configurationRevision':info.get('configurationRevision')} for w in await client.workflows()]
    @app.get('/api/runs')
    async def runs(req:Request,runtimeId:str='configured'):
        who=await identity(req);client=await client_for(who,runtimeId)
        return [{**r,'runtimeId':runtimeId} for r in await client.runs()]
    @app.get('/api/runtimes/{rid}/runs')
    async def runtime_runs(rid:str,req:Request):return await runs(req,rid)
    @app.get('/api/runs/{run_id}')
    async def run_state(run_id:str,req:Request,runtimeId:str='configured'):
        for r in await runs(req,runtimeId):
            if r.get('id',r.get('runId'))==run_id:return r
        raise HTTPException(404,'Run not found')
    async def control(req,action,target):
        async with target_lock:
            who=await identity(req,'control',True)
            if config.preview:raise HTTPException(403,'Preview is read-only')
            body=await json_object(req)
            client=await client_for(who,body.get('runtimeId','configured'),'control')
            await client.info() # fail closed before storing unexecutable intent
            if isinstance(client,MockRuntime):client.validate_control(action,target,body)
            result=await ops.execute(who['userId'],body.get('requestId'),action,target,body,client)
            await asyncio.to_thread(auth.audit,who['username'],action,json.dumps({k:body.get(k) for k in ('requestId','runtimeId','runtimeInstanceId','projectId','workflowId','runGroupId')}|{'runId':target,'operationId':result['operationId']},sort_keys=True),result['state'])
            return result
    @app.post('/api/runs/start')
    async def start(req:Request):return await control(req,'start','')
    @app.post('/api/runs/{run_id}/stop')
    async def stop(run_id:str,req:Request):return await control(req,'stop',run_id)
    @app.get('/api/operations/by-request/{request_id}')
    async def operation_by_request(request_id:str,req:Request):
        who=await identity(req);r=await asyncio.to_thread(ops.by_request,who['userId'],request_id)
        if not r:raise HTTPException(404,'Operation not found; outcome is not assumed')
        return r
    @app.get('/api/operations/{op_id}')
    async def operation(op_id:str,req:Request):
        who=await identity(req);r=await asyncio.to_thread(ops.get,op_id,who['userId'])
        if not r:raise HTTPException(404,'Operation not found')
        return r
    def panel_list():
        active=packages.active()
        if active:
            p=active['presentation'];return [{**p,'id':active['packageId'],'packageRevision':active['manifest']['packageRevision']}]
        return [demo_panel()] if config.mode=='mock' else []
    @app.get('/api/panels')
    async def panels(req:Request):
        who=await identity(req);result=[]
        for panel in await asyncio.to_thread(panel_list):
            try:await package_access(who,panel['id'])
            except HTTPException:continue
            result.append(panel)
        return result
    @app.get('/api/panels/{panel_id}')
    async def panel(panel_id:str,req:Request):
        who=await identity(req);await package_access(who,panel_id)
        return await find_panel(panel_id)
    async def find_panel(panel_id):
        for p in await asyncio.to_thread(panel_list):
            if p['id']==panel_id:return p
        raise HTTPException(404,'Panel not active')
    @app.post('/api/views')
    async def new_view(req:Request):
        async with target_lock:
            who=await identity(req,mutating=True);b=await json_object(req);p=await find_panel(b.get('panelId'))
            rid=b.get('runtimeId','configured');await package_access(who,b['panelId'],rid);client=await client_for(who,rid)
            if b['panelId']=='demo-panel' and not isinstance(client,MockRuntime):raise HTTPException(404,'Demo panel is mock-only')
            run_id=b.get('runId','')
            if run_id and run_id not in [r.get('id',r.get('runId')) for r in await client.runs()]:raise HTTPException(404,'Run not found')
            v=views.create(who['userId'],b['panelId'],run_id,p['packageRevision'],runtime_id=rid,runtime=client);v['presentation']=p;return {'id':v['id']}
    @app.delete('/api/views/{vid}')
    async def delete_view(vid:str,req:Request):
        who=await identity(req,mutating=True);views.get(vid,who['userId']);resources.release_owner('view:'+vid);await views.remove(vid)
        return {'ok':True}
    @app.post('/api/views/{vid}/{action}')
    async def view_action(vid:str,action:str,req:Request):
        who=await identity(req,mutating=True);v=views.get(vid,who['userId'])
        if action not in {'freeze','resume'}:raise HTTPException(404,'Unknown view action')
        await asyncio.to_thread(registry.require,who,v['runtimeId'])
        await package_access(who,v['panelId'],v['runtimeId'])
        if action=='freeze':
            body=await json_object(req)
            result=views.freeze(v,body.get('resultKeys') or body.get('resultKey'))
            for scope in v['frozenScopes'].values():
                for source in scope.get('sources',{}).values():
                    image=source.get('image')
                    if not image:continue
                    key=(scope['runtimeInstanceId']+':'+v['runtimeId'],scope['jobId'],image['resourceId'],image.get('sha256',''))
                    try:resources.pin(key,'view:'+vid,ttl=config.freeze_ttl)
                    except (KeyError,ValueError):
                        source.clear();source.update(state='unavailable',reasonCode='FROZEN_RESOURCE_UNAVAILABLE',reason='Exact frozen image is not cached or exceeds budget')
            return result
        resources.release_owner('view:'+vid)
        return views.resume(v)
    @app.get('/api/views/{vid}/history/{component_id}')
    async def history(vid:str,component_id:str,req:Request,page:int=1,pageSize:int=10,q:str='',sort:str='id',order:str='asc'):
        who=await identity(req);v=views.get(vid,who['userId']);p=v['presentation']
        await package_access(who,v['panelId'],v['runtimeId'])
        def find(cs):
            for c in cs:
                if c['id']==component_id and c['type'] in {'sqlite_table','sqlite_value'}:return c
                nested=find(c.get('children',[]))
                if nested:return nested
        component=next((found for page in p['pages'] if (found:=find(page['components']))),None)
        if not component:raise HTTPException(404,'Undeclared history source')
        if not 1<=page<=10000 or not 1<=pageSize<=100:raise HTTPException(422,'Invalid page')
        client=await client_for(who,v['runtimeId'])
        props=component.get('props',{})
        return await client.history(component_id,page,pageSize,q=q,sort=sort,order=order,aggregation=props.get('aggregation',props.get('aggregate','count')),field=props.get('aggregateField','measurement'),follow_filter=props.get('followsFilter',False))
    async def mock_image(who,rid,image_ref):
        client=await client_for(who,rid)
        if not isinstance(client,MockRuntime) or image_ref!='demo-image':raise HTTPException(404,'Unknown opaque image reference')
        async def load():return IMAGE
        info=await client.info()
        data=await resources.get((info['runtimeInstanceId']+':'+rid,'demo-run','demo-image',hashlib.sha256(IMAGE).hexdigest()),load)
        return Response(data,media_type='image/png')
    @app.get('/api/views/{vid}/images/{component_id}')
    async def fixed_history_image(vid:str,component_id:str,req:Request):
        who=await identity(req);v=views.get(vid,who['userId'])
        await asyncio.to_thread(registry.require,who,v['runtimeId'])
        await package_access(who,v['panelId'],v['runtimeId'])
        def declared(cs):
            return any((c['id']==component_id and c['type']=='image' and c.get('props',{}).get('sourceMode')=='history_fixed') or declared(c.get('children',[])) for c in cs)
        if not any(declared(page['components']) for page in v['presentation']['pages']):raise HTTPException(404,'Fixed history image not declared')
        client=await client_for(who,v['runtimeId'])
        if not isinstance(client,MockRuntime):await client.info()
        return await mock_image(who,v['runtimeId'],'demo-image')
    @app.get('/api/history-images/{image_ref}')
    async def history_image(image_ref:str,req:Request):
        who=await identity(req)
        rid,sep,ref=image_ref.partition(':')
        return await mock_image(who,rid if sep else 'configured',ref if sep else image_ref)
    @app.get('/api/assets/runtime/{rid}/{image_ref}')
    async def result_image(rid:str,image_ref:str,req:Request):
        return await mock_image(await identity(req),rid,image_ref)
    @app.get('/api/assets/demo-image')
    async def demo_image(req:Request):return await mock_image(await identity(req),'configured','demo-image')
    @app.get('/api/assets/{package_id}/{asset_id}')
    async def asset(package_id:str,asset_id:str,req:Request):
        who=await identity(req);await package_access(who,package_id)
        # Imported immutable versions stay readable by authenticated viewers.
        # Activation changes defaults, not existing view resources.
        data,mime=await asyncio.to_thread(packages.asset,package_id,asset_id)
        return Response(data,media_type=mime)
    @app.get('/api/packages')
    async def package_list(req:Request):
        await identity(req,'admin')
        return [{**p,'runtimeIds':await asyncio.to_thread(package_targets,p['packageId'])} for p in await asyncio.to_thread(packages.list)]
    @app.post('/api/packages')
    async def package_install(req:Request):
        await identity(req,'admin',True)
        data=bytearray()
        async for chunk in req.stream():
            data.extend(chunk)
            if len(data)>24*1024*1024:raise HTTPException(413,'Package too large')
        return await asyncio.to_thread(packages.install,bytes(data))
    @app.post('/api/packages/{pid}/activate')
    async def package_activate(pid:str,req:Request):
        who=await identity(req,'admin',True);b=await json_object(req,True)
        rids=b.get('runtimeIds',['configured'])
        if not isinstance(rids,list) or not 1<=len(rids)<=32 or any(not isinstance(rid,str) for rid in rids):raise HTTPException(422,'Runtime association required')
        for rid in rids:await asyncio.to_thread(registry.require,who,rid)
        result=await asyncio.to_thread(packages.activate,pid)
        await asyncio.to_thread(associate_package,pid,rids)
        await asyncio.to_thread(auth.audit,who['username'],'activate_package',json.dumps({'packageId':pid,'runtimeIds':rids}), 'succeeded')
        for v in views.views.values():views.publish_notice(v,{'type':'package_updated','packageId':pid})
        return result
    @app.websocket('/ws/views/{vid}')
    async def ws(ws:WebSocket,vid:str):
        token=ws.cookies.get(COOKIE);who=await asyncio.to_thread(auth.session,token)
        scheme='https' if ws.url.scheme=='wss' else 'http'
        if not who or not origin_ok(ws.headers.get('origin'),ws.headers.get('host'),scheme):await ws.close(code=1008);return
        try:
            v=views.get(vid,who['userId'])
            await asyncio.to_thread(registry.require,who,v['runtimeId'])
            if not v['runId']:raise ValueError('History-only view has no stream')
            await views.attach(v)
        except (KeyError,ValueError,RegistryDenied):await ws.close(code=1008);return
        await ws.accept()
        try:
            while True:
                if vid not in views.views or not await asyncio.to_thread(auth.session,token):await ws.close(code=1008);break
                try:
                    await asyncio.to_thread(registry.require,who,v['runtimeId'])
                    await package_access(who,v['panelId'],v['runtimeId'])
                except (RegistryDenied,HTTPException):await ws.close(code=1008);break
                try:msg=await asyncio.wait_for(views.next_message(v),timeout=2)
                except asyncio.TimeoutError:msg={'type':'heartbeat','mode':config.mode}
                if msg.get('type') in {'reset_required','freeze_expired'}:resources.release_owner('view:'+vid)
                await ws.send_json(msg)
        except (WebSocketDisconnect,RuntimeError):pass
        finally:
            resources.release_owner('view:'+vid)
            await views.remove(vid)
    static=Path(__file__).parent/'static'
    if static.exists():app.mount('/',StaticFiles(directory=static,html=True),name='frontend')
    return app
