"""Additional real Edge evidence: reach the footer on a short host viewport.

HTTP / WS are mocked. Does not mutate product sources or the frozen build.
"""
import functools
import json
import sys
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

from playwright.sync_api import sync_playwright

EVIDENCE = Path(__file__).resolve().parent
REPO = EVIDENCE.parents[3]
sys.path.insert(0, str(REPO / 'web_panel/frontend/tests'))
import editor_preview_height_browser as tests

server = ThreadingHTTPServer(('127.0.0.1', 0), functools.partial(tests.fixtures.Handler, directory=str(tests.fixtures.ROOT / 'dist')))
threading.Thread(target=server.serve_forever, daemon=True).start()
report = {'evidenceLevel': 'Real Edge / built React with mocked HTTP+WS; scroll geometry, no Runtime acceptance', 'cases': {}}
try:
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=tests.fixtures.EDGE, headless=True)
        for kind in ('fixed', 'overflow'):
            for mode in ('fixtures', 'runtime'):
                context = browser.new_context(viewport={'width': 1500, 'height': 900})
                fixture = tests.HeightFixture(tests.source(overflow=kind == 'overflow'))
                context.route('**/api/**', fixture.route)
                context.route_web_socket('**/ws/views/height-review-view', lambda ws: ws.on_message(lambda message: None))
                page = context.new_page()
                page.goto(f'http://127.0.0.1:{server.server_port}/editor')
                page.get_by_label('页面名称', exact=True).wait_for()
                page.get_by_label('终端尺寸').select_option('1024x768')
                if mode == 'runtime':
                    page.get_by_label('实时预览任务').select_option('run-A')
                page.get_by_role('button', name='示例预览' if mode == 'fixtures' else '实时预览', exact=True).click()
                page.get_by_role('dialog').wait_for()
                page.wait_for_function("() => document.querySelector('.editor-preview-terminal > .panel-responsive')?.style.height")
                page.locator('.editor-preview-terminal').evaluate('el => {el.scrollTop=el.scrollHeight}')
                page.locator('.editor-preview-backdrop').evaluate('el => {el.scrollTop=el.scrollHeight}')
                value = page.evaluate(tests.GEOMETRY)
                value['backdropScrollTop'] = page.locator('.editor-preview-backdrop').evaluate('el => el.scrollTop')
                assert value['bottomWidget']['top'] >= 0
                assert value['bottomWidget']['bottom'] <= value['windowHeight']
                assert value['bottomWidget']['bottom'] <= value['terminal']['bottom'] + 1
                assert value['backdropScrollTop'] > 0
                value['status'] = 'passed'
                name = f'{kind}-{mode}'
                report['cases'][name] = value
                page.screenshot(path=str(EVIDENCE / f'scroll-visible-{name}-host-900.png'))
                print(name, 'passed', flush=True)
                context.close()
        browser.close()
finally:
    server.shutdown()
    (EVIDENCE / 'scroll-visibility-probe.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
