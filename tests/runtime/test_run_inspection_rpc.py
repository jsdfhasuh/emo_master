"""Actual spawn Runner and typed loopback gRPC, isolated files/db/output."""
from contextlib import contextmanager
from dataclasses import asdict
import json
import time

import cv2
import grpc
import numpy as np
import pytest

from emo_master.apps.runtime.grpc_server.generated import runtime_pb2_grpc as rpc
from emo_master.apps.runtime.grpc_server.service import RuntimeService
from emo_master.apps.runtime.grpc_server.aio_entry import AioRuntimeServer
from emo_master.apps.runtime.presentation.service import PresentationService
from emo_master.apps.designer.services.runtime_client import RuntimeClient
from emo_master.apps.designer.services.display_calls import DisplayCallContext
from emo_master.apps.designer.state.project_store import saveProject
from examples.flow_run_inspection import sampleProject
from tests.runtime.runtime_test_utils import waitForTerminal


@contextmanager
def running(root, *, configure=None):
    project = root / '本地 图片'
    document = sampleProject(project)
    plugin = root / 'controlled-plugin'
    plugin.mkdir()
    (plugin / 'manifest.json').write_text(json.dumps({'operatorId': 'test.inspection', 'displayName': '受控测试',
        'version': '1.0.0', 'entry': 'tests.runtime.inspection_test_operator:InspectionTestOperator',
        'inputPorts': {'index': {'type': 'integer', 'required': False}}, 'outputPorts': {'image': 'image'},
        'paramSchema': {'type': 'object'}, 'minCoreVersion': '0.1.0', 'maxCoreVersion': '1.x'}), encoding='utf-8')
    if configure:
        configure(document, root)
    saveProject(project, document.model_dump())
    from pathlib import Path
    builtins = Path(__file__).resolve().parents[2] / 'src/emo_master/plugins/builtins'
    runtime = RuntimeService(dbPath=root / 'temporary.sqlite3', workspaceRoot=root / 'jobs', logDirectory=root / 'logs',
                             pluginRootPaths=(str(builtins), str(plugin)))
    display = PresentationService(runtime, root / 'display')
    server = AioRuntimeServer(runtime, display)
    connection = grpc.insecure_channel(f'127.0.0.1:{server.port}')
    client = RuntimeClient(rpc.RuntimeServiceStub(connection), ownedChannel=connection,
                           runtimeTarget=f'127.0.0.1:{server.port}')
    try:
        assert client.loadProject(str(project)).ok, runtime.pluginScanResult.rejectedOperators
        session = client.inspectionSession('open', str(project)).session_id
        yield runtime, client, str(project), session
    finally:
        client.close()
        server.close()
        runtime.close()
        assert runtime.runInspectionStore.stats()['encodedBytes'] == 0


def listed(client, project, session, job, node='load'):
    deadline = time.monotonic() + 5
    while True:
        listing = client.listNodePreviewSourcesWithMetadata(project, 'main', node, jobId=job, inspectionSessionId=session)
        if listing.captureState != 'PREPARING':
            return listing
        assert time.monotonic() < deadline, listing
        time.sleep(.01)


def testDifferentRealJobsRetainSameInvocationImageAndCountAcrossPromotion(tmp_path):
    with running(tmp_path) as (runtime, client, project, session):
        first = client.startJob(project, inspectionSessionId=session)
        assert first.ok and first.project_revision == runtime.loadedDocument.project.revision
        assert waitForTerminal(runtime, first.job_id).status == 'COMPLETED'
        try:
            listing = listed(client, project, session, first.job_id)
        except AssertionError:
            print('INSPECTION', runtime.runInspectionStore.sessions[session].jobs[first.job_id])
            print('FINALIZATION', runtime.jobSupervisor._recoveries)
            raise
        assert listing.sources, listing
        a = listing.sources[0]
        before, mime = client.readInspectionAsset(project, session, a.sourceId, DisplayCallContext())
        assert mime == 'image/png'
        pixels = cv2.imdecode(np.frombuffer(before, np.uint8), cv2.IMREAD_COLOR)
        pixels[10:30, 200:220] = 255
        ok, encoded = cv2.imencode('.png', pixels)
        assert ok
        encoded.tofile(tmp_path / '本地 图片' / 'input.png')
        second = client.startJob(project, inspectionSessionId=session)
        assert second.ok and waitForTerminal(runtime, second.job_id).status == 'COMPLETED'
        b = listed(client, project, session, second.job_id).sources[0]
        after, _ = client.readInspectionAsset(project, session, a.sourceId, DisplayCallContext())
        current, _ = client.readInspectionAsset(project, session, b.sourceId, DisplayCallContext())
        assert after == before and current != before
        for job, expected, source in ((first.job_id, 2, a), (second.job_id, 3, b)):
            events = runtime.eventStore.readMerged(job)
            load = next(event for event in events if event.eventType == 'node.completed' and event.nodeId == 'load')
            count = next(event for event in events if event.eventType == 'node.completed' and event.nodeId == 'count')
            assert json.loads(count.payloadJson)['outputs']['count'] == expected
            assert source.nodeRunId == load.nodeRunId and source.workflowRunId == load.workflowRunId
            assert source.originJobId == job and source.captureId
            saved = listed(client, project, session, job, 'save')
            assert any(item.port == '__saved_result__' for item in saved.sources)
        # Edited draft ports/nodes cannot change the earlier accepted definitions.
        runtime.loadedDocument.workflows['main'].nodes = []
        assert listed(client, project, session, first.job_id).sources[0] == a
        client.inspectionSession('close', project, session)
        assert runtime.runInspectionStore.stats()['encodedBytes'] == 0
        assert len(runtime.jobRepository.all()) == 2


