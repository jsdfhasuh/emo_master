"""Chromium directory/identity refresh regressions with controlled HTTP ordering.
Synthetic gateway fixtures only; no Runtime or device acceptance claim.
Command transport failure and an explicit accepted query are separate fixtures;
accepted never claims that the Job completed or released its resources.
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
state.update(username='operator', catalog_status=200, logout_status=503, command_posts=[], operation_queries=[])
def info(rid):
 target=state[rid];return {'runtimeInstanceId':target['instance'],'projectId':target['project'],'configurationRevision':target['revision']}
def panel(rid):
 return {'id':'panel-'+rid,'name':'Panel '+rid,'packageRevision':state[rid]['revision'],'pages':[{'id':'home','title':'Page '+rid,'components':[{'id':'text','type':'text','props':{'text':'Current target '+rid}}]}]}
def mock(route):
 parsed=urlsplit(route.request.url);path=parsed.path;query=parse_qs(parsed.query)
 if path=='/api/session':
  state['logged']=True;state['username']=route.request.post_data_json['username']
 if path=='/api/logout':
  # Deliberately do not claim that the server invalidated its session on failure.
  route.fulfill(status=state['logout_status'],json={'detail':'logout unavailable'});return
 if path=='/api/runtimes' and state['catalog_status']!=200:
  if state['catalog_status']==401:state['logged']=False
  route.fulfill(status=state['catalog_status'],json={'detail':'fixture catalog failure'});return
 if path=='/api/runs/start':
  state['command_posts'].append(route.request.post_data_json)
  # A lost/unavailable response does not establish whether the command executed.
  route.fulfill(status=503,json={'detail':'fixture command response unavailable'});return
 if path.startswith('/api/operations/by-request/'):
  state['operation_queries'].append(path.rsplit('/',1)[1])
  route.fulfill(json={'state':'accepted','runId':'fixture-job','run':{'id':'fixture-job','state':'running','ownsResources':True,'displayResourcesReleased':False}});return
 if path in ['/api/me','/api/session']:
  if not state['logged']:route.fulfill(status=401,json={'detail':'login'});return
  data={'username':state['username'],'role':'operator','csrf':'token','mode':'grpc'}
 elif path=='/api/runtimes':data=[{'id':rid,'alias':'Runtime '+rid,'mode':'grpc','permissions':['view','control']} for rid in ['A','B']]
 elif path.startswith('/api/runtimes/'):
  rid=path.split('/')[3];target=state[rid]
  if path.endswith('/info'):
   data=info(rid)
   if rid=='A' and state['hold_a']:state['held'].append((route,dict(data)));return
  elif path.endswith('/runs'):data=[{'id':job,'name':job,'workflowId':target['workflows'][0] if target['workflows'] else '', 'state':'running','runGroupId':'','allowedActions':['stop'],**info(rid)} for job in target['runs']]
  elif path.endswith('/workflows'):data=[{'id':workflow,'name':workflow,'canStart':target.get('canStart',True),'startBlockReason':target.get('startBlockReason',''),'inputsJson':json.dumps({'threshold':target['default']}),'inputDeclarations':{'threshold':'integer'},**info(rid)} for workflow in target['workflows']]
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
   # Backend-provided blocking reason must be visible beside a disabled Start.
   state['B'].update(canStart=False,startBlockReason='fixture resource occupied')
   refresh.click();expect(refresh).to_be_enabled()
   page.get_by_label('启动工作流目标',exact=True).select_option('wb1')
   start=page.get_by_role('button',name='启动工作流',exact=True)
   expect(start).to_be_disabled();expect(page.get_by_text('暂不能启动：fixture resource occupied',exact=False)).to_be_visible()
   assert not state['command_posts']
   state['B']['canStart']=True
   refresh.click();expect(refresh).to_be_enabled();expect(start).to_be_enabled()
   expect(page.get_by_text('暂不能启动：',exact=False)).to_have_count(0)

   # Invalid JSON is local validation: no POST, pending record or unknown-result advice.
   editor.fill('{invalid JSON');start.click()
   validation=page.get_by_role('alert').filter(has_text='尚未发送命令：')
   expect(validation).to_be_visible();expect(start).to_be_enabled()
   expect(validation).not_to_contain_text('结果未确认');expect(validation).not_to_contain_text('不自动重发')
   expect(page.get_by_role('button',name='查询操作结果',exact=True)).to_have_count(0)
   assert not state['command_posts']
   assert page.evaluate("sessionStorage.getItem('emo-pending:operator')") is None

   # Successful directory recovery removes only its own error, not validation errors.
   state['catalog_status']=503;refresh.click()
   catalog_error=page.get_by_role('alert').filter(has_text='目录刷新失败：')
   expect(catalog_error).to_be_visible();expect(validation).to_be_visible();expect(refresh).to_be_enabled()
   state['catalog_status']=200;refresh.click();expect(refresh).to_be_enabled()
   expect(catalog_error).to_have_count(0);expect(validation).to_be_visible()

   # Create a genuinely uncertain submitted intent, then expire this session.
   editor.fill('{"threshold":8}');start.click()
   query=page.get_by_role('button',name='查询操作结果',exact=True)
   expect(query).to_be_enabled()
   expect(page.get_by_role('alert').filter(has_text='结果未确认时不自动重发')).to_be_visible()
   assert len(state['command_posts'])==1
   original=state['command_posts'][0]['requestId']
   saved=page.evaluate("sessionStorage.getItem('emo-pending:operator')")
   assert json.loads(saved)['id']==original
   state['catalog_status']=401;refresh.click()
   expect(page.get_by_role('button',name='登录',exact=True)).to_be_enabled()
   expect(page.get_by_role('alert')).to_contain_text('会话已失效，请重新登录')
   expect(identity).to_have_count(0);expect(query).to_have_count(0)
   assert page.evaluate("sessionStorage.getItem('emo-pending:operator')")==saved

   def login_as(username):
    page.get_by_label('用户名',exact=True).fill(username)
    page.get_by_label('密码',exact=True).fill('fixture')
    page.get_by_role('button',name='登录',exact=True).click()
    expect(identity).to_contain_text('A-1 / project-A');expect(refresh).to_be_enabled()

   state['catalog_status']=200;login_as('other-operator')
   expect(query).to_have_count(0)
   assert page.evaluate("sessionStorage.getItem('emo-pending:other-operator')") is None
   assert page.evaluate("sessionStorage.getItem('emo-pending:operator')")==saved
   assert len(state['command_posts'])==1 and not state['operation_queries']
   # Failed remote logout still clears the local UI and permits another login.
   page.get_by_role('button',name='退出',exact=True).click()
   expect(page.get_by_role('button',name='登录',exact=True)).to_be_enabled()
   expect(identity).to_have_count(0);expect(query).to_have_count(0)
   login_as('operator');expect(query).to_be_enabled()
   expect(page.get_by_role('button',name='结束跟踪（不重发）',exact=True)).to_be_visible()
   assert len(state['command_posts'])==1 and not state['operation_queries']
   # Logout with the restored unknown intent must preserve it too.
   page.get_by_role('button',name='退出',exact=True).click()
   expect(page.get_by_role('button',name='登录',exact=True)).to_be_enabled()
   assert page.evaluate("sessionStorage.getItem('emo-pending:operator')")==saved
   login_as('operator');expect(query).to_be_enabled()
   page.get_by_label('Runtime 目标',exact=True).select_option('B')
   expect(identity).to_contain_text('B-2 / project-B-new');expect(refresh).to_be_enabled()
   query.click()
   expect(query).to_have_count(0)
   expect(page.get_by_text('启动已受理：fixture-job；Job 尚未结束',exact=False)).to_be_visible()
   expect(page.get_by_label('原控制运行状态',exact=True)).to_contain_text('持有资源：是')
   expect(page.get_by_label('原控制运行状态',exact=True)).to_contain_text('展示资源已释放：否')
   assert state['operation_queries']==[original] and len(state['command_posts'])==1
   assert page.evaluate("sessionStorage.getItem('emo-pending:operator')") is None
   assert not errors,errors
  except Exception:
   page.screenshot(path=str(ROOT/'test-tmp/catalog-refresh-failure.png'),full_page=True);(ROOT/'test-tmp/catalog-refresh-failure.html').write_text(page.content(),encoding='utf-8');raise
  finally:browser.close()
finally:server.shutdown()
print('PASS: manual full directory refresh, valid selection/dirty input preservation, invalid target clearing, delayed A response after B, Runtime identity replacement, local validation, isolated error recovery, blocked Start reason, session expiry, failed logout and user-keyed unknown intent recovery without replay. HTTP fixtures only; accepted is not Job completion.')
