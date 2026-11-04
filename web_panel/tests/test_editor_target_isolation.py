"""A paused publication must coordinate its own Runtime without blocking others."""
import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
import json
import os
from pathlib import Path
import tempfile
import unittest

from httpx import ASGITransport, AsyncClient

from emo_web_panel.app import create_app
from emo_web_panel.config import Config
from emo_web_panel.mock_runtime import MockRuntime


class DelayedSubmit(MockRuntime):
    def __init__(self, *, fail=False):
        super().__init__()
        self.entered, self.resume = asyncio.Event(), asyncio.Event()
        self.submissions = []
        self.fail = fail

    async def submit_output_requests(self, body):
        self.submissions.append(deepcopy(body))
        if not self.entered.is_set():
            self.entered.set()
            await self.resume.wait()
            if self.fail:
                raise TimeoutError('One ordinary lost submit response')
        return await super().submit_output_requests(body)


class EditorTargetIsolationTests(unittest.IsolatedAsyncioTestCase):
    @asynccontextmanager
    async def application(self, *, fail=False):
        tasks = []
        with tempfile.TemporaryDirectory() as directory:
            runtime = DelayedSubmit(fail=fail)
            app = create_app(Config(Path(directory), mode='grpc', runtime_address='127.0.0.1:50051'), runtime)
            app.state.auth.create_user('admin', 'safe-test-password', 'admin')
            async with app.router.lifespan_context(app), AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
                login = await client.post('/api/session', json={'username': 'admin', 'password': 'safe-test-password'})
                self.headers = {'X-CSRF-Token': login.json()['csrf']}
                added = await client.post('/api/admin/runtimes', headers=self.headers,
                    json={'id': 'other', 'alias': 'Other Runtime', 'mode': 'mock', 'address': ''})
                self.assertEqual(added.status_code, 200, added.text)
                self.tasks = tasks
                try:
                    yield app, client, runtime
                finally:
                    runtime.resume.set()
                    for task in tasks:
                        if not task.done():
                            task.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)

    def spawn(self, coroutine):
        task = asyncio.create_task(coroutine)
        self.tasks.append(task)
        return task

    def evidence(self, name, body):
        root = Path(os.environ.get('EMO_WEB_ROUND2_EVIDENCE', str(Path(__file__).resolve().parents[2] /
            'docs/testing/2026-11-03-independent-web-editor/review-round2/backend-cases')))
        root.mkdir(parents=True, exist_ok=True)
        (root / (name + '.json')).write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding='utf-8')

    async def draft(self, client, rid):
        context = (await client.get(f'/api/editor/{rid}/context')).json()
        source = context['draft']['presentation']
        choice = next(item for item in context['catalog']['choices'] if item['source']['port'] == 'count')
        source['dataSources'][choice['sourceId']] = choice['source']
        source['resultScopes'][choice['scopeId']] = choice['scope']
        source['pages'][source['defaultPageId']]['components'] = [
            {'componentId': 'count', 'type': 'number', 'bindings': {'value': choice['sourceId']}}]
        project = context['runtime']['projectId']
        saved = await client.put(f'/api/editor/{rid}/draft', headers=self.headers,
            json={'projectId': project, 'presentation': source})
        self.assertEqual(saved.status_code, 200, saved.text)
        return {'projectId': project, 'expectedDraftRevision': saved.json()['draftRevision']}

    async def publish(self, client, rid, body, request):
        return await client.post(f'/api/editor/{rid}/publish', headers=self.headers, json={**body, 'requestId': request})

    async def command(self, client, action, request):
        info = (await client.get('/api/runtimes/other/info')).json()
        return await client.post('/api/runs/start' if action == 'start' else '/api/runs/demo-run/stop',
            headers=self.headers, json={'runtimeId': 'other', 'runtimeInstanceId': info['runtimeInstanceId'],
                'projectId': info['projectId'], 'configurationRevision': info['configurationRevision'],
                'workflowId': 'demo-workflow', 'runGroupId': '', 'inputs': {}, 'requestId': request})

    async def cross_target_control(self, action):
        async with self.application() as (app, client, runtime):
            body = await self.draft(client, 'configured')
            if action == 'start':
                initial = await self.command(client, 'stop', 'initial-stop-before-publication')
                self.assertEqual(initial.status_code, 200, initial.text)
            publishing = self.spawn(self.publish(client, 'configured', body, 'paused-publish-' + action))
            await asyncio.wait_for(runtime.entered.wait(), 5)
            controlling = self.spawn(self.command(client, action, 'other-command-' + action))
            await asyncio.wait([controlling], timeout=1)
            completed = controlling.done()
            state = (await client.get('/api/runtimes/other/runs')).json()[0]['state']
            publication_pending = not publishing.done()
            runtime.resume.set()
            published, controlled = await asyncio.gather(publishing, controlling)
            record = {'otherAction': action, 'completedWhilePublicationPending': completed,
                'publicationStillPending': publication_pending, 'otherStateWhilePending': state,
                'publishState': published.json()['state'], 'controlStatus': controlled.status_code,
                'submissions': len(runtime.submissions), 'implicitControlsOnPublishingRuntime': runtime.control_count}
            self.evidence('cross-target-' + action, record)
            self.assertTrue(completed, record)
            self.assertTrue(publication_pending, record)
            self.assertEqual(state, 'running' if action == 'start' else 'stopped', record)
            self.assertEqual(controlled.status_code, 200, controlled.text)
            self.assertEqual(published.json()['state'], 'PUBLISHED')
            self.assertEqual((len(runtime.submissions), runtime.control_count), (1, 0))

    async def test_other_runtime_stop_completes_while_publication_waits(self):
        await self.cross_target_control('stop')

    async def test_other_runtime_start_completes_while_publication_waits(self):
        await self.cross_target_control('start')

    async def test_other_runtime_publication_completes_while_publication_waits(self):
        async with self.application() as (app, client, runtime):
            body_a, body_b = await self.draft(client, 'configured'), await self.draft(client, 'other')
            first = self.spawn(self.publish(client, 'configured', body_a, 'paused-first-publication'))
            await asyncio.wait_for(runtime.entered.wait(), 5)
            second = self.spawn(self.publish(client, 'other', body_b, 'independent-second-publication'))
            await asyncio.wait([second], timeout=1)
            completed, pending = second.done(), not first.done()
            runtime.resume.set()
            a, b = await asyncio.gather(first, second)
            record = {'secondCompletedWhileFirstPending': completed, 'firstStillPending': pending,
                      'firstState': a.json()['state'], 'secondState': b.json()['state']}
            self.evidence('cross-target-publish', record)
            self.assertTrue(completed, record)
            self.assertTrue(pending, record)
            self.assertEqual((a.json()['state'], b.json()['state']), ('PUBLISHED', 'PUBLISHED'))

    async def test_same_target_address_change_and_delete_wait_for_publication(self):
        for mutation in ('address', 'delete'):
            with self.subTest(mutation=mutation):
                async with self.application() as (app, client, runtime):
                    body = await self.draft(client, 'configured')
                    publishing = self.spawn(self.publish(client, 'configured', body, 'same-target-' + mutation))
                    await asyncio.wait_for(runtime.entered.wait(), 5)
                    changing = self.spawn(client.patch('/api/admin/runtimes/configured', headers=self.headers,
                        json={'address': '127.0.0.1:50052'}) if mutation == 'address' else
                        client.delete('/api/admin/runtimes/configured', headers=self.headers))
                    await asyncio.wait([changing], timeout=.3)
                    early = changing.done()
                    observed = app.state.registry.get({'username': 'admin', 'role': 'admin'}, 'configured')['address']
                    runtime.resume.set()
                    published, changed = await asyncio.gather(publishing, changing)
                    record = {'changedBeforePublicationFinished': early, 'addressWhilePending': observed,
                              'publicationState': published.json()['state'], 'mutationStatus': changed.status_code}
                    self.evidence('same-target-' + mutation, record)
                    self.assertFalse(early, record)
                    self.assertEqual(observed, '127.0.0.1:50051')
                    self.assertEqual((published.json()['state'], changed.status_code), ('PUBLISHED', 200))
                    if mutation == 'delete':
                        self.assertIsNone(app.state.editor.packages.active('configured', body['projectId']))

    async def test_queued_grant_on_publishing_target_does_not_hold_shared_user_guard(self):
        async with self.application() as (app, client, runtime):
            app.state.auth.create_user('viewer', 'safe-test-password', 'viewer')
            body = await self.draft(client, 'configured')
            publishing = self.spawn(self.publish(client, 'configured', body, 'publish-with-queued-grant'))
            await asyncio.wait_for(runtime.entered.wait(), 5)
            granting_a = self.spawn(client.put('/api/admin/runtimes/configured/grants', headers=self.headers,
                json={'username': 'viewer', 'view': True, 'control': False}))
            await asyncio.sleep(0)
            granting_b = self.spawn(client.put('/api/admin/runtimes/other/grants', headers=self.headers,
                json={'username': 'viewer', 'view': True, 'control': False}))
            stopping_b = self.spawn(self.command(client, 'stop', 'stop-while-grant-queued'))
            await asyncio.wait([granting_b, stopping_b], timeout=1)
            independent = granting_b.done() and stopping_b.done()
            same_target_waiting = not granting_a.done()
            runtime.resume.set()
            published, grant_a, grant_b, stopped = await asyncio.gather(publishing, granting_a, granting_b, stopping_b)
            record = {'otherGrantAndStopCompletedBeforeRelease': independent,
                'sameTargetGrantStillWaiting': same_target_waiting, 'publicationState': published.json()['state'],
                'managementStatuses': [grant_a.status_code, grant_b.status_code, stopped.status_code]}
            self.evidence('queued-grant-user-guard', record)
            self.assertTrue(independent, record)
            self.assertTrue(same_target_waiting, record)
            self.assertEqual(record['managementStatuses'], [200, 200, 200])

    async def test_cancelled_or_failed_publication_can_query_continue_end_and_publish_again(self):
        for outcome in ('cancel', 'failure'):
            with self.subTest(outcome=outcome):
                async with self.application(fail=outcome == 'failure') as (app, client, runtime):
                    body = await self.draft(client, 'configured')
                    publishing = self.spawn(self.publish(client, 'configured', body, 'one-submit-' + outcome))
                    await asyncio.wait_for(runtime.entered.wait(), 5)
                    if outcome == 'cancel':
                        publishing.cancel()
                        with self.assertRaises(asyncio.CancelledError):
                            await publishing
                    else:
                        runtime.resume.set()
                        failed = await asyncio.wait_for(publishing, 5)
                        self.assertEqual(failed.json()['state'], 'REQUEST_UNKNOWN')
                    old = app.state.editor.versions('configured', body['projectId'])[0]
                    base = '/api/editor/configured/publications/' + old['versionId']
                    query = await asyncio.wait_for(client.get(base, params={'projectId': body['projectId']}), 3)
                    continued = await asyncio.wait_for(client.post(base + '/complete', headers=self.headers,
                        json={'projectId': body['projectId']}), 3)
                    self.assertEqual((query.json()['state'], continued.json()['state']), ('REQUEST_UNKNOWN',) * 2)
                    self.assertEqual(len(runtime.submissions), 1, 'Unknown submit must not be replayed')
                    ended = await asyncio.wait_for(client.post(base + '/abandon', headers=self.headers,
                        json={'projectId': body['projectId']}), 3)
                    self.assertEqual(ended.json()['state'], 'ABANDONED')
                    again = await asyncio.wait_for(self.publish(client, 'configured', body, 'new-explicit-' + outcome), 3)
                    changed = await asyncio.wait_for(client.patch('/api/admin/runtimes/configured', headers=self.headers,
                        json={'alias': 'Normal management after publication'}), 3)
                    record = {'outcome': outcome, 'queryState': query.json()['state'],
                        'continueState': continued.json()['state'], 'endState': ended.json()['state'],
                        'newExplicitPublishState': again.json()['state'], 'laterManagementStatus': changed.status_code,
                        'submissionsAfterExplicitNewRequest': len(runtime.submissions), 'implicitControls': runtime.control_count}
                    self.evidence('release-after-' + outcome, record)
                    self.assertEqual(again.json()['state'], 'PUBLISHED')
                    self.assertEqual(changed.status_code, 200)
                    self.assertEqual((len(runtime.submissions), runtime.control_count), (2, 0))
