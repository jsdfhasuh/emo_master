"""Built React + mocked HTTP/WS: capture loss clears only its owning Job.

Run after npm run build. This is browser behavior coverage, not real-device acceptance.
"""
import argparse
import functools
import json
import shutil
import threading
from http.server import ThreadingHTTPServer
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright
import editor_review_browser as fixtures


class DispatchFixture(fixtures.Fixture):
    def __init__(self, grouped):
        doc = fixtures.presentation()
        self.jobs = ['run-A', 'consumer', 'sibling'] if grouped else ['run-A']
        captured = self.jobs[1:] if grouped else self.jobs
        components = []
        for index, job in enumerate(captured):
            scope, source = job + '-scope', job + '-value'
            item = fixtures.component(job + '-number')
            item.update(type='number', bindings={'value': source}, props={'title': job})
            item['layout']['row'] = index
            components.append(item)
            doc['resultScopes'][scope] = {'entryWorkflowId': job, 'scopeWorkflowId': job}
            doc['dataSources'][source] = {'kind': 'global_variable', 'resultScopeId': scope,
                'variableId': source, 'expectedType': 'integer'}
        doc['pages']['page-A']['components'] = components
        super().__init__(doc)

    def route(self, route):
        if urlsplit(route.request.url).path.endswith('/preview'):
            route.fulfill(status=200, content_type='application/json', body=json.dumps({
                'viewId': 'dispatch-view', 'runIds': self.jobs, 'sourceProblems': {}}))
        else:
            super().route(route)


def result(job, value):
    return {'event': 'result', 'runtimeInstanceId': 'runtime-1', 'projectId': 'A', 'jobId': job,
        'result': {'identity': {'runtimeInstanceId': 'runtime-1', 'jobId': job,
            'resultScopeId': job + '-scope', 'resultKey': job + '-result', 'resultOrdinal': 1,
            'invocationId': job + '-invocation', 'executionRevision': 'a' * 64,
            'capturePlanRevision': 'b' * 64, 'mode': 'runtime'},
            'status': 'COMPLETE', 'executionTerminal': 'COMPLETED',
            'expectedSourceIds': [job + '-value'],
            'sources': [{'sourceId': job + '-value', 'state': 'AVAILABLE', 'valueJson': str(value)}]}}


def unavailable(job):
    return {'event': 'capture_unavailable', 'runtimeInstanceId': 'runtime-1',
        'projectId': 'A', 'jobId': job, 'reason': 'This Job has no capture'}


def check_case(browser, origin, grouped):
    fixture = DispatchFixture(grouped)
    context = browser.new_context(viewport={'width': 1500, 'height': 1050})
    context.route('**/api/**', fixture.route)
    sockets = []
    context.route_web_socket('**/ws/views/dispatch-view', sockets.append)
    page = context.new_page()
    try:
        page.goto(origin + '/editor')
        page.get_by_label('页面名称', exact=True).wait_for()
        page.get_by_label('实时预览任务').select_option('run-A')
        page.get_by_role('button', name='实时预览', exact=True).click()
        page.get_by_role('dialog').wait_for()
        page.wait_for_function('() => document.querySelector(".editor-preview")?.textContent.includes("已连接")')
        ws = sockets[0]
        captured = fixture.jobs[1:] if grouped else fixture.jobs
        for index, job in enumerate(captured):
            ws.send(json.dumps(result(job, 314159 + index)))
            page.locator(f'.editor-preview [data-component-id="{job}-number"]').get_by_text(str(314159 + index), exact=True).wait_for()
        ws.send(json.dumps(unavailable('run-A')))
        page.wait_for_function('() => document.querySelector(".editor-preview header")?.textContent.includes("This Job has no capture")')
        if grouped:
            for index, job in enumerate(captured):
                assert page.locator(f'.editor-preview [data-component-id="{job}-number"]').get_by_text(str(314159 + index), exact=True).is_visible()
            ws.send(json.dumps(unavailable('consumer')))
            page.locator('.editor-preview [data-component-id="consumer-number"]').get_by_text('314159', exact=True).wait_for(state='hidden')
            assert page.locator('.editor-preview [data-component-id="sibling-number"]').get_by_text('314160', exact=True).is_visible()
        else:
            page.locator('.editor-preview [data-component-id="run-A-number"]').get_by_text('314159', exact=True).wait_for(state='hidden')
        print('PASS:', 'consumer-only Dispatch retains unaffected members' if grouped else 'ordinary Job clears its own capture')
    finally:
        context.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--browser-executable', default=shutil.which('chromium') or None)
    args = parser.parse_args()
    server = ThreadingHTTPServer(('127.0.0.1', 0), functools.partial(fixtures.Handler, directory=str(fixtures.ROOT / 'dist')))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(executable_path=args.browser_executable, headless=True)
            try:
                for grouped in (False, True):
                    check_case(browser, f'http://127.0.0.1:{server.server_port}', grouped)
            finally:
                browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


if __name__ == '__main__':
    main()
