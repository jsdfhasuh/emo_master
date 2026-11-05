"""Real Chromium + HTTP/WS, opt-in local mode and synthetic Runtime only.

No account is created. This verifies the requested passwordless feature, not
manual password-login acceptance or a real Runtime/device deployment.
"""
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent / 'backend'))

import uvicorn
from playwright.sync_api import expect, sync_playwright

from emo_web_panel.app import COOKIE, create_app
from emo_web_panel.config import Config


def main():
    evidence = ROOT / 'test-tmp'
    evidence.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory() as directory, socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        sock.listen(128)
        port = sock.getsockname()[1]
        base = f'http://127.0.0.1:{port}'
        app = create_app(Config(Path(directory), port=port, local_no_auth=True))
        server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=port,
                                proxy_headers=False, access_log=False, log_level='warning'))
        thread = threading.Thread(target=server.run, kwargs={'sockets': [sock]}, daemon=True)
        thread.start()
        deadline = time.monotonic() + 10
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert server.started, 'Synthetic test server did not start'
        try:
            with sync_playwright() as playwright:
                options = {'executable_path': os.environ['CHROMIUM_PATH']} if os.environ.get('CHROMIUM_PATH') else {}
                browser = playwright.chromium.launch(headless=True, **options)
                context = browser.new_context(viewport={'width': 1280, 'height': 900})
                page = context.new_page()
                errors, entries, frames = [], [], []
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.on('request', lambda request: entries.append(request.url)
                        if request.url.endswith('/api/local/session') else None)
                page.on('dialog', lambda dialog: dialog.accept())
                page.on('websocket', lambda ws: ws.on('framereceived', lambda frame: frames.append(frame)))
                page.goto(base + '/editor')
                name = page.get_by_label('页面名称', exact=True)
                expect(name).to_be_visible()
                expect(page.get_by_role('note')).to_contain_text('本机免密管理员')
                assert len(entries) == 1, 'StrictMode must not double-sign a local session'
                me = page.request.get(base + '/api/me').json()
                assert me['role'] == 'admin' and me['authMode'] == 'local'
                assert app.state.auth.users() == []
                assert app.state.runtime.control_count == 0
                name.fill('本机免建账号保存')
                page.get_by_role('button', name='保存草稿', exact=True).click()
                expect(page.locator('.editor-draft-state')).to_have_text('✓ 草稿已保存')
                page.reload()
                expect(name).to_have_value('本机免建账号保存')
                assert len(entries) == 1

                # Expire the synthetic test session, not an external login. A failed
                # save must be retained locally and never replayed after re-entry.
                name.fill('过期后保留但不重放')
                token = next(cookie['value'] for cookie in context.cookies() if cookie['name'] == COOKIE)
                app.state.auth.logout(token)
                page.get_by_role('button', name='保存草稿', exact=True).click()
                enter = page.get_by_role('button', name='进入本机模式', exact=True)
                expect(enter).to_be_visible()
                assert len(entries) == 1
                page.reload()
                expect(enter).to_be_visible()
                assert len(entries) == 1
                enter.click()
                expect(name).to_have_value('过期后保留但不重放')
                restored = page.request.get(base + '/api/editor/configured/context').json()
                source = restored['draft']['presentation']
                assert source['pages'][source['defaultPageId']]['name'] == '本机免建账号保存'
                assert len(entries) == 2
                page.screenshot(path=str(evidence / 'local-no-auth-editor.png'), full_page=True)

                page.get_by_role('button', name='退出', exact=True).click()
                expect(enter).to_be_visible()
                page.reload()
                expect(enter).to_be_visible()
                assert len(entries) == 2
                assert page.request.get(base + '/api/me').status == 401
                enter.click()
                expect(name).to_have_value('过期后保留但不重放')
                assert page.request.get(base + '/api/me').json()['userId'] == me['userId']

                # Actual browser WebSocket handshake includes exact Origin and the
                # same cookie; mock results do not start real or synthetic Jobs.
                page.goto(base + '/')
                expect(page.get_by_role('note')).to_contain_text('全部已登记 Runtime')
                page.get_by_label('查看运行', exact=True).select_option('demo-run')
                page.get_by_text('99.50', exact=False).wait_for()
                assert frames, 'Expected actual WebSocket messages'
                assert app.state.runtime.control_count == 0
                assert app.state.auth.users() == []
                assert not errors, errors
                page.screenshot(path=str(evidence / 'local-no-auth-runtime.png'), full_page=True)
                browser.close()
                print(json.dumps({'result': 'passed', 'localSessions': len(entries),
                                  'websocketFrames': len(frames), 'accountsCreated': 0,
                                  'runtimeControlCalls': 0, 'scope': 'Chromium + mock Runtime'}, ensure_ascii=False))
        finally:
            server.should_exit = True
            thread.join(timeout=10)
            assert not thread.is_alive(), 'Test server failed to stop'


if __name__ == '__main__':
    main()
