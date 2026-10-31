"""SDK 1.1.1 ordinary/grouped messages and exact optional Stop identity."""
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
import unittest

from emo_panel_protocol.messages import BrowserMessage
from emo_panel_protocol.generated import runtime_pb2 as pb
from emo_web_panel.services import Views
from emo_web_panel.runtime_client import GrpcRuntimeClient

FIXTURES=Path(__file__).resolve().parents[1]/'contracts'/'panel-contract-v1'/'fixtures'


def state(group):
    return {'runtimeInstanceId':'instance','projectId':'project','jobId':'job','runGroupId':group,
            'status':'ABORTED','active':False,'ownsResources':True,'displayResourcesReleased':False,
            'allowedActions':['view']}


class FormalBrowserTests(unittest.IsolatedAsyncioTestCase):
    async def test_actual_view_attach_and_state_use_formal_messages_for_both_groups(self):
        for group in ('','real-group'):
            with self.subTest(group=group):
                class Runtime:
                    async def run_state(self,job):return state(group)
                    async def snapshot(self,job):return []
                views=Views(Runtime());view=views.create('owner','panel','job')
                view.update(instanceId='instance',projectId='project')
                try:
                    await views.attach(view)
                    messages=[(await asyncio.wait_for(views.next_message(view),.5))['contractMessage'] for _ in range(2)]
                    self.assertEqual([m['event'] for m in messages],['job_changed','job_state'])
                    for message in messages:
                        checked=BrowserMessage.model_validate(message).model_dump(mode='json')
                        self.assertEqual(checked['runGroupId'],group)
                        self.assertEqual(checked['jobId'],'job')
                    self.assertEqual(messages[1]['status'],'ABORTED')
                    self.assertTrue(messages[1]['ownsResources'])
                    self.assertFalse(messages[1]['displayResourcesReleased'])
                finally:await views.close()

    async def test_control_result_known_empty_or_real_group_and_unknown_nullable(self):
        client=GrpcRuntimeClient('127.0.0.1:1234')
        for group in ('','real-group'):
            async def run_state(job):return state(group)
            client.run_state=run_state
            for operation in ('start','stop'):
                intent={'runtimeInstanceId':'instance','projectId':'project','target':'job','runGroupId':group,'requestId':'request-123'}
                message=await client.control_message(operation,{'state':'accepted','outcome':'ACCEPTED','runId':'job','message':'Accepted'},intent)
                self.assertEqual(message['event'],'control_result');self.assertEqual(message['runGroupId'],group)
                BrowserMessage.model_validate(message)
        unknown=await client.control_message('start',{'state':'unknown','outcome':'UNKNOWN','message':'Response lost'},
            {'runtimeInstanceId':'instance','projectId':'project','target':'','requestId':'request-123'})
        self.assertIsNone(unknown['jobId']);self.assertIsNone(unknown['runGroupId'])
        self.assertEqual(unknown['outcome'],'UNKNOWN')
        async def disconnected(job):raise ConnectionError('optional state read unavailable')
        client.run_state=disconnected
        accepted=await client.control_message('start',{'state':'accepted','outcome':'ACCEPTED','runId':'job','message':'Accepted'},
            {'runtimeInstanceId':'instance','projectId':'project','target':'','requestId':'request-123'})
        self.assertEqual(accepted['outcome'],'ACCEPTED')
        self.assertEqual(accepted['jobId'],'job');self.assertIsNone(accepted['runGroupId'])

    async def test_stop_keeps_optional_empty_or_group_presence_and_rejects_absence(self):
        client=GrpcRuntimeClient('127.0.0.1:1234');client.pb=pb;requests=[]
        async def info():return SimpleNamespace(runtime_instance_id='instance',project_id='project')
        async def stop(request,timeout):
            requests.append(request);return pb.StopJobReply(ok=True,status='STOP_REQUESTED')
        client._info=info;client.runtime=SimpleNamespace(StopJob=stop)
        body={'runtimeInstanceId':'instance','projectId':'project'}
        for group in ('','real-group'):
            result=await client.control('stop','job',{**body,'runGroupId':group})
            self.assertEqual(result['state'],'accepted')
            self.assertTrue(requests[-1].HasField('expected_run_group_id'))
            self.assertEqual(requests[-1].expected_run_group_id,group)
            self.assertEqual(requests[-1].job_id,'job')
        for invalid in (body,{**body,'runGroupId':None}):
            with self.assertRaises(ValueError):await client.control('stop','job',invalid)
        self.assertEqual(len(requests),2)

    async def test_official_fixtures_keep_ordinary_empty_and_grouped_real_ids(self):
        for filename in ('ungrouped-running','ungrouped-job-changed','ungrouped-start-accepted',
                         'ungrouped-stop-accepted','ungrouped-aborted','job-changed','start-accepted','stop-accepted'):
            message=Views.formal_message(json.loads((FIXTURES/(filename+'.json')).read_text()))
            self.assertEqual(message['runGroupId']=='',filename.startswith('ungrouped-'))
        ordinary=json.loads((FIXTURES/'ungrouped-running.json').read_text())
        for bad in ({k:v for k,v in ordinary.items() if k!='runGroupId'},{**ordinary,'runGroupId':None}):
            with self.assertRaises(ValueError):Views.formal_message(bad)
