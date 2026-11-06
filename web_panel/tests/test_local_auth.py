"""Opt-in loopback administrator sessions, using only an in-process mock Runtime."""
from copy import deepcopy
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from emo_web_panel.app import COOKIE, create_app
from emo_web_panel.auth import AuthStore
from emo_web_panel.config import Config

ORIGIN = 'http://127.0.0.1:8780'
BOOTSTRAP = {'Origin': ORIGIN, 'X-Local-Session': '1'}


class LocalConfigTests(unittest.TestCase):
    def test_default_remains_password_mode(self):
        self.assertFalse(Config(Path('unused')).local_no_auth)

    def test_local_configuration_is_explicit_loopback_only(self):
        for host in ('127.0.0.1', '::1'):
            self.assertTrue(Config(Path('unused'), host=host, local_no_auth=True).local_no_auth)
        for changes in ({'host': '0.0.0.0'}, {'host': 'localhost'}, {'host': '192.0.2.1'},
                        {'host': '127.0.0.2'}, {'port': 0}, {'port': 65536},
                        {'preview': True}, {'allowed_origins': ('https://example.org',)}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                Config(Path('unused'), local_no_auth=True, **changes)

    def test_cli_disables_proxy_headers_and_warns_about_admin_access(self):
        from emo_web_panel.main import main
        with tempfile.TemporaryDirectory() as directory, \
                patch('sys.argv', ['emo-web-panel', '--data-dir', directory, 'serve',
                                   '--mode', 'mock', '--local-no-auth']), \
                patch('uvicorn.run') as run, patch('builtins.print') as output:
            main()
        self.assertEqual(run.call_args.kwargs['proxy_headers'], False)
        self.assertEqual(run.call_args.kwargs['host'], '127.0.0.1')
        self.assertTrue(output.called)
        warning = ' '.join(str(arg) for call in output.call_args_list for arg in call.args).lower()
        self.assertTrue('admin' in warning or '管理员' in warning, warning)


class LocalStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'auth.sqlite'

    def test_no_account_stable_separate_identity_and_process_scoped_tokens(self):
        store = AuthStore(self.path, local_no_auth=True)
        with patch.object(store, 'create_user', side_effect=AssertionError('No account bootstrap')):
            token = store.login_local()
        who = store.session(token)
        self.assertEqual(who['authMode'], 'local')
        self.assertEqual(who['role'], 'admin')
        self.assertEqual(set(who['permissions']), {'view', 'control', 'admin'})
        self.assertTrue(who['userId'].startswith('local:'))
        self.assertEqual(store.users(), [])
        again = store.session(store.login_local())
        self.assertEqual(again['userId'], who['userId'])
        restarted = AuthStore(self.path, local_no_auth=True)
        self.assertIsNone(restarted.session(token))
        self.assertEqual(restarted.session(restarted.login_local())['userId'], who['userId'])
        disabled = AuthStore(self.path)
        self.assertIsNone(disabled.session(token))
        with self.assertRaises(ValueError):
            disabled.login_local()

    def test_expiry_logout_and_regular_user_isolation(self):
        store = AuthStore(self.path, ttl=60, local_no_auth=True)
        token = store.login_local()
        who = store.session(token)
        store.create_user(who['username'], 'safe-test-password', 'viewer')
        ordinary = store.login(who['username'], 'safe-test-password')
        self.assertNotEqual(store.session(ordinary)['userId'], who['userId'])
        self.assertEqual(store.session(ordinary)['role'], 'viewer')
        self.assertEqual(store.session(token)['role'], 'admin')
        store.update_user(who['username'], password='replacement-password')
        self.assertIsNotNone(store.session(token))
        self.assertIsNone(store.session(ordinary))
        ordinary = store.login(who['username'], 'replacement-password')
        with patch('emo_web_panel.auth.time.time', return_value=time.time() + 61):
            self.assertIsNone(store.session(token))
        store.logout(token)
        self.assertIsNone(store.session(token))
        self.assertIsNotNone(store.session(ordinary))
        self.assertIsNotNone(AuthStore(self.path).session(ordinary))
        fresh = store.login_local()
        store.delete_user(who['username'])
        self.assertIsNotNone(store.session(fresh))
        self.assertIsNone(store.session(ordinary))


class LocalHTTPTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.config = Config(Path(self.tmp.name), local_no_auth=True)
        self.app = create_app(self.config)
        self.client = self.enter_client(self.app)

    def enter_client(self, app, **kwargs):
        client = TestClient(app, base_url=ORIGIN, client=('127.0.0.1', 12345), **kwargs)
        client.__enter__()
        self.addCleanup(client.__exit__, None, None, None)
        return client

    def login(self):
        response = self.client.post('/api/local/session', headers=BOOTSTRAP)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json(), {'Origin': ORIGIN, 'X-CSRF-Token': response.json()['csrf']}

    def view(self):
        _, headers = self.login()
        response = self.client.post('/api/views', headers=headers,
                                    json={'panelId': 'demo-panel', 'runId': 'demo-run'})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()['id'], headers

    def test_first_start_no_users_get_does_not_create_session_and_bootstrap_reuses_cookie(self):
        self.assertEqual(self.app.state.auth.users(), [])
        for path in ('/api/me', '/api/runs'):
            self.assertEqual(self.client.get(path).status_code, 401)
        self.assertNotIn(COOKIE, self.client.cookies)
        response = self.client.post('/api/local/session', headers=BOOTSTRAP)
        self.assertEqual(response.status_code, 200, response.text)
        cookie = response.headers['set-cookie'].lower()
        self.assertIn('httponly', cookie)
        self.assertIn('samesite=strict', cookie)
        self.assertIn('max-age=', cookie)
        me = response.json()
        self.assertEqual((me['authMode'], me['role']), ('local', 'admin'))
        self.assertEqual(self.app.state.auth.users(), [])
        self.assertEqual(self.client.get('/api/admin/users').status_code, 200)
        token = self.client.cookies.get(COOKIE)
        again, _ = self.login()
        self.assertEqual(again['csrf'], me['csrf'])
        self.assertEqual(again['userId'], me['userId'])
        self.assertEqual(self.client.cookies.get(COOKIE), token)
        self.assertEqual(self.app.state.runtime.control_count, 0)

    def test_bootstrap_requires_explicit_header_and_complete_same_origin(self):
        for headers in ({}, {'Origin': ORIGIN}, {'X-Local-Session': '1'},
                        {**BOOTSTRAP, 'X-Local-Session': '0'},
                        {**BOOTSTRAP, 'Origin': 'null'},
                        {**BOOTSTRAP, 'Origin': ORIGIN + '/'},
                        {**BOOTSTRAP, 'Origin': 'https://127.0.0.1:8780'},
                        {**BOOTSTRAP, 'Origin': 'http://localhost:8780'}):
            with self.subTest(headers=headers):
                self.assertEqual(self.client.post('/api/local/session', headers=headers).status_code, 403)
        self.assertEqual(self.app.state.auth.users(), [])
        self.assertNotIn(COOKIE, self.client.cookies)

    def test_csrf_logout_and_renewal(self):
        me, headers = self.login()
        old = self.client.cookies.get(COOKIE)
        for invalid in ({'Origin': ORIGIN}, {**headers, 'X-CSRF-Token': 'wrong'},
                        {'X-CSRF-Token': me['csrf']}):
            self.assertEqual(self.client.post('/api/logout', headers=invalid).status_code, 403)
        self.assertEqual(self.client.post('/api/logout', headers=headers).status_code, 200)
        self.assertIsNone(self.app.state.auth.session(old))
        self.assertEqual(self.client.get('/api/me').status_code, 401)
        renewed, _ = self.login()
        self.assertEqual(renewed['userId'], me['userId'])
        self.assertNotEqual(renewed['csrf'], me['csrf'])

    def test_expired_http_session_requires_fresh_explicit_bootstrap(self):
        me, _ = self.login()
        old = self.client.cookies.get(COOKIE)
        with self.app.state.auth.db() as db:
            db.execute('UPDATE sessions SET expires=?', (time.time() - 1,))
        response = self.client.get('/api/me')
        self.assertEqual(response.status_code, 401)
        self.assertNotIn('set-cookie', response.headers)
        renewed, _ = self.login()
        self.assertEqual(renewed['userId'], me['userId'])
        self.assertNotEqual(self.client.cookies.get(COOKIE), old)

    def test_shared_cookie_csrf_recovery_keeps_current_session_and_requires_explicit_save(self):
        old_me, old_headers = self.login()
        old_cookie = self.client.cookies.get(COOKIE)
        base = '/api/editor/configured'
        context = self.client.get(base + '/context').json()
        # Persist the starting draft: an empty editor otherwise generates fresh page IDs on each read.
        seeded = self.client.put(base + '/draft', headers=old_headers, json={
            'projectId': context['runtime']['projectId'], 'presentation': context['draft']['presentation'],
            'expectedDraftRevision': context['draft']['draftRevision']})
        self.assertEqual(seeded.status_code, 200, seeded.text)
        context = self.client.get(base + '/context').json()
        presentation = deepcopy(context['draft']['presentation'])
        presentation['pages'][presentation['defaultPageId']]['name'] = 'Cross-tab draft'
        body = {'projectId': context['runtime']['projectId'], 'presentation': presentation,
                'expectedDraftRevision': context['draft']['draftRevision']}
        with self.app.state.auth.db() as db:
            db.execute('UPDATE sessions SET expires=?', (time.time() - 1,))
        # A second tab reenters; TestClient's real cookie jar now sends its fresh cookie
        # while the first tab still supplies its original cached CSRF header.
        current_me, _ = self.login()
        current_cookie = self.client.cookies.get(COOKIE)
        self.assertNotEqual(current_cookie, old_cookie)
        self.assertNotEqual(current_me['csrf'], old_me['csrf'])
        self.assertEqual(current_me['userId'], old_me['userId'])
        for headers in (old_headers, {'Origin': ORIGIN}):
            denied = self.client.put(base + '/draft', headers=headers, json=body)
            self.assertEqual(denied.status_code, 403, denied.text)
            self.assertEqual(denied.json(), {'detail': 'CSRF denied', 'code': 'CSRF_MISMATCH'})
            self.assertNotIn('set-cookie', denied.headers)
            self.assertEqual(self.client.get(base + '/context').json()['draft'], context['draft'])
        self.assertEqual(self.client.cookies.get(COOKIE), current_cookie)
        self.assertIsNotNone(self.app.state.auth.session(current_cookie))
        adopted_me, adopted_headers = self.login()
        self.assertEqual(adopted_me['csrf'], current_me['csrf'])
        self.assertEqual(self.client.cookies.get(COOKIE), current_cookie)
        saved = self.client.put(base + '/draft', headers=adopted_headers, json=body)
        self.assertEqual(saved.status_code, 200, saved.text)
        self.assertEqual(saved.json()['presentation'], presentation)
        self.assertEqual(self.app.state.runtime.control_count, 0)
        self.assertEqual(self.app.state.runtime.output_config['sources'], {})
        self.assertEqual(self.app.state.runtime.output_receipts, {})

    def test_origin_and_permission_denials_never_report_csrf_recovery_code(self):
        _, headers = self.login()
        base = '/api/editor/configured'
        context = self.client.get(base + '/context').json()
        body = {'projectId': context['runtime']['projectId'],
                'presentation': context['draft']['presentation']}
        wrong_origin = self.client.put(base + '/draft', headers={**headers, 'Origin': 'https://evil.example'}, json=body)
        self.assertEqual(wrong_origin.status_code, 403)
        self.assertNotIn('code', wrong_origin.json())
        self.app.state.auth.create_user('viewer', 'safe-test-password', 'viewer')
        viewer = self.client.post('/api/session', headers={'Origin': ORIGIN},
                                  json={'username': 'viewer', 'password': 'safe-test-password'})
        self.assertEqual(viewer.status_code, 200, viewer.text)
        denied = self.client.put(base + '/draft', headers=headers, json=body)
        self.assertEqual(denied.status_code, 403)
        self.assertEqual(denied.json(), {'detail': 'Permission denied'})

    def test_disabling_local_mode_rejects_old_local_cookie(self):
        self.login()
        token = self.client.cookies.get(COOKIE)
        normal = self.enter_client(create_app(Config(Path(self.tmp.name))))
        normal.cookies.set(COOKIE, token)
        self.assertEqual(normal.get('/api/me').status_code, 401)
        self.assertEqual(normal.post('/api/local/session', headers=BOOTSTRAP).status_code, 404)
        self.assertEqual(normal.get('/api/admin/users').status_code, 401)

    def test_boundary_is_global_including_health_and_static_entry(self):
        attacks = [
            {'Host': 'evil.example:8780'}, {'Host': '127.0.0.1:9999'}, {'Host': '127.0.0.1'},
            {'Host': '127.0.0.2:8780'}, {'Origin': 'https://evil.example'},
            {'Forwarded': 'for=127.0.0.1'}, {'X-Forwarded-For': '127.0.0.1'},
            {'X-Forwarded-Host': '127.0.0.1:8780'}, {'X-Forwarded-Proto': 'http'},
            {'X-Real-IP': '127.0.0.1'}, {'Via': 'proxy'},
            {'Sec-Fetch-Site': 'cross-site'}, {'Sec-Fetch-Site': 'same-site'},
            [('Host', '127.0.0.1:8780'), ('Host', 'evil.example:8780')],
            [('Origin', ORIGIN), ('Origin', ORIGIN)],
        ]
        for headers in attacks:
            for path in ('/api/health', '/api/me', '/editor'):
                with self.subTest(path=path, headers=headers):
                    self.assertEqual(self.client.get(path, headers=headers).status_code, 403)
        self.assertEqual(self.client.get('/api/health', headers={'Host': 'localhost:8780'}).status_code, 200)
        remote = TestClient(self.app, base_url=ORIGIN, client=('192.0.2.10', 12345))
        self.assertEqual(remote.get('/api/health').status_code, 403)
        self.assertEqual(remote.post('/api/local/session', headers=BOOTSTRAP).status_code, 403)

    def test_password_mode_remains_default_and_existing_users_work_in_local_mode(self):
        self.app.state.auth.create_user('viewer', 'safe-test-password', 'viewer')
        response = self.client.post('/api/session', headers={'Origin': ORIGIN},
                                    json={'username': 'viewer', 'password': 'safe-test-password'})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['role'], 'viewer')
        self.assertEqual(self.client.get('/api/admin/users').status_code, 403)
        normal = self.enter_client(create_app(Config(Path(self.tmp.name))))
        self.assertEqual(normal.post('/api/local/session', headers=BOOTSTRAP).status_code, 404)
        self.assertEqual(normal.get('/api/me').status_code, 401)
        self.assertEqual(normal.post('/api/session', json={
            'username': 'viewer', 'password': 'safe-test-password'}).status_code, 200)
        self.assertEqual(normal.get('/api/admin/users').status_code, 403)

    def test_restart_preserves_saved_draft_not_session_and_never_writes_runtime(self):
        me, headers = self.login()
        token = self.client.cookies.get(COOKIE)
        base = '/api/editor/configured'
        context = self.client.get(base + '/context').json()
        presentation = deepcopy(context['draft']['presentation'])
        presentation['pages'][presentation['defaultPageId']]['name'] = 'Saved local page'
        saved = self.client.put(base + '/draft', headers=headers, json={
            'projectId': context['runtime']['projectId'], 'presentation': presentation})
        self.assertEqual(saved.status_code, 200, saved.text)
        self.assertEqual(self.app.state.runtime.control_count, 0)
        self.assertEqual(self.app.state.runtime.output_config['sources'], {})
        self.assertEqual(self.app.state.runtime.output_receipts, {})
        restarted = self.enter_client(create_app(self.config))
        restarted.cookies.set(COOKIE, token)
        self.assertEqual(restarted.get('/api/me').status_code, 401)
        restarted.cookies.clear()
        new = restarted.post('/api/local/session', headers=BOOTSTRAP)
        self.assertEqual(new.status_code, 200, new.text)
        self.assertEqual(new.json()['userId'], me['userId'])
        self.assertEqual(restarted.get(base + '/context').json()['draft'], saved.json())

    def test_websocket_requires_same_origin_and_unforwarded_loopback_peer(self):
        vid, _ = self.view()
        for headers in ({}, {'Origin': 'https://evil.example'},
                        {'Origin': ORIGIN, 'Host': 'evil.example:8780'},
                        {'Origin': ORIGIN, 'X-Forwarded-For': '127.0.0.1'},
                        {'Origin': ORIGIN, 'Sec-Fetch-Site': 'same-site'}):
            with self.subTest(headers=headers), self.assertRaises(WebSocketDisconnect) as denied:
                with self.client.websocket_connect('ws://127.0.0.1:8780/ws/views/' + vid, headers=headers):
                    self.fail('Untrusted WebSocket accepted')
            self.assertEqual(denied.exception.code, 1008)
        remote = TestClient(self.app, base_url=ORIGIN, client=('192.0.2.10', 12345),
                            cookies={COOKIE: self.client.cookies.get(COOKIE)})
        with self.assertRaises(WebSocketDisconnect):
            with remote.websocket_connect('ws://127.0.0.1:8780/ws/views/' + vid, headers={'Origin': ORIGIN}):
                self.fail('Remote WebSocket accepted')

    def test_live_websocket_revokes_after_logout_and_releases_view(self):
        vid, headers = self.view()
        with self.client.websocket_connect('ws://127.0.0.1:8780/ws/views/' + vid, headers={'Origin': ORIGIN}) as ws:
            self.assertEqual(ws.receive_json()['type'], 'result')
            self.assertEqual(self.client.post('/api/logout', headers=headers).status_code, 200)
            with self.assertRaises(WebSocketDisconnect) as closed:
                # At most one already-produced message can precede the next session check.
                for _ in range(3):
                    ws.receive_json()
            self.assertEqual(closed.exception.code, 1008)
        self.assertNotIn(vid, self.app.state.views.views)

    def test_live_websocket_revokes_after_session_expiry(self):
        vid, _ = self.view()
        with self.client.websocket_connect('ws://127.0.0.1:8780/ws/views/' + vid,
                                          headers={'Origin': ORIGIN}) as ws:
            self.assertEqual(ws.receive_json()['type'], 'result')
            with self.app.state.auth.db() as db:
                db.execute('UPDATE sessions SET expires=?', (time.time() - 1,))
            with self.assertRaises(WebSocketDisconnect) as closed:
                for _ in range(3):
                    ws.receive_json()
            self.assertEqual(closed.exception.code, 1008)
        self.assertNotIn(vid, self.app.state.views.views)

    def test_ipv6_and_default_port_authorities(self):
        for host, port, origin, peer in (('::1', 8780, 'http://[::1]:8780', '::1'),
                                         ('127.0.0.1', 80, 'http://127.0.0.1', '127.0.0.1')):
            with self.subTest(origin=origin), tempfile.TemporaryDirectory() as directory:
                app = create_app(Config(Path(directory), host=host, port=port, local_no_auth=True))
                with TestClient(app, base_url=origin, client=(peer, 12345)) as client:
                    response = client.post('/api/local/session', headers={
                        'Origin': origin, 'X-Local-Session': '1'})
                    self.assertEqual(response.status_code, 200, response.text)


if __name__ == '__main__':
    unittest.main()
