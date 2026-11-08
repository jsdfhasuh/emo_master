"""Real React/Edge review regressions with an explicitly mocked HTTP API.

This checks persisted Presentation source, not Runtime, Qt, LAN or device acceptance.
Run with the Web venv Python after npm run build; --label preserves each evidence run.
"""
import argparse
import copy
import functools
import json
import threading
import traceback
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parents[1]
EVIDENCE = REPO / 'docs/testing/2026-11-03-independent-web-editor/review-fixes'
EDGE = r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe'


def component(cid='component-A'):
    return {'componentId': cid, 'type': 'text', 'version': '1.0',
            'layout': {'row': 0, 'column': 0, 'rowSpan': 1, 'columnSpan': 1},
            'responsive': {'width': 'fill', 'minWidth': 160, 'preferredWidth': 240,
                           'minHeight': 64, 'preferredHeight': 0, 'weight': 1, 'alignSelf': 'auto'},
            'props': {'title': cid, 'text': 'Editable text'}, 'bindings': {}, 'actions': {},
            'children': [], 'grid': {'type': 'responsive', 'direction': 'vertical', 'spacing': 12}}


def presentation(project='A', two=False):
    pid = f'page-{project}'
    doc = {'schemaVersion': '1.2', 'defaultPageId': pid, 'pageOrder': [pid],
           'navigation': {'mode': 'tabs'}, 'dataSources': {}, 'resultScopes': {},
           'pages': {pid: {'name': f'{project} 检测页', 'targets': ['web'],
                           'layout': {'type': 'responsive', 'direction': 'vertical', 'spacing': 12,
                                      'alignment': 'stretch', 'justify': 'start', 'equalWidth': False},
                           'components': [component(f'component-{project}')], 'resultScopeIds': []}}}
    if two:
        doc['pageOrder'].append('detail')
        doc['pages']['detail'] = copy.deepcopy(doc['pages'][pid])
        doc['pages']['detail']['name'] = '详情页'
        doc['pages']['detail']['components'] = [component('detail-component')]
    return doc


class Handler(SimpleHTTPRequestHandler):
    def do_GET(self):
        if urlsplit(self.path).path.startswith('/editor'):
            self.path = '/index.html'
        super().do_GET()

    def log_message(self, *args):
        pass


class Fixture:
    def __init__(self, doc=None):
        self.project = 'A'
        self.instance = 'runtime-1'
        self.documents = {'A': doc or presentation(), 'B': presentation('B')}
        self.saves = []
        self.revision = 1
        self.preview_requests = []
        self.fail_contexts = 0

    def route(self, route):
        request = route.request
        path = urlsplit(request.url).path
        if path == '/api/me':
            body = {'username': 'browser-review', 'role': 'admin', 'csrf': 'test-only'}
        elif path == '/api/runtimes':
            body = [{'id': 'configured', 'alias': 'Synthetic review', 'mode': 'mock'}]
        elif path == '/api/editor/configured/context':
            if self.fail_contexts:
                self.fail_contexts -= 1
                route.fulfill(status=503, content_type='application/json', body=json.dumps({'detail': 'Review: initial Runtime connection unavailable'}))
                return
            body = {'runtime': {'runtimeInstanceId': self.instance, 'projectId': self.project,
                                'projectName': f'Project {self.project}'},
                    'catalog': {'choices': []}, 'requests': {'diagnostics': []}, 'versions': [],
                    'draft': {'presentation': self.documents[self.project], 'draftRevision': str(self.revision)}}
        elif path == '/api/runtimes/configured/runs':
            body = [{'id': f'run-{self.project}', 'name': f'Task {self.project}', 'state': 'COMPLETED'}]
        elif path == '/api/editor/configured/draft' and request.method == 'PUT':
            payload = request.post_data_json
            self.saves.append(copy.deepcopy(payload))
            self.documents[payload['projectId']] = copy.deepcopy(payload['presentation'])
            self.revision += 1
            body = {'draftRevision': str(self.revision), 'presentation': payload['presentation']}
        elif path == '/api/editor/configured/preview':
            self.preview_requests.append(request.post_data_json)
            body = {}  # Product performs its normal source -> renderer projection.
        elif path.startswith('/api/views/') and request.method == 'DELETE':
            route.fulfill(status=204)
            return
        else:
            route.fulfill(status=404, content_type='application/json', body=json.dumps({'detail': path}))
            return
        route.fulfill(status=200, content_type='application/json', body=json.dumps(body))


