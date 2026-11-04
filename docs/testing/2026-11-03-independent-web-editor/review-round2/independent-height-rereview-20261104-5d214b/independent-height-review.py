"""Independent rerun of current height regressions plus same-session mode/host transitions."""
import functools,importlib.util,json,sys,threading,traceback
from http.server import ThreadingHTTPServer
from pathlib import Path
from playwright.sync_api import sync_playwright
repo=Path(r'D:\jsdfhasuh\documents\my_project\emo_master');evidence=Path(__file__).resolve().parent
sys.path.insert(0,str(repo/'web_panel/frontend/tests'))
import editor_preview_height_browser as m
m.EVIDENCE=evidence;m.fixtures.ROOT=evidence
sys.argv=[str(Path(m.__file__)),'--label','independent-current']
exitcode=m.main()
server=ThreadingHTTPServer(('127.0.0.1',0),functools.partial(m.fixtures.Handler,directory=str(evidence/'dist')))
threading.Thread(target=server.serve_forever,daemon=True).start()
report={'evidenceLevel':'Independently built current React + real headless Edge + mocked HTTP/WS','cases':{}}
try:
 with sync_playwright() as pw:
  browser=pw.chromium.launch(executable_path=m.fixtures.EDGE,headless=True)
  for mode in ('fixtures','runtime'):
   case=report['cases'][mode]={'measurements':{}}
   fixture=m.HeightFixture(m.source());context=browser.new_context(viewport={'width':1500,'height':900})
   context.route('**/api/**',fixture.route)
   context.route_web_socket('**/ws/views/height-review-view',lambda ws:ws.on_message(lambda message:None))
   page=context.new_page()
   try:
    page.goto(f'http://127.0.0.1:{server.server_port}/editor');page.get_by_label('页面名称',exact=True).wait_for()
    if mode=='runtime':page.get_by_label('实时预览任务').select_option('run-A')
    def open_preview(viewport):
     page.get_by_label('终端尺寸').select_option(viewport)
     page.get_by_role('button',name='示例预览' if mode=='fixtures' else '实时预览',exact=True).click()
     page.get_by_role('dialog').wait_for()
     page.wait_for_function("() => document.querySelector('.editor-preview-terminal > .panel-responsive')?.style.height")
    def measure(name):
     value=page.evaluate(m.GEOMETRY);case['measurements'][name]=value;return value
    def fixed_assert(value,height):
     assert abs(value['terminal']['height']-height)<=1
     assert abs(value['content']['height']-(height-16))<=1
     assert abs(value['footerBottomInTerminal']-height)<=1
    open_preview('1024x768');a=measure('fixed900');fixed_assert(a,768)
    page.set_viewport_size({'width':1500,'height':1200});page.wait_for_timeout(100)
    b=measure('fixedResize1200');fixed_assert(b,768)
    assert abs(a['spacer']['height']-b['spacer']['height'])<=1
    page.get_by_role('button',name='关闭预览',exact=True).click()
    open_preview('follow');c=measure('follow1200')
    assert abs(c['content']['height']-(1200-c['content']['top']-16))<=1
    page.set_viewport_size({'width':1500,'height':900})
    page.wait_for_function("() => {const c=document.querySelector('.editor-preview-terminal > .panel-responsive');return Math.abs(c.getBoundingClientRect().height-(innerHeight-c.getBoundingClientRect().top-16))<=1}")
    d=measure('followResize900');assert c['content']['height']-d['content']['height']>200
    page.get_by_role('button',name='关闭预览',exact=True).click()
    open_preview('390x844');e=measure('fixedAgain390');fixed_assert(e,844)
    assert abs(e['terminal']['width']-390)<=1
    page.screenshot(path=str(evidence/f'mode-transition-{mode}.png'),full_page=True)
    case['status']='passed'
   except Exception:
    case['status']='failed';case['failure']=traceback.format_exc();exitcode=1
   finally:context.close()
   print('mode-transition-'+mode,case['status'],flush=True)
  browser.close()
finally:
 server.shutdown();(evidence/'mode-transition-browser.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
raise SystemExit(exitcode)
