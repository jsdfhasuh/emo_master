"""Read-only full-plan UI probes; mocked API, real current React/Edge; no product/test edits."""
import copy,functools,json,sys,threading,traceback
from pathlib import Path
from http.server import ThreadingHTTPServer
from urllib.parse import urlsplit
from playwright.sync_api import sync_playwright
repo=Path(r'D:\jsdfhasuh\documents\my_project\emo_master');evidence=Path(__file__).resolve().parent/'before-user-probe';evidence.mkdir(exist_ok=True)
built=repo/'web_panel/frontend/dist'
sys.path.insert(0,str(repo/'web_panel/frontend/tests'));sys.path.insert(0,str(repo/'web_panel/backend'))
import editor_review_browser as f
from emo_web_panel.editor import validate_source
scope={'entryWorkflowId':'wf','scopeWorkflowId':'wf','callPath':[],'commitPolicy':'workflow_terminal'}
choice={'label':'入口 wf / 检测节点 node / count (integer) · 入口','sourceId':'source-count','scopeId':'scope-wf','scope':scope,'source':{'kind':'node_output','resultScopeId':'scope-wf','workflowId':'wf','nodeId':'node','port':'count','callPath':[],'fieldPath':[],'expectedType':'integer','ruleVersion':'1.0'},'enabled':True}
class Fixture(f.Fixture):
 def route(self,r):
  if urlsplit(r.request.url).path.endswith('/context'):
   body={'runtime':{'runtimeInstanceId':self.instance,'projectId':self.project,'projectName':'Review Project','documentRevision':'999'},'catalog':{'choices':[choice],'capabilities':{'maxSourceIds':16,'maxScopes':16,'maxImageSources':2}},'requests':{'diagnostics':[]},'versions':[],'draft':{'presentation':self.documents[self.project],'draftRevision':str(self.revision)}}
   r.fulfill(status=200,content_type='application/json',body=json.dumps(body))
  else:super().route(r)
server=ThreadingHTTPServer(('127.0.0.1',0),functools.partial(f.Handler,directory=str(built)));threading.Thread(target=server.serve_forever,daemon=True).start()
origin=f'http://127.0.0.1:{server.server_port}'
report={'evidenceLevel':'Real headless Edge + independently built unchanged React + mocked API; actual saved source checked','cases':{}}
try:
 with sync_playwright() as pw:
  b=pw.chromium.launch(executable_path=f.EDGE,headless=True)
  for name in ('drag-bind-copy-delete-paste','preview-page-selection','grid-resize','nested-grid-properties'):
   case=report['cases'][name]={};doc=f.presentation(two=name=='preview-page-selection')
   if name=='grid-resize':doc['pages']['page-A']['layout']={'type':'grid','columns':2,'spacing':12}
   if name=='nested-grid-properties':
    container=f.component('container');container['type']='container';container['props']={'title':'Container'};container['grid']={'type':'grid','columns':2,'spacing':12};container['children']=[f.component('nested-child')];doc['pages']['page-A']['components']=[container]
   fixture=Fixture(doc);context,page=f.boot(b,origin,fixture)
   try:
    if name=='drag-bind-copy-delete-paste':
     card=page.locator('[data-editor-component="component-A"]');card.click();page.get_by_role('button',name='流程结果',exact=True).click()
     page.locator('.editor-source-list > button').first.drag_to(card)
     saved=f.save(page,fixture)['presentation'];case['directDragBound']=saved['pages']['page-A']['components'][0]['bindings'];case['sourcesBeforeCopy']=list(saved['dataSources'])
     card.click();page.keyboard.press('Control+c');page.keyboard.press('Delete');page.locator('.editor-canvas').click(position={'x':30,'y':30});page.keyboard.press('Control+v')
     pasted=f.save(page,fixture)['presentation'];case['pastedSource']=pasted
     try:validate_source(pasted,{});case['serverSourceValidation']='passed'
     except Exception as err:case['serverSourceValidation']=str(err)
    elif name=='preview-page-selection':
     page.locator('[data-editor-component="component-A"]').click();page.get_by_role('button',name='示例预览',exact=True).click()
     page.get_by_role('dialog').get_by_role('button',name='详情页',exact=True).click();page.get_by_role('button',name='关闭预览',exact=True).click()
     case['currentPage']=page.get_by_label('页面名称',exact=True).input_value();case['propertyTitle']=page.get_by_label('组件标题',exact=True).input_value();case['selectedCanvasCards']=page.locator('.editor-component.selected').count()
     page.get_by_label('组件标题',exact=True).fill('Changed after preview switch');saved=f.save(page,fixture)['presentation']
     case['savedTitles']={pid:[c['props'].get('title') for c in p['components']] for pid,p in saved['pages'].items()}
    elif name=='grid-resize':
     card=page.locator('[data-editor-component="component-A"]');card.click();handle=page.get_by_role('button',name='拖动调整组件尺寸',exact=True);handle.scroll_into_view_if_needed()
     before=card.bounding_box();hb=handle.bounding_box();x=hb['x']+hb['width']/2;y=hb['y']+hb['height']/2
     page.mouse.move(x,y);page.mouse.down();page.mouse.move(x+50,y+50,steps=4);page.mouse.up();page.wait_for_timeout(80)
     after=card.bounding_box();saved=f.save(page,fixture)['presentation'];item=saved['pages']['page-A']['components'][0]
     case.update(before=before,after=after,savedResponsive=item['responsive'],savedGridLayout=item['layout'])
    else:
     page.locator('[data-editor-component="nested-child"]').click();case['rowInputs']=page.get_by_label('row',exact=True).count();case['columnInputs']=page.get_by_label('column',exact=True).count();case['preferredWidthInputs']=page.get_by_label('首选宽度',exact=True).count()
    page.screenshot(path=str(evidence/f'{name}.png'),full_page=True);case['probe']='completed'
   except Exception:case['probe']='failed';case['error']=traceback.format_exc()
   finally:context.close()
   print(name,json.dumps(case,ensure_ascii=False),flush=True)
  b.close()
finally:
 server.shutdown();(evidence/'ui-plan-probes.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
