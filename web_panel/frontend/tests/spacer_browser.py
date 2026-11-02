"""Chromium geometry acceptance for transparent/flexible page spacers.

Uses HTTP fixtures and the production frontend build; no live Runtime/device.
"""
import json
import os
from pathlib import Path
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / 'test-tmp' / 'spacer-browser'
ARTIFACTS.mkdir(parents=True, exist_ok=True)


def spacer(cid, *, flex=False, weight=1):
    return {'id':cid, 'type':'spacer', 'label':'HIDDEN SPACER TITLE',
            'props':{'spacerMode':'flex' if flex else 'fixed', 'cardStyle':'soft'},
            'responsive':{'width':'content','minWidth':24,'preferredWidth':24,
                          'minHeight':24,'preferredHeight':24,'weight':weight}}


def text(cid, width=100, height=64):
    return {'id':cid,'type':'text','props':{'text':cid},
            'responsive':{'width':'content','minWidth':width,'preferredWidth':width,
                          'minHeight':height,'preferredHeight':height}}


PANEL = {'id':'spacers','name':'Spacers','packageRevision':'spacer-browser',
         'defaultPageId':'fixed','pages':[
    {'id':'fixed','title':'Fixed','layout':{'type':'responsive','direction':'horizontal',
        'equalWidth':True,'alignment':'stretch','spacing':12},
     'components':[spacer('fixed-gap'),text('fixed-neighbor')]},
    {'id':'horizontal','title':'Horizontal','layout':{'type':'responsive','direction':'horizontal','spacing':12},
     'components':[text('horizontal-content'),spacer('horizontal-one',flex=True),
                   spacer('horizontal-two',flex=True,weight=2)]},
    {'id':'vertical','title':'Vertical','layout':{'type':'responsive','direction':'vertical','spacing':12},
     'components':[text('vertical-content'),spacer('vertical-one',flex=True),
                   spacer('vertical-two',flex=True,weight=2)]},
    {'id':'wrap','title':'Wrap','layout':{'type':'responsive','direction':'wrap','spacing':12},
     'components':[text('wrap-first',260),spacer('wrap-gap',flex=True),text('wrap-next',260)]},
    {'id':'nested','title':'Nested','layout':{'type':'responsive','direction':'vertical','spacing':12},
     'components':[{'id':'nested-container','type':'container','props':{'title':'Group'},
          'responsive':{'minHeight':360,'preferredHeight':360},
          'grid':{'type':'responsive','direction':'vertical','spacing':12},
          'children':[text('nested-content'),spacer('nested-gap',flex=True)]}]},
    {'id':'weighted-fill','title':'Weighted fill','layout':{'type':'responsive','direction':'horizontal','spacing':0},
     'components':[{'id':'weighted-content','type':'text','props':{'text':'Content'},
          'responsive':{'width':'fill','minWidth':80,'preferredWidth':240,'minHeight':64,'weight':1}},
          spacer('weighted-gap',flex=True)]},
    {'id':'equal-fill','title':'Equal fill','layout':{'type':'responsive','direction':'horizontal','spacing':0,'equalWidth':True},
     'components':[{'id':'equal-first','type':'text','props':{'text':'First'},
          'responsive':{'width':'fill','minWidth':80,'preferredWidth':240,'minHeight':64,'weight':1}},
          {'id':'equal-second','type':'text','props':{'text':'Second'},
          'responsive':{'width':'fill','minWidth':160,'preferredWidth':320,'minHeight':64,'weight':1}},
          spacer('equal-gap',flex=True)]},
    {'id':'zero-height','title':'Zero height','layout':{'type':'responsive','direction':'vertical','spacing':0},
     'components':[{'id':'zero-gap','type':'spacer','responsive':{'width':'content','minWidth':24,'preferredWidth':24,'minHeight':0,'preferredHeight':0}},text('zero-neighbor')]},
    {'id':'grid','title':'Grid','layout':{'type':'grid','columns':4,'spacing':12},
     'components':[{'id':'grid-gap','type':'spacer','layout':{'row':0,'column':1,'rowSpan':2,'columnSpan':2}}]},
]}


class Handler(SimpleHTTPRequestHandler):
    def __init__(self,*args,**kwargs):
        super().__init__(*args,directory=str(ROOT/'dist'),**kwargs)
    def log_message(self,*args):
        pass


server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
threading.Thread(target=server.serve_forever,daemon=True).start()
origin=f'http://127.0.0.1:{server.server_port}'


def mock(route):
    path=route.request.url.replace(origin,'').split('?')[0]
    if path=='/api/me':
        data={'username':'spacer-viewer','role':'viewer','csrf':'fixture','permissions':['view']}
    elif path=='/api/runtimes':
        data=[{'id':'configured','alias':'Fixture','mode':'mock','permissions':['view']}]
    elif path=='/api/runtimes/configured/info':
        data={'runtimeInstanceId':'fixture-instance','projectId':'fixture-project'}
    elif path.endswith('/runs') or path.endswith('/workflows'):
        data=[]
    elif path=='/api/panels':
        data=[PANEL]
    elif path=='/api/panels/spacers':
        data=PANEL
    elif path=='/api/views':
        data={'id':'spacer-view'}
    elif path.endswith('/status'):
        data={}
    elif path=='/api/views/spacer-view':
        data={}
    else:
        route.fulfill(status=404,json={'detail':path})
        return
    route.fulfill(json=data)


def rect(page,cid):
    return page.locator(f'[data-component-id="{cid}"]').bounding_box()


