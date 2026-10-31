"""Real FastAPI HTTP/WS + explicit mock Runtime + real Chromium. No hardware."""
import json,os,socket,sys,tempfile,threading,time,zipfile,hashlib
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
(ROOT/'test-tmp').mkdir(exist_ok=True)
sys.path.insert(0,str(ROOT.parent/'backend'))
import uvicorn
from emo_web_panel.app import create_app
from emo_web_panel.auth import AuthStore
from emo_web_panel.mock_runtime import demo_panel, IMAGE
from emo_web_panel.config import Config
from playwright.sync_api import sync_playwright

def make_package(directory,revision):
 panel=demo_panel();panel['pages'][0]['title']=f'Package {revision}'
 panel['pages'][0]['components'].append({'id':'package-static','type':'image','label':'Package asset','props':{'assetId':'pinned'}})
 manifest={'format':'emo-web-panel-1','projectId':'demo-project','packageRevision':revision,'panelContractVersion':'draft-unverified','rendererRequirements':[],'presentationFile':'presentation.json','assets':[{'id':'pinned','path':'assets/pinned.png','mimeType':'image/png','sha256':hashlib.sha256(IMAGE).hexdigest()}]}
 path=directory/f'{revision}.emoweb'
 with zipfile.ZipFile(path,'w',compression=zipfile.ZIP_DEFLATED) as archive:
  archive.writestr('assets/pinned.png',IMAGE);archive.writestr('manifest.json',json.dumps(manifest));archive.writestr('presentation.json',json.dumps({'title':f'Package {revision}','pages':panel['pages']}))
 return path