def boot(browser, origin, fixture, wait_editor=True):
    context = browser.new_context(viewport={'width': 1500, 'height': 1050})
    context.route('**/api/**', fixture.route)
    page = context.new_page()
    page.on('dialog', lambda dialog: dialog.accept())
    page.goto(origin + '/editor')
    if wait_editor:
        page.get_by_label('页面名称', exact=True).wait_for()
    return context, page


def save(page, fixture):
    count = len(fixture.saves)
    page.get_by_role('button', name='保存草稿', exact=True).click()
    page.wait_for_function("() => document.querySelector('.editor-draft-state')?.textContent.includes('已保存')")
    assert len(fixture.saves) == count + 1
    return fixture.saves[-1]


def persisted_after_reload(page, fixture):
    page.reload()
    page.get_by_label('页面名称', exact=True).wait_for()
    return save(page, fixture)['presentation']


def identity_case(browser, origin, report, screenshot, change='project'):
    fixture = Fixture()
    context, page = boot(browser, origin, fixture)
    try:
        page.locator('[data-editor-component="component-A"]').click()
        page.keyboard.press('Control+c')
        page.get_by_label('实时预览任务').select_option('run-A')
        page.get_by_label('页面名称', exact=True).fill('A unsaved editing')
        page.get_by_label('组件标题', exact=True).fill('A second edit')
        page.get_by_role('button', name='撤销', exact=True).click()
        page.get_by_role('button', name='刷新目录和任务', exact=True).click()
        page.get_by_role('button', name='保存草稿', exact=True).wait_for(state='visible')
        assert page.get_by_label('页面名称', exact=True).input_value() == 'A unsaved editing'
        assert page.get_by_role('button', name='撤销', exact=True).is_enabled()
        assert page.get_by_role('button', name='重做', exact=True).is_enabled()
        report['sameIdentityPreservesUnsavedHistory'] = True
        if change == 'project':
            fixture.project = 'B'
        else:
            fixture.instance = 'runtime-2'
        page.get_by_role('button', name='刷新目录和任务', exact=True).click()
        page.wait_for_function("name => document.querySelector('input[aria-label=\"页面名称\"]')?.value === name", arg=f'{fixture.project} 检测页' if change == 'project' else 'A unsaved editing')
        report['newIdentityUndoEnabled'] = page.get_by_role('button', name='撤销', exact=True).is_enabled()
        report['newIdentityRedoEnabled'] = page.get_by_role('button', name='重做', exact=True).is_enabled()
        report['newIdentityRunSelection'] = page.get_by_label('实时预览任务').input_value()
        report['newIdentityLivePreviewEnabled'] = page.get_by_role('button', name='实时预览', exact=True).is_enabled()
        if change == 'project' and report['newIdentityUndoEnabled']:
            page.get_by_role('button', name='撤销', exact=True).click()
        submitted = save(page, fixture)
        report['saveAfterIdentitySwitch'] = copy.deepcopy(submitted)
        # Clipboard must not transfer a copied component from A into B.
        page.locator('[data-editor-component]').first.click()
        page.keyboard.press('Control+v')
        after_paste = save(page, fixture)['presentation']
        report['componentIdsAfterPaste'] = [c['componentId'] for p in after_paste['pages'].values() for c in p['components']]
        reopened = persisted_after_reload(page, fixture)
        report['reopenedPageIds'] = reopened['pageOrder']
        page.screenshot(path=str(screenshot), full_page=True)
        assert submitted['projectId'] == fixture.project and submitted['presentation']['pageOrder'] == [f'page-{fixture.project}'], 'old source was saved under the new identity'
        assert reopened['pages'][f'page-{fixture.project}']['name'] == (f'{fixture.project} 检测页' if change == 'project' else 'A unsaved editing'), 'project source was not preserved correctly'
        assert report['newIdentityUndoEnabled'] == (change == 'instance') and report['newIdentityRedoEnabled'] == (change == 'instance'), 'history must follow the project, not the Runtime instance'
        assert report['newIdentityRunSelection'] == '', 'old task survived'
        assert not report['newIdentityLivePreviewEnabled'], 'old task remains usable for live preview'
        assert len(report['componentIdsAfterPaste']) == (2 if change == 'instance' else 1), 'clipboard must follow the project, not the Runtime instance'
    finally:
        context.close()