report=[]
with sync_playwright() as runtime:
    browser=runtime.chromium.launch(headless=True,**(
        {'executable_path':os.environ['CHROMIUM_PATH']} if os.environ.get('CHROMIUM_PATH') else {}))
    page=browser.new_page(viewport={'width':1280,'height':1200})
    errors=[]
    page.on('pageerror',lambda error:errors.append(str(error)))
    page.route('**/api/**',mock)
    try:
        page.goto(origin)
        page.locator('[data-component-id="fixed-gap"]').wait_for()
        box=rect(page,'fixed-gap')
        assert abs(box['width']-24)<1 and abs(box['height']-24)<1,box
        assert page.get_by_text('HIDDEN SPACER TITLE',exact=True).count()==0
        appearance=page.locator('[data-component-id="fixed-gap"]').evaluate('(e)=>({background:getComputedStyle(e).backgroundColor,border:getComputedStyle(e).borderWidth,padding:getComputedStyle(e).padding})')
        assert appearance=={'background':'rgba(0, 0, 0, 0)','border':'0px','padding':'0px'},appearance
        report.append({'case':'fixed-transparent-equalwidth','box':box,'appearance':appearance})

        page.get_by_role('button',name='Horizontal',exact=True).click()
        a,b=rect(page,'horizontal-one'),rect(page,'horizontal-two')
        assert abs((b['width']-24)-2*(a['width']-24))<2,(a,b)
        report.append({'case':'horizontal-weighted','one':a,'two':b})

        page.get_by_role('button',name='Vertical',exact=True).click()
        page.wait_for_function('document.querySelector("[data-component-id=vertical-two]")?.getBoundingClientRect().height>24')
        a,b=rect(page,'vertical-one'),rect(page,'vertical-two')
        assert abs((b['height']-24)-2*(a['height']-24))<2,(a,b)
        assert rect(page,'vertical-content')['height']>=64
        report.append({'case':'vertical-weighted','one':a,'two':b})
        page.set_viewport_size({'width':1280,'height':400})
        page.wait_for_timeout(60)
        assert abs(rect(page,'vertical-one')['height']-24)<1 and abs(rect(page,'vertical-two')['height']-24)<1
        assert rect(page,'vertical-content')['height']>=64
        report.append({'case':'vertical-insufficient-fallback','one':rect(page,'vertical-one'),'two':rect(page,'vertical-two')})

        page.set_viewport_size({'width':500,'height':1000})
        page.get_by_role('button',name='Wrap',exact=True).click()
        a,b,c=rect(page,'wrap-first'),rect(page,'wrap-gap'),rect(page,'wrap-next')
        assert abs(a['y']-b['y'])<1 and c['y']>=a['y']+a['height'],(a,b,c)
        assert b['width']>24,(a,b,c)
        report.append({'case':'wrap-row-only','first':a,'gap':b,'next':c})

        page.set_viewport_size({'width':1280,'height':1200})
        page.get_by_role('button',name='Nested',exact=True).click()
        container,content,gap=rect(page,'nested-container'),rect(page,'nested-content'),rect(page,'nested-gap')
        assert gap['height']>24 and gap['y']+gap['height']<=container['y']+container['height'],(container,content,gap)
        report.append({'case':'nested-finite-height','container':container,'gap':gap})

        page.get_by_role('button',name='Weighted fill',exact=True).click()
        page.locator('[data-component-id="weighted-content"]').evaluate('(e)=>e.parentElement.style.width="400px"')
        content,gap=rect(page,'weighted-content'),rect(page,'weighted-gap')
        assert abs(content['width']-228)<1 and abs(gap['width']-172)<1,(content,gap)
        report.append({'case':'horizontal-fill-minimum-basis','content':content,'gap':gap})

        page.get_by_role('button',name='Equal fill',exact=True).click()
        page.locator('[data-component-id="equal-first"]').evaluate('(e)=>e.parentElement.style.width="600px"')
        first,second,gap=rect(page,'equal-first'),rect(page,'equal-second'),rect(page,'equal-gap')
        # Qt: two common 160px bases + 24px spacer, then 256px remainder / 3.
        assert abs(first['width']-(160+256/3))<1 and abs(second['width']-first['width'])<1,(first,second,gap)
        assert abs(gap['width']-(24+256/3))<1,(first,second,gap)
        report.append({'case':'horizontal-equal-width-spacer-basis','first':first,'second':second,'gap':gap})

        page.get_by_role('button',name='Zero height',exact=True).click()
        zero=page.locator('[data-component-id="zero-gap"]').evaluate('(e)=>({width:e.getBoundingClientRect().width,height:e.getBoundingClientRect().height})')
        assert zero=={'width':24,'height':0},zero
        report.append({'case':'explicit-zero-height','box':zero})
        page.locator('[data-component-id="zero-gap"]').evaluate('(e)=>e.parentElement.style.width=""')

        page.get_by_role('button',name='Grid',exact=True).click()
        box=rect(page,'grid-gap')
        assert box['height']>=24 and box['width']>24,box
        report.append({'case':'grid-span','box':box})
        assert not errors,errors
        page.screenshot(path=str(ARTIFACTS/'grid.png'),full_page=True)
        (ARTIFACTS/'result.json').write_text(json.dumps({'cases':report,'pageErrors':errors},ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({'passed':len(report),'evidence':str(ARTIFACTS)},ensure_ascii=False))
    finally:
        browser.close()
        server.shutdown()
