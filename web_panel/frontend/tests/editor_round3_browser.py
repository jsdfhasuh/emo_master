"""Real Edge/React editor operations; mocked API, formal backend source validation.

Checks saved payloads and reloads. Live preview WS is mocked: no Runtime claim.
"""
import argparse
import copy
import functools
import json
import re
import sys
import threading
import traceback
from http.server import ThreadingHTTPServer
from urllib.parse import urlsplit
from playwright.sync_api import sync_playwright
import editor_review_browser as f

sys.path.insert(0, str(f.REPO / 'web_panel/backend'))
from emo_web_panel.editor import validate_source

EVIDENCE = f.REPO / 'docs/testing/2026-11-03-independent-web-editor/review-round3'
SCOPE = {'entryWorkflowId': 'wf', 'scopeWorkflowId': 'wf', 'callPath': [], 'commitPolicy': 'workflow_terminal'}
CHOICE = {'label': '流程 wf / 数量 count', 'sourceId': 'source-count', 'scopeId': 'scope-wf', 'scope': SCOPE,
          'source': {'kind': 'node_output', 'resultScopeId': 'scope-wf', 'workflowId': 'wf', 'nodeId': 'node', 'port': 'count',
                     'callPath': [], 'fieldPath': [], 'expectedType': 'integer', 'ruleVersion': '1.0'}, 'enabled': True}


class Fixture(f.Fixture):
    def __init__(self, doc):
        super().__init__(doc)
        self.validated = 0
        self.metadata = {'jobId': 'run-A', 'runtimeInstanceId': 'runtime-1', 'projectId': 'A',
                         'projectRevision': '3', 'executionRevision': 'execution-old-3-full', 'capturePlanRevision': 'capture-old-5-full'}

    def route(self, route):
        path = urlsplit(route.request.url).path
        if path.endswith('/context'):
            body = {'runtime': {'runtimeInstanceId': self.instance, 'projectId': self.project, 'projectName': 'Review project', 'documentRevision': '999'},
                    'catalog': {'choices': [CHOICE]}, 'requests': {'diagnostics': []}, 'versions': [],
                    'draft': {'presentation': self.documents[self.project], 'draftRevision': str(self.revision)}}
        elif path.endswith('/draft') and route.request.method == 'PUT':
            try:
                validate_source(route.request.post_data_json['presentation'], {})
                self.validated += 1
            except Exception as error:
                route.fulfill(status=422, content_type='application/json', body=json.dumps({'detail': str(error)}))
                return
            super().route(route)
            return
        elif path.endswith('/preview') and route.request.post_data_json['mode'] == 'runtime':
            body = {'viewId': 'round3-view', 'runIds': ['run-A'], 'jobMetadata': self.metadata}
        elif path.endswith('/publish'):
            body = {'versionId': 'rejected-version', 'state': 'REJECTED', 'message': '采集额度不足', 'diagnostics': [
                {'code': 'CAPTURE_IMAGE_LIMIT', 'message': '流程 wf 请求了3种图片，上限2种；包含Qt需求',
                 'componentRefs': [{'pageId': 'page-A', 'pageName': 'A 检测页', 'componentId': 'component-A', 'componentName': '检测图'}]}]}
        elif path.endswith('/versions'):
            body = []
        else:
            super().route(route)
            return
        route.fulfill(status=200, content_type='application/json', body=json.dumps(body))


def boot(browser, origin, fixture):
    context, page = f.boot(browser, origin, fixture)
    context.route_web_socket('**/ws/views/round3-view', lambda ws: ws.on_message(lambda message: None))
    return context, page


def preview(page, mode):
    if mode == 'runtime':
        page.get_by_label('实时预览任务').select_option('run-A')
    page.get_by_role('button', name='示例预览' if mode == 'fixtures' else '实时预览', exact=True).click()
    return page.get_by_role('dialog')


def page_selection(browser, origin, report, mode, route):
    doc = f.presentation(two=True)
    nav = f.component('nav')
    nav.update(type='navigation_button', props={'title': '去详情', 'text': '去详情'}, actions={'clicked': {'type': 'navigate', 'pageId': 'detail', 'context': 'live'}})
    doc['pages']['page-A']['components'].append(nav)
    fixture = Fixture(doc)
    context, page = boot(browser, origin, fixture)
    try:
        page.locator('[data-editor-component="component-A"]').click()
        if route == 'editor':
            page.get_by_role('navigation', name='编辑页面标签').get_by_role('button', name='详情页', exact=True).click()
        else:
            dialog = preview(page, mode)
            if route == 'tab':
                dialog.locator(':scope > nav').get_by_role('button', name='详情页', exact=True).click()
            else:
                dialog.get_by_role('button', name='去详情', exact=True).click()
            page.get_by_role('button', name='关闭预览', exact=True).click()
        assert page.get_by_label('页面名称', exact=True).input_value() == '详情页'
        assert page.get_by_label('组件标题', exact=True).count() == 0, 'old-page properties survived page switch'
        assert page.locator('.editor-component.selected').count() == 0
        page.locator('[data-editor-component="detail-component"]').click()
        page.get_by_label('组件标题', exact=True).fill('Edited detail only')
        saved = f.save(page, fixture)['presentation']
        assert saved['pages']['page-A']['components'][0]['props']['title'] == 'component-A'
        assert saved['pages']['detail']['components'][0]['props']['title'] == 'Edited detail only'
        reopened = f.persisted_after_reload(page, fixture)
        assert reopened == saved
        report.update(saved=saved, reopened=reopened, backendValidatedSaves=fixture.validated)
        page.screenshot(path=str(EVIDENCE / f"{report['label']}.png"), full_page=True)
    finally:
        context.close()


