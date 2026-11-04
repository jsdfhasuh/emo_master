import json
import threading
from pathlib import Path
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from urllib.parse import urlsplit, parse_qs
from playwright.sync_api import sync_playwright, expect
ROOT=Path(__file__).parent
class Handler(SimpleHTTPRequestHandler):
    def __init__(self,*args,**kwargs):super().__init__(*args,directory=str(ROOT/'dist'),**kwargs)
    def log_message(self,*args):pass
server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
threading.Thread(target=server.serve_forever,daemon=True).start()
state={'active':'old','requests':[],'views':[],'sockets':[],'errors':[]}
identity={'runtimeInstanceId':'instance-A','projectId':'project-A','configurationRevision':'1'}
def panel(revision):
    components=[{'id':'version','type':'text','props':{'text':'VISIBLE-PAGE-'+revision.upper()}}]
    if revision=='new':components.append({'id':'added-count','type':'number','label':'New source','binding':'added'})
    return {'id':'package-'+revision,'name':'Package '+revision,'packageRevision':revision,'pages':[{'id':'home','title':'Page '+revision,'components':components}]}
def mock(route):
    request=route.request;parsed=urlsplit(request.url);path=parsed.path
    state['requests'].append({'method':request.method,'path':path})
    if path=='/api/me':data={'username':'viewer','role':'viewer','csrf':'fixture-token','mode':'grpc'}
    elif path=='/api/runtimes':data=[{'id':'A','alias':'Runtime A','mode':'grpc','permissions':['view']}]
    elif path=='/api/runtimes/A/info':data=identity
    elif path=='/api/runtimes/A/runs':data=[{'id':'job-existing','state':'running','name':'Existing job',**identity}]
    elif path=='/api/runtimes/A/workflows':data=[]
    elif path=='/api/runtimes/A/status':data={'runtime':identity,'counters':{'source':'fixture','counters':[]},'events':{'events':[],'nextSequence':'0','resetRequired':False,'source':'fixture'}}
    elif path=='/api/runtimes/A/events':data={'events':[],'nextSequence':'0','resetRequired':False,'source':'fixture',**identity}
    elif path=='/api/panels':data=[panel(state['active'])]
    elif path.startswith('/api/panels/'):
        if path.rsplit('/',1)[1]!='package-'+state['active']:
            route.fulfill(status=404,json={'detail':'Panel not active for this Runtime and project'});return
        data=panel(state['active'])
    elif path=='/api/views':
        body=request.post_data_json;state['views'].append(body)
        data={'id':'view-'+str(len(state['views'])),'runIds':[body['runId']] if body.get('runId') else [],'sourceProblems':{'added':'SOURCE_NOT_CAPTURED'} if body['panelId']=='package-new' else {}}
    elif path.startswith('/api/views/') and request.method=='DELETE':data={}
    else:route.fulfill(status=404,json={'detail':path});return
    route.fulfill(json=data)
def socket(ws):state['sockets'].append(ws)
try:
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True,executable_path=r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe')
        page=browser.new_page();page.on('pageerror',lambda error:state['errors'].append(str(error)))
        page.route('**/api/**',mock);page.route_web_socket('**/ws/**',socket)
        page.goto(f'http://127.0.0.1:{server.server_port}')
        expect(page.get_by_text('VISIBLE-PAGE-OLD',exact=True)).to_be_visible()
        expect(page.get_by_role('button',name='刷新页面包和状态',exact=True)).to_be_enabled()
        page.get_by_label('查看运行',exact=True).select_option('job-existing')
        for _ in range(50):
            if state['sockets']:break
            page.wait_for_timeout(20)
        assert state['sockets'],'Expected a connected view'
        old_socket=state['sockets'][-1]
        state['active']='new'
        old_socket.send(json.dumps({'type':'package_updated','packageId':'package-new','packageRevision':'new','explicitRefreshRequired':True}))
        expect(page.get_by_text('页面包已有更新，请点击刷新页面包',exact=True)).to_be_visible()
        expect(page.get_by_text('VISIBLE-PAGE-OLD',exact=True)).to_be_visible()
        page.screenshot(path=str(ROOT/'01-update-notice-retains-old.png'),full_page=True)
        before={'footer':page.locator('footer').inner_text(),'visibleOld':page.get_by_text('VISIBLE-PAGE-OLD',exact=True).is_visible(),'selectedRun':page.get_by_label('查看运行',exact=True).input_value(),'views':list(state['views'])}
        old_socket.close(code=1001,reason='single ordinary disconnect')
        # Deliberately no refresh/switch/control UI action after the publication notice.
        expect(page.get_by_text('VISIBLE-PAGE-NEW',exact=True)).to_be_visible(timeout=8000)
        for _ in range(50):
            if len(state['sockets'])>=2:break
            page.wait_for_timeout(20)
        state['sockets'][-1].send(json.dumps({'type':'result','result':{'resultKey':'same-existing-job-result','jobId':'job-existing','resultScopeId':'scope-1','sources':{'existing':{'state':'available','value':2}}}}))
        expect(page.get_by_text('SOURCE_NOT_CAPTURED',exact=False)).to_be_visible()
        page.screenshot(path=str(ROOT/'02-reconnect-silently-adopts-new.png'),full_page=True)
        after={'footer':page.locator('footer').inner_text(),'visibleNew':page.get_by_text('VISIBLE-PAGE-NEW',exact=True).is_visible(),'selectedRun':page.get_by_label('查看运行',exact=True).input_value(),'sourceUnavailable':page.get_by_text('SOURCE_NOT_CAPTURED',exact=False).inner_text(),'views':list(state['views'])}
        production=[item for item in state['requests'] if item['path'].startswith('/api/runs/')]
        report={'status':'REPRODUCED_PLAN_213_VIOLATION','browser':'installed Edge headless / current Vite build / synthetic HTTP and WS','humanOrControlActionAfterNotice':False,'before':before,'after':after,'productionCommands':production,'errors':state['errors'],'requests':state['requests']}
        (ROOT/'browser-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        assert not production and not state['errors']
        browser.close()
finally:server.shutdown()
print(json.dumps({'status':'REPRODUCED','report':str(ROOT/'browser-report.json')},ensure_ascii=False))
