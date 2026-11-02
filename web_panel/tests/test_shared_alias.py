"""A physical shared stream retains its client after its originating alias view closes."""
import unittest
from unittest.mock import patch
from emo_web_panel.mock_runtime import MockRuntime
import test_app


class SharedAliasTests(unittest.TestCase):
    setUp=test_app.AppTests.setUp
    tearDown=test_app.AppTests.tearDown
    login=test_app.AppTests.login

    def test_alias_client_cannot_close_while_other_alias_uses_shared_stream(self):
        headers=self.login()
        self.assertEqual(self.client.post('/api/admin/runtimes',headers=headers,
            json={'id':'alias-b','alias':'Same Runtime B','mode':'mock','address':''}).status_code,200)
        def create(alias):
            response=self.client.post('/api/views',headers=headers,
                json={'runtimeId':alias,'panelId':'demo-panel','runId':'demo-run'})
            self.assertEqual(response.status_code,200)
            return response.json()['id']
        # These directory entries intentionally alias ONE physical Runtime,
        # unlike the normal independent mock-target fixture. Preserve that
        # authoritative identity in every method, not just the info response.
        original_init=MockRuntime.__init__
        shared_instance=self.app.state.runtime.instance_id
        def alias_init(runtime,runtime_id='configured'):
            original_init(runtime,runtime_id)
            runtime.instance_id=shared_instance
        with patch.object(MockRuntime,'__init__',alias_init):
            a,b=create('configured'),create('alias-b')
        with self.client.websocket_connect('/ws/views/'+a) as wa:
            wa.receive_json()
            with self.client.websocket_connect('/ws/views/'+b) as wb:
                wb.receive_json()
                self.assertEqual(len(self.app.state.views.streams),1)
                self.assertEqual(self.client.delete('/api/views/'+a,headers=headers).status_code,200)
                self.assertFalse(any(v['runtimeId']=='configured' for v in self.app.state.views.views.values()))
                self.assertEqual(self.client.patch('/api/admin/runtimes/configured',headers=headers,
                    json={'alias':'changed'}).status_code,409)
                deleted=self.client.delete('/api/admin/runtimes/configured?revoke_acl=true',headers=headers)
                self.assertEqual(deleted.status_code,422)
                self.assertIn('active references',deleted.json()['detail'])
                self.assertEqual(len(self.app.state.views.streams),1)
                self.assertEqual(self.client.delete('/api/views/'+b,headers=headers).status_code,200)
                self.assertFalse(self.app.state.views.streams)
                self.assertEqual(self.client.patch('/api/admin/runtimes/configured',headers=headers,
                    json={'alias':'changed'}).status_code,200)