def clipboard(browser, origin, report, kind):
    doc = f.presentation(two=kind == 'nested')
    item = doc['pages']['page-A']['components'][0]
    if kind != 'static':
        item['bindings'] = {'value': 'source-count'}
        doc['dataSources'] = {'source-count': copy.deepcopy(CHOICE['source'])}
        doc['resultScopes'] = {'scope-wf': copy.deepcopy(SCOPE)}
        doc['pages']['page-A']['resultScopeIds'] = ['scope-wf']
    if kind == 'shared':
        shared = copy.deepcopy(item)
        shared['componentId'] = 'other-user'
        doc['pages']['page-A']['components'].append(shared)
    if kind == 'nested':
        container = f.component('container')
        nav = f.component('nested-nav')
        nav.update(type='navigation_button', props={'text': '查看已显示结果'}, actions={'clicked': {'type': 'navigate', 'pageId': 'detail', 'context': 'displayed_result', 'resultScopeId': 'scope-action'}})
        container.update(type='container', children=[item, nav])
        doc['resultScopes']['scope-action'] = copy.deepcopy(SCOPE)
        doc['pages']['page-A']['components'] = [container]
    fixture = Fixture(doc)
    context, page = boot(browser, origin, fixture)
    try:
        target = page.locator('[data-editor-component="container"' + ']' if kind == 'nested' else '[data-editor-component="component-A"]')
        target.locator(':scope > .editor-card-heading').click()
        page.keyboard.press('Control+c')
        page.keyboard.press('Delete')
        page.locator('.editor-canvas').click(position={'x': 5, 'y': 5})
        page.keyboard.press('Control+v')
        saved = f.save(page, fixture)['presentation']
        assert len(saved['pages']['page-A']['components']) == (2 if kind == 'shared' else 1)
        if kind != 'static':
            assert list(saved['dataSources']) == ['source-count']
            assert 'scope-wf' in saved['resultScopes']
            c = saved['pages']['page-A']['components'][-1]
            assert (c['children'][0] if kind == 'nested' else c)['bindings']['value'] == 'source-count'
            if kind == 'nested':
                assert 'scope-action' in saved['resultScopes']
        reopened = f.persisted_after_reload(page, fixture)
        assert reopened == saved
        report.update(saved=saved, reopened=reopened, backendValidatedSaves=fixture.validated)
        page.screenshot(path=str(EVIDENCE / f"{report['label']}.png"), full_page=True)
    finally:
        context.close()


def drag(page, card, dx, dy):
    card.click()
    handle = card.get_by_role('button', name='拖动调整组件尺寸')
    handle.scroll_into_view_if_needed()
    hb = handle.bounding_box()
    x, y = hb['x'] + hb['width']/2, hb['y'] + hb['height']/2
    page.mouse.move(x, y);page.mouse.down();page.mouse.move(x+dx, y+dy, steps=5);page.mouse.up()


