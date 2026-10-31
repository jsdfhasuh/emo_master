"""Independent synthetic targets must not masquerade as aliases of one Runtime."""
import asyncio
import unittest
import test_app
from emo_web_panel.mock_runtime import MockRuntime
from emo_web_panel.services import Views


class MockIdentityTests(unittest.IsolatedAsyncioTestCase):
    async def test_identity_is_consistent_and_cross_target_control_rejected(self):
        a,b=MockRuntime(),MockRuntime('line-b')
        self.assertEqual((await a.info())['runtimeInstanceId'],'mock-instance')
        self.assertNotEqual((await a.info())['runtimeInstanceId'],(await b.info())['runtimeInstanceId'])
        for runtime in (a,b):
            info=await runtime.info()
            for record in [*(await runtime.runs()),*(await runtime.workflows()),await runtime.snapshot('demo-run')]:
                self.assertEqual(record['runtimeInstanceId'],info['runtimeInstanceId'])
                if 'projectId' in record:self.assertEqual(record['projectId'],info['projectId'])
            body={**info,'workflowId':'demo-workflow'}
            self.assertEqual((await runtime.control('start','',body))['state'],'succeeded')
        with self.assertRaises(ValueError):
            await b.control('stop','demo-run',{**await a.info(),'workflowId':'demo-workflow'})
        self.assertEqual(b.control_count,1)

    async def test_same_instance_alias_shares_but_independent_b_stream_does_not(self):
        a,b=MockRuntime(),MockRuntime('line-b')
        views=Views(a,poll_interval=.01)
        prepared=[]
        try:
            for alias,runtime in [('configured',a),('alias-of-a',a),('line-b',b)]:
                view=views.create(alias,'demo-panel','demo-run',runtime_id=alias,runtime=runtime)
                info=await runtime.info();view.update(instanceId=info['runtimeInstanceId'],projectId=info['projectId'])
                await views.attach(view);prepared.append(view)
            self.assertEqual(len(views.streams),2)
            for view in prepared:
                message=await asyncio.wait_for(views.next_message(view),2)
                result=message['result']
                self.assertEqual(result['runtimeInstanceId'],view['instanceId'])
                expected='line-b' if view['runtimeId']=='line-b' else 'configured'
                self.assertEqual(result['sources']['image']['image']['url'],f'/api/assets/runtime/{expected}/demo-image')
        finally:await views.close()


class MockTargetHttpTests(unittest.TestCase):
    setUp=test_app.AppTests.setUp
    tearDown=test_app.AppTests.tearDown
    login=test_app.AppTests.login
    def test_b_only_viewer_receives_b_image_while_a_stream_is_open(self):
        headers=self.login()
        self.assertEqual(self.client.post('/api/admin/runtimes',headers=headers,
                         json={'id':'line-b','alias':'B','mode':'mock','address':''}).status_code,200)
        self.client.put('/api/admin/runtimes/line-b/grants',headers=headers,
                        json={'username':'viewer','view':True,'control':False})
        aid=self.client.post('/api/views',headers=headers,
                            json={'panelId':'demo-panel','runtimeId':'configured','runId':'demo-run'}).json()['id']
        with self.client.websocket_connect('/ws/views/'+aid) as aws:
            aresult=aws.receive_json()['result']
            vh=self.login('viewer')
            bid=self.client.post('/api/views',headers=vh,
                                json={'panelId':'demo-panel','runtimeId':'line-b','runId':'demo-run'}).json()['id']
            with self.client.websocket_connect('/ws/views/'+bid) as bws:
                bresult=bws.receive_json()['result']
                self.assertNotEqual(aresult['runtimeInstanceId'],bresult['runtimeInstanceId'])
                self.assertEqual(len(self.app.state.views.streams),2)
                image=bresult['sources']['image']['image']
                self.assertEqual(image['url'],'/api/assets/runtime/line-b/demo-image')
                self.assertEqual(self.client.get(image['url']).status_code,200)
                self.assertEqual(self.client.get(aresult['sources']['image']['image']['url']).status_code,403)


if __name__=='__main__':unittest.main()
