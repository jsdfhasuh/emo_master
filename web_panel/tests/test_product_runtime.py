"""Opt-in full owning Runtime + gateway RPC integration on synthetic local data.

Run with EMO_WEB_PRODUCT_RUNTIME=1 and Runtime dependencies installed in
EMO_WEB_PRODUCT_PYTHON (defaults to the test interpreter). This intentionally
uses a separate Runtime process: the independently installed gateway needs no
Qt, vision stack, application source imports, or device ownership.
"""
import asyncio
import json
import os
from pathlib import Path
import queue
import shutil
import time
import subprocess
import sys
import tempfile
import threading
import unittest

ROOT = Path(__file__).resolve().parents[2]
CONTRACT = ROOT / 'web_panel/contracts/panel-contract-v1'


def run_product_server(root, *, continuous=False):
    # Imports exclusively in the Runtime subprocess, not the gateway process.
    import contextlib
    import faulthandler
    import shutil
    import sqlite3
    from emo_master.apps.runtime.grpc_server.service import RuntimeService
    from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb
    from emo_master.apps.runtime.main import createRuntimeServer

    root = Path(root)
    source = CONTRACT / 'sample-panels/source'
    if continuous:
        # Only the synthetic template's normal production settings change; the
        # contract package, source/scope definitions and owning Runtime do not.
        from emo_master.core.project.models import ProjectDocument
        payload = json.loads((source / 'demo.emoproj').read_text(encoding='utf-8'))
        if payload['devices']['definitions'] or any(node.get('operatorId') for workflow in payload['workflows'].values() for node in workflow['nodes']):
            raise AssertionError('Browser fixture must contain no devices or executable operators')
        payload['production'].update(autoStart=False, mode='continuous', cycleIntervalMs=100)
        document = ProjectDocument.model_validate(payload)
        (root / 'project.emoproj').write_text(document.model_dump_json(), encoding='utf-8')
    else:
        shutil.copyfile(source / 'demo.emoproj', root / 'project.emoproj')
    shutil.copyfile(source / 'demo.png', root / 'demo.png')
    (root / 'demo-images').mkdir()
    shutil.copyfile(source / 'demo.png', root / 'demo-images/reference.png')
    with sqlite3.connect(root / 'demo-history.sqlite') as db:
        db.execute('CREATE TABLE records (id INTEGER PRIMARY KEY, verdict TEXT, image TEXT)')
        db.execute('INSERT INTO records VALUES (1, ?, ?)', ('OK', 'reference.png'))
    with contextlib.redirect_stdout(sys.stderr):
        service = RuntimeService(dbPath=root / 'runtime.db', workspaceRoot=root / 'jobs', productionMode=continuous)
        loaded = service.LoadProject(pb.LoadProjectRequest(project_path=str(root / 'project.emoproj')), None)
        if not loaded.ok:
            service.close()
            raise RuntimeError(loaded.message)
        server, port, _ = createRuntimeServer(port=0, runtimeService=service)
        server.start()
    # Diagnostic watchdog only: capture all actual owning Runtime threads if
    # a control call wedges. It neither interrupts nor changes Runtime behavior.
    faulthandler.enable(file=sys.stderr, all_threads=True)
    faulthandler.dump_traceback_later(15, repeat=True, file=sys.stderr)
    try:
        print(json.dumps({'event':'ready', 'address':f'127.0.0.1:{port}'}), flush=True)
        # Do not wait on a piped stdin here. On Windows the spawned worker
        # inherits that synchronous pipe; an outstanding parent read can block
        # child interpreter stdio initialization (CPython bpo-34780), leaving
        # multiprocessing's bootstrap pipe flush stuck before any Job starts.
        stop_file = root / 'stop-requested'
        while not stop_file.exists():
            time.sleep(.05)
    finally:
        faulthandler.cancel_dump_traceback_later()
        with contextlib.redirect_stdout(sys.stderr):
            server.stop(0).wait()
            service.close()


@unittest.skipUnless(os.environ.get('EMO_WEB_PRODUCT_RUNTIME') == '1',
                     'Opt-in full owning Runtime integration; requires its separate dependencies')
class ProductRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from emo_web_panel.runtime_client import GrpcRuntimeClient
        import emo_panel_protocol
        self.observations = [{'operation': 'gateway_environment', 'python': sys.executable,
            'protocolVersion': emo_panel_protocol.__version__, 'protocolPath': emo_panel_protocol.__file__}]
        self.stdout_lines = []
        self.directory = tempfile.TemporaryDirectory(prefix='web-product-runtime-')
        self.log = open(Path(self.directory.name) / 'runtime.log', 'w+')
        env = os.environ.copy()
        # Deliberately exclude gateway and SDK from the execution host PYTHONPATH.
        env['PYTHONPATH'] = str(ROOT / 'src')
        env['PYTHONDONTWRITEBYTECODE'] = '1'
        env['QT_QPA_PLATFORM'] = 'offscreen'
        self.process = subprocess.Popen([os.environ.get('EMO_WEB_PRODUCT_PYTHON', sys.executable),
            str(Path(__file__).resolve()), getattr(self, 'server_flag', '--runtime-server'), self.directory.name], cwd=ROOT,
            env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=self.log, text=True)
        ready = queue.Queue()
        threading.Thread(target=lambda: ready.put(self.process.stdout.readline()), daemon=True).start()
        try:
            line = await asyncio.to_thread(ready.get, True, 40)
            data = json.loads(line)
            if data.get('event') != 'ready':
                raise ValueError('Unexpected Runtime startup output')
            # Drain diagnostics after the ready line so worker stdout cannot
            # block the Runtime behind an unread pipe. Retain a bounded tail.
            def drain_stdout():
                for line in self.process.stdout:
                    self.stdout_lines.append(line)
                    if len(self.stdout_lines) > 1000:
                        del self.stdout_lines[0]
            self.stdout_thread = threading.Thread(target=drain_stdout, daemon=True)
            self.stdout_thread.start()
            self.client = GrpcRuntimeClient(data['address'])
            await self.client.start()
        except Exception:
            self.process.kill()
            self.process.wait(timeout=10)
            self.log.flush()
            self.log.seek(0)
            diagnostic = self.log.read()
            self.preserve_evidence()
            self.log.close()
            self.directory.cleanup()
            raise AssertionError('Owning Runtime subprocess did not start:\n' + diagnostic)

    async def asyncTearDown(self):
        await self.client.close()
        if self.process.poll() is None:
            (Path(self.directory.name) / 'stop-requested').touch()
            try:
                await asyncio.to_thread(self.process.wait, timeout=20)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=10)
        self.stdout_thread.join(timeout=2)
        self.preserve_evidence()
        self.process.stdout.close()
        self.log.close()
        self.directory.cleanup()

    def preserve_evidence(self):
        destination = Path(os.environ.get('EMO_WEB_PRODUCT_EVIDENCE_DIR',
            str(ROOT / 'web_panel/test-results/product-runtime'))) / self._testMethodName
        # Nested software Runtime diagnostics can exceed Windows MAX_PATH.
        destination = destination.resolve()
        if os.name == 'nt':
            destination = Path('\\\\?\\' + str(destination))
        destination.mkdir(parents=True, exist_ok=True)
        self.log.flush()
        root = Path(self.directory.name)
        # Only synthetic test diagnostics; never production databases or images.
        remaining = 8 * 1024 * 1024
        for path in sorted(root.rglob('*')):
            if path.is_file() and path.suffix.lower() in {'.log','.jsonl','.json'}:
                size = path.stat().st_size
                if size <= min(remaining, 2 * 1024 * 1024):
                    output = destination / path.relative_to(root)
                    output.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(path, output)
                    remaining -= size
        (destination / 'stdout.log').write_text(''.join(self.stdout_lines), encoding='utf-8')
        (destination / 'gateway-observations.json').write_text(
            json.dumps(self.observations, ensure_ascii=False, indent=2), encoding='utf-8')
        print(f'Product Runtime evidence: {destination}', file=sys.stderr)

    async def reconcile_start(self, accepted, body, *, timeout=60):
        """One Start only. Bounded, read-only correlation after uncertain reply."""
        import grpc
        deadline = time.monotonic() + timeout
        self.observations.append({'operation':'start','reply':accepted})
        while accepted['outcome'] == 'UNKNOWN' and time.monotonic() < deadline:
            if self.process.poll() is not None:
                self.fail('Runtime subprocess exited while reconciling Start')
            await asyncio.sleep(.2)
            try:
                accepted = await self.client.lookup_start(body['requestId'], body['runtimeInstanceId'])
                self.observations.append({'operation':'lookup_start','reply':accepted})
            except grpc.RpcError as error:
                # A read deadline is still inconclusive, never authorization to
                # repeat Start. Identity/semantic failures are not caught here.
                self.observations.append({'operation':'lookup_start','transportError':str(error)})
        return accepted

    async def test_actual_runtime_history_without_jobs_and_image_ownership(self):
        info = await self.client.info()
        self.assertEqual(info['projectId'], 'panel-contract-demo')
        self.assertEqual(await self.client.runs(), [])
        logs = await self.client.query_runtime_logs({'runtimeInstanceId': info['runtimeInstanceId']})
        self.observations.append({'operation': 'query_runtime_logs_without_job', 'reply': logs})
        self.assertTrue(logs['ok'], logs)
        self.assertEqual(logs['runtimeInstanceId'], info['runtimeInstanceId'])
        self.assertTrue(logs['upperEventId'].isdecimal())
        self.assertTrue(logs['nextEventId'].isdecimal())
        self.assertIsInstance(logs['hasMore'], bool)
        self.assertTrue(all(event['originApp'] == 'runtime' for event in logs['events']))
        self.assertEqual(await self.client.runs(), [])
        context = {key: info[key] for key in ('runtimeInstanceId','projectId','historyDefinitionRevision')}
        page = await self.client.history('records',1,20,context=context,sort='id')
        self.assertTrue(page['stableRowIdentity'])
        self.assertEqual(page['rows'][0]['values'], [1, 'OK'])
        ref = page['rows'][0]['imageRefs']['record-image']['imageRef']
        self.assertNotIn('reference.png', ref)
        image, mime, status = await self.client.history_image(image_ref=ref, context=context)
        self.assertEqual(status, 'AVAILABLE')
        self.assertEqual(mime, 'image/png')
        fixed, _, _ = await self.client.history_image(component_id='fixed-image', context=context)
        self.assertEqual(image, fixed)
        total = await self.client.history('total',1,20,context=context,kind='aggregate')
        self.assertEqual(total['value'], 1)
        self.assertEqual(await self.client.runs(), [])

    async def test_actual_runtime_start_without_external_capture_lookup_stop_and_no_replay(self):
        from emo_web_panel.runtime_client import RuntimeRejected
        info = await self.client.info()
        workflows = await self.client.workflows()
        workflow = next(w for w in workflows if w['id'] == 'main')
        self.assertTrue(workflow['canStart'])
        body = {'runtimeInstanceId':info['runtimeInstanceId'],'projectId':info['projectId'],
            'configurationRevision':info['configurationRevision'],'workflowId':'main',
            'requestId':'web-product-runtime-start-1','inputs':{}}
        accepted = await self.client.control('start','main',body)
        accepted = await self.reconcile_start(accepted, body)
        self.assertEqual(accepted['outcome'],'ACCEPTED', accepted)
        job = accepted['runId']
        lookup = await self.client.lookup_start(body['requestId'], info['runtimeInstanceId'])
        self.assertEqual(lookup['runId'],job)
        # This fixture has Qt pages, but no external requests saved by Web.
        # Independent Web Start must not implicitly opt into Qt capture.
        state = await self.client.run_state(job)
        self.observations.append({'operation': 'run_state_without_external_requests', 'reply': state})
        self.assertFalse(state['captureEnabled'])
        with self.assertRaises(RuntimeRejected) as unavailable:
            await self.client.snapshot(job)
        self.assertEqual(unavailable.exception.code, 'CAPTURE_UNAVAILABLE')
        logs = await self.client.query_runtime_logs({'runtimeInstanceId': info['runtimeInstanceId'],
            'jobId': job, 'component': 'execution', 'limit': 1000})
        self.observations.append({'operation': 'query_runtime_logs_for_actual_job', 'reply': logs})
        self.assertTrue(logs['ok'], logs)
        self.assertEqual(logs['runtimeInstanceId'], info['runtimeInstanceId'])
        self.assertTrue(logs['events'], 'Actual Job execution logs were not persisted or queryable')
        ids = [int(event['eventId']) for event in logs['events']]
        self.assertEqual(ids, sorted(set(ids)))
        self.assertGreater(ids[0], 0)
        self.assertLessEqual(ids[-1], int(logs['upperEventId']))
        for event in logs['events']:
            self.assertEqual(event['jobId'], job)
            self.assertEqual(event['projectId'], info['projectId'])
            self.assertEqual(event['workflowId'], 'main')
            self.assertEqual(event['originApp'], 'runtime')
            self.assertEqual(event['component'], 'execution')
            self.assertEqual(event['runtimeInstanceId'], info['runtimeInstanceId'])
            self.assertTrue(event['sequence'].isdecimal())
            self.assertTrue(event['timestampMs'].isdecimal())
        unfiltered = await self.client.query_runtime_logs({'runtimeInstanceId': info['runtimeInstanceId'], 'limit': 1000})
        self.observations.append({'operation': 'query_runtime_logs_across_jobs', 'reply': unfiltered})
        self.assertTrue(unfiltered['ok'], unfiltered)
        self.assertTrue(any(event['jobId'] == job for event in unfiltered['events']))
        state = await self.client.run_state(job)
        stop = await self.client.control('stop',job,body | {'runGroupId':state['runGroupId']})
        self.assertEqual(stop['outcome'],'ACCEPTED', stop)
        self.assertEqual(len(await self.client.runs()),1)


