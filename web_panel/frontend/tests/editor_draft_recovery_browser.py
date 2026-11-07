"""Real React + Chromium regressions against a synthetic CAS/auth API.

No Runtime or device is started. Run after npm run build. The optional report
and screenshots are local verification outputs, not deployment evidence.
"""
import argparse
import copy
import functools
import json
import os
import re
import shutil
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright
from editor_review_browser import Fixture, Handler, ROOT


class RecoveryFixture(Fixture):
    def __init__(self):
        super().__init__()
        self.username = 'alice'
        self.authenticated = True
        self.expire_on = ''
        self.attempts = []

    def reply(self, route, body, status=200):
        route.fulfill(status=status, content_type='application/json', body=json.dumps(body))

    def route(self, route):
        path = urlsplit(route.request.url).path
        if path == '/api/session':
            self.username = route.request.post_data_json['username']
            self.authenticated = True
            return self.reply(route, self.identity())
        if path == self.expire_on:
            self.expire_on = ''
            self.authenticated = False
        if not self.authenticated:
            return self.reply(route, {'detail': 'Login required'}, 401)
        if path == '/api/me':
            return self.reply(route, self.identity())
        if path == '/api/logout':
            self.authenticated = False
            return self.reply(route, {})
        if path.endswith('/draft') and route.request.method == 'PUT':
            payload = route.request.post_data_json
            self.attempts.append(copy.deepcopy(payload))
            if payload['expectedDraftRevision'] != str(self.revision):
                return self.reply(route, {'detail': '草稿已被其他编辑窗口修改', 'code': 'EDITOR_CONFLICT'}, 409)
        return super().route(route)

    def identity(self):
        return {'userId': 'user-' + self.username, 'username': self.username, 'role': 'admin', 'csrf': 'synthetic-only'}

    def remote_write(self, name):
        self.documents[self.project]['pages'][f'page-{self.project}']['name'] = name
        self.revision += 1


def open_page(context, origin):
    page = context.new_page()
    page._accept_dialog = lambda dialog: dialog.accept()
    page.on('dialog', page._accept_dialog)
    page.goto(origin + '/editor')
    expect(page.get_by_label('页面名称', exact=True)).to_be_visible()
    return page


def rename(page, name):
    page.get_by_label('页面名称', exact=True).fill(name)


def named(page, name):
    expect(page.get_by_label('页面名称', exact=True)).to_have_value(name)


def refresh(page):
    page.get_by_role('button', name='刷新目录和任务', exact=True).click()
    expect(page.get_by_role('button', name='保存草稿', exact=True)).to_be_enabled()


def save(page):
    page.get_by_role('button', name='保存草稿', exact=True).click()
    expect(page.locator('.editor-draft-state')).to_have_text('✓ 草稿已保存')


def login(page, username='alice'):
    page.get_by_label('用户名', exact=True).fill(username)
    page.get_by_label('密码', exact=True).fill('synthetic-password')
    page.get_by_role('button', name='登录', exact=True).click()
    expect(page.get_by_label('页面名称', exact=True)).to_be_visible()


def restart(context, origin, fixture):
    page = open_page(context, origin)
    page.locator('[data-editor-component="component-A"]').click()
    page.get_by_label('实时预览任务', exact=True).select_option('run-A')
    rename(page, 'unsaved across restart')
    page.get_by_label('组件标题', exact=True).fill('component edit')
    page.get_by_role('button', name='撤销', exact=True).click()
    fixture.instance = 'runtime-2'
    refresh(page)
    named(page, 'unsaved across restart')
    expect(page.get_by_role('button', name='撤销', exact=True)).to_be_enabled()
    expect(page.get_by_role('button', name='重做', exact=True)).to_be_enabled()
    expect(page.get_by_label('实时预览任务', exact=True)).to_have_value('')
    expect(page.get_by_role('button', name='实时预览', exact=True)).to_be_disabled()
    assert fixture.saves == []
    save(page)
    assert fixture.saves[-1]['expectedDraftRevision'] == '1'
    return page


def expire_and_logout(context, origin, fixture):
    page = open_page(context, origin)
    rename(page, 'private alice draft')
    fixture.expire_on = '/api/editor/configured/draft'
    page.get_by_role('button', name='保存草稿', exact=True).click()
    expect(page.get_by_role('button', name='登录', exact=True)).to_be_visible()
    assert fixture.saves == []
    # A different user on the same tab cannot adopt Alice's local source.
    login(page, 'bob')
    named(page, 'A 检测页')
    page.get_by_role('button', name='退出', exact=True).click()
    login(page)
    named(page, 'private alice draft')
    assert fixture.saves == []  # no replay after authentication
    page.get_by_role('button', name='退出', exact=True).click()
    expect(page.get_by_role('button', name='登录', exact=True)).to_be_visible()
    page.reload()  # persistent tab recovery survives full reload at login
    login(page)
    named(page, 'private alice draft')
    save(page)
    assert len(fixture.saves) == 1
    assert page.evaluate("Object.keys(sessionStorage).filter(k=>k.startsWith('emo-editor-draft:')).length") == 0
    return page