def testActualFailedJobKeepsEarlierCompletedNodeAndNoFailedNodePixels(tmp_path):
    with running(tmp_path) as (runtime, client, project, session):
        save = next(node for node in runtime.loadedDocument.workflows['main'].nodes if node.nodeId == 'save')
        save.params['outputPath'] = str(tmp_path / 'nonexistent-directory' / 'result.unsupported')
        start = client.startJob(project, inspectionSessionId=session)
        assert start.ok and waitForTerminal(runtime, start.job_id).status == 'FAILED'
        source = listed(client, project, session, start.job_id).sources[0]
        content, _ = client.readInspectionAsset(project, session, source.sourceId, DisplayCallContext())
        assert cv2.imdecode(np.frombuffer(content, np.uint8), cv2.IMREAD_COLOR).shape == (240, 360, 3)
        assert not listed(client, project, session, start.job_id, 'save').sources
        runtime.jobSupervisor.waitForRetirement(deadline=time.monotonic() + 5)
        runtime.jobSupervisor.finishRetirements()
        assert not (runtime.workspaceRoot / start.job_id).exists()
        events = [asdict(event) for event in runtime.eventStore.readMerged(start.job_id)]
        assert any(event['eventType'] == 'node.failed' and event['nodeId'] == 'save' for event in events)


def testSessionCapacityRejectedRequestOldClientAndLeaseCloseDoNotStopJob(tmp_path):
    with running(tmp_path) as (runtime, client, project, session):
        second = client.inspectionSession('open', project)
        with pytest.raises(Exception, match='SESSION_BUDGET'):
            client.inspectionSession('open', project)
        before = len(runtime.jobRepository.all())
        rejected = client.startJob(project, workflowId='missing', inspectionSessionId=session)
        assert not rejected.ok and not rejected.job_id and len(runtime.jobRepository.all()) == before
        assert not runtime.runInspectionStore.sessions[session].jobs
        # Old calls without an inspection ID use exactly the existing behavior.
        legacy = client.startJob(project)
        assert legacy.ok and waitForTerminal(runtime, legacy.job_id).status == 'COMPLETED'
        assert client.listNodePreviewSourcesWithMetadata(project, 'main', 'load', jobId=legacy.job_id).sources
        client.inspectionSession('close', project, second.session_id)
        assert client.getJobStatus(legacy.job_id).status == 'COMPLETED'
        assert runtime.runInspectionStore.stats()['sessions'] == 1


def testFrozenSessionCanRenewReadAndCloseAfterAnotherProjectCopyLoads(tmp_path):
    with running(tmp_path) as (runtime, client, project, session):
        reply = client.startJob(project, inspectionSessionId=session)
        assert reply.ok and waitForTerminal(runtime, reply.job_id).status == 'COMPLETED'
        source = listed(client, project, session, reply.job_id).sources[0]
        other = tmp_path / '同 projectId 的另一 工程副本'
        saveProject(other, sampleProject(other).model_dump())
        # Terminal output is frozen before process/resource retirement completes.
        # Loading another project must still respect that ownership fence.
        runtime.jobSupervisor.waitForRetirement(deadline=time.monotonic() + 5)
        assert client.loadProject(str(other)).ok
        # An identical projectId is insufficient to substitute another copy.
        with pytest.raises(Exception, match='EXPIRED'):
            client.inspectionSession('renew', str(other), session)
        assert client.inspectionSession('renew', project, session).ok
        assert listed(client, project, session, reply.job_id).sources[0] == source
        payload, _ = client.readInspectionAsset(project, session, source.sourceId, DisplayCallContext())
        assert cv2.imdecode(np.frombuffer(payload, np.uint8), cv2.IMREAD_COLOR).shape == (240, 360, 3)
        assert client.inspectionSession('close', project, session).ok
        assert runtime.runInspectionStore.stats()['encodedBytes'] == 0
        assert runtime.runInspectionStore.stats()['sessions'] == 0
        assert len(runtime.jobRepository.all()) == 1 and not runtime._closed