def target_isolation_check(page,browser,base,data,versions,password,errors):
 """W5-E: actual browser target contexts; synthetic Runtime data, no hardware."""
 from playwright.sync_api import expect
 headers={'X-CSRF-Token':page.request.get(base+'/api/me').json()['csrf']}
 def post(path,payload):
  response=page.request.post(base+path,headers=headers,data=payload)
  assert response.ok,(path,response.status,response.text())
  return response.json()
 post('/api/admin/runtimes',{'id':'line-b','alias':'Independent B','mode':'mock','address':''})
 post('/api/admin/users',{'username':'bviewer','password':'B-viewer-fixture-123!','role':'viewer'})
 grant=page.request.put(base+'/api/admin/runtimes/line-b/grants',headers=headers,
                       data={'username':'bviewer','view':True,'control':False})
 assert grant.ok
 page.get_by_role('button',name='刷新页面包和状态',exact=True).click()
 expect(page.get_by_label('Runtime 目标',exact=True).locator('option[value="line-b"]')).to_have_count(1)
 page.get_by_label('Runtime 目标',exact=True).select_option('line-b')
 page.get_by_role('heading',name='Demo production',exact=True).wait_for()
 path=make_package(data,'b-v1')
 with page.expect_response(lambda r:r.url.endswith('/api/packages') and r.request.method=='POST') as upload:
  page.get_by_label('导入页面包',exact=True).set_input_files(str(path))
 assert upload.value.ok
 bid=hashlib.sha256(path.read_bytes()).hexdigest()
 installed=page.request.get(base+'/api/packages?runtimeId=line-b&projectId=demo-project')
 assert installed.ok
 matches=[item for item in installed.json() if item['packageId']==bid and item['manifest']['packageRevision']=='b-v1']
 assert len(matches)==1
 def activate(pid,title):
  row=page.locator(f'[data-package-id="{pid}"]');row.wait_for()
  page.once('dialog',lambda dialog:dialog.accept())
  with page.expect_response(lambda r:r.url.endswith(f'/api/packages/{pid}/activate')) as response:
   row.get_by_role('button',name='对当前目标启用 / 切回此版本',exact=True).click()
  assert response.value.ok,response.value.text()
  page.get_by_role('heading',name=title,exact=True).wait_for()
 activate(bid,'Package b-v1')
 assert page.request.get(base+'/api/panels?runtimeId=configured&projectId=demo-project').json()[0]['id']==versions[0]
 aobserver=browser.new_page();bobserver=browser.new_page();bmessages=[]
 for observer in (aobserver,bobserver):observer.on('pageerror',lambda error:errors.append(str(error)))
 bobserver.on('websocket',lambda ws:ws.on('framereceived',lambda payload:bmessages.append(json.loads(payload))))
 def login(observer,name,pw):
  observer.goto(base);observer.get_by_label('用户名',exact=True).fill(name)
  observer.get_by_label('密码',exact=True).fill(pw);observer.get_by_role('button',name='登录',exact=True).click()
 try:
  login(aobserver,'tester',password);aobserver.get_by_role('heading',name='Package e2e-v1',exact=True).wait_for()
  aobserver.get_by_label('查看运行',exact=True).select_option('demo-run')
  aobserver.get_by_text('99.50',exact=False).wait_for()
  login(bobserver,'bviewer','B-viewer-fixture-123!');bobserver.get_by_role('heading',name='Package b-v1',exact=True).wait_for()
  bobserver.get_by_label('查看运行',exact=True).select_option('demo-run')
  bobserver.get_by_text('99.50',exact=False).wait_for()
  expect(bobserver.get_by_label('Runtime 目标',exact=True)).to_have_value('line-b')
  assert bobserver.get_by_label('Runtime 目标',exact=True).locator('option').count()==1
  assert bobserver.request.get(base+'/api/panels?runtimeId=configured').status==403
  assert bobserver.request.get(base+'/api/panels?runtimeId=line-b&projectId=demo-project').json()[0]['id']==bid
  aobserver.get_by_role('img',name='Package asset',exact=True).wait_for()
  old_asset=aobserver.get_by_role('img',name='Package asset',exact=True)
  aobserver.wait_for_function('(image)=>image.complete && image.naturalWidth>0',arg=old_asset.element_handle())
  assert versions[0] in old_asset.get_attribute('src')
  page.get_by_label('Runtime 目标',exact=True).select_option('configured')
  page.get_by_role('heading',name='Package e2e-v1',exact=True).wait_for()
  for pid,title in [(versions[1],'Package e2e-v2'),(versions[0],'Package e2e-v1')]:
   before=sum(message.get('type')=='result' for message in bmessages)
   activate(pid,title)
   # A's original view remains pinned until its user explicitly refreshes.
   aobserver.get_by_text('页面包已有更新，请点击刷新页面包',exact=True).wait_for()
   expect(aobserver.get_by_role('heading',name='Package e2e-v1',exact=True)).to_be_visible()
   assert versions[0] in old_asset.get_attribute('src')
   assert aobserver.request.get(base+'/api/assets/'+versions[0]+'/pinned').ok
   # B continues receiving its own snapshots and never receives A's update.
   deadline=time.monotonic()+10
   while sum(message.get('type')=='result' for message in bmessages)<before+2 and time.monotonic()<deadline:
    bobserver.wait_for_timeout(50)
   assert sum(message.get('type')=='result' for message in bmessages)>=before+2
   assert not any(message.get('type')=='package_updated' for message in bmessages)
   expect(bobserver.get_by_role('heading',name='Package b-v1',exact=True)).to_be_visible()
   assert bobserver.request.get(base+'/api/panels?runtimeId=line-b&projectId=demo-project').json()[0]['id']==bid
   assert bobserver.request.get(base+'/api/assets/'+bid+'/pinned').ok
   active=page.request.get(base+'/api/packages?runtimeId=line-b&projectId=demo-project').json()
   assert [item['packageId'] for item in active if item['active']]==[bid]
  bobserver.get_by_role('button',name='刷新页面包和状态',exact=True).click()
  bobserver.get_by_role('heading',name='Package b-v1',exact=True).wait_for()
  aobserver.screenshot(path=str(ROOT/'test-tmp'/'target-a-pinned.png'),full_page=True)
  bobserver.screenshot(path=str(ROOT/'test-tmp'/'target-b-isolated.png'),full_page=True)
 finally:
  aobserver.close();bobserver.close()

