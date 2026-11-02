"""Chromium directory/identity refresh regressions with controlled HTTP ordering.
Synthetic gateway fixtures only; no Runtime or device acceptance claim.
"""
import json
import os
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
import threading
from urllib.parse import urlsplit, parse_qs
from playwright.sync_api import sync_playwright, expect

ROOT=Path(__file__).resolve().parents[1]
(ROOT/'test-tmp').mkdir(exist_ok=True)
class Handler(SimpleHTTPRequestHandler):
 def __init__(self,*args,**kwargs):super().__init__(*args,directory=str(ROOT/'dist'),**kwargs)
 def log_message(self,*args):pass
server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
threading.Thread(target=server.serve_forever,daemon=True).start()
origin=f'http://127.0.0.1:{server.server_port}'
state={'logged':False,'hold_a':False,'held':[], 'A':{'instance':'A-1','project':'project-A','revision':'1','runs':['a1'],'workflows':['wa1'],'default':1}, 'B':{'instance':'B-1','project':'project-B','revision':'8','runs':['b1'],'workflows':['wb1'],'default':8}}
def info(rid):
 target=state[rid];return {'runtimeInstanceId':target['instance'],'projectId':target['project'],'configurationRevision':target['revision']}
def panel(rid):
 return {'id':'panel-'+rid,'name':'Panel '+rid,'packageRevision':state[rid]['revision'],'pages':[{'id':'home','title':'Page '+rid,'components':[{'id':'text','type':'text','props':{'text':'Current target '+rid}}]}]}
def mock(route):
 parsed=urlsplit(route.request.url);path=parsed.path;query=parse_qs(parsed.query)
 if path=='/api/session':state['logged']=True
 if path in ['/api/me','/api/session']:
  if not state['logged']:route.fulfill(status=401,json={'detail':'login'});return
  data={'username':'operator','role':'operator','csrf':'token','mode':'grpc'}
 elif path=='/api/runtimes':data=[{'id':rid,'alias':'Runtime '+rid,'mode':'grpc','permissions':['view','control']} for rid in ['A','B']]
 elif path.startswith('/api/runtimes/'):
  rid=path.split('/')[3];target=state[rid]
  if path.endswith('/info'):
   data=info(rid)
   if rid=='A' and state['hold_a']:state['held'].append((route,dict(data)));return
  elif path.endswith('/runs'):data=[{'id':job,'name':job,'workflowId':target['workflows'][0] if target['workflows'] else '', 'state':'running','runGroupId':'','allowedActions':['stop'],**info(rid)} for job in target['runs']]
  elif path.endswith('/workflows'):data=[{'id':workflow,'name':workflow,'canStart':True,'inputsJson':json.dumps({'threshold':target['default']}),'inputDeclarations':{'threshold':'integer'},**info(rid)} for workflow in target['workflows']]
  elif path.endswith('/status'):data={'runtime':info(rid),'counters':{'source':'fixture','counters':[]},'events':{'events':[],'nextSequence':'0','resetRequired':False,'source':'fixture'}}
  elif path.endswith('/events'):data={'events':[],'nextSequence':'0','resetRequired':False,'source':'fixture',**info(rid)}
  else:route.fulfill(status=404,json={'detail':path});return
 elif path=='/api/panels' or path.startswith('/api/panels/'):
  rid=query['runtimeId'][0];assert query['projectId'][0]==state[rid]['project'];data=[panel(rid)] if path=='/api/panels' else panel(rid)
 elif path=='/api/views':
  body=route.request.post_data_json;assert body['projectId']==state[body['runtimeId']]['project'];data={'id':'view-'+body['runtimeId'],'runIds':[body['runId']] if body.get('runId') else []}
 elif path.startswith('/api/views/') and route.request.method=='DELETE':data={}
 else:route.fulfill(status=404,json={'detail':path});return
 route.fulfill(json=data)
