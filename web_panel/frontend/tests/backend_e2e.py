"""Real FastAPI HTTP/WS + explicit mock Runtime + real Chromium. No hardware."""
import json,os,socket,sys,tempfile,threading,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
(ROOT/'test-tmp').mkdir(exist_ok=True)
sys.path.insert(0,str(ROOT.parent/'backend'))
import uvicorn
from emo_web_panel.app import create_app
from emo_web_panel.auth import AuthStore
from emo_web_panel.config import Config
from playwright.sync_api import sync_playwright

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
     history=page.locator('section.panel-card').filter(has=page.get_by_label('history-table 筛选'))
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
     page.get_by_label('history-table 筛选').fill('NG');history.get_by_text('共 7 条',exact=False).wait_for();assert history.locator('tbody tr').count()==7
     page.set_viewport_size({'width':390,'height':844});assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth'),page.evaluate("Array.from(document.querySelectorAll('body *')).filter(e=>e.getBoundingClientRect().right>innerWidth+1).map(e=>({tag:e.tagName,class:e.className,width:e.getBoundingClientRect().width})).slice(0,20)")
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
 print('PASS: real FastAPI HTTP/WS explicit MOCK Runtime browser integration, login, real-time result, freeze/resume, control, admin create, viewer restriction, mobile; no real Runtime or hardware')
if __name__=='__main__':main()
