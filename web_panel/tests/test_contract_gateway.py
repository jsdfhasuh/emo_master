"""Gateway-only conformance checks; no physical Runtime or production devices."""
import asyncio
from pathlib import Path
import tempfile
import unittest
from fastapi.testclient import TestClient
from emo_web_panel.app import create_app
from emo_web_panel.config import Config
from emo_web_panel.services import Operations, Views

class PreviewGateTests(unittest.TestCase):
    def test_single_use_token_and_all_production_mutators_denied(self):
        with tempfile.TemporaryDirectory() as d:
            app=create_app(Config(Path(d),preview=True,preview_token='preview-token'))
            with TestClient(app) as client:
                response=client.post('/api/preview/session',json={'token':'preview-token'})
                self.assertEqual(response.status_code,200)
                csrf=response.json()['csrf'];self.assertEqual(response.json()['role'],'viewer')
                self.assertEqual(client.post('/api/preview/session',json={'token':'preview-token'}).status_code,403)
                headers={'x-csrf-token':csrf}
                for path in ['/api/runs/start','/api/runs/J1/stop','/api/packages','/api/packages/x/activate','/api/admin/users','/api/admin/runtimes']:
                    self.assertEqual(client.post(path,json={},headers=headers).status_code,403,path)
                self.assertEqual(client.delete('/api/admin/runtimes/configured',headers=headers).status_code,403)
                self.assertEqual(client.get('/api/me').status_code,200)

class OperationsContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_unknown_reconciles_only_original_instance_endpoint_and_request(self):
        class Client:
            address='explicit:123'
            def __init__(self):self.calls=0;self.queries=[]
            async def control(self,*args):self.calls+=1;return {'state':'unknown'}
            async def info(self):return {'runtimeInstanceId':'instance1','projectId':'p1'}
            async def lookup_start(self,rid,instance):self.queries.append((rid,instance));return {'state':'accepted','runId':'J1'}
        with tempfile.TemporaryDirectory() as d:
            ops=Operations(Path(d)/'ops.sqlite');client=Client()
            body={'runtimeInstanceId':'instance1','projectId':'p1'}
            result=await ops.execute('owner','request1','start','',body,client)
            result=await ops.reconcile(result,client)
            self.assertEqual(result['state'],'accepted');self.assertEqual(client.calls,1)
            self.assertEqual(client.queries,[('request1','instance1')])
            await ops.execute('owner','request1','start','',body,client);self.assertEqual(client.calls,1)
            client.address='other:123';result['state']='unknown'
            self.assertEqual((await ops.reconcile(result,client))['state'],'unknown');self.assertEqual(len(client.queries),1)

    async def test_preflight_rejection_is_not_unknown(self):
        class Client:
            async def control(self,*args):raise ValueError('stale revision; no Start sent')
        with tempfile.TemporaryDirectory() as d:
            result=await Operations(Path(d)/'ops.sqlite').execute('owner','request1','start','',{},Client())
            self.assertEqual(result['state'],'rejected')

class ViewContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_aliases_share_actual_identity_and_restart_cannot_leak_results(self):
        class Client:
            def __init__(self):self.calls=0
            async def subscribe(self,run):
                self.calls+=1
                await asyncio.Event().wait()
                yield None
        client=Client();views=Views(client)
        a=views.create('a','panel','J1',runtime_id='alias-a');b=views.create('b','panel','J1',runtime_id='alias-b')
        for v in (a,b):v.update(instanceId='instance1',projectId='project1')
        await views.attach(a);await views.attach(b);await asyncio.sleep(.01)
        self.assertEqual(client.calls,1);self.assertEqual(len(views.streams),1)
        views._deliver(views._key(a),{'runtimeInstanceId':'instance2','projectId':'project1','resultScopeId':'main','resultKey':'r1'})
        self.assertFalse(a['scopes']);self.assertEqual((await asyncio.wait_for(views.next_message(a),.5))['type'],'reset_required')
        await views.close()