def project_switch(context, origin, fixture):
    page = open_page(context, origin)
    rename(page, 'A protected draft')
    fixture.project = 'B'
    refresh(page)
    named(page, 'B 检测页')
    expect(page.get_by_role('button', name='撤销', exact=True)).to_be_disabled()
    fixture.project = 'A'
    refresh(page)
    named(page, 'A protected draft')
    save(page)
    assert fixture.saves[-1]['projectId'] == 'A'
    assert fixture.documents['B']['pages']['page-B']['name'] == 'B 检测页'
    return page


def cas_conflict(context, origin, fixture):
    page = open_page(context, origin)
    other = open_page(context, origin)
    rename(page, 'my local edit')
    rename(other, 'other tab edit')
    save(other)
    page.get_by_role('button', name='保存草稿', exact=True).click()
    expect(page.get_by_role('heading', name='草稿已被其他窗口修改', exact=True)).to_be_visible()
    named(page, 'my local edit')
    assert len(fixture.saves) == 1
    page.get_by_text('比较本地与服务器源', exact=True).click()
    expect(page.get_by_label('本地草稿源', exact=True)).to_have_value(re.compile('my local edit'))
    expect(page.get_by_label('服务器草稿源', exact=True)).to_have_value(re.compile('other tab edit'))
    # A newer write after comparison still fails the displayed revision's CAS.
    fixture.remote_write('newer remote edit')
    page.get_by_role('button', name='保留本地并覆盖此版本', exact=True).click()
    expect(page.get_by_label('服务器草稿源', exact=True)).to_have_value(re.compile('newer remote edit'))
    named(page, 'my local edit')
    assert len(fixture.saves) == 1 and fixture.attempts[-1]['expectedDraftRevision'] == '2'
    page.get_by_role('button', name='保留本地并覆盖此版本', exact=True).click()
    expect(page.locator('.editor-draft-state')).to_have_text('✓ 草稿已保存')
    assert fixture.saves[-1]['expectedDraftRevision'] == '3'
    named(page, 'my local edit')
    # Normal refresh detects later conflicts; server adoption remains undoable.
    rename(page, 'local before adopt')
    fixture.remote_write('server choice')
    refresh(page)
    page.get_by_role('button', name='采用服务器草稿', exact=True).click()
    named(page, 'server choice')
    page.get_by_role('button', name='撤销', exact=True).click()
    named(page, 'local before adopt')
    save(page)
    assert fixture.saves[-1]['expectedDraftRevision'] == '5'
    other.close()
    return page


def logout_cancel(context, origin, fixture):
    page = open_page(context, origin)
    rename(page, 'keep editing')
    page.remove_listener('dialog', page._accept_dialog)
    page.on('dialog', lambda dialog: dialog.dismiss())
    page.get_by_role('button', name='退出', exact=True).click()
    named(page, 'keep editing')
    assert fixture.authenticated and not fixture.saves
    return page


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--report', type=Path)
    parser.add_argument('--browser', default=os.environ.get('CHROME_PATH') or shutil.which('chromium') or None)
    args = parser.parse_args()
    server = ThreadingHTTPServer(('127.0.0.1', 0), functools.partial(Handler, directory=str(ROOT / 'dist')))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    report = {'level': 'built React + real headless Chromium + synthetic HTTP CAS/auth; no Runtime/devices', 'cases': []}
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(executable_path=args.browser, headless=True, args=['--no-sandbox'])
            for case in [restart, expire_and_logout, project_switch, cas_conflict, logout_cancel]:
                fixture = RecoveryFixture()
                context = browser.new_context(viewport={'width': 1500, 'height': 1050})
                context.route('**/api/**', fixture.route)
                page = case(context, f'http://127.0.0.1:{server.server_port}', fixture)
                if args.report:
                    args.report.parent.mkdir(parents=True, exist_ok=True)
                    page.screenshot(path=str(args.report.with_name(case.__name__ + '.png')), full_page=True)
                report['cases'].append({'name': case.__name__, 'status': 'passed', 'acceptedSaves': len(fixture.saves)})
                print(case.__name__, 'passed', flush=True)
                context.close()
            browser.close()
    finally:
        server.shutdown()
        if args.report:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
