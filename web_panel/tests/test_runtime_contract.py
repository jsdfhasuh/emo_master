"""Real loopback gRPC wire tests against a synthetic service, never production."""
import asyncio
import hashlib
import json
import unittest

import grpc
from emo_panel_protocol.generated import runtime_pb2 as pb, runtime_pb2_grpc as rpc
from emo_panel_protocol.canonical import revisionOf
from emo_panel_protocol.models import DataSource, ResultScope
from emo_panel_protocol.preview import PreviewContext
from emo_web_panel.runtime_client import GrpcRuntimeClient, RuntimeRejected, browser_safe


class Service(rpc.RuntimeServiceServicer, rpc.DisplayServiceServicer):
    def __init__(self):
        self.instance = 'runtime-1'
        self.project = 'project-1'
        self.revision = 7
        self.history_revision = 'a' * 64
        self.starts = []
        self.stops = []
        self.lookups = []
        self.pages = []
        self.leases_released = []
        self.capture = False
        self.timeout_start = False
        self.snapshots = []
        self.definition = None
        self.subscriptions = 0

    async def GetRuntimeInfo(self, request, context):
        return pb.RuntimeInfoReply(ok=True, runtime_instance_id=self.instance, project_id=self.project,
            document_revision=self.revision, history_definition_revision=self.history_revision,
            protocol_version='1.0', capabilities=['runtime_info_v1', 'run_catalog_v1', 'stop_identity_v1', 'history_query_v1', 'history_image_v1'])

    def run(self):
        return pb.RunState(runtime_instance_id=self.instance, project_id=self.project, workflow_id='wf',
            job_id='j1', run_group_id='group-1', source_job_id='j1', status='COMPLETED',
            owns_resources=True, display_resources_released=False, capture_enabled=self.capture,
            allowed_actions=['view'], accepted_at_ms=9007199254740993)

    async def ListRuns(self, request, context):
        return pb.ListRunsReply(ok=True, runtime_instance_id=self.instance, runs=[self.run()])

    async def GetRunState(self, request, context):
        return pb.GetRunStateReply(ok=True, run=self.run())

    async def ListWorkflows(self, request, context):
        return pb.ListWorkflowsReply(workflows=[pb.WorkflowInfo(workflow_id='wf', name='Workflow',
            is_entry=False, can_start=True, default_inputs_json='{"count":9007199254740993}')])

    async def GetJob(self, request, context):
        return pb.DisplayJob(runtime_instance_id=self.instance, project_id=self.project, job_id='j1', capture_enabled=self.capture)

    async def ListJobs(self, request, context):
        return pb.DisplayJobs(jobs=[self.metadata()])

    def metadata(self):
        if not self.capture:
            return pb.DisplayJob(runtime_instance_id=self.instance, project_id=self.project, job_id='j1')
        return pb.DisplayJob(runtime_instance_id=self.instance, project_id=self.project, job_id='j1',
            capture_enabled=True, execution_revision='b'*64, capture_plan_revision=revisionOf(self.definition),
            source_ids=['count'], sources_json=json.dumps({'count': self.definition['sources'][0]}),
            capture_definition_json=json.dumps(self.definition))

    async def Snapshot(self, request, context):
        return pb.DisplaySnapshot(runtime_instance_id=self.instance, job_id='j1', cursor=9007199254740993,
                                  results=self.snapshots)

    async def Subscribe(self, request, context):
        self.subscriptions += 1
        yield await self.Snapshot(request, context)

    async def StartJob(self, request, context):
        self.starts.append(request)
        if self.timeout_start:
            await context.abort(grpc.StatusCode.DEADLINE_EXCEEDED, 'accepted but response lost')
        return pb.StartJobReply(ok=True, job_id='j2', status='ACCEPTED', start_request_id=request.start_request_id,
                               runtime_instance_id=self.instance, project_revision=self.revision)

    async def GetStartRequest(self, request, context):
        self.lookups.append(request)
        return pb.StartJobReply(ok=True, job_id='j2', status='ACCEPTED')

    async def StopJob(self, request, context):
        self.stops.append(request)
        return pb.StopJobReply(ok=True, status='STOP_REQUESTED')

    async def QueryHistoryPage(self, request, context):
        self.pages.append(request)
        return pb.HistoryReply(ok=True, result_json=json.dumps({'columns':[{'name':'count'}],
            'rows':[{'rowId':'row-1','values':[9007199254740993],'imageRefs':{'image':{'status':'AVAILABLE','imageRef':'opaque-ref'}}}],
            'page':request.page,'pageSize':request.page_size,'hasNext':False,'stableRowIdentity':True,
            'historyDefinitionRevision':self.history_revision}))

    async def QueryHistoryAggregate(self, request, context):
        return pb.HistoryReply(ok=True, result_json=json.dumps({'value':None,'aggregate':'avg',
                                'historyDefinitionRevision':self.history_revision}))

    async def ReadHistoryImage(self, request, context):
        return pb.HistoryImageReply(ok=True, status='MISSING')

    async def AcquireLease(self, request, context):
        return pb.DisplayLease(lease_id='lease-1',resource_id=request.resource_id,ttl_ms=5000)

    async def ReadAsset(self, request, context):
        return pb.DisplayAsset(content=b'png',sha256=hashlib.sha256(b'png').hexdigest())

    async def ReleaseLease(self, request, context):
        self.leases_released.append(request.lease_id)
        return pb.DisplayEmpty()

    async def ListGlobalCounters(self, request, context):
        return pb.ListGlobalCountersReply(ok=True,counters=[pb.GlobalCounterInfo(name='total',value=9007199254740993)])


class RuntimeContractTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.service = Service()
        self.server = grpc.aio.server()
        rpc.add_RuntimeServiceServicer_to_server(self.service, self.server)
        rpc.add_DisplayServiceServicer_to_server(self.service, self.server)
        self.port = self.server.add_insecure_port('127.0.0.1:0')
        await self.server.start()
        self.client = GrpcRuntimeClient(f'127.0.0.1:{self.port}')
        await self.client.start()

    async def asyncTearDown(self):
        await self.client.close()
        await self.server.stop(None)

    def body(self):
        return {'runtimeInstanceId':'runtime-1','projectId':'project-1','configurationRevision':'7',
                'requestId':'stable-request-1','workflowId':'wf','inputs':{},'runGroupId':'group-1'}

    async def test_identity_and_authoritative_resources_and_large_counters(self):
        info = await self.client.info()
        self.assertEqual(info['configurationRevision'], '7')
        run = (await self.client.runs())[0]
        self.assertTrue(run['ownsResources'])
        self.assertFalse(run['displayResourcesReleased'])
        self.assertEqual(run['acceptedAtMs'], '9007199254740993')
        self.assertEqual((await self.client.counters())['counters'][0]['value'],'9007199254740993')
        self.assertEqual((await self.client.workflows())[0]['inputs']['count'],'9007199254740993')
        self.assertTrue((await self.client.workflows())[0]['canStart'])

    async def test_start_pins_cas_and_preserves_intent_and_stop_job(self):
        result = await self.client.control('start','wf',self.body())
        self.assertEqual(result['outcome'],'ACCEPTED')
        request = self.service.starts[0]
        self.assertEqual(request.expected_document_revision,7)
        self.assertEqual(request.start_request_id,'stable-request-1')
        self.assertTrue(request.capture_presentation)
        await self.client.control('stop','j1',self.body())
        self.assertEqual(self.service.stops[0].job_id,'j1')
        self.assertEqual(self.service.stops[0].mode,'graceful')
        self.assertEqual(self.service.stops[0].expected_run_group_id,'group-1')

    async def test_stop_explicit_empty_ungrouped_identity(self):
        await self.client.control('stop','j1',self.body() | {'runGroupId':''})
        self.assertTrue(self.service.stops[0].HasField('expected_run_group_id'))
        self.assertEqual(self.service.stops[0].expected_run_group_id,'')

    async def test_unknown_start_never_replays_and_lookup_same_id(self):
        self.service.timeout_start = True
        self.assertEqual((await self.client.control('start','wf',self.body()))['outcome'],'UNKNOWN')
        await self.client.lookup_start('stable-request-1','runtime-1')
        self.assertEqual(len(self.service.starts),1)
        self.assertEqual(self.service.lookups[0].start_request_id,'stable-request-1')

    async def test_stale_control_rejected_before_mutation(self):
        for body in (self.body() | {'configurationRevision':'6'},self.body() | {'runtimeInstanceId':'old'}):
            with self.assertRaises(RuntimeRejected):
                await self.client.control('start','wf',body)
        self.assertEqual(self.service.starts,[])

    async def test_no_capture_never_start(self):
        with self.assertRaisesRegex(RuntimeRejected,'CAPTURE_UNAVAILABLE'):
            await self.client.snapshot('j1')
        self.assertEqual(self.service.starts,[])

    async def test_history_zero_based_wire_and_opaque_image_and_revision(self):
        page = await self.client.history('table',1,20)
        self.assertEqual(self.service.pages[0].page,0)
        self.assertTrue(page['stableRowIdentity'])
        self.assertEqual(page['rows'][0]['values'][0],9007199254740993)
        self.assertEqual(page['rows'][0]['imageRefs']['image']['imageRef'],'opaque-ref')
        self.assertEqual(await self.client.history_image(image_ref='opaque-ref'),(b'','','MISSING'))
        aggregate = await self.client.history('aggregate',1,20,kind='aggregate')
        self.assertIsNone(aggregate['value'])
        with self.assertRaisesRegex(RuntimeRejected,'REVISION_CHANGED'):
            await self.client.history('table',1,20,context={'runtimeInstanceId':'runtime-1','projectId':'project-1',
                                      'historyDefinitionRevision':'c'*64})
        self.assertEqual(len(self.service.pages),1)

    async def test_asset_integrity_releases_lease_on_failure(self):
        resource = {'resourceId':'image','byteSize':3,'mimeType':'image/png','sha256':'a'*64}
        with self.assertRaisesRegex(RuntimeRejected,'DIGEST_MISMATCH'):
            await self.client.read_asset('j1',resource)
        self.assertEqual(self.service.leases_released,['lease-1'])
        resource['sha256'] = hashlib.sha256(b'png').hexdigest()
        self.assertEqual(await self.client.read_asset('j1',resource),(b'png','image/png'))

    async def test_preview_pins_identity_but_allows_layout_document_revision(self):
        self.client.preview_context = PreviewContext.model_validate({'mode':'runtime','projectId':'project-1',
            'packageRevision':'d'*64,'historyDefinitionRevision':'a'*64,
            'runtime':{'target':f'127.0.0.1:{self.port}','runtimeInstanceId':'runtime-1','documentRevision':'7'}})
        self.service.revision = 8
        self.assertEqual((await self.client.info())['configurationRevision'],'8')
        with self.assertRaisesRegex(RuntimeRejected,'READ_ONLY'):
            await self.client.control('stop','j1',self.body())
        self.service.instance = 'runtime-2'
        with self.assertRaises(ValueError):
            await self.client.info()
        self.assertEqual(self.service.stops,[])

    async def test_sdk_coverage_decoding_and_shared_stream_cursor(self):
        source = DataSource.model_validate({'kind':'global_variable','resultScopeId':'root',
            'variableId':'count','expectedType':'integer','callPath':[],'fieldPath':[],'ruleVersion':'1.0'}).model_dump()
        scope = ResultScope.model_validate({'callPath':[],'commitPolicy':'workflow_terminal',
            'entryWorkflowId':'wf','scopeWorkflowId':'wf'}).model_dump()
        self.service.definition = {'ruleVersion':'1.0','sources':[source],'scopes':{'root':scope}}
        self.service.capture = True
        wire = pb.DisplayResult(identity=pb.DisplayIdentity(runtime_instance_id='runtime-1',job_id='j1',
            result_scope_id='root',invocation_id='invocation-1',result_key='result-1',result_ordinal=9007199254740993,
            execution_revision='b'*64,capture_plan_revision=revisionOf(self.service.definition),mode='runtime'),
            expected_source_ids=['count'], sources=[pb.DisplaySource(source_id='count',state='AVAILABLE',
            value_json='9007199254740993')],status='COMPLETE',execution_terminal='COMPLETED')
        self.service.snapshots = [wire]
        result = (await self.client.snapshot('j1'))[0]
        self.assertEqual(result['sources']['count']['valueJson'],'9007199254740993')
        self.assertEqual(result['sources']['count']['value'],'9007199254740993')
        self.assertEqual(result['resultOrdinal'],'9007199254740993')
        self.assertEqual(result['cursor'],'9007199254740993')
        received = [r async for r in self.client.subscribe('j1')]
        self.assertEqual(len(received),2)
        self.assertEqual(self.service.subscriptions,1)
        wire.identity.execution_revision = 'c'*64
        with self.assertRaisesRegex(RuntimeRejected,'CAPTURE_MISMATCH'):
            await self.client.snapshot('j1')

    def test_browser_safe_never_rounds_large_nested_integer(self):
        self.assertEqual(browser_safe({'x':[2**63,1.0,False]}),{'x':[str(2**63),1.0,False]})