def identity_preview_case(browser, origin, report, screenshot):
    fixture = Fixture()
    context, page = boot(browser, origin, fixture)
    try:
        page.get_by_role('button', name='示例预览', exact=True).click()
        page.get_by_role('dialog').wait_for()
        fixture.project = 'B'
        # Invoke the same real React refresh handler while the preview is open.
        # This checks cleanup on identity detection, not modal focus management.
        page.get_by_role('button', name='刷新目录和任务', exact=True).evaluate('el => el.click()')
        page.wait_for_function("() => document.querySelector('.editor-project strong')?.textContent === 'Project B'")
        report['dialogsAfterIdentityChange'] = page.get_by_role('dialog').count()
        page.screenshot(path=str(screenshot), full_page=True)
        assert report['dialogsAfterIdentityChange'] == 0, 'old preview remained open after identity change'
    finally:
        context.close()


def initial_load_recovery_case(browser, origin, report, screenshot):
    fixture = Fixture()
    fixture.fail_contexts = 1
    context, page = boot(browser, origin, fixture, wait_editor=False)
    try:
        page.get_by_role('alert').wait_for()
        report['initialFailureVisible'] = 'initial Runtime connection unavailable' in page.get_by_role('alert').inner_text()
        page.get_by_role('button', name='刷新目录和任务', exact=True).click()
        page.wait_for_function("() => document.querySelector('.editor-project strong')?.textContent === 'Project A'")
        report['editorAfterRetry'] = page.get_by_label('页面名称', exact=True).count() == 1
        page.screenshot(path=str(screenshot), full_page=True)
        assert report['editorAfterRetry'], 'successful refresh did not load document after initial failure'
        page.get_by_label('页面名称', exact=True).fill('Recovered editable')
        report['saved'] = save(page, fixture)
        report['reopened'] = persisted_after_reload(page, fixture)
        assert report['reopened']['pages']['page-A']['name'] == 'Recovered editable'
    finally:
        context.close()


def history_case(browser, origin, report, screenshot, action):
    fixture = Fixture(presentation(two=action == 'delete'))
    context, page = boot(browser, origin, fixture)
    try:
        page.locator('[data-editor-component]').first.click()
        page.get_by_role('button', name={'new': '＋ 新页面', 'duplicate': '复制页', 'delete': '删除页'}[action], exact=True).click()
        page.get_by_role('button', name='撤销', exact=True).click()
        report['editorAfterUndo'] = page.get_by_label('页面名称', exact=True).count() > 0
        page.screenshot(path=str(screenshot), full_page=True)
        assert report['editorAfterUndo'], 'undo left pageId outside restored document and editor vanished'
        assert page.get_by_role('button', name='重做', exact=True).is_enabled()
        page.get_by_role('button', name='重做', exact=True).click()
        assert page.get_by_label('页面名称', exact=True).count() == 1
        assert page.get_by_label('组件标题', exact=True).count() == 0, 'selection remained on a different page'
        page.get_by_role('button', name='撤销', exact=True).click()
        page.get_by_label('页面名称', exact=True).fill(f'{action} restored editable')
        submitted = save(page, fixture)['presentation']
        reopened = persisted_after_reload(page, fixture)
        report['saved'] = submitted
        report['reopened'] = reopened
        assert any(p['name'] == f'{action} restored editable' for p in reopened['pages'].values())
    finally:
        context.close()


