"""Real Edge + built React geometry; mocked HTTP/WS, no Runtime/device acceptance."""
import argparse
import functools
import json
import sys
import threading
import traceback
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright
import editor_review_browser as fixtures

EVIDENCE = fixtures.REPO / 'docs/testing/2026-11-03-independent-web-editor/review-round2'


class HeightFixture(fixtures.Fixture):
    def route(self, route):
        if urlsplit(route.request.url).path.endswith('/preview') and route.request.post_data_json.get('mode') == 'runtime':
            route.fulfill(status=200, content_type='application/json', body=json.dumps({'viewId': 'height-review-view', 'runIds': ['run-A']}))
        else:
            super().route(route)


def source(overflow=False):
    doc = fixtures.presentation()
    top, bottom, spacer = [fixtures.component(cid) for cid in ('top', 'bottom', 'flex-space')]
    spacer.update(type='spacer', props={'spacerMode': 'flex'})
    spacer['responsive'].update(width='content', minWidth=24, preferredWidth=24, minHeight=24, preferredHeight=24)
    if overflow:
        top['responsive'].update(minHeight=900, preferredHeight=900)
        bottom['responsive'].update(minHeight=400, preferredHeight=400)
    doc['pages']['page-A']['components'] = [top, spacer, bottom]
    return doc


GEOMETRY = """() => {
 const t=document.querySelector('.editor-preview-terminal'),c=t.querySelector('.panel-responsive');
 const geom=e=>{const r=e.getBoundingClientRect();return {top:r.top,bottom:r.bottom,width:r.width,height:r.height}};
 return {windowHeight:innerHeight,terminal:geom(t),content:geom(c),topWidget:geom(t.querySelector('[data-component-id=top]')),
   bottomWidget:geom(t.querySelector('[data-component-id=bottom]')),spacer:geom(t.querySelector('[data-component-id=flex-space]')),
   contentStyleHeight:c.style.height,terminalScrollHeight:t.scrollHeight,terminalClientHeight:t.clientHeight,
   terminalOverflowY:getComputedStyle(t).overflowY,terminalScrollTop:t.scrollTop,
   contentTopInTerminal:c.getBoundingClientRect().top-t.getBoundingClientRect().top,
   footerBottomInTerminal:t.querySelector('[data-component-id=bottom]').getBoundingClientRect().bottom-t.getBoundingClientRect().top};
}"""


def measure_case(browser, origin, label, mode, kind):
    values = []
    for hostheight in (900, 1200):
        fixture = HeightFixture(source(overflow=kind == 'overflow'))
        context = browser.new_context(viewport={'width': 1500, 'height': hostheight})
        context.route('**/api/**', fixture.route)
        context.route_web_socket('**/ws/views/height-review-view', lambda ws: ws.on_message(lambda message: None))
        page = context.new_page()
        try:
            page.goto(origin + '/editor')
            page.get_by_label('页面名称', exact=True).wait_for()
            page.get_by_label('终端尺寸').select_option('follow' if kind == 'follow' else '1024x768')
            if mode == 'runtime':
                page.get_by_label('实时预览任务').select_option('run-A')
            page.get_by_role('button', name='示例预览' if mode == 'fixtures' else '实时预览', exact=True).click()
            page.get_by_role('dialog').wait_for()
            page.wait_for_function("() => document.querySelector('.editor-preview-terminal > .panel-responsive')?.style.height")
            page.wait_for_timeout(100)
            value = page.evaluate(GEOMETRY)
            if kind == 'overflow':
                page.locator('.editor-preview-terminal').evaluate('el => {el.scrollTop=el.scrollHeight}')
                value['afterScroll'] = page.evaluate(GEOMETRY)
            values.append(value)
            page.screenshot(path=str(EVIDENCE / f'{label}-{kind}-{mode}-host-{hostheight}.png'), full_page=True)
        finally:
            context.close()
    return values


def assert_geometry(values, kind):
    for value in values:
        t, c, b = value['terminal'], value['content'], value['bottomWidget']
        if kind == 'fixed':
            assert abs(t['height'] - 768) <= 1, 'fixed terminal stretched with browser height'
            assert abs(c['height'] - 752) <= 1, 'internal flow does not account for terminal height and 16px top gap'
            assert abs(value['contentTopInTerminal'] - 16) <= 1
            assert abs(value['footerBottomInTerminal'] - 768) <= 1, 'footer misses terminal logical bottom'
            assert value['terminalScrollHeight'] == value['terminalClientHeight'], 'normal content unexpectedly overflows'
            assert abs(value['topWidget']['height'] - 108) <= 1 and abs(b['height'] - 108) <= 1, 'fixed content was squeezed'
        elif kind == 'follow':
            assert abs(c['height'] - (value['windowHeight'] - c['top'] - 16)) <= 1, 'follow mode lost original window measurement'
            assert abs(b['bottom'] - c['bottom']) <= 1
        else:
            assert abs(t['height'] - 768) <= 1, 'overflow expanded the chosen terminal'
            assert value['topWidget']['height'] >= 900 and b['height'] >= 400, 'overflow content was compressed'
            assert value['terminalScrollHeight'] > value['terminalClientHeight']
            after = value['afterScroll']
            assert after['terminalScrollTop'] > 0, 'overflow cannot scroll'
            assert after['bottomWidget']['top'] >= after['terminal']['top'] - 1
            assert after['bottomWidget']['bottom'] <= after['terminal']['bottom'] + 1, 'footer cannot be reached by scrolling'
    if kind == 'fixed':
        for key in ('content', 'bottomWidget', 'spacer'):
            assert abs(values[0][key]['height'] - values[1][key]['height']) <= 1
        assert abs((values[0]['bottomWidget']['top'] - values[0]['content']['top']) - (values[1]['bottomWidget']['top'] - values[1]['content']['top'])) <= 1
    elif kind == 'follow':
        assert values[1]['content']['height'] - values[0]['content']['height'] > 200


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--label', required=True)
    parser.add_argument('--prior-regressions', action='store_true')
    args = parser.parse_args()
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    if args.prior_regressions:
        fixtures.EVIDENCE = EVIDENCE
        sys.argv = [str(Path(fixtures.__file__)), '--label', args.label]
        return fixtures.main()
    server = ThreadingHTTPServer(('127.0.0.1', 0), functools.partial(fixtures.Handler, directory=str(fixtures.ROOT / 'dist')))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    report = {'evidenceLevel': 'Built React + real headless Edge; mocked HTTP and live-view WS; no Runtime/device acceptance',
              'label': args.label, 'cases': {}}
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(executable_path=fixtures.EDGE, headless=True)
            for kind in ('fixed', 'follow', 'overflow'):
                for mode in ('fixtures', 'runtime'):
                    name = f'{kind}-{mode}'
                    case = report['cases'][name] = {}
                    try:
                        case['measurements'] = measure_case(browser, f'http://127.0.0.1:{server.server_port}', args.label, mode, kind)
                        assert_geometry(case['measurements'], kind)
                        case['status'] = 'passed'
                    except Exception:
                        case['status'] = 'failed'
                        case['failure'] = traceback.format_exc()
                    print(name, case['status'], flush=True)
            browser.close()
    finally:
        server.shutdown()
        path = EVIDENCE / f'{args.label}-height-browser.json'
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(path, flush=True)
    return 1 if any(case['status'] == 'failed' for case in report['cases'].values()) else 0


if __name__ == '__main__':
    raise SystemExit(main())
