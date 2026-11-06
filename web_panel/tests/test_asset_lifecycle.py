"""Late browser reads retain exact identity and explicit availability reasons."""
import json
from pathlib import Path
import tempfile
import unittest

from fastapi.testclient import TestClient

from emo_web_panel.app import create_app
from emo_web_panel.config import Config
from emo_web_panel.mock_runtime import IMAGE, MockRuntime


class AssetRuntime(MockRuntime):
    def __init__(self):
        super().__init__()
        self.asset_reads = 0

    async def read_asset(self, job_id, resource, *, runtime_instance_id):
        assert job_id == 'demo-run' and runtime_instance_id == self.instance_id
        self.asset_reads += 1
        return IMAGE, 'image/png'


class AssetLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.runtime = AssetRuntime()
        self.app = create_app(Config(Path(self.temporary.name)), runtime=self.runtime)
        self.app.state.auth.create_user('asset-tester', 'Synthetic-asset-test-2026!', 'admin')
        self.client = TestClient(self.app)
        self.client.__enter__()
        session = self.client.post('/api/session', json={
            'username': 'asset-tester', 'password': 'Synthetic-asset-test-2026!'})
        self.headers = {'X-CSRF-Token': session.json()['csrf']}
        response = self.client.post('/api/views', headers=self.headers,
                                    json={'panelId': 'demo-panel', 'runId': 'demo-run'})
        self.assertEqual(response.status_code, 200)
        self.view_id = response.json()['id']
        self.view = self.app.state.views.views[self.view_id]

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.temporary.cleanup()

    def publish(self):
        result = self.client.portal.call(self.runtime.snapshot, 'demo-run')
        result['projectId'] = 'demo-project'
        self.client.portal.call(self.app.state.views._receive, self.view, result)
        return result

    def image(self, result_key, **extra):
        return self.client.get(f'/api/views/{self.view_id}/assets/image',
            params={'scope': 'scope-1', 'resultKey': result_key, **extra})

    def test_late_advertised_result_is_retired_without_reading_or_replacing_its_image(self):
        first = self.publish()
        initial = self.image(first['resultKey'])
        self.assertEqual(initial.status_code, 200)
        self.assertEqual(initial.content, IMAGE)
        # A native confirmation can pause browser JS while the server continues
        # receiving ordinary results. The existing four-result bound stays intact.
        for _ in range(5):
            newest = self.publish()
        self.assertEqual(len(self.view['scopeRecent']['scope-1']), 4)
        reads = self.runtime.asset_reads
        expired = self.image(first['resultKey'], unneededQuery='not-part-of-asset-diagnostics')
        self.assertEqual(expired.status_code, 404)
        self.assertEqual(expired.json()['code'], 'RESULT_RETIRED')
        self.assertEqual(expired.json()['detail'], 'Exact advertised result retired or expired')
        self.assertEqual(self.runtime.asset_reads, reads)
        self.assertEqual(self.image(newest['resultKey']).content, IMAGE)
        failures = [row for row in self.app.state.gateway_log.records()
                    if row['eventType'] == 'gateway.request' and row['payload'].get('status') == 404]
        self.assertEqual(len(failures), 1)
        fields = failures[0]['payload']
        self.assertEqual(fields['assetCode'], 'RESULT_RETIRED')
        self.assertEqual(fields['viewId'], self.view_id)
        self.assertEqual(fields['viewRunId'], 'demo-run')
        self.assertEqual(fields['runtimeInstanceId'], 'mock-instance')
        self.assertEqual(fields['projectId'], 'demo-project')
        self.assertEqual(fields['resultScopeId'], 'scope-1')
        self.assertEqual(fields['resultKey'], first['resultKey'])
        self.assertEqual(fields['sourceId'], 'image')
        self.assertNotIn('not-part-of-asset-diagnostics', json.dumps(failures))

    def test_declared_unavailable_source_does_not_attempt_a_runtime_asset_read(self):
        result = self.publish()
        result['sources']['image'] = {'state': 'unavailable', 'reason': 'execution cancelled',
                                      'reasonCode': 'EXECUTION_CANCELLED'}
        self.client.portal.call(self.app.state.views._receive, self.view, result)
        response = self.image(result['resultKey'])
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()['code'], 'SOURCE_UNAVAILABLE')
        self.assertEqual(self.runtime.asset_reads, 0)
        diagnostic = next(row['payload'] for row in self.app.state.gateway_log.records()
                          if row['payload'].get('assetCode') == 'SOURCE_UNAVAILABLE')
        self.assertEqual(diagnostic['sourceState'], 'unavailable')
        self.assertEqual(diagnostic['sourceReasonCode'], 'EXECUTION_CANCELLED')
        self.assertEqual(diagnostic['jobId'], 'demo-run')


if __name__ == '__main__':
    unittest.main()
