"""Normal duplicate completion must not reactivate an older page release."""
import asyncio
from copy import deepcopy
import json
import os
from pathlib import Path
import tempfile
import unittest

from emo_web_panel.editor import EditorStore, blank_presentation
from emo_web_panel.mock_runtime import MockRuntime
from emo_web_panel.packages import PackageStore


class DelayedRead:
    """A normal read-only RPC response that arrives later than another request."""
    def __init__(self, runtime):
        self.runtime = runtime
        self.queried = asyncio.Event()
        self.resume = asyncio.Event()

    async def output_requests(self):
        snapshot = await self.runtime.output_requests()
        self.queried.set()
        await self.resume.wait()
        return snapshot


class EditorConcurrencyTests(unittest.IsolatedAsyncioTestCase):
    async def test_publish_and_target_address_edit_share_existing_target_coordination(self):
        from httpx import AsyncClient, ASGITransport
        from emo_web_panel.app import create_app
        from emo_web_panel.config import Config

        class DelayedSubmit(MockRuntime):
            def __init__(self):
                super().__init__()
                self.submitting, self.resume = asyncio.Event(), asyncio.Event()

            async def submit_output_requests(self, body):
                self.submitting.set()
                await self.resume.wait()
                return await super().submit_output_requests(body)

        with tempfile.TemporaryDirectory() as directory:
            runtime = DelayedSubmit()
            app = create_app(Config(Path(directory), mode='grpc', runtime_address='127.0.0.1:50051'), runtime)
            app.state.auth.create_user('admin', 'safe-test-password', 'admin')
            async with app.router.lifespan_context(app), AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
                login = await client.post('/api/session', json={'username': 'admin', 'password': 'safe-test-password'})
                headers = {'X-CSRF-Token': login.json()['csrf']}
                context = (await client.get('/api/editor/configured/context')).json()
                source = context['draft']['presentation']
                count = next(item for item in context['catalog']['choices'] if item['source']['port'] == 'count')
                source['dataSources'][count['sourceId']] = count['source']
                source['resultScopes'][count['scopeId']] = count['scope']
                source['pages'][source['defaultPageId']]['components'] = [
                    {'componentId': 'count', 'type': 'number', 'bindings': {'value': count['sourceId']}}]
                project = context['runtime']['projectId']
                saved = await client.put('/api/editor/configured/draft', headers=headers,
                    json={'projectId': project, 'presentation': source})
                self.assertEqual(saved.status_code, 200, saved.text)
                publishing = asyncio.create_task(client.post('/api/editor/configured/publish', headers=headers,
                    json={'projectId': project, 'expectedDraftRevision': saved.json()['draftRevision'], 'requestId': 'address-race-1'}))
                await asyncio.wait_for(runtime.submitting.wait(), 5)
                changing = asyncio.create_task(client.patch('/api/admin/runtimes/configured', headers=headers,
                    json={'address': '127.0.0.1:50052'}))
                await asyncio.wait([changing], timeout=.3)
                changed_during_publish = changing.done()
                observed = app.state.registry.get({'username': 'admin', 'role': 'admin'}, 'configured')['address']
                runtime.resume.set()
                published, changed = await asyncio.gather(publishing, changing)
                evidence = {'changedDuringPublish': changed_during_publish, 'addressDuringPublish': observed,
                            'publish': published.json(), 'changed': changed.json()}
                destination = Path(os.environ.get('EMO_WEB_REVIEW_EVIDENCE_DIR', str(Path(__file__).resolve().parents[2] /
                    'docs/testing/2026-11-03-independent-web-editor/review-fixes')))
                destination.mkdir(parents=True, exist_ok=True)
                (destination / 'publish-target-address.json').write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding='utf-8')
                self.assertEqual(published.status_code, 200, published.text)
                self.assertEqual(published.json()['state'], 'PUBLISHED')
                self.assertEqual(changed.status_code, 200, changed.text)
                self.assertFalse(changed_during_publish, evidence)
                self.assertEqual(observed, '127.0.0.1:50051')

    async def test_duplicate_complete_followed_by_new_publish_keeps_latest_page_and_capture_together(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            packages = PackageStore(root / 'packages')
            store = EditorStore(root / 'assets', packages)
            runtime = MockRuntime()
            catalog = await runtime.output_catalog()
            info = await runtime.info()
            rid, project = 'configured', info['projectId']
            source = blank_presentation()
            page = source['pages'][source['defaultPageId']]

            def bind(port, kind):
                choice = next(c for c in catalog['choices'] if c['source']['port'] == port)
                source['dataSources'][choice['sourceId']] = choice['source']
                source['resultScopes'][choice['scopeId']] = choice['scope']
                page['resultScopeIds'] = [choice['scopeId']]
                page['components'].append(dict(componentId=port, type=kind, bindings={'value': choice['sourceId']}))

            bind('count', 'number')
            store.save(rid, project, source)
            package, _, config = store.prepare(rid, project, source)
            current = await runtime.output_requests()
            body = dict(runtimeInstanceId=info['runtimeInstanceId'], projectId=project,
                        expectedDocumentRevision=info['documentRevision'], expectedConfigRevision=current['configRevision'],
                        requestId='publish-A', config=config)
            saved = await runtime.submit_output_requests(body)
            self.assertTrue(saved['ok'])
            first = store.update(dict(runtimeId=rid, projectId=project, versionId='version-A',
                requestId='publish-A', state='REQUEST_SAVED', source=deepcopy(source), sourceRevision='test-source-A',
                config=config, packageId=package['packageId'], configRevision=saved['configRevision'],
                runtimeInstanceId=info['runtimeInstanceId'], submitBody=body))
            delayed = DelayedRead(runtime)
            slow = asyncio.create_task(store.complete(deepcopy(first), delayed))
            await delayed.queried.wait()
            duplicate = asyncio.create_task(store.complete(deepcopy(first), runtime))
            bind('result', 'indicator')
            later = asyncio.create_task(store.publish(rid, project, runtime, source=deepcopy(source), request_id='publish-B'))
            # Let ordinary duplicate requests progress while A's read is pending.
            for _ in range(5):
                await asyncio.sleep(0)
            delayed.resume.set()
            a, duplicate_a, b = await asyncio.gather(slow, duplicate, later)
            self.assertEqual((a['state'], duplicate_a['state'], b['state']), ('PUBLISHED',) * 3)
            self.assertEqual(packages.active(rid, project)['packageId'], b['packageId'])
            self.assertNotEqual(a['packageId'], b['packageId'])
            self.assertEqual((await runtime.output_requests())['config'], b['config'])
            # Retrying a previously read completion after B must be observation only.
            await store.complete(first, runtime)
            self.assertEqual(packages.active(rid, project)['packageId'], b['packageId'])