try:
 with sync_playwright() as p:
  browser=p.chromium.launch(headless=True,args=['--no-sandbox','--disable-dev-shm-usage'],**({'executable_path':os.environ['CHROMIUM_PATH']} if os.environ.get('CHROMIUM_PATH') else {}))
  page=browser.new_page();errors=[];page.on('pageerror',lambda error:errors.append(str(error)));page.route('**/api/**',mock);page.route_web_socket('**/ws/**',lambda ws:None)
  try:
   page.goto(origin);page.get_by_label('用户名',exact=True).fill('operator');page.get_by_label('密码',exact=True).fill('fixture');page.get_by_role('button',name='登录',exact=True).click()
   identity=page.get_by_label('当前 Runtime 身份',exact=True);refresh=page.get_by_role('button',name='刷新页面包和状态',exact=True)
   expect(identity).to_contain_text('A-1 / project-A');expect(refresh).to_be_enabled()
   page.get_by_label('查看运行',exact=True).select_option('a1');page.get_by_label('停止控制目标',exact=True).select_option('a1')
   editor=page.get_by_label('工作流输入',exact=True);editor.fill('{"threshold":42}')
   state['A'].update(runs=['a1','a2'],workflows=['wa1','wa2'],revision='2',default=9)
   refresh.click();expect(identity).to_contain_text('配置 2');expect(refresh).to_be_enabled()
   expect(page.get_by_label('查看运行',exact=True)).to_have_value('a1');expect(page.get_by_label('停止控制目标',exact=True)).to_have_value('a1')
   expect(page.get_by_label('查看运行',exact=True).locator('option[value="a2"]')).to_have_count(1)
   expect(page.get_by_label('启动工作流目标',exact=True).locator('option[value="wa2"]')).to_have_count(1)
   expect(editor).to_have_value('{"threshold":42}');page.get_by_text('输入文本未覆盖',exact=False).wait_for()
   state['A'].update(runs=['a2'],workflows=['wa2'])
   refresh.click();expect(refresh).to_be_enabled();expect(page.get_by_label('查看运行',exact=True)).to_have_value('');expect(page.get_by_label('停止控制目标',exact=True)).to_have_value('');expect(page.get_by_label('启动工作流目标',exact=True)).to_have_value('')
   expect(editor).to_have_value('{"threshold":42}')
   # Deliberately release an old A response only after B is fully selected.
   state['hold_a']=True;refresh.click()
   for _ in range(100):
    if state['held']:break
    page.wait_for_timeout(20)
   assert state['held'],'A refresh must be in flight'
   page.get_by_label('Runtime 目标',exact=True).select_option('B');expect(identity).to_contain_text('B-1 / project-B');expect(refresh).to_be_enabled()
   for route,data in state['held']:
    try:route.fulfill(json=data)
    except Exception:pass # Aborted A fetch may already have been retired by the browser.
   state['held'].clear();state['hold_a']=False;page.wait_for_timeout(100)
   expect(identity).to_contain_text('B-1 / project-B');expect(identity).to_contain_text('配置 8');expect(page.get_by_text('Current target B',exact=True)).to_have_count(1)
   expect(page.get_by_label('查看运行',exact=True).locator('option[value="b1"]')).to_have_count(1);expect(page.get_by_label('查看运行',exact=True).locator('option[value="a2"]')).to_have_count(0)
   expect(page.get_by_label('启动工作流目标',exact=True)).to_have_value('wb1');expect(editor).to_have_value('{"threshold": 8}')
   # Reusing Job IDs in a different Runtime instance must not preserve Stop.
   page.get_by_label('查看运行',exact=True).select_option('b1');page.get_by_label('停止控制目标',exact=True).select_option('b1')
   state['B'].update(instance='B-2',project='project-B-new',revision='9')
   refresh.click();expect(identity).to_contain_text('B-2 / project-B-new');expect(refresh).to_be_enabled()
   expect(page.get_by_label('查看运行',exact=True)).to_have_value('');expect(page.get_by_label('停止控制目标',exact=True)).to_have_value('');expect(page.get_by_label('启动工作流目标',exact=True)).to_have_value('')
   assert not errors,errors
  except Exception:
   page.screenshot(path=str(ROOT/'test-tmp/catalog-refresh-failure.png'),full_page=True);(ROOT/'test-tmp/catalog-refresh-failure.html').write_text(page.content(),encoding='utf-8');raise
  finally:browser.close()
finally:server.shutdown()
print('PASS: manual full directory refresh, valid selection/dirty input preservation, invalid target clearing, delayed A response after B, and Runtime identity replacement. HTTP fixtures only.')
