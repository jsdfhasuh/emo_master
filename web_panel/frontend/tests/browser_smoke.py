"""Cloud browser verification using HTTP/WS fixtures, NOT real Runtime acceptance."""
import json, threading, os, base64
from http.server import ThreadingHTTPServer,SimpleHTTPRequestHandler
from pathlib import Path
from playwright.sync_api import sync_playwright
ROOT=Path(__file__).resolve().parents[1]
(ROOT/'test-tmp').mkdir(exist_ok=True)
class Handler(SimpleHTTPRequestHandler):
 def __init__(self,*a,**kw):super().__init__(*a,directory=str(ROOT/'dist'),**kw)
 def log_message(self,*a):pass
server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
threading.Thread(target=server.serve_forever,daemon=True).start()
origin=f'http://127.0.0.1:{server.server_port}'
panel={'id':'demo-panel','name':'演示面板','packageRevision':'draft-1','pages':[{'id':'home','title':'生产总览','components':[{'id':'title','type':'page_title'},{'id':'image','type':'image','label':'本轮图片','binding':'image'},{'id':'score','type':'number','label':'分数','binding':'score'},{'id':'verdict','type':'indicator','label':'判定','binding':'verdict'},{'id':'clock','type':'clock'},{'id':'state','type':'runtime_status'},{'id':'table','type':'table','props':{'value':[{'x':1}]}},{'id':'text','type':'text','props':{'text':'<script>window.PWNED=1</script>'}},{'id':'box','type':'container','children':[{'id':'nested','type':'text','props':{'text':'嵌套内容'}}]},{'id':'nav','type':'page_navigation'},{'id':'button','type':'navigation_button','props':{'action':'navigate','targetPageId':'detail','text':'详情'}},{'id':'history-table','type':'sqlite_table'},{'id':'aggregate','type':'sqlite_value'}]},{'id':'detail','title':'详情','components':[{'id':'detail-title','type':'page_title'}]}]}
result={'resultKey':'r1','resultScopeId':'scope-1','sources':{'image':{'state':'ready','image':{'url':'/api/assets/demo-image','ownerResultKey':'r1'}},'score':{'state':'ready','value':97},'verdict':{'state':'ready','value':'OK'}}}
state={'logged':False,'start':0,'freeze':0}
def mock(route):
 path=route.request.url.replace(origin,'').split('?')[0]
 if path=='/api/session':state['logged']=True
 if path in ['/api/me','/api/session']:
  if not state['logged']:route.fulfill(status=401,json={'detail':'unauthorized'});return
  data={'username':'tester','role':'admin','csrf':'fixture','mode':'mock'}
 elif path=='/api/panels':data=[panel]
 elif path=='/api/panels/demo-panel':data=panel
 elif path in ['/api/runs','/api/runtimes/configured/runs']:data=[{'id':'demo-run','state':'running','workflowId':'demo-workflow','runtimeInstanceId':'mock-instance','projectId':'demo-project','configurationRevision':'1'}]
 elif path=='/api/runtimes':data=[{'id':'configured','alias':'演示','mode':'mock'}]
 elif path=='/api/runtimes/configured/status':data={'counters':{'source':'mock-authoritative','total':'1','ok':'1','ng':'0','at':'fixture'},'events':{'events':[],'nextSequence':'0','resetRequired':False,'source':'mock-events'}}
 elif path=='/api/runtimes/configured/workflows':data=[{'id':'demo-workflow','name':'Demo workflow','runtimeInstanceId':'mock-instance','projectId':'demo-project','configurationRevision':'1'}]
 elif path=='/api/views':data={'id':'view-1'}
 elif path=='/api/views/view-1' and route.request.method=='DELETE':data={}
 elif path=='/api/views/view-1/freeze':state['freeze']+=1;data={'result':result}
 elif path=='/api/views/view-1/resume':data={'result':result}
 elif '/history/' in path:data={'rows':[{'id':1,'result':'OK'}],'total':1,'page':1,'pageSize':20}
 elif path=='/api/packages':data=[]
 elif path=='/api/admin/users':data=[{'username':'tester','role':'admin'}]
 elif path=='/api/admin/runtimes':data=[{'id':'configured','alias':'演示','mode':'mock','address':''}]
 elif path=='/api/runs/start':state['start']+=1;data={'operationId':'op-1','state':'succeeded'}
 elif path=='/api/assets/demo-image':route.fulfill(content_type='image/png',body=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a1ioAAAAASUVORK5CYII='));return
 elif path=='/api/logout':state['logged']=False;data={}
 else:route.fulfill(status=404,json={'detail':path});return
 route.fulfill(json=data)
with sync_playwright() as p:
 browser=p.chromium.launch(**({'executable_path':os.environ['CHROMIUM_PATH']} if os.environ.get('CHROMIUM_PATH') else {}),headless=True,args=['--no-sandbox','--disable-dev-shm-usage'],env={'TMPDIR':str(ROOT/'test-tmp')})
 page=browser.new_page(viewport={'width':1280,'height':900});errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
 page.route('**/api/**',mock)
 sockets=[]
 def connect(ws):
  sockets.append(ws);ws.send(json.dumps({'type':'snapshot','result':result}))
 page.route_web_socket('**/ws/**',connect)
 try:
  page.goto(origin);page.get_by_label('用户名').fill('tester');page.get_by_label('密码').fill('fixture');page.get_by_role('button',name='登录',exact=True).click()
  page.get_by_text('97',exact=True).wait_for();assert page.get_by_text('嵌套内容',exact=True).count()==1
  assert page.evaluate('window.PWNED') is None
  page.get_by_role('button',name='冻结查看',exact=True).click();page.get_by_text('已冻结查看，后台仍在运行').wait_for();assert state['freeze']==1
  page.get_by_role('button',name='恢复实时',exact=True).click()
  held=[]
  page.route('**/api/assets/held-image',lambda route:held.append(route))
  candidate=json.loads(json.dumps(result));candidate['resultKey']='late';candidate['sources']['score']['value']=888;candidate['sources']['image']['image']={'url':'/api/assets/held-image','ownerResultKey':'late'}
  sockets[-1].send(json.dumps({'type':'result','result':candidate}))
  page.wait_for_timeout(100);assert held
  page.get_by_role('button',name='冻结查看',exact=True).click();page.get_by_text('已冻结查看，后台仍在运行').wait_for()
  try:held.pop().fulfill(content_type='image/png',body=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a1ioAAAAASUVORK5CYII='))
  except Exception:pass # Browser may already have cancelled the old candidate fetch.
  page.wait_for_timeout(100);assert page.get_by_text('888',exact=True).count()==0
  page.get_by_text('97',exact=True).wait_for()
  page.get_by_role('button',name='恢复实时',exact=True).click();page.get_by_role('button',name='启动工作流',exact=True).click();page.get_by_text('操作已受理：op-1').wait_for();assert state['start']==1
  page.get_by_role('button',name='详情',exact=True).first.click();page.get_by_role('heading',name='详情',exact=True).wait_for()
  page.set_viewport_size({'width':390,'height':844});page.get_by_role('button',name='生产总览',exact=True).first.click();page.get_by_text('97',exact=True).wait_for();assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
  page.screenshot(path=str(ROOT/'browser-mobile.png'),full_page=True)
  # Invalidate a candidate while its image is still preparing; it must never commit.
  def reset_during_image(route):
   sockets[-1].send(json.dumps({'type':'reset_required'}))
   route.fulfill(content_type='image/png',body=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a1ioAAAAASUVORK5CYII='))
  page.route('**/api/assets/slow-image',reset_during_image)
  stale=json.loads(json.dumps(result));stale['resultKey']='stale';stale['sources']['score']['value']=999;stale['sources']['image']['image']={'url':'/api/assets/slow-image','ownerResultKey':'stale'}
  sockets[-1].send(json.dumps({'type':'result','result':stale}))
  page.wait_for_timeout(500);assert page.get_by_text('999',exact=True).count()==0
  page.get_by_label('停止控制目标',exact=True).select_option('demo-run')
  page.get_by_label('查看运行',exact=True).select_option('')
  assert page.get_by_label('停止控制目标',exact=True).input_value()=='demo-run'
  page.get_by_label('history-table 筛选').wait_for()
  page.get_by_role('button',name='启动工作流',exact=True).click();page.wait_for_timeout(200);assert state['start']==2


  assert not errors,errors
 except Exception:
  try:
   page.screenshot(path=str(ROOT/'test-tmp'/'failure.png'),full_page=True)
   (ROOT/'test-tmp'/'failure.html').write_text(page.content(),encoding='utf-8')
  except Exception:pass
  raise
 browser.close()
server.shutdown();print('PASS: desktop/mobile login, all 13 renderer types, script text escaping, staged result, freeze/resume, control, navigation; HTTP/WS fixtures only')
