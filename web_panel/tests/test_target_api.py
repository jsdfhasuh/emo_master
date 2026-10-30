"""Real HTTP target permissions with explicit mock adapters; no SDK claims."""
import unittest
import test_app

class TargetApiTests(unittest.TestCase):
    setUp=test_app.AppTests.setUp
    tearDown=test_app.AppTests.tearDown
    login=test_app.AppTests.login
    def test_target_acl_covers_http_resources_history_and_controls(self):
        h=self.login()
        self.assertEqual(self.client.post('/api/admin/runtimes',headers=h,json={'id':'other','alias':'Other line','mode':'mock','address':''}).status_code,200)
        self.client.put('/api/admin/runtimes/other/grants',headers=h,json={'username':'viewer','view':True,'control':False})
        self.login('viewer')
        self.assertEqual([r['id'] for r in self.client.get('/api/runtimes').json()],['other'])
        self.assertEqual(self.client.get('/api/runtimes/configured/runs').status_code,403)
        self.assertEqual(self.client.get('/api/assets/demo-image').status_code,403)
        self.assertEqual(self.client.get('/api/assets/runtime/other/demo-image').status_code,200)
        self.assertEqual(self.client.get('/api/history-images/configured:demo-image').status_code,403)
        self.assertNotIn('address',self.client.get('/api/runtimes').json()[0])
    def test_no_run_history_workflow_and_target_change_guard(self):
        h=self.login();self.app.state.runtime.running=False
        workflows=self.client.get('/api/runtimes/configured/workflows').json()
        self.assertEqual(workflows[0]['projectId'],'demo-project')
        view=self.client.post('/api/views',headers=h,json={'runtimeId':'configured','panelId':'demo-panel','runId':''}).json()['id']
        self.assertEqual(self.client.get(f'/api/views/{view}/history/history-count').json()['value'],30)
        self.assertEqual(self.client.patch('/api/admin/runtimes/configured',headers=h,json={'alias':'changed'}).status_code,409)
        self.client.delete('/api/views/'+view,headers=h)
        self.assertEqual(self.client.patch('/api/admin/runtimes/configured',headers=h,json={'alias':'changed'}).status_code,200)
    def test_new_same_name_user_cannot_read_old_operation(self):
        h=self.login()
        self.client.post('/api/admin/users',headers=h,json={'username':'temp','password':'safe-test-password','role':'operator'})
        self.client.put('/api/admin/runtimes/configured/grants',headers=h,json={'username':'temp','view':True,'control':True})
        th=self.login('temp')
        body={'requestId':'old-intent-123','configurationRevision':'1','runtimeInstanceId':'mock-instance','projectId':'demo-project','workflowId':'demo-workflow'}
        self.assertEqual(self.client.post('/api/runs/start',headers=th,json=body).status_code,200)
        old=self.client.get('/api/me').json()['userId']
        h=self.login();self.client.delete('/api/admin/users/temp',headers=h)
        self.client.post('/api/admin/users',headers=h,json={'username':'temp','password':'safe-test-password','role':'operator'})
        self.login('temp')
        self.assertNotEqual(old,self.client.get('/api/me').json()['userId'])
        self.assertEqual(self.client.get('/api/operations/by-request/old-intent-123').status_code,404)
        self.assertEqual(self.client.get('/api/runtimes').json(),[])
    def test_viewer_acl_revocation_closes_ws(self):
        h=self.login();self.client.put('/api/admin/runtimes/configured/grants',headers=h,json={'username':'viewer','view':True,'control':False})
        vh=self.login('viewer');vid=self.client.post('/api/views',headers=vh,json={'panelId':'demo-panel','runId':'demo-run'}).json()['id']
        with self.client.websocket_connect('/ws/views/'+vid) as ws:
            self.assertEqual(ws.receive_json()['type'],'result')
            self.app.state.registry.set_acl({'username':'admin','role':'admin'},'configured','viewer',False)
            from starlette.websockets import WebSocketDisconnect
            with self.assertRaises(WebSocketDisconnect):
                for _ in range(5):ws.receive_json()

    def test_persisted_runtime_address_wins_over_bootstrap_cli(self):
        from emo_web_panel.app import create_app
        from emo_web_panel.config import Config
        from pathlib import Path
        h=self.login()
        result=self.client.patch('/api/admin/runtimes/configured',headers=h,json={'mode':'grpc','address':'192.0.2.10:50051'})
        self.assertEqual(result.status_code,200)
        restarted=create_app(Config(Path(self.tmp.name)))
        self.assertEqual(restarted.state.runtime.address,'192.0.2.10:50051')
    def test_package_static_assets_require_associated_target(self):
        from test_packages import bundle
        h=self.login();item=self.client.post('/api/packages',headers=h,content=bundle()).json()
        self.client.post('/api/packages/'+item['packageId']+'/activate',headers=h,json={'runtimeIds':['configured']})
        self.login('viewer')
        self.assertEqual(self.client.get('/api/assets/'+item['packageId']+'/sample').status_code,403)
        self.assertEqual(self.client.get('/api/panels').json(),[])
    def test_mock_status_is_runtime_owned_and_events_use_cursor(self):
        h=self.login()
        first=self.client.get('/api/runtimes/configured/status').json()
        self.assertEqual(first['events']['events'],[])
        self.client.post('/api/runs/start',headers=h,json={'requestId':'event-test-123','configurationRevision':'1','runtimeInstanceId':'mock-instance','projectId':'demo-project','workflowId':'demo-workflow'})
        status=self.client.get('/api/runtimes/configured/status').json()
        self.assertEqual(len(status['events']['events']),1)
        self.assertEqual(status['counters']['source'],'mock-runtime-authoritative')
        self.assertEqual(self.client.get('/api/runtimes/configured/status?after=1').json()['events']['events'],[])

    def test_duplicate_package_association_does_not_change_active(self):
        from test_packages import bundle
        h=self.login()
        a=self.client.post('/api/packages',headers=h,content=bundle('a')).json()
        b=self.client.post('/api/packages',headers=h,content=bundle('b')).json()
        self.client.post('/api/packages/'+a['packageId']+'/activate',headers=h)
        result=self.client.post('/api/packages/'+b['packageId']+'/activate',headers=h,json={'runtimeIds':['configured','configured']})
        self.assertEqual(result.status_code,422)
        self.assertEqual(self.client.get('/api/panels').json()[0]['id'],a['packageId'])
