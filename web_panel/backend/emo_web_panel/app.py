import asyncio
import hmac
import json
import time
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

COOKIE='emo_panel_session'

def create_app(config: Config, runtime=None):
    config.data_dir.mkdir(parents=True,exist_ok=True)
    auth=AuthStore(config.data_dir/'web.sqlite',config.session_ttl)
    packages=PackageStore(config.data_dir/'packages')
    rt=runtime or (MockRuntime() if config.mode=='mock' else GrpcRuntimeClient(config.runtime_address))
    views=Views(rt,config.max_views,config.freeze_ttl)
    ops=Operations(config.data_dir/'web.sqlite')
    login_failures={}
    @asynccontextmanager
    async def lifespan(app):
        await rt.start()
        yield
        await views.close()
        await rt.close()
    app=FastAPI(title='emo_master independent Web panel',lifespan=lifespan)
    app.state.auth,app.state.views,app.state.runtime=auth,views,rt
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
        body=await req.json()
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
    @app.get('/api/admin/users')
    async def users(req:Request):await identity(req,'admin');return await asyncio.to_thread(auth.users)
    @app.post('/api/admin/users')
    async def create_user(req:Request):
        await identity(req,'admin',True);b=await req.json()
        await asyncio.to_thread(auth.create_user,b.get('username'),b.get('password'),b.get('role'))
        return {'ok':True}
    @app.patch('/api/admin/users/{username}')
    async def update_user(username:str,req:Request):
        who=await identity(req,'admin',True);b=await req.json()
        if username==who['username'] and b.get('role',who['role'])!=who['role']:raise HTTPException(422,'Change your role from another administrator account')
        await asyncio.to_thread(auth.update_user,username,b.get('role'),b.get('password'))
        await asyncio.to_thread(auth.audit,who['username'],'update_user',username,'succeeded')
        return {'ok':True}
    @app.delete('/api/admin/users/{username}')
    async def delete_user(username:str,req:Request):
        who=await identity(req,'admin',True)
        if username==who['username']:raise HTTPException(422,'Cannot delete own active administrator')
        await asyncio.to_thread(auth.delete_user,username)
        await asyncio.to_thread(auth.audit,who['username'],'delete_user',username,'succeeded')
        return {'ok':True}
    @app.get('/api/admin/runtimes')
    async def runtimes(req:Request):
        await identity(req,'admin');return [{'id':'configured','mode':config.mode,'editable':False}]
    @app.get('/api/runtimes/configured/workflows')
    async def workflows(req:Request):await identity(req);return await rt.workflows()
    @app.get('/api/runs')
    async def runs(req:Request):await identity(req);return await rt.runs()
    @app.get('/api/runtimes/configured/runs')
    async def runtime_runs(req:Request):return await runs(req)
    async def control(req,action,target):
        who=await identity(req,'control',True)
        if config.preview:raise HTTPException(403,'Preview is read-only')
        if config.mode!='mock':await rt.info() # fail closed before storing unexecutable intent
        body=await req.json()
        result=await ops.execute(who['username'],body.get('requestId'),action,target,body,rt)
        await asyncio.to_thread(auth.audit,who['username'],action,target,result['state'])
        return result
    @app.post('/api/runs/start')
    async def start(req:Request):return await control(req,'start','')
    @app.post('/api/runs/{run_id}/stop')
    async def stop(run_id:str,req:Request):return await control(req,'stop',run_id)
    @app.get('/api/operations/by-request/{request_id}')
    async def operation_by_request(request_id:str,req:Request):
        who=await identity(req);r=await asyncio.to_thread(ops.by_request,who['username'],request_id)
        if not r:raise HTTPException(404,'Operation not found; outcome is not assumed')
        return r
    @app.get('/api/operations/{op_id}')
    async def operation(op_id:str,req:Request):
        who=await identity(req);r=await asyncio.to_thread(ops.get,op_id,who['username'])
        if not r:raise HTTPException(404,'Operation not found')
        return r
    def panel_list():
        active=packages.active()
        if active:
            p=active['presentation'];return [{**p,'id':active['packageId'],'packageRevision':active['manifest']['packageRevision']}]
        return [demo_panel()] if config.mode=='mock' else []
    @app.get('/api/panels')
    async def panels(req:Request):await identity(req);return await asyncio.to_thread(panel_list)
    @app.get('/api/panels/{panel_id}')
    async def panel(panel_id:str,req:Request):
        await identity(req)
        return await find_panel(panel_id)
    async def find_panel(panel_id):
        for p in await asyncio.to_thread(panel_list):
            if p['id']==panel_id:return p
        raise HTTPException(404,'Panel not active')
    @app.post('/api/views')
    async def new_view(req:Request):
        who=await identity(req,mutating=True);b=await req.json();p=await find_panel(b.get('panelId'))
        valid=await rt.runs()
        if b.get('runId') not in [r.get('id',r.get('runId')) for r in valid]:raise HTTPException(404,'Run not found')
        v=views.create(who['username'],b['panelId'],b['runId'],p['packageRevision']);return {'id':v['id']}
    @app.post('/api/views/{vid}/{action}')
    async def view_action(vid:str,action:str,req:Request):
        who=await identity(req,mutating=True);v=views.get(vid,who['username'])
        if action not in {'freeze','resume'}:raise HTTPException(404,'Unknown view action')
        if action=='freeze':
            body=await req.json()
            key=body.get('resultKey')
            if key not in v['recent']:raise HTTPException(409,'Displayed result expired; refresh before freezing')
            v['last']=v['recent'][key]
        v['frozen']=action=='freeze';v['freezeUntil']=time.monotonic()+config.freeze_ttl
        return {'frozen':v['frozen'],'resultKey':(v['last'] or {}).get('resultKey')}
    @app.get('/api/views/{vid}/history/{component_id}')
    async def history(vid:str,component_id:str,req:Request,page:int=1,pageSize:int=10,q:str='',sort:str='id',order:str='asc'):
        who=await identity(req);v=views.get(vid,who['username']);p=await find_panel(v['panelId'])
        def contains(cs):return any(c['id']==component_id and c['type'] in {'sqlite_table','sqlite_value'} or contains(c.get('children',[])) for c in cs)
        if not any(contains(page['components']) for page in p['pages']):raise HTTPException(404,'Undeclared history source')
        if not 1<=page<=10000 or not 1<=pageSize<=100:raise HTTPException(422,'Invalid page')
        return await rt.history(component_id,page,pageSize,q=q,sort=sort,order=order)
    @app.get('/api/history-images/{image_ref}')
    async def history_image(image_ref:str,req:Request):
        await identity(req)
        if config.mode!='mock' or image_ref!='demo-image':raise HTTPException(404,'Unknown opaque image reference')
        return Response(IMAGE,media_type='image/png')
    @app.get('/api/assets/demo-image')
    async def demo_image(req:Request):
        await identity(req)
        if config.mode!='mock':raise HTTPException(404)
        return Response(IMAGE,media_type='image/png')
    @app.get('/api/assets/{package_id}/{asset_id}')
    async def asset(package_id:str,asset_id:str,req:Request):
        await identity(req);await find_panel(package_id)
        data,mime=await asyncio.to_thread(packages.asset,package_id,asset_id)
        return Response(data,media_type=mime)
    @app.get('/api/packages')
    async def package_list(req:Request):await identity(req,'admin');return await asyncio.to_thread(packages.list)
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
        await identity(req,'admin',True);result=await asyncio.to_thread(packages.activate,pid)
        for v in views.views.values():
            if v['queue'].full():v['queue'].get_nowait()
            v['queue'].put_nowait({'type':'package_updated','packageId':pid})
        return result
    @app.websocket('/ws/views/{vid}')
    async def ws(ws:WebSocket,vid:str):
        token=ws.cookies.get(COOKIE);who=await asyncio.to_thread(auth.session,token)
        scheme='https' if ws.url.scheme=='wss' else 'http'
        if not who or not origin_ok(ws.headers.get('origin'),ws.headers.get('host'),scheme):await ws.close(code=1008);return
        try:v=views.get(vid,who['username']);await views.attach(v)
        except (KeyError,ValueError):await ws.close(code=1008);return
        await ws.accept()
        try:
            while True:
                if not await asyncio.to_thread(auth.session,token):await ws.close(code=1008);break
                try:msg=await asyncio.wait_for(v['queue'].get(),timeout=2)
                except asyncio.TimeoutError:msg={'type':'heartbeat','mode':config.mode}
                await ws.send_json(msg)
        except (WebSocketDisconnect,RuntimeError):pass
        finally:await views.remove(vid)
    static=Path(__file__).parent/'static'
    if static.exists():app.mount('/',StaticFiles(directory=static,html=True),name='frontend')
    return app
