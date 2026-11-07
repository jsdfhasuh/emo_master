"""Real ASGI app route/auth with a controlled Runtime method; no real-device claim."""
import unittest
import test_app
from emo_web_panel.runtime_client import RuntimeRejected


class RunStateHttpTests(unittest.TestCase):
    setUp = test_app.AppTests.setUp
    tearDown = test_app.AppTests.tearDown
    login = test_app.AppTests.login

    def attach_direct_state(self, code=''):
        self.direct_calls = []
        self.catalog_calls = []
        runtime = self.app.state.runtime
        catalog = runtime.runs

        async def direct(job_id):
            self.direct_calls.append(job_id)
            if code:
                raise RuntimeRejected(code, 'controlled Runtime reply')
            return {'id':job_id, 'jobId':job_id, 'runtimeInstanceId':'mock-instance',
                    'projectId':'demo-project', 'status':'RUNNING', 'state':'running'}

        async def listed():
            self.catalog_calls.append(True)
            return await catalog()

        runtime.run_state = direct
        runtime.runs = listed

    def test_direct_state_still_requires_session_and_target_view_acl(self):
        self.attach_direct_state()
        path = '/api/runs/exact-source?runtimeId=configured'
        self.assertEqual(self.client.get(path).status_code, 401)
        self.login('viewer')
        self.assertEqual(self.client.get(path).status_code, 403)
        self.assertEqual(self.direct_calls, [])
        self.app.state.registry.set_acl({'username':'admin','role':'admin'}, 'configured', 'viewer', True, False)
        response = self.client.get(path)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['id'], 'exact-source')
        self.assertEqual(response.json()['runtimeId'], 'configured')
        self.assertEqual(self.direct_calls, ['exact-source'])
        self.assertEqual(self.catalog_calls, [])

    def test_runtime_rejections_keep_not_found_and_validation_http_semantics(self):
        self.login()
        for code, expected in [('JOB_NOT_FOUND',404), ('PROJECT_MISMATCH',404), ('RESET_REQUIRED',422)]:
            with self.subTest(code=code):
                self.attach_direct_state(code)
                response = self.client.get('/api/runs/exact-source?runtimeId=configured')
                self.assertEqual(response.status_code, expected)
                self.assertEqual(self.direct_calls, ['exact-source'])
                self.assertEqual(self.catalog_calls, [])
                if expected == 404:
                    self.assertEqual(response.json(), {'detail':'Run not found'})
                else:
                    self.assertIn(code, response.json()['detail'])

    def test_existing_mock_without_direct_state_keeps_catalog_fallback(self):
        self.login()
        self.assertFalse(hasattr(self.app.state.runtime,'run_state'))
        self.assertEqual(self.client.get('/api/runs/demo-run').status_code, 200)
        self.assertEqual(self.client.get('/api/runs/missing').status_code, 404)


if __name__ == '__main__':
    unittest.main()
