"""Independent adjacent probe: fixed terminal content geometry with a supported flex spacer."""
import copy, functools, importlib.util, json, threading
from pathlib import Path
from http.server import ThreadingHTTPServer
from playwright.sync_api import sync_playwright
repo=Path(r'D:\jsdfhasuh\documents\my_project\emo_master')
evidence=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('fixture',repo/'web_panel/frontend/tests/editor_review_browser.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
doc=m.presentation()
first=m.component('top');last=m.component('bottom');spacer=m.component('flex-space')
spacer.update(type='spacer',props={'spacerMode':'flex'})
spacer['responsive'].update(width='content',minWidth=24,preferredWidth=24,minHeight=24,preferredHeight=24)
doc['pages']['page-A']['components']=[first,spacer,last]
server=ThreadingHTTPServer(('127.0.0.1',0),functools.partial(m.Handler,directory=str(evidence/'dist')))
threading.Thread(target=server.serve_forever,daemon=True).start()
report={'evidenceLevel':'Current independently-built React + real headless Edge + mocked HTTP; geometry of fixed preview only','cases':[]}
try:
 with sync_playwright() as pw:
  browser=pw.chromium.launch(executable_path=m.EDGE,headless=True)
  for hostheight in (900,1200):
   fixture=m.Fixture(copy.deepcopy(doc))
   context,page=m.boot(browser,f'http://127.0.0.1:{server.server_port}',fixture)
   page.set_viewport_size({'width':1500,'height':hostheight})
   page.get_by_label('终端尺寸').select_option('1024x768')
   page.get_by_role('button',name='示例预览',exact=True).click()
   terminal=page.locator('.editor-preview-terminal');terminal.wait_for()
   page.wait_for_function("() => document.querySelector('.editor-preview-terminal > .panel-responsive')?.style.height")
   value=page.evaluate("""() => {const t=document.querySelector('.editor-preview-terminal');const c=t.querySelector('.panel-responsive');const b=t.querySelector('[data-component-id=bottom]'); const geom=e=>{const r=e.getBoundingClientRect();return {top:r.top,bottom:r.bottom,width:r.width,height:r.height}};return {windowHeight:innerHeight,terminal:geom(t),content:geom(c),bottomWidget:geom(b)}}""")
   report['cases'].append(value)
   page.screenshot(path=str(evidence/f'fixed-height-host-{hostheight}.png'),full_page=True)
   context.close()
  browser.close()
finally:
 server.shutdown()
 (evidence/'fixed-height-adjacent-probe.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
 print(json.dumps(report,ensure_ascii=False,indent=2))
