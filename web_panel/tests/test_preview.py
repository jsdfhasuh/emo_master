"""Actual preview CLI: loopback, official context, ready token and read-only session."""
import json
from pathlib import Path
import queue
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request

from emo_web_panel.config import Config
from emo_web_panel.main import validate_preview_context

CONTRACT = Path(__file__).resolve().parents[1] / 'contracts' / 'panel-contract-v1'


class PreviewTests(unittest.TestCase):
    def launch(self, *extra):
        return subprocess.Popen([sys.executable, '-m', 'emo_web_panel.main', 'preview',
            '--package', str(CONTRACT / 'sample-panels' / 'demo.emoweb'),
            '--context', str(CONTRACT / 'fixtures' / 'preview-fixtures.json'),
            '--host', '127.0.0.1', '--port', '0', '--json', '--no-control', *extra],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    def test_preview_cli(self):
        process = self.launch()
        try:
            lines = queue.Queue()
            threading.Thread(target=lambda: lines.put(process.stdout.readline()), daemon=True).start()
            line = lines.get(timeout=20)
            if not line:
                self.fail('Preview exited before ready: ' + process.stderr.read())
            ready = json.loads(line)
            self.assertEqual(set(ready), {'event', 'protocolVersion', 'url'})
            self.assertEqual(ready['event'], 'ready')
            self.assertEqual(ready['protocolVersion'], '1')
            parsed = urllib.parse.urlsplit(ready['url'])
            self.assertEqual(parsed.hostname, '127.0.0.1')
            self.assertGreater(parsed.port, 0)
            base = f'{parsed.scheme}://{parsed.netloc}'
            token = urllib.parse.parse_qs(parsed.query)['token'][0]
            self.assertGreaterEqual(len(token), 32)
            with urllib.request.urlopen(base + '/api/health', timeout=3) as response:
                health = json.load(response)
            self.assertEqual(health['mode'], 'mock')
            import http.cookiejar
            jar = http.cookiejar.CookieJar()
            browser = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
            def request(path, body=None, csrf=None):
                headers = {'Content-Type': 'application/json', 'Origin': base}
                if csrf:
                    headers['X-CSRF-Token'] = csrf
                req = urllib.request.Request(base+path, json.dumps(body).encode() if body is not None else None,
                                             headers=headers)
                return browser.open(req, timeout=3)
            with request('/api/preview/session', {'token': token}) as response:
                me = json.load(response)
            self.assertEqual(me['role'], 'viewer')
            self.assertNotIn('control', me['permissions'])
            with self.assertRaises(urllib.error.HTTPError) as error:
                request('/api/preview/session', {'token': token})
            self.assertIn(error.exception.code, {401, 403})
            with request('/api/panels') as response:
                panels = json.load(response)
            self.assertEqual(len(panels), 1)
            with request('/api/runs') as response:
                runs = json.load(response)
            self.assertEqual(runs[0]['id'], 'demo-j1')
            with request('/api/views', {'panelId': panels[0]['id'], 'runtimeId': 'configured', 'runId': 'demo-j1'}, me['csrf']) as response:
                self.assertIn('id', json.load(response))
            for path, body in [('/api/runs/start', {}), ('/api/runs/demo-j1/stop', {}),
                               ('/api/packages', {}), ('/api/packages/anything/activate', {}),
                               ('/api/admin/runtimes', {})]:
                with self.assertRaises(urllib.error.HTTPError) as error:
                    request(path, body, me['csrf'])
                self.assertEqual(error.exception.code, 403)
        finally:
            process.terminate()
            try:
                process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate()

    def test_context_required_and_loopback_only(self):
        command = [sys.executable, '-m', 'emo_web_panel.main', 'preview', '--package', 'absent']
        result = subprocess.run(command, capture_output=True, text=True, timeout=5)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, '')
        result = self.launch('--host', '0.0.0.0')
        out, err = result.communicate(timeout=5)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(out, '')
        with self.assertRaises(ValueError):
            Config(Path('.'), preview=True, host='0.0.0.0', allowed_origins=('http://example.test',))

    def test_unavailable_runtime_never_falls_back_or_announces_ready(self):
        value = json.loads((CONTRACT / 'fixtures' / 'preview-runtime.json').read_text())
        # Reserve then close a loopback port: no production endpoint is involved.
        import socket
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        value['runtime']['target'] = f'127.0.0.1:{port}'
        value['runtime']['timeoutSeconds'] = 0.2
        with tempfile.TemporaryDirectory() as directory:
            context = Path(directory) / 'runtime-context.json'
            context.write_text(json.dumps(value))
            process = self.launch('--context', str(context))
            stdout, stderr = process.communicate(timeout=10)
            self.assertNotEqual(process.returncode, 0)
            self.assertEqual(stdout, '')
            self.assertNotIn('"event": "ready"', stdout)
            self.assertRegex(stderr, r'StatusCode\.(UNAVAILABLE|DEADLINE_EXCEEDED)')

    def test_context_identity_validation(self):
        value = json.loads((CONTRACT / 'fixtures' / 'preview-fixtures.json').read_text())
        manifest = {key: value[key] for key in ('projectId', 'packageRevision', 'historyDefinitionRevision')}
        path = CONTRACT / 'fixtures' / 'preview-fixtures.json'
        self.assertEqual(validate_preview_context(path, manifest).mode, 'fixtures')
        for key in manifest:
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate_preview_context(path, {**manifest, key: 'different'})
        with tempfile.TemporaryDirectory() as directory:
            context = Path(directory) / 'context.json'
            for invalid in ({**value, 'readOnly': False}, {**value, 'mode': 'runtime'},
                            {**value, 'runtime': {'target': '127.0.0.1:1'}}):
                context.write_text(json.dumps(invalid))
                with self.assertRaises(ValueError):
                    validate_preview_context(context, manifest)


class FixtureTests(unittest.IsolatedAsyncioTestCase):
    async def test_fixture_provider_is_explicit_and_read_only(self):
        from emo_panel_protocol.preview import PreviewContext
        from emo_web_panel.fixture_runtime import FixtureRuntime
        context = PreviewContext.model_validate_json((CONTRACT / 'fixtures' / 'preview-fixtures.json').read_bytes())
        runtime = FixtureRuntime(context)
        await runtime.start()
        self.assertEqual((await runtime.info())['projectId'], context.projectId)
        self.assertEqual((await runtime.runs())[0]['allowedActions'], [])
        result = (await runtime.snapshot('demo-j1'))[0]
        self.assertEqual(result['sources']['count']['valueJson'], '12')
        self.assertEqual(result['closedResult']['identity']['jobId'], 'demo-j1')
        self.assertTrue(result['synthetic'])
        page = await runtime.history('records', 1, 20)
        self.assertEqual(page['page'], 0)
        self.assertTrue(page['stableRowIdentity'])
        image_ref = page['rows'][0]['imageRefs']['record-image']['imageRef']
        data, mime, status = await runtime.history_image(image_ref=image_ref)
        self.assertEqual(status, 'AVAILABLE')
        self.assertEqual(mime, 'image/png')
        self.assertGreater(len(data), 0)
        self.assertEqual((await runtime.history_image(image_ref='unknown'))[2], 'MISSING')
        with self.assertRaises(ValueError):
            await runtime.control('start', '', {})
        runtime_context = PreviewContext.model_validate_json((CONTRACT / 'fixtures' / 'preview-runtime.json').read_bytes())
        with self.assertRaises(ValueError):
            FixtureRuntime(runtime_context)
        await runtime.close()