@unittest.skipUnless(os.environ.get('EMO_WEB_PRODUCT_RUNTIME') == '1',
                     'Opt-in full owning Runtime integration; requires its separate dependencies')
class EditorPublishedRuntimeTests(unittest.IsolatedAsyncioTestCase):
    """The browser gate's current-format fixture through actual publish + RPC.

    This verifies the software contract without substituting for Chromium UI/WS
    assertions in frontend/tests/product_runtime_e2e.py.
    """
    server_flag = '--runtime-server-web-editor-continuous'
    asyncSetUp = ProductRuntimeTests.asyncSetUp
    asyncTearDown = ProductRuntimeTests.asyncTearDown
    preserve_evidence = ProductRuntimeTests.preserve_evidence

    async def test_editor_publish_registers_real_outputs_before_explicit_start_and_stop(self):
        from copy import deepcopy
        import httpx
        from emo_panel_protocol.messages import BrowserMessage
        from emo_web_panel.app import create_app
        from emo_web_panel.config import Config

        fixture = json.loads((Path(self.directory.name) / 'project/web-editor-demo.emoproj').read_text(encoding='utf-8'))
        self.assertEqual((fixture['schemaVersion'], fixture['presentation']['schemaVersion']), ('2.9', '1.2'))
        self.assertEqual(fixture['devices']['definitions'], {})
        self.assertEqual(fixture['presentation']['pages'], {}, 'Capture must not come from a Qt page')
        app = create_app(Config(Path(self.directory.name) / 'gateway', mode='grpc', runtime_address=self.client.address))
        app.state.auth.create_user('editor-test', 'Synthetic-editor-test-only!', 'admin')
        async with app.router.lifespan_context(app), httpx.AsyncClient(
                transport=httpx.ASGITransport(app), base_url='http://testserver') as web:
            async def request(method, path, **kwargs):
                response = await web.request(method, path, **kwargs)
                self.assertEqual(response.status_code, 200, response.text)
                return response.json()

            login = await request('POST', '/api/session', json={'username': 'editor-test', 'password': 'Synthetic-editor-test-only!'})
            web.headers['X-CSRF-Token'] = login['csrf']
            base = '/api/editor/configured'
            context = await request('GET', base + '/context')
            info = context['runtime']
            self.assertEqual(info['projectId'], 'independent-web-editor-software-demo')
            self.assertEqual(info['productionExecutionMode'], 'continuous')
            self.assertEqual(context['requests']['config']['sources'], {})
            self.assertEqual(await self.client.runs(), [])
            source = deepcopy(context['draft']['presentation'])
            page = source['pages'][source['defaultPageId']]
            sources = {}
            for node, port, kind in [('count', 'count', 'number'), ('judge', 'result', 'indicator')]:
                choice = next(item for item in context['catalog']['choices'] if item['source']['kind'] == 'node_output'
                              and item['source']['nodeId'] == node and item['source']['port'] == port
                              and not item['source']['fieldPath'])
                sid, scope = choice['sourceId'], choice['scopeId']
                source['dataSources'][sid] = choice['source']
                source['resultScopes'][scope] = choice['scope']
                if scope not in page['resultScopeIds']:
                    page['resultScopeIds'].append(scope)
                page['components'].append({'componentId': 'component-' + port, 'type': kind,
                                           'bindings': {'value': sid}})
                sources[port] = sid
            page['components'].append({'componentId': 'runtime-state', 'type': 'runtime_status'})
            saved = await request('PUT', base + '/draft', json={'projectId': info['projectId'],
                'expectedDraftRevision': context['draft']['draftRevision'], 'presentation': source})
            self.assertEqual((await self.client.output_requests())['config']['sources'], {})
            publication = await request('POST', base + '/publish', json={'projectId': info['projectId'],
                'expectedDraftRevision': saved['draftRevision'], 'requestId': 'editor-contract-publish-1'})
            self.assertEqual(publication['state'], 'PUBLISHED', publication)
            registered = await self.client.output_requests()
            self.assertEqual(set(registered['config']['sources']), set(sources.values()))
            self.assertEqual(await self.client.runs(), [], 'Publish must not create a Job')
            self.observations.append({'operation': 'editor_publish', 'reply': publication, 'registered': registered})
            body = {'runtimeId': 'configured', 'runtimeInstanceId': info['runtimeInstanceId'], 'projectId': info['projectId'],
                    'configurationRevision': info['configurationRevision'], 'workflowId': 'main',
                    'requestId': 'editor-contract-start-1', 'inputs': {}}
            accepted = await request('POST', '/api/runs/start', json=body)
            self.assertEqual(accepted['state'], 'accepted', accepted)
            BrowserMessage.model_validate(accepted['controlMessage'])
            job = accepted['runId']
            state = await self.client.run_state(job)
            self.assertEqual((state['runtimeInstanceId'], state['projectId'], state['runGroupId']),
                             (info['runtimeInstanceId'], info['projectId'], ''))
            self.assertTrue(state['captureEnabled'])
            deadline = time.monotonic() + 30
            result = None
            while time.monotonic() < deadline:
                result = next((item for item in await self.client.snapshot(job) if item.get('type') != 'scope_status'), None)
                if result:
                    break
                await asyncio.sleep(.05)
            self.assertIsNotNone(result, 'Published count/verdict never reached actual DisplayService')
            self.assertEqual((result['runtimeInstanceId'], result['projectId'], result['jobId']),
                             (info['runtimeInstanceId'], info['projectId'], job))
            self.assertTrue(result['capturePlanRevision'])
            self.assertEqual(set(result['sources']), set(sources.values()))
            self.assertEqual(result['sources'][sources['count']]['value'], 2)
            self.assertIs(result['sources'][sources['result']]['value'], True)
            self.observations.append({'operation': 'actual_published_result', 'reply': result})
            stopped = await request('POST', f'/api/runs/{job}/stop', json={**body,
                'requestId': 'editor-contract-stop-1', 'runGroupId': ''})
            self.assertEqual(stopped['state'], 'accepted', stopped)
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                state = await self.client.run_state(job)
                if state['status'] == 'ABORTED' and not state['active'] and not state['ownsResources']:
                    break
                await asyncio.sleep(.05)
            self.assertEqual((state['id'], state['runGroupId'], state['status'], state['active'], state['ownsResources']),
                             (job, '', 'ABORTED', False, False))
            self.assertEqual([run['id'] for run in await self.client.runs()], [job])
            self.observations.append({'operation': 'actual_exact_stop', 'reply': stopped, 'terminal': state})