def grid(browser, origin, report, nested):
    doc = f.presentation()
    if nested:
        c = f.component('container')
        c.update(type='container', grid={'type': 'grid', 'columns': 3, 'spacing': 12}, children=[f.component('nested')])
        doc['pages']['page-A']['components'] = [c]
    else:
        doc['pages']['page-A']['layout'] = {'type': 'grid', 'columns': 2, 'spacing': 12}
    fixture = Fixture(doc)
    context, page = boot(browser, origin, fixture)
    try:
        cid = 'nested' if nested else 'component-A'
        card = page.locator(f'[data-editor-component="{cid}"]')
        card.click();before = card.bounding_box()
        assert page.get_by_label('row', exact=True).count() == 1
        assert page.get_by_label('首选宽度', exact=True).count() == 0
        if nested:
            for key, value in [('column', 1), ('columnSpan', 2), ('row', 1), ('rowSpan', 2)]:
                page.get_by_label(key, exact=True).fill(str(value))
            drag(page, card, 0, 64)
        else:
            drag(page, card, 50, 50)
            assert card.bounding_box()['height'] > before['height'] + 30
            drag(page, card, before['width'] * .7, 0)
        after = card.bounding_box()
        saved = f.save(page, fixture)['presentation']
        item = saved['pages']['page-A']['components'][0]
        if nested:
            item = item['children'][0]
            assert item['layout'] == {'row': 1, 'column': 1, 'rowSpan': 3, 'columnSpan': 2}
        else:
            assert item['layout']['columnSpan'] == 2 and item['layout']['rowSpan'] == 2
            assert after['width'] > before['width'] * 1.8
        assert item['responsive'] == f.component()['responsive']
        reopened = f.persisted_after_reload(page, fixture)
        assert reopened == saved
        dialog = preview(page, 'fixtures')
        rendered = dialog.locator(f'[data-component-id="{cid}"]')
        geometry = rendered.evaluate('el => ({row:getComputedStyle(el).gridRow,column:getComputedStyle(el).gridColumn,height:el.getBoundingClientRect().height})')
        assert f"span {item['layout']['rowSpan']}" in geometry['row']
        assert f"span {item['layout']['columnSpan']}" in geometry['column']
        assert geometry['height'] >= item['layout']['rowSpan'] * 64
        report.update(before=before, after=after, saved=saved, reopened=reopened, preview=geometry, backendValidatedSaves=fixture.validated)
        page.screenshot(path=str(EVIDENCE / f"{report['label']}.png"), full_page=True)
    finally:
        context.close()


def details(browser, origin, report, missing):
    fixture = Fixture(f.presentation())
    if missing:
        fixture.metadata.pop('projectRevision')
    context, page = boot(browser, origin, fixture)
    try:
        dialog = preview(page, 'runtime')
        info = dialog.get_by_role('region', name='所选任务修订')
        text = info.inner_text()
        assert '999' not in text
        assert '工程修订：' + ('Runtime未提供' if missing else '3') in text
        assert fixture.metadata['executionRevision'] in text and fixture.metadata['capturePlanRevision'] in text
        report['jobMetadataText'] = text
        page.get_by_role('button', name='关闭预览', exact=True).click()
        palette = page.locator('.editor-palette')
        for name in ['图像', '文字', '集合表格', '连接状态', '空白块']:
            assert palette.get_by_role('button', name=re.compile(f'{name}$')).count() == 1
        page.get_by_role('button', name='发布页面', exact=True).click()
        publication = page.get_by_role('region', name='发布状态')
        publication.wait_for()
        report['diagnosticText'] = publication.inner_text()
        assert '包含Qt需求' in report['diagnosticText'] and 'A 检测页 / 检测图 · component-A' in report['diagnosticText']
        page.screenshot(path=str(EVIDENCE / f"{report['label']}.png"), full_page=True)
    finally:
        context.close()


def main():
    parser = argparse.ArgumentParser();parser.add_argument('--label', required=True);parser.add_argument('--suite', choices=['editor', 'prior12', 'height'], default='editor');args = parser.parse_args()
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    if args.suite != 'editor':
        if args.suite == 'prior12':
            f.EVIDENCE = EVIDENCE;sys.argv = [f.__file__, '--label', args.label];return f.main()
        import editor_preview_height_browser as height
        height.EVIDENCE = EVIDENCE;sys.argv = [height.__file__, '--label', args.label];return height.main()
    server = ThreadingHTTPServer(('127.0.0.1', 0), functools.partial(f.Handler, directory=str(f.ROOT / 'dist')))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    cases = [(f'page-{mode}-{route}', functools.partial(page_selection, mode=mode, route=route)) for mode in ['fixtures', 'runtime'] for route in ['tab', 'button']]
    cases += [('page-editor', functools.partial(page_selection, mode='fixtures', route='editor'))]
    cases += [(f'clipboard-{kind}', functools.partial(clipboard, kind=kind)) for kind in ['bound', 'nested', 'shared', 'static']]
    cases += [('grid-root', functools.partial(grid, nested=False)), ('grid-nested', functools.partial(grid, nested=True))]
    cases += [('metadata-known', functools.partial(details, missing=False)), ('metadata-unavailable', functools.partial(details, missing=True))]
    report = {'evidenceLevel': 'Real built React / Edge; mocked HTTP+WS; formal backend validate_source on actual PUTs; saved source and reload', 'cases': {}}
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(executable_path=f.EDGE, headless=True)
            for name, case in cases:
                outcome = report['cases'][name] = {'label': f'{args.label}-{name}'}
                try:
                    case(browser, f'http://127.0.0.1:{server.server_port}', outcome);outcome['status'] = 'passed'
                except Exception:
                    outcome.update(status='failed', failure=traceback.format_exc())
                print(name, outcome['status'], flush=True)
            browser.close()
    finally:
        server.shutdown();path = EVIDENCE / f'{args.label}-browser.json';path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8');print(path, flush=True)
    return int(any(c['status'] == 'failed' for c in report['cases'].values()))


if __name__ == '__main__':
    raise SystemExit(main())
