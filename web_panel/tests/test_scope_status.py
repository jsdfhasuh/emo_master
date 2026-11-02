"""Scope lifecycle metadata must survive snapshots with no result DTOs."""
import asyncio
import unittest
from emo_web_panel.services import Views


def result(ordinal,scope='scope'):
    identity={'runtimeInstanceId':'instance','jobId':'job','resultScopeId':scope,
              'resultKey':'r'+str(ordinal),'resultOrdinal':str(ordinal)}
    return {**identity,'projectId':'project','cursor':str(ordinal),
            'sources':{'value':{'state':'available','value':ordinal}},
            'closedResult':{'identity':identity,'sources':[{'sourceId':'value','state':'AVAILABLE','valueJson':str(ordinal)}]}}


def status(started=None,expired=None):
    return {'type':'scope_status','event':'scope_status','runtimeInstanceId':'instance',
            'projectId':'project','jobId':'job','cursor':'99',
            'latestStartedOrdinals':started or {},'expiredScopeOrdinals':expired or {}}


class ScopeStatusTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.views=Views(object())
        self.view=self.views.create('owner','panel','job')
        self.view.update(instanceId='instance',projectId='project')
        self.key=self.views._key(self.view)
        self.views.streams[self.key]={'runtime':self.views.runtime,'viewers':{self.view['id']},
            'latest':{},'task':None,'stateTask':None,'scopeStatus':None}

    async def asyncTearDown(self):
        await self.views.close()

    async def test_latest_started_keeps_complete_result_and_forwards_decimal(self):
        self.views._deliver(self.key,result(1))
        self.views._deliver(self.key,status(started={'scope':'9007199254740993'}))
        message=await self.views.next_message(self.view)
        self.assertEqual(message['type'],'scope_status')
        self.assertEqual(message['latestStartedOrdinals']['scope'],'9007199254740993')
        self.assertEqual(self.view['scopes']['scope']['resultKey'],'r1')
        self.assertIn('contractMessage',await self.views.next_message(self.view))

    async def test_empty_expiry_snapshot_retires_live_and_queued_original(self):
        self.views._deliver(self.key,result(1))
        self.views._deliver(self.key,status(expired={'scope':'1'}))
        self.assertEqual((await self.views.next_message(self.view))['expiredScopeOrdinals'],{'scope':'1'})
        self.assertNotIn('scope',self.view['scopes'])
        self.assertNotIn('scope',self.view['latest'])
        self.assertFalse(self.views.streams[self.key]['latest'])
        self.assertTrue(self.view['queue'].empty())
        with self.assertRaises(KeyError):self.views.advertised_result(self.view,'scope','r1')
        self.views._deliver(self.key,result(1))
        self.assertNotIn('scope',self.view['scopes'],'A retired snapshot must not revive live availability')
        self.views._deliver(self.key,result(2))
        self.assertEqual(self.view['scopes']['scope']['resultKey'],'r2')

    async def test_expiry_threshold_preserves_newer_result_other_scope_and_frozen(self):
        self.views._deliver(self.key,result(1))
        self.views.freeze(self.view,{'scope':'r1'})
        self.views._deliver(self.key,result(2))
        self.views._deliver(self.key,result(1,'other'))
        self.views._deliver(self.key,status(expired={'scope':'2'}))
        notice=await self.views.next_message(self.view)
        self.assertEqual(notice['frozenScopes'],['scope'])
        self.assertEqual(self.view['scopes']['scope']['resultKey'],'r1')
        self.assertEqual(self.views.advertised_result(self.view,'scope','r1')[0]['sources']['value']['state'],'available')
        self.assertIn('other',self.view['scopes'])
        self.views.resume(self.view)
        self.assertNotIn('scope',self.view['scopes'])
        self.assertIn('other',self.view['scopes'])
        self.views._deliver(self.key,result(4))
        self.views._deliver(self.key,status(expired={'scope':'3'}))
        self.assertEqual(self.view['scopes']['scope']['resultKey'],'r4')

    async def test_late_view_inherits_expiry_even_without_cached_result(self):
        self.views._deliver(self.key,result(1))
        self.views._deliver(self.key,status(started={'scope':'2'},expired={'scope':'1'}))
        late=self.views.create('other','panel','job')
        late.update(instanceId='instance',projectId='project')
        await self.views.attach(late)
        event=await asyncio.wait_for(self.views.next_message(late),.5)
        self.assertEqual(event['type'],'scope_status')
        self.assertEqual(event['expiredScopeOrdinals'],{'scope':'1'})
        self.assertFalse(late['scopes'])
