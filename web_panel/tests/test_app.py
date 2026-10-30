import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from fastapi.testclient import TestClient
from emo_web_panel.app import create_app
from emo_web_panel.config import Config
from emo_web_panel.runtime_client import ContractUnavailable

class AppTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.app=create_app(Config(Path(self.tmp.name)))
        self.app.state.auth.create_user('admin','safe-test-password','admin')
        self.app.state.auth.create_user('viewer','safe-test-password','viewer')
        self.client=TestClient(self.app)
        self.client.__enter__()
    def tearDown(self):self.client.__exit__(None,None,None);self.tmp.cleanup()
    def login(self,name='admin'):
        r=self.client.post('/api/session',json={'username':name,'password':'safe-test-password'})
        self.assertEqual(r.status_code,200)
        return {'X-CSRF-Token':r.json()['csrf']}
    def test_login_csrf_permissions(self):
        self.assertEqual(self.client.get('/api/runs').status_code,401)
        h=self.login('viewer')
        self.assertEqual(self.client.post('/api/runs/start',headers=h,json={}).status_code,403)
        self.assertEqual(self.client.get('/api/admin/users').status_code,403)
        self.assertEqual(self.client.post('/api/logout').status_code,403)
        self.assertEqual(self.client.post('/api/logout',headers=h).status_code,200)
        self.assertEqual(self.client.get('/api/me').status_code,401)
    def test_admin_crud_and_request_lookup(self):
        h=self.login()
        self.assertEqual(self.client.post('/api/admin/users',headers=h,json={'username':'other','password':'another-safe-password','role':'operator'}).status_code,200)
        self.assertEqual(self.client.patch('/api/admin/users/other',headers=h,json={'role':'viewer'}).status_code,200)
        self.assertEqual(self.client.delete('/api/admin/users/other',headers=h).status_code,200)
        self.assertEqual(self.client.delete('/api/admin/users/admin',headers=h).status_code,422)
        self.assertEqual(self.client.get('/api/operations/by-request/missing-request').status_code,404)
        b={'requestId':'lookup-123456','runtimeInstanceId':'mock-instance','projectId':'demo-project','workflowId':'demo-workflow'}
        result=self.client.post('/api/runs/start',headers=h,json=b).json()
        self.assertEqual(self.client.get('/api/operations/by-request/lookup-123456').json(),result)
        self.login('viewer')
        self.assertEqual(self.client.get('/api/operations/by-request/lookup-123456').status_code,404)
    def test_history_filter_and_opaque_images(self):
        h=self.login();v=self.client.post('/api/views',headers=h,json={'panelId':'demo-panel','runId':'demo-run'}).json()['id']
        rows=self.client.get(f'/api/views/{v}/history/history-table?q=NG&sort=id&order=desc&pageSize=2').json()
        self.assertEqual(rows['total'],7);self.assertEqual([r['id'] for r in rows['rows']],['28','24'])
        self.assertEqual(self.client.get('/api/history-images/demo-image').headers['content-type'],'image/png')
        self.assertEqual(self.client.get('/api/history-images/arbitrary-file').status_code,404)
    def test_login_origin(self):
        self.assertEqual(self.client.post('/api/session',headers={'Origin':'https://evil.example'},json={}).status_code,403)
    def test_idempotent_start_and_explicit_new_intent(self):
        h=self.login();body={'requestId':'intent-123456','runtimeInstanceId':'mock-instance','projectId':'demo-project','workflowId':'demo-workflow'}
        a=self.client.post('/api/runs/start',headers=h,json=body)
        self.assertEqual(a.status_code,200);self.assertEqual(a.json()['state'],'succeeded')
        b=self.client.post('/api/runs/start',headers=h,json=body)
        self.assertEqual(a.json(),b.json());self.assertEqual(self.app.state.runtime.control_count,1)
        body['workflowId']='other'
        self.assertEqual(self.client.post('/api/runs/start',headers=h,json=body).status_code,422)
        self.assertEqual(self.client.get('/api/operations/'+a.json()['operationId']).json()['state'],'succeeded')
    def test_view_owner_history_and_websocket(self):
        h=self.login();v=self.client.post('/api/views',headers=h,json={'panelId':'demo-panel','runId':'demo-run'}).json()['id']
        self.assertEqual(self.client.get(f'/api/views/{v}/history/history-table').json()['total'],30)
        self.assertEqual(self.client.get(f'/api/views/{v}/history/not-declared').status_code,404)
        with self.client.websocket_connect(f'/ws/views/{v}') as ws:
            msg=ws.receive_json();self.assertEqual(msg['type'],'result')
            r=self.client.post(f'/api/views/{v}/freeze',headers=h,json={'resultKey':msg['result']['resultKey']})
            self.assertEqual(r.status_code,200)
        self.login('viewer')
        self.assertEqual(self.client.get(f'/api/views/{v}/history/history-table').status_code,404)
    def test_wrong_runtime_no_side_effect(self):
        h=self.login();r=self.client.post('/api/runs/start',headers=h,json={'requestId':'wrong-123456','runtimeInstanceId':'wrong','projectId':'demo-project','workflowId':'demo-workflow'})
        self.assertEqual(r.json()['state'],'unknown');self.assertEqual(self.app.state.runtime.control_count,0)
    def test_preview_is_readonly_even_admin(self):
        self.app= create_app(Config(Path(self.tmp.name),preview=True))
        with TestClient(self.app) as c:
            me=c.post('/api/session',json={'username':'admin','password':'safe-test-password'}).json()
            self.assertEqual(c.post('/api/runs/start',headers={'X-CSRF-Token':me['csrf']},json={}).status_code,403)
    def test_real_mode_never_falls_back(self):
        class Missing:
            async def start(self):pass
            async def close(self):pass
            async def runs(self):raise ContractUnavailable('SDK missing')
        app=create_app(Config(Path(self.tmp.name),mode='grpc',runtime_address='127.0.0.1:50051'),Missing())
        with TestClient(app) as c:
            c.post('/api/session',json={'username':'admin','password':'safe-test-password'})
            self.assertEqual(c.get('/api/runs').status_code,503)
            self.assertEqual(c.get('/api/assets/demo-image').status_code,404)
            self.assertEqual(c.get('/api/panels').json(),[])

if __name__=='__main__':unittest.main()