class StartReconciliationTests(unittest.IsolatedAsyncioTestCase):
    async def test_unknown_queries_same_intent_without_another_start(self):
        from types import SimpleNamespace
        from unittest.mock import AsyncMock, Mock, patch
        client = SimpleNamespace(lookup_start=AsyncMock(side_effect=[
            {'outcome':'UNKNOWN'}, {'outcome':'ACCEPTED','runId':'actual-job'}]), control=AsyncMock())
        host = SimpleNamespace(client=client, observations=[], process=SimpleNamespace(poll=lambda: None), fail=Mock())
        with patch('asyncio.sleep', new=AsyncMock()):
            result = await ProductRuntimeTests.reconcile_start(host, {'outcome':'UNKNOWN'},
                {'requestId':'same-intent','runtimeInstanceId':'same-instance'})
        self.assertEqual(result, {'outcome':'ACCEPTED','runId':'actual-job'})
        self.assertEqual(client.lookup_start.await_count,2)
        self.assertTrue(all(call.args == ('same-intent','same-instance') for call in client.lookup_start.await_args_list))
        client.control.assert_not_awaited()

    async def test_reconciliation_timeout_does_not_invent_acceptance(self):
        from types import SimpleNamespace
        from unittest.mock import AsyncMock
        host = SimpleNamespace(observations=[],client=SimpleNamespace(lookup_start=AsyncMock()))
        result = await ProductRuntimeTests.reconcile_start(host, {'outcome':'UNKNOWN'}, {}, timeout=0)
        self.assertEqual(result, {'outcome':'UNKNOWN'})
        host.client.lookup_start.assert_not_awaited()


if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] == '--runtime-server-independent-web':
        from test_web_start_capture import run_independent_capture_server
        run_independent_capture_server(Path(sys.argv[2]))
    elif len(sys.argv) == 3 and sys.argv[1] == '--runtime-server-web-editor-continuous':
        sys.path.insert(0, str(ROOT))
        from scripts.independent_web_editor_demo import serve
        serve(Path(sys.argv[2]), port=0)
    elif len(sys.argv) == 3 and sys.argv[1] in {'--runtime-server', '--runtime-server-continuous'}:
        run_product_server(sys.argv[2], continuous=sys.argv[1] == '--runtime-server-continuous')
    else:
        unittest.main()
