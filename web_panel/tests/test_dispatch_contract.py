"""SDK-validated Dispatch membership; no controls or inferred group ownership."""
import json
from pathlib import Path
import zipfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

from emo_panel_protocol.canonical import revisionOf
from emo_panel_protocol.coverage import CaptureCoverage
from emo_panel_protocol.generated import runtime_pb2 as pb
from emo_panel_protocol.models import DataSource, ResultScope
from emo_web_panel.runtime_client import GrpcRuntimeClient, RuntimeRejected


def metadata(job, source, scope, *, group='group', producer='root', slot=''):
    item = DataSource.model_validate({'kind':'global_variable','resultScopeId':scope,
        'variableId':source,'expectedType':'integer'}).model_dump()
    spec = ResultScope.model_validate({'entryWorkflowId':job,'scopeWorkflowId':job}).model_dump()
    definition = {'ruleVersion':'1.0','sources':[item],'scopes':{scope:spec}}
    return pb.DisplayJob(runtime_instance_id='instance',project_id='project',job_id=job,
        run_group_id=group,source_job_id=producer,target_slot_id=slot,capture_enabled=True,
        capture_plan_revision=revisionOf(definition),execution_revision='a'*64,source_ids=[source],
        sources_json=json.dumps({source:item}),capture_definition_json=json.dumps(definition))


def run(job, *, group='group', producer='root', slot='', instance='instance', project='project'):
    return {'id':job,'runtimeInstanceId':instance,'projectId':project,'runGroupId':group,
            'sourceJobId':producer,'targetSlotId':slot}


class DispatchContractTests(unittest.IsolatedAsyncioTestCase):
    def client(self, root, runs, jobs):
        client = GrpcRuntimeClient('127.0.0.1:1234')
        client.pb = pb
        client.run_state = AsyncMock(return_value=root)
        client.runs = AsyncMock(return_value=runs)
        client.display = SimpleNamespace(ListJobs=AsyncMock(return_value=pb.DisplayJobs(jobs=jobs)))
        return client

    async def test_root_includes_only_exact_direct_consumers(self):
        root = run('root')
        child = run('child',slot='slot-1')
        unrelated = [run('other',group='other',slot='x'),run('wrong-parent',producer='elsewhere',slot='y'),
                     run('foreign',instance='other',slot='z'),run('other-project',project='other',slot='a')]
        client = self.client(root,[root,child,*unrelated],[metadata('root','a','root-scope'),
            metadata('child','b','child-scope',slot='slot-1')])
        self.assertEqual(await client.group_members('root'),['root','child'])

    async def test_group_source_problems_use_each_sdk_coverage_owner(self):
        root, child = run('root'), run('child',slot='slot-1')
        jobs = [metadata('root','count','root'),metadata('child','verdict','child-scope',slot='slot-1')]
        client = self.client(root,[root,child],jobs)
        package = Path(__file__).resolve().parents[1] / 'contracts/panel-contract-v1/sample-panels/demo.emoweb'
        with zipfile.ZipFile(package) as archive:
            presentation = json.loads(archive.read('presentation.json'))
        presentation['dataSources'] = {key:value for item in jobs for key,value in json.loads(item.sources_json).items()}
        presentation['resultScopes'] = {key:value for item in jobs
            for key,value in json.loads(item.capture_definition_json)['scopes'].items()}
        value = await client.validate_group('root',presentation)
        self.assertEqual(value,{'runIds':['root','child'],'sourceProblems':{}})

    async def test_consumer_selection_never_expands_to_group(self):
        child = run('child',slot='slot-1')
        client = self.client(child,[],[])
        coverage = CaptureCoverage.fromMetadata(metadata('child','b','child-scope',slot='slot-1'))
        client._job = AsyncMock(return_value=(None,None,coverage))
        self.assertEqual(await client.group_members('child'),['child'])
        client.runs.assert_not_awaited()
        client.display.ListJobs.assert_not_awaited()

    async def test_ordinary_job_remains_single(self):
        root = run('solo',group='',producer='')
        client = self.client(root,[],[])
        client._job = AsyncMock(return_value=(None,None,CaptureCoverage.fromMetadata(metadata('solo','a','scope'))))
        self.assertEqual(await client.group_members('solo'),['solo'])

    async def test_changed_membership_rejected(self):
        root, child = run('root'),run('child',slot='slot-1')
        client = self.client(root,[root,child],[metadata('root','a','root-scope'),
            metadata('child','b','child-scope',producer='other',slot='slot-1')])
        with self.assertRaisesRegex(RuntimeRejected,'GROUP_IDENTITY_CHANGED'):
            await client.group_members('root')

    async def test_multiple_consumer_scopes_must_be_disjoint(self):
        root = run('root'); child = run('child',slot='1'); child2 = run('child2',slot='2')
        client = self.client(root,[root,child,child2],[metadata('root','a','root-scope'),
            metadata('child','b','child-scope',slot='1'),metadata('child2','c','child-scope',slot='2')])
        with self.assertRaisesRegex(RuntimeRejected,'GROUP_SCOPE_OVERLAP'):
            await client.group_members('root')
