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


def run_product_server(root):
    # Imports exclusively in the Runtime subprocess, not the gateway process.
    import contextlib
    import shutil
    import sqlite3
    from emo_master.apps.runtime.grpc_server.service import RuntimeService
    from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb
    from emo_master.apps.runtime.main import createRuntimeServer

    root = Path(root)
    source = CONTRACT / 'sample-panels/source'
    shutil.copyfile(source / 'demo.emoproj', root / 'project.emoproj')
    shutil.copyfile(source / 'demo.png', root / 'demo.png')
    (root / 'demo-images').mkdir()
    shutil.copyfile(source / 'demo.png', root / 'demo-images/reference.png')
    with sqlite3.connect(root / 'demo-history.sqlite') as db:
        db.execute('CREATE TABLE records (id INTEGER PRIMARY KEY, verdict TEXT, image TEXT)')
        db.execute('INSERT INTO records VALUES (1, ?, ?)', ('OK', 'reference.png'))
    with contextlib.redirect_stdout(sys.stderr):
        service = RuntimeService(dbPath=root / 'runtime.db', workspaceRoot=root / 'jobs')
        loaded = service.LoadProject(pb.LoadProjectRequest(project_path=str(root / 'project.emoproj')), None)
        if not loaded.ok:
            service.close()
            raise RuntimeError(loaded.message)
        server, port, _ = createRuntimeServer(port=0, runtimeService=service)
        server.start()
    try:
        print(json.dumps({'event':'ready', 'address':f'127.0.0.1:{port}'}), flush=True)
        sys.stdin.readline()
    finally:
        with contextlib.redirect_stdout(sys.stderr):
            server.stop(0).wait()
            service.close()


@unittest.skipUnless(os.environ.get('EMO_WEB_PRODUCT_RUNTIME') == '1',
                     'Opt-in full owning Runtime integration; requires its separate dependencies')
class ProductRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from emo_web_panel.runtime_client import GrpcRuntimeClient
        self.observations = []
        self.stdout_lines = []
        self.directory = tempfile.TemporaryDirectory(prefix='web-product-runtime-')
        self.log = open(Path(self.directory.name) / 'runtime.log', 'w+')
        env = os.environ.copy()
        # Deliberately exclude gateway and SDK from the execution host PYTHONPATH.
        env['PYTHONPATH'] = str(ROOT / 'src')
        env['PYTHONDONTWRITEBYTECODE'] = '1'
        env['QT_QPA_PLATFORM'] = 'offscreen'
        self.process = subprocess.Popen([os.environ.get('EMO_WEB_PRODUCT_PYTHON', sys.executable),
            str(Path(__file__).resolve()), '--runtime-server', self.directory.name], cwd=ROOT,
            env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.log, text=True)
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
            self.log.flush(); self.log.seek(0)
            diagnostic = self.log.read()
            self.preserve_evidence()
            self.log.close(); self.directory.cleanup()
            raise AssertionError('Owning Runtime subprocess did not start:\n' + diagnostic)

    async def asyncTearDown(self):
        await self.client.close()
        if self.process.poll() is None:
            self.process.stdin.write('\n'); self.process.stdin.flush()
            try:
                await asyncio.to_thread(self.process.wait, timeout=20)
            except subprocess.TimeoutExpired:
                self.process.kill(); self.process.wait(timeout=10)
        self.stdout_thread.join(timeout=2)
        self.preserve_evidence()
        self.process.stdin.close(); self.process.stdout.close()
        self.log.close(); self.directory.cleanup()

    def preserve_evidence(self):
        destination = Path(os.environ.get('EMO_WEB_PRODUCT_EVIDENCE_DIR',
            str(ROOT / 'web_panel/test-results/product-runtime'))) / self._testMethodName
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
        context = {key: info[key] for key in ('runtimeInstanceId','projectId','historyDefinitionRevision')}
        page = await self.client.history('records',1,20,context=context,sort='id')
        self.assertTrue(page['stableRowIdentity'])
        self.assertEqual(page['rows'][0]['values'], [1, 'OK'])
        ref = page['rows'][0]['imageRefs']['record-image']['imageRef']
        self.assertNotIn('reference.png', ref)
        image, mime, status = await self.client.history_image(image_ref=ref, context=context)
        self.assertEqual(status, 'AVAILABLE'); self.assertEqual(mime, 'image/png')
        fixed, _, _ = await self.client.history_image(component_id='fixed-image', context=context)
        self.assertEqual(image, fixed)
        total = await self.client.history('total',1,20,context=context,kind='aggregate')
        self.assertEqual(total['value'], 1)
        self.assertEqual(await self.client.runs(), [])

    async def test_actual_runtime_start_capture_lookup_stop_and_no_replay(self):
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
        result = []
        for _ in range(100):
            result = await self.client.snapshot(job)
            if result:
                break
            await asyncio.sleep(.05)
        if not result:
            self.observations.append({'operation':'run_state','reply':await self.client.run_state(job)})
            self.observations.append({'operation':'job_events','reply':await self.client.job_events(job)})
        self.assertTrue(result, 'Actual Runtime never produced the supplied sample capture')
        self.assertEqual(result[0]['jobId'],job)
        self.assertIn('count',result[0]['sources'])
        state = await self.client.run_state(job)
        stop = await self.client.control('stop',job,body | {'runGroupId':state['runGroupId']})
        self.assertEqual(stop['outcome'],'ACCEPTED', stop)
        self.assertEqual(len(await self.client.runs()),1)


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
    if len(sys.argv) == 3 and sys.argv[1] == '--runtime-server':
        run_product_server(sys.argv[2])
    else:
        unittest.main()