def resize_case(browser, origin, report, screenshot, mode, fit=True):
    fixture = Fixture()
    original = copy.deepcopy(fixture.documents['A']['pages']['page-A']['components'][0]['responsive'])
    context, page = boot(browser, origin, fixture)
    try:
        page.get_by_label('终端尺寸').select_option('1920x1080')
        if fit:
            page.get_by_role('button', name='适应画布', exact=True).click()
        else:
            page.get_by_label('终端尺寸').select_option('follow')
        card = page.locator('[data-editor-component="component-A"]')
        card.click()
        page.wait_for_timeout(220)  # CSS terminal width transition, not a result timeout.
        box = card.bounding_box()
        zoom = page.locator('.editor-terminal').evaluate('el => new DOMMatrix(getComputedStyle(el).transform).a')
        handle = page.get_by_role('button', name='拖动调整组件尺寸')
        hb = handle.bounding_box()
        start = {'x': hb['x'] + hb['width'] / 2, 'y': hb['y'] + hb['height'] / 2}
        page.mouse.move(**start)
        page.mouse.down()
        if mode != 'noop':
            page.mouse.move(start['x'] + 20, start['y'] + 10, steps=4)
        if mode == 'cancel':
            handle.dispatch_event('pointercancel', {'pointerId': 1})
        page.mouse.up()
        submitted = save(page, fixture)['presentation']
        actual = submitted['pages']['page-A']['components'][0]['responsive']
        reopened = persisted_after_reload(page, fixture)['pages']['page-A']['components'][0]['responsive']
        report.update({'zoom': zoom, 'screenBefore': box, 'responsiveBefore': original,
                       'savedResponsive': actual, 'reopenedResponsive': reopened})
        page.screenshot(path=str(screenshot), full_page=True)
        assert reopened == actual
        if mode == 'drag':
            expected_width, expected_height = round((box['width'] + 20) / zoom), round((box['height'] + 10) / zoom)
            report['expectedLogicalSize'] = [expected_width, expected_height]
            assert abs(actual['preferredWidth'] - expected_width) <= 1, 'screen width was persisted as logical width'
            assert abs(actual['preferredHeight'] - expected_height) <= 1, 'screen height was persisted as logical height'
        else:
            assert actual == original, f'{mode} changed source dimensions'
    finally:
        context.close()


def preview_case(browser, origin, report, screenshot):
    fixture = Fixture()
    context, page = boot(browser, origin, fixture)
    try:
        page.get_by_label('终端尺寸').select_option('1366x768')
        page.get_by_role('button', name='示例预览', exact=True).click()
        dialog = page.get_by_role('dialog')
        dialog.wait_for()
        viewport = dialog.locator('.editor-preview-terminal')
        report['terminalCount'] = viewport.count()
        page.screenshot(path=str(screenshot), full_page=True)
        assert viewport.count() == 1, 'preview ignores chosen fixed terminal'
        report['terminalSize'] = viewport.evaluate('el => ({width:el.getBoundingClientRect().width,minHeight:parseFloat(getComputedStyle(el).minHeight)})')
        assert report['terminalSize'] == {'width': 1366, 'minHeight': 768}
        page.get_by_role('button', name='关闭预览', exact=True).click()
    finally:
        context.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--label', required=True)
    parser.add_argument('--case', action='append')
    args = parser.parse_args()
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer(('127.0.0.1', 0), functools.partial(Handler, directory=str(ROOT / 'dist')))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    report = {'evidenceLevel': 'Real headless Edge + built React frontend + mocked API; persisted source checked',
              'label': args.label, 'cases': {}}
    cases = [('identity-switch', identity_case),
             ('initial-load-recovery', initial_load_recovery_case),
             ('identity-instance', functools.partial(identity_case, change='instance')),
             ('identity-preview', identity_preview_case),
             *[(f'history-{action}', functools.partial(history_case, action=action)) for action in ['new', 'duplicate', 'delete']],
             *[(f'resize-{mode}', functools.partial(resize_case, mode=mode)) for mode in ['drag', 'noop', 'cancel']],
             ('resize-unscaled', functools.partial(resize_case, mode='drag', fit=False)),
             ('fixed-terminal-preview', preview_case)]
    if args.case:
        cases = [(name, case) for name, case in cases if name in args.case]
        assert cases, 'unknown case filter'
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(executable_path=EDGE, headless=True)
            for name, case in cases:
                outcome = report['cases'][name] = {}
                try:
                    case(browser, f'http://127.0.0.1:{server.server_port}', outcome, EVIDENCE / f'{args.label}-{name}.png')
                    outcome['status'] = 'passed'
                except Exception:
                    outcome['status'] = 'failed'
                    outcome['failure'] = traceback.format_exc()
                print(name, outcome['status'], flush=True)
            browser.close()
    finally:
        server.shutdown()
        path = EVIDENCE / f'{args.label}-browser.json'
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(path, flush=True)
    return 1 if any(c['status'] == 'failed' for c in report['cases'].values()) else 0


if __name__ == '__main__':
    raise SystemExit(main())