@pytest.mark.parametrize('mode', ['graceful', 'force'])
def testRealCancelAndStrongKillKeepCommittedPrecedingNodeOnly(tmp_path, mode):
    def configure(document, root):
        from emo_master.core.project.models import WorkflowNode, WorkflowEdge
        document.workflows['main'].nodes.append(WorkflowNode(nodeId='gate', operatorId='test.inspection',
            inputPorts={'index': 'integer'}, outputPorts={'image': 'image'},
            params={'block': True, 'cooperative': mode == 'graceful'}))
        document.workflows['main'].edges.append(WorkflowEdge(fromNode='count', fromPort='count', toNode='gate', toPort='index'))
    with running(tmp_path, configure=configure) as (runtime, client, project, session):
        reply = client.startJob(project, inspectionSessionId=session)
        assert reply.ok
        deadline = time.monotonic() + 10
        while not any(event.eventType == 'node.started' and event.nodeId == 'gate'
                      for event in runtime.eventStore.readMerged(reply.job_id)):
            assert time.monotonic() < deadline
            time.sleep(.01)
        # Closing a read-only lease while the Runner is actually blocked must
        # not signal its cancellation token or remove its physical process.
        extra = client.inspectionSession('open', project).session_id
        client.inspectionSession('close', project, extra)
        assert client.getJobStatus(reply.job_id).status == 'RUNNING'
        assert client.stopJob(reply.job_id, mode=mode).ok
        assert waitForTerminal(runtime, reply.job_id).status == 'ABORTED'
        source = listed(client, project, session, reply.job_id).sources[0]
        assert client.readInspectionAsset(project, session, source.sourceId, DisplayCallContext())[0]
        assert not listed(client, project, session, reply.job_id, 'gate').sources
        runtime.jobSupervisor.waitForRetirement(deadline=time.monotonic() + 5)
        runtime.jobSupervisor.finishRetirements()
        assert not runtime.jobSupervisor.ownsJobResources(reply.job_id)


def testRealLoopLastFailureCannotDisplayEarlierIterationSnapshot(tmp_path):
    def configure(document, root):
        from emo_master.core.project.models import WorkflowDefinition
        body = WorkflowDefinition.model_validate({'name': '受控循环体', 'inputs': {'index': 'integer'},
            'outputs': {'image': 'image'}, 'nodes': [{'nodeId': 'input', 'kind': 'workflow_input'},
                {'nodeId': 'image', 'operatorId': 'test.inspection', 'inputPorts': {'index': 'integer'},
                 'outputPorts': {'image': 'image'}, 'params': {'failIndex': 1}},
                {'nodeId': 'output', 'kind': 'workflow_output'}], 'edges': [
                {'fromNode': 'input', 'fromPort': 'index', 'toNode': 'image', 'toPort': 'index'},
                {'fromNode': 'image', 'fromPort': 'image', 'toNode': 'output', 'toPort': 'image'}]})
        main = WorkflowDefinition.model_validate({'name': '循环最后失败', 'inputs': {'items': 'list<integer>'},
            'nodes': [{'nodeId': 'input', 'kind': 'workflow_input'},
                {'nodeId': 'loop', 'kind': 'loop', 'inputPorts': {'items': 'list<integer>'},
                 'outputPorts': {'image': 'list<image>'}, 'loop': {'contractVersion': 2, 'mode': 'foreach',
                    'bodyWorkflowId': 'body', 'itemInputPort': 'index', 'maxIterations': 2}},
                {'nodeId': 'output', 'kind': 'workflow_output'}], 'edges': [
                {'fromNode': 'input', 'fromPort': 'items', 'toNode': 'loop', 'toPort': 'items'}]})
        document.workflows = {'main': main, 'body': body}
        document.workflowOrder = ['main', 'body']
    with running(tmp_path, configure=configure) as (runtime, client, project, session):
        reply = client.startJob(project, inputs={'items': [0, 1]}, inspectionSessionId=session)
        assert reply.ok and waitForTerminal(runtime, reply.job_id).status == 'FAILED'
        listing = client.listNodePreviewSourcesWithMetadata(project, 'body', 'image', jobId=reply.job_id,
                                                            inspectionSessionId=session)
        assert not listing.sources
        events = runtime.eventStore.readMerged(reply.job_id)
        assert any(event.nodeId == 'image' and event.eventType == 'node.completed' for event in events)
        assert any(event.nodeId == 'image' and event.eventType == 'node.failed' for event in events)