def main():
 with tempfile.TemporaryDirectory(dir=ROOT/'test-tmp') as d:
  data=Path(d);auth=AuthStore(data/'web.sqlite');password='Temporary-e2e-fixture!';auth.create_user('tester',password,'admin')
  sock=socket.socket();sock.bind(('127.0.0.1',0));port=sock.getsockname()[1];sock.close()
  app=create_app(Config(data,mode='mock',port=port));config=uvicorn.Config(app,host='127.0.0.1',port=port,log_level='warning');server=uvicorn.Server(config)
  thread=threading.Thread(target=server.run,daemon=True);thread.start()
  for _ in range(100):
   if server.started:break
   time.sleep(.05)
  if not server.started:raise RuntimeError('Backend failed to start')
  try:
   with sync_playwright() as p:
    browser=p.chromium.launch(**({'executable_path':os.environ['CHROMIUM_PATH']} if os.environ.get('CHROMIUM_PATH') else {}),headless=True,args=['--no-sandbox','--disable-dev-shm-usage']);page=browser.new_page(viewport={'width':1280,'height':900});errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
    try:
     page.goto(f'http://127.0.0.1:{port}');page.get_by_label('用户名',exact=True).fill('tester');page.get_by_label('密码',exact=True).fill(password);page.get_by_role('button',name='登录',exact=True).click()
     page.get_by_text('99.50',exact=False).wait_for();page.get_by_role('button',name='冻结查看',exact=True).click();page.get_by_text('已冻结查看，后台仍在运行').wait_for();page.get_by_role('button',name='恢复实时',exact=True).click()
     page.get_by_role('button',name='启动工作流',exact=True).click();page.get_by_text('操作已受理：',exact=False).wait_for()
     page.get_by_text('账户与 Runtime 管理',exact=True).click();page.get_by_label('新用户名',exact=True).fill('readonly');page.get_by_label('初始密码',exact=True).fill('Readonly-fixture-123!');page.get_by_role('button',name='创建用户',exact=True).click();page.get_by_role('cell',name='readonly',exact=True).wait_for()
     history=page.locator('section.panel-card').filter(has=page.get_by_label('history-table 筛选', exact=True))
     history.get_by_text('共 30 条',exact=False).wait_for();assert history.locator('tbody tr').count()==10
     page.get_by_role('button',name='编辑 / 授权',exact=False).first.click()
     with page.expect_response(lambda response:'/api/admin/runtimes/configured/grants' in response.url and response.request.method=='PUT') as grant_response:
      page.get_by_label('readonly 查看 configured',exact=True).check()
     assert grant_response.value.ok
     assert any(g=={'username':'readonly','view':True,'control':False} for g in page.request.get(f'http://127.0.0.1:{port}/api/admin/runtimes/configured/grants').json())
     page.get_by_role('button',name='新建目标',exact=True).click();page.get_by_label('Runtime ID',exact=True).fill('second');page.get_by_label('Runtime 别名',exact=True).fill('Second mock');page.get_by_role('button',name='新增连接',exact=True).click();page.get_by_role('button',name='编辑 / 授权 Second mock',exact=True).wait_for()
     page.get_by_label('Runtime 别名',exact=True).fill('Second edited');page.get_by_role('button',name='保存连接',exact=True).click();page.get_by_role('button',name='编辑 / 授权 Second edited',exact=True).wait_for()
     with page.expect_response(lambda response:'/api/admin/runtimes/second/grants' in response.url and response.request.method=='PUT') as second_grant:
      page.get_by_label('readonly 查看 second',exact=True).check()
     assert second_grant.value.ok
     page.get_by_label('删除时撤销目标授权',exact=True).check();page.once('dialog',lambda dialog:dialog.accept());page.get_by_role('row').filter(has=page.get_by_role('button',name='编辑 / 授权 Second edited',exact=True)).get_by_role('button',name='删除目标',exact=True).click();page.get_by_role('button',name='编辑 / 授权 Second edited',exact=True).wait_for(state='detached')
     page.get_by_label('查看运行',exact=True).select_option('');history.get_by_text('共 30 条',exact=False).wait_for()
     page.get_by_role('button',name='启动工作流',exact=True).click();page.get_by_text('操作已受理：',exact=False).wait_for()
     page.get_by_label('查看运行',exact=True).select_option('demo-run');page.get_by_text('99.50',exact=False).wait_for()
     page.get_by_label('history-table 筛选', exact=True).fill('NG');history.get_by_text('共 7 条',exact=False).wait_for();assert history.locator('tbody tr').count()==7
     history.locator('tbody tr').first.click();history.get_by_role('img',name='选中历史记录',exact=True).wait_for()
     page.wait_for_function('(image)=>image.complete && image.naturalWidth>0',arg=history.get_by_role('img',name='选中历史记录',exact=True).element_handle())
     page.get_by_label('停止控制目标',exact=True).select_option('demo-run');page.once('dialog',lambda dialog:dialog.accept())
     with page.expect_response(lambda response:response.url.endswith('/api/runs/demo-run/stop') and response.request.method=='POST') as stopped:
      page.get_by_role('button',name='停止选中运行',exact=True).click()
     assert stopped.value.ok
     page.get_by_role('button',name='启动工作流',exact=True).click()
     # Complete real upload -> activate -> second-client notification -> switch -> rollback.
     observer=browser.new_page();notices=[]
     observer.on('websocket',lambda ws:ws.on('framereceived',lambda payload:notices.append(json.loads(payload))))
     observer.goto(f'http://127.0.0.1:{port}');observer.get_by_label('用户名',exact=True).fill('readonly');observer.get_by_label('密码',exact=True).fill('Readonly-fixture-123!');observer.get_by_role('button',name='登录',exact=True).click();observer.get_by_text('99.50',exact=False).wait_for()
     page.get_by_text('页面包管理',exact=True).click()
     versions=[];previous_title='Demo production'
     for revision in ['e2e-v1','e2e-v2']:
      package_path=make_package(data,revision)
      with page.expect_response(lambda response:response.url.endswith('/api/packages') and response.request.method=='POST') as uploaded:
       page.get_by_label('导入页面包',exact=True).set_input_files(str(package_path))
      assert uploaded.value.ok
      installed=page.request.get(f'http://127.0.0.1:{port}/api/packages?runtimeId=configured&projectId=demo-project');assert installed.ok
      matching=[item for item in installed.json() if item['manifest']['packageRevision']==revision]
      assert len(matching)==1;pid=matching[0]['packageId'];assert pid==hashlib.sha256(package_path.read_bytes()).hexdigest();versions.append(pid)
      row=page.locator(f'[data-package-id="{pid}"]');row.wait_for();page.once('dialog',lambda dialog:dialog.accept())
      with page.expect_response(lambda response:response.url.endswith(f'/api/packages/{pid}/activate')) as activated:
       row.get_by_role('button',name='对当前目标启用 / 切回此版本',exact=True).click()
      assert activated.value.ok;page.get_by_role('heading',name=f'Package {revision}',exact=True).wait_for()
      deadline=time.monotonic()+10
      while not any(item.get('type')=='package_updated' and item.get('packageId')==pid for item in notices) and time.monotonic()<deadline:observer.wait_for_timeout(50)
      assert any(item.get('type')=='package_updated' and item.get('packageId')==pid for item in notices)
      observer.get_by_text('页面包已有更新，请点击刷新页面包',exact=True).wait_for();assert observer.get_by_role('heading',name=previous_title,exact=True).count()==1
      observer.get_by_role('button',name='刷新页面包和状态',exact=True).click();observer.get_by_role('heading',name=f'Package {revision}',exact=True).wait_for()
      page.get_by_text('99.50',exact=False).wait_for();previous_title=f'Package {revision}'
     page.once('dialog',lambda dialog:dialog.accept());page.locator(f'[data-package-id="{versions[0]}"]').get_by_role('button',name='对当前目标启用 / 切回此版本',exact=True).click();page.get_by_role('heading',name='Package e2e-v1',exact=True).wait_for()
     assert page.request.get(f'http://127.0.0.1:{port}/api/panels?runtimeId=configured&projectId=demo-project').json()[0]['packageRevision']=='e2e-v1'
     observer.close()
     target_isolation_check(page,browser,f'http://127.0.0.1:{port}',data,versions,password,errors)
     page.set_viewport_size({'width':390,'height':844});assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth'),page.evaluate("Array.from(document.querySelectorAll('body *')).filter(e=>e.getBoundingClientRect().right>innerWidth+1).map(e=>({tag:e.tagName,class:e.className,width:e.getBoundingClientRect().width})).slice(0,20)")
     page.get_by_text('99.50',exact=False).wait_for()
     page.get_by_role('img',name='检测结果',exact=True).wait_for()
     page.wait_for_function('(image)=>image.complete && image.naturalWidth>0',arg=page.get_by_role('img',name='检测结果',exact=True).element_handle())
     page.screenshot(path=str(ROOT/'backend-mobile.png'),full_page=True)
     page.get_by_role('button',name='退出',exact=True).click();page.get_by_label('用户名',exact=True).fill('readonly');page.get_by_label('密码',exact=True).fill('Readonly-fixture-123!');page.get_by_role('button',name='登录',exact=True).click();page.get_by_text('99.50',exact=False).wait_for();assert page.get_by_role('button',name='启动工作流',exact=True).count()==0
     assert not errors,errors
    except Exception:
     try:
      page.screenshot(path=str(ROOT/'test-tmp'/'failure.png'),full_page=True)
      (ROOT/'test-tmp'/'failure.html').write_text(page.content(),encoding='utf-8')
     except Exception:pass
     raise
    browser.close()
  finally:server.should_exit=True;thread.join(timeout=5)
 print('PASS: real FastAPI HTTP/WS explicit MOCK Runtime browser integration, login, real-time result, freeze/resume, control, admin create, viewer restriction, mobile, A/B contextual activation and rollback, isolated B viewer and pinned A assets; no real Runtime or hardware')
if __name__=='__main__':main()
