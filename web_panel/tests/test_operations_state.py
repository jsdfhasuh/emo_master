"""Command acknowledgement is not Job termination or resource release."""
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest

from emo_web_panel.services import Operations
from emo_web_panel.runtime_client import GrpcRuntimeClient


class Runtime:
    address='explicit:1234'
    def __init__(self):
        self.commands=[];self.lookups=[];self.info_calls=0
        self.response={'state':'accepted','outcome':'ACCEPTED','runId':'job','message':'accepted'}
        self.lookup={'state':'accepted','outcome':'ACCEPTED','runId':'job','message':'accepted'}
        self.identity={'runtimeInstanceId':'instance','projectId':'project'}
        self.run={**self.identity,'jobId':'job','runGroupId':'','status':'STOPPING',
                  'active':True,'ownsResources':True,'displayResourcesReleased':False}
    async def info(self):self.info_calls+=1;return dict(self.identity)
    async def control(self,action,target,body):
        self.commands.append((action,target,dict(body)))
        if self.response is None:raise TimeoutError('response lost')
        return dict(self.response)
    async def lookup_start(self,request,instance):
        self.lookups.append((request,instance));return dict(self.lookup)
    async def run_state(self,target):
        assert target=='job'
        return dict(self.run)


class OperationStateTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.ops=Operations(Path(self.tmp.name)/'web.sqlite');self.runtime=Runtime()
        self.body={'runtimeId':'configured','runtimeInstanceId':'instance','projectId':'project','runGroupId':''}
    async def asyncTearDown(self):self.tmp.cleanup()
    async def execute(self,action='start'):
        return await self.ops.execute('owner','request-'+action,action,'job' if action=='stop' else '',self.body,self.runtime)

    async def test_accepted_start_confirms_command_not_job_and_does_not_keep_querying(self):
        result=await self.execute()
        self.assertEqual(result['state'],'accepted');self.assertTrue(result['commandConfirmed'])
        self.assertEqual(result['runId'],'job');self.assertNotIn('jobTerminal',result)
        self.assertEqual(await self.ops.reconcile(result,self.runtime),result)
        self.assertEqual(self.runtime.info_calls,0);self.assertEqual(self.runtime.lookups,[])
        stop=await self.execute('stop')
        self.assertEqual(stop['state'],'accepted')
        self.assertEqual([call[0] for call in self.runtime.commands],['start','stop'])

    async def test_unknown_start_lookup_accepted_uses_same_transition_without_replay(self):
        self.runtime.response=None
        result=await self.execute();self.assertFalse(result['commandConfirmed'])
        confirmed=await self.ops.reconcile(result,self.runtime)
        self.assertEqual(confirmed['state'],'accepted');self.assertTrue(confirmed['commandConfirmed'])
        self.assertEqual(confirmed['runId'],'job');self.assertNotIn('jobTerminal',confirmed)
        self.assertEqual(self.runtime.lookups,[('request-start','instance')])
        again=await self.execute()
        self.assertEqual(again['state'],'accepted');self.assertEqual(len(self.runtime.commands),1)

    async def test_accepted_stop_reports_stopping_then_actual_terminal_and_retained_resources(self):
        result=await self.execute('stop')
        self.assertEqual(result['state'],'accepted');self.assertNotIn('jobTerminal',result)
        progress=await self.ops.reconcile(result,self.runtime)
        self.assertEqual(progress['state'],'accepted');self.assertFalse(progress['jobTerminal'])
        self.assertTrue(progress['run']['ownsResources']);self.assertIn('stopping',progress['message'])
        self.runtime.run.update(status='ABORTED',active=False)
        ended=await self.ops.reconcile(progress,self.runtime)
        self.assertEqual(ended['state'],'succeeded');self.assertTrue(ended['jobTerminal'])
        self.assertEqual(ended['run']['status'],'ABORTED')
        self.assertTrue(ended['run']['ownsResources']);self.assertFalse(ended['run']['displayResourcesReleased'])
        self.assertEqual(len(self.runtime.commands),1)

    async def test_unknown_stop_aborted_resolves_original_empty_group_without_inventing_ack(self):
        self.runtime.response=None;result=await self.execute('stop')
        self.runtime.run.update(status='ABORTED',active=False)
        resolved=await self.ops.reconcile(result,self.runtime)
        self.assertEqual(resolved['state'],'succeeded');self.assertTrue(resolved['jobTerminal'])
        self.assertTrue(resolved['commandConfirmed']);self.assertEqual(resolved['outcome'],'UNKNOWN')
        self.assertEqual(resolved['run']['runGroupId'],'')
        self.assertTrue(resolved['run']['ownsResources']);self.assertFalse(resolved['run']['displayResourcesReleased'])
        self.assertEqual(len(self.runtime.commands),1)

    async def test_stop_reconciliation_rejects_changed_original_identity(self):
        self.runtime.response=None;result=await self.execute('stop')
        self.runtime.run.update(status='ABORTED',active=False)
        for field in ('runtimeInstanceId','projectId','jobId','runGroupId'):
            with self.subTest(field=field):
                old=self.runtime.run[field];self.runtime.run[field]='changed'
                self.assertEqual((await self.ops.reconcile(result,self.runtime))['state'],'unknown')
                self.runtime.run[field]=old
        self.runtime.address='another:1234'
        self.assertEqual((await self.ops.reconcile(result,self.runtime))['state'],'unknown')
        self.runtime.address='explicit:1234';self.runtime.identity['runtimeInstanceId']='new-instance'
        self.assertEqual((await self.ops.reconcile(result,self.runtime))['state'],'unknown')
        self.assertEqual(len(self.runtime.commands),1)

    async def test_rejection_keeps_reason_not_generic_acceptance(self):
        self.runtime.response={'state':'rejected','outcome':'REJECTED','message':'Workflow is not a production entry'}
        result=await self.execute()
        self.assertEqual(result['state'],'rejected');self.assertEqual(result['message'],'Workflow is not a production entry')
        self.assertTrue(result['commandConfirmed'])
        self.assertEqual(await self.ops.reconcile(result,self.runtime),result)

    async def test_accepted_without_job_identity_remains_queryable_unknown(self):
        self.runtime.response.update(runId=None)
        result=await self.execute();self.assertEqual(result['state'],'unknown')
        self.runtime.lookup.update(runId=None)
        self.assertEqual((await self.ops.reconcile(result,self.runtime))['state'],'unknown')
        reply=SimpleNamespace(ok=True,job_id='',status='ACCEPTED',message='',project_revision=1)
        self.assertEqual(GrpcRuntimeClient._start_result(reply,'request')['state'],'unknown')
