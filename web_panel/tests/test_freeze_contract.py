"""Gateway freeze/stream/control reconciliation regressions; no real Runtime."""
import asyncio
from pathlib import Path
import tempfile
import unittest

from emo_web_panel.services import Operations, Views


def closed(job='root-job', scope='root', key='r1'):
    return {'runtimeInstanceId':'instance','projectId':'project','jobId':job,'resultScopeId':scope,
            'resultKey':key,'cursor':'1','sources':{'image':{'state':'available','image':{'resourceId':key}}},
            'closedResult':{'identity':{'jobId':job,'resultKey':key},'sources':[{'state':'AVAILABLE'}]}}


class FreezeContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_terminal_freeze_expires_without_another_frame(self):
        views=Views(object(),freeze_ttl=.02)
        view=views.create('u','p','root-job')
        views._receive(view,closed())
        views.freeze(view,{'root':'r1'})
        self.assertIn('contractMessage',await views.next_message(view))
        notice=await asyncio.wait_for(views.next_message(view),.5)
        self.assertEqual(notice['type'],'freeze_expired')
        self.assertTrue(view['freezeExpired'])
        self.assertEqual(view['last']['sources']['image']['reasonCode'],'FREEZE_EXPIRED')
        self.assertNotIn('closedResult',view['last'])
        with self.assertRaises(KeyError):views.advertised_result(view,'root','r1')
        views._receive(view,closed(key='r2'))
        self.assertTrue(view['queue'].empty(),'Do not reemit expired official AVAILABLE')
        views.resume(view)
        resumed=await views.next_message(view)
        self.assertEqual(resumed['contractMessage']['result']['identity']['resultKey'],'r2')
        await views.close()

    async def test_timer_cancelled_on_resume_remove_and_close(self):
        views=Views(object(),freeze_ttl=.03)
        for action in ('resume','remove','close'):
            view=views.create('u','p','root-job')
            views._receive(view,closed());views.freeze(view,{'root':'r1'})
            handle=view['freezeTimer']
            if action=='resume':views.resume(view)
            elif action=='remove':await views.remove(view['id'])
            else:await views.close()
            self.assertTrue(handle.cancelled())
            self.assertIsNone(view['freezeTimer'])
            await asyncio.sleep(.04)
            self.assertNotIn('freeze_expired',view['notices'])
        await views.close()

    async def test_slow_asset_uses_exact_advertised_recent_result_not_newest(self):
        views=Views(object())
        view=views.create('u','p','root-job')
        views._receive(view,closed(key='r1'))
        views._receive(view,closed(key='r2'))
        selected,frozen=views.advertised_result(view,'root','r1')
        self.assertEqual(selected['sources']['image']['image']['resourceId'],'r1')
        self.assertFalse(frozen)
        views.freeze(view,{'root':'r1'})
        selected,frozen=views.advertised_result(view,'root','r1')
        self.assertTrue(frozen)
        for i in range(3,9):views._receive(view,closed(key=f'r{i}'))
        self.assertEqual(views.advertised_result(view,'root','r1')[0]['resultKey'],'r1')
        with self.assertRaises(KeyError):views.advertised_result(view,'root','r2')
        await views.close()

    async def test_frozen_read_failure_is_gateway_message_not_forged_contract(self):
        views=Views(object());view=views.create('u','p','root-job')
        views._receive(view,closed());views.freeze(view,{'root':'r1'})
        frozen=view['frozenScopes']['root']
        frozen['sources']['image']={'state':'unavailable','reasonCode':'FROZEN_RESOURCE_UNAVAILABLE'}
        frozen['gatewayTransformed']=True;frozen.pop('closedResult',None)
        views.refresh_frozen(view)
        message=await views.next_message(view)
        self.assertNotIn('contractMessage',message)
        self.assertEqual(message['result']['sources']['image']['state'],'unavailable')
        await views.close()

    async def test_group_shares_each_member_and_never_joins_unrelated_job(self):
        class Runtime:
            def __init__(self):self.calls=[]
            async def snapshot(self,job):
                self.calls.append(job)
                return closed(job,'root' if job=='root-job' else 'consumer')
        runtime=Runtime();views=Views(runtime,poll_interval=10)
        group=views.create('a','p','root-job');solo=views.create('b','p','child-job')
        for view in (group,solo):view.update(instanceId='instance',projectId='project')
        group['runIds']=['root-job','child-job'];solo['runIds']=['child-job']
        await views.attach(group);await views.attach(solo);await asyncio.sleep(.01)
        self.assertEqual(set(group['scopes']),{'root','consumer'})
        self.assertEqual(set(solo['scopes']),{'consumer'})
        self.assertCountEqual(runtime.calls,['root-job','child-job'])
        self.assertEqual(len(views.streams),2)
        views._deliver(('instance','project','child-job'),closed('unrelated-job','other'))
        self.assertNotIn('other',group['scopes'])
        await views.remove(group['id'])
        self.assertEqual(list(views.streams),[('instance','project','child-job')])
        await views.close();self.assertFalse(views.streams)


class StopReconcileTests(unittest.IsolatedAsyncioTestCase):
    async def test_stop_timeout_observes_original_job_terminal_without_replay(self):
        class Runtime:
            address='127.0.0.1:1234'
            def __init__(self):self.calls=0;self.active=True;self.group='group'
            async def info(self):return {'runtimeInstanceId':'instance','projectId':'project'}
            async def control(self,*args):self.calls+=1;raise TimeoutError()
            async def run_state(self,target):
                self.asserted_target=target
                return {'runtimeInstanceId':'instance','projectId':'project','jobId':target,
                        'runGroupId':self.group,'status':'RUNNING' if self.active else 'CANCELLED','active':self.active}
        with tempfile.TemporaryDirectory() as directory:
            ops=Operations(Path(directory)/'ops.sqlite');runtime=Runtime()
            body={'runtimeId':'configured','runtimeInstanceId':'instance','projectId':'project','runGroupId':'group'}
            result=await ops.execute('u','request-123','stop','original-job',body,runtime)
            self.assertEqual(result['state'],'unknown')
            self.assertEqual((await ops.reconcile(result,runtime))['state'],'unknown')
            runtime.active=False;runtime.group='other-group'
            self.assertEqual((await ops.reconcile(result,runtime))['state'],'unknown')
            runtime.group='group'
            resolved=await ops.reconcile(result,runtime)
            self.assertEqual(resolved['state'],'succeeded')
            self.assertEqual(resolved['status'],'CANCELLED')
            self.assertEqual(runtime.asserted_target,'original-job')
            self.assertEqual(runtime.calls,1)
            self.assertEqual(ops.get(result['operationId'],'u')['state'],'succeeded')
