"""Qt/Web scope aliases share physical capture, retaining result-owned images."""
from copy import deepcopy
import json
import time

import cv2
import grpc
import numpy as np
import pytest

from scripts.independent_web_editor_demo import create_demo_project
from emo_master.apps.designer.state.project_store import loadProjectDocument, saveProject
from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb
from emo_master.apps.runtime.grpc_server.generated import runtime_pb2_grpc as rpc
from emo_master.apps.runtime.grpc_server.aio_entry import AioRuntimeServer
from emo_master.apps.runtime.presentation.normal_capture import freezeNormalCapture
from emo_master.apps.runtime.presentation.output_capture import freezeMergedCapture, outputCatalog
from emo_master.core.project.models import ProjectDocument
from emo_master.core.workflow.compiler import WorkflowCompiler
from emo_panel_protocol.output_requests import OutputRequestConfig
from tests.runtime.presentation.test_external_output_requests import request, start
from tests.runtime.presentation.test_normal_capture import release
from tests.runtime.runtime_test_utils import jobFailureDetails, waitForTerminal


def imageProject(root, registry):
    path, document = create_demo_project(root, registry, continuous=False)
    raw = document.model_dump(mode='json')
    presentation = raw['presentation']
    presentation.update(defaultPageId='qt', pageOrder=['qt'],
        resultScopes={'qt-main': dict(entryWorkflowId='main', scopeWorkflowId='main')},
        dataSources={key: dict(kind='node_output', resultScopeId='qt-main', workflowId='main',
                              nodeId=node, port=port, expectedType='image')
                     for key, node, port in [('qt-original', 'load', 'image'), ('qt-mask', 'blob', 'mask')]},
        pages={'qt': dict(name='Qt images', targets=['desktop'], components=[
            dict(componentId=key + '-view', type='image', bindings={'image': key}, layout={'row': row})
            for row, key in enumerate(('qt-original', 'qt-mask'))])})
    document = ProjectDocument.model_validate(raw)
    assert document.schemaVersion == '2.9' and document.presentation.schemaVersion == '1.2'
    assert document.devices.schemaVersion == '1.0' and not document.devices.definitions
    saveProject(path, document.model_dump(mode='json'))
    reloaded = loadProjectDocument(path)
    assert reloaded == document
    WorkflowCompiler(operatorRegistry=registry).compile(reloaded)
    return path, reloaded


def imageRequests(document, registry, *, third=False):
    ports = {('load', 'image'), ('blob', 'mask')}
    if third:
        ports.add(('blob', 'overlay'))
    choices = [choice for choice in outputCatalog(document, registry)
               if (choice['source']['nodeId'], choice['source']['port']) in ports
               and not choice['source']['fieldPath'] and not choice['scope']['callPath']]
    return dict(schemaVersion='1.0', sources={c['sourceId']: c['source'] for c in choices},
                scopes={c['scopeId']: c['scope'] for c in choices})


@pytest.fixture
def displayClient(channel):
    server = AioRuntimeServer(channel.runtime, channel)
    try:
        with grpc.insecure_channel(f'127.0.0.1:{server.port}') as connection:
            yield rpc.DisplayServiceStub(connection)
    finally:
        server.close()


def testQtAndWebEquivalentImagesCaptureOnceAndKeepIndependentResultOwnership(channel, displayClient, tmp_path):
    runtime = channel.runtime
    registry = runtime.pluginScanResult.activeOperators
    path, document = imageProject(tmp_path / 'project', registry)
    assert runtime.LoadProject(pb.LoadProjectRequest(project_path=str(path)), None).ok
    config = imageRequests(document, registry)
    qt = freezeNormalCapture(document, registry, path.parent, 'main')
    web = freezeMergedCapture(document, registry, path.parent, 'main', OutputRequestConfig.model_validate(config))
    assert len(set(json.loads(qt.limitsJson)['imageLaneBySource'].values())) == 2
    assert len(set(json.loads(web.limitsJson)['imageLaneBySource'].values())) == 2
    validation = runtime.ValidateOutputRequests(request(runtime, config), None)
    print('Qt standalone two lanes: PASS; Web standalone two lanes: PASS; merged Runtime validation:', validation.code, validation.message)
    assert validation.ok, validation.message
    assert runtime.SubmitOutputRequests(request(runtime, config), None).ok
    job = start(runtime, qt=True)
    assert waitForTerminal(runtime, job).status == 'COMPLETED', jobFailureDetails(runtime, job)
    deadline = time.monotonic() + 5
    while len(channel.store.snapshot(job)['results']) < 2 and time.monotonic() < deadline:
        time.sleep(.02)
    frames = channel.store.snapshot(job)['results']
    assert len(frames) == 2 and all(frame.status == 'COMPLETE' for frame in frames), jobFailureDetails(runtime, job)
    assert len({frame.identity.resultScopeId for frame in frames}) == 2
    assert len({frame.identity.resultKey for frame in frames}) == 2
    assert len({frame.identity.invocationId for frame in frames}) == 1
    expectedIds = set(document.presentation.dataSources) | set(config['sources'])
    assert {source.sourceId for frame in frames for source in frame.sources} == expectedIds
    public = displayClient.Snapshot(pb.DisplayRequest(runtime_instance_id=runtime.runtimeInstanceId, job_id=job), timeout=5)
    assert {frame.identity.result_key for frame in public.results} == {frame.identity.resultKey for frame in frames}
    assert {source.source_id for frame in public.results for source in frame.sources} == expectedIds
    assert all(source.image.owner_result_key == frame.identity.result_key
               for frame in public.results for source in frame.sources)
    byScope = {}
    resources = []
    for frame in frames:
        decoded = {}
        for source in frame.sources:
            assert source.state == 'AVAILABLE'
            image = source.image
            assert image.ownerResultKey == frame.identity.resultKey
            asset = displayClient.ReadAsset(pb.DisplayAssetRequest(runtime_instance_id=runtime.runtimeInstanceId,
                job_id=job, resource_id=image.resourceId), timeout=5)
            data = asset.content
            assert asset.sha256 == image.sha256
            port = (document.presentation.dataSources[source.sourceId].port if source.sourceId.startswith('qt-')
                    else config['sources'][source.sourceId]['port'])
            decoded[port] = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_UNCHANGED)
            resources.append((frame.identity.resultScopeId, image.resourceId))
        assert decoded['image'].shape == (240, 320, 3) and decoded['mask'].shape == (240, 320)
        byScope[frame.identity.resultScopeId] = decoded
    qtValues, webValues = byScope.values()
    assert all(np.array_equal(qtValues[port], webValues[port]) for port in ('image', 'mask'))
    assert channel.exporter.stats['completed'] == 2
    assert len({resourceId for _scope, resourceId in resources}) == 4
    lanes = json.loads(channel.jobs[job]['limitsJson'])
    assert lanes['maxImageSources'] == 2 and len(set(lanes['imageLaneBySource'].values())) == 2
    assert sum(slot['capacity'] for slot in channel.jobs[job]['slots']) == 8 * 1024 * 1024
    # A truly different third output still exceeds the unchanged two-image cap.
    rejected = runtime.ValidateOutputRequests(request(runtime, imageRequests(document, registry, third=True)), None)
    assert not rejected.ok and 'at most two distinct image sources' in rejected.message
    diagnostic = json.loads(rejected.state_json)['diagnostics'][0]
    assert diagnostic['code'] == 'CAPTURE_IMAGES_LIMIT'
    assert (diagnostic['workflowId'], diagnostic['count'], diagnostic['limit']) == ('main', 3, 2)
    assert {source['sourceId'] for source in diagnostic['sources'] if source['origins'] == ['qt']} == {'qt-original', 'qt-mask'}
    assert sorted(len(group['sourceIds']) for group in diagnostic['imageGroups']) == [1, 2, 2]
    assert len(diagnostic['sourceIds']) == 5 and len(diagnostic['scopeIds']) == 2
    assert 'Qt' in diagnostic['message'] and 'blob/overlay' in diagnostic['message']
    assert not any(key in json.dumps(diagnostic) for key in ('pageId', 'componentId', 'componentRefs'))
    # Releasing the Job drops Qt assets, while ordinary Web leases retain only
    # their independently owned result resources until those leases are released.
    webIds = [resourceId for scope, resourceId in resources if scope != 'qt-main']
    leases = [displayClient.AcquireLease(pb.DisplayAssetRequest(runtime_instance_id=runtime.runtimeInstanceId,
        job_id=job, resource_id=resourceId, ttl_ms=30000), timeout=5) for resourceId in webIds]
    release(channel, job)
    assert channel.assets.stats()['assets'] == 2
    for scope, resourceId in resources:
        if scope == 'qt-main':
            with pytest.raises(grpc.RpcError) as expired:
                displayClient.ReadAsset(pb.DisplayAssetRequest(runtime_instance_id=runtime.runtimeInstanceId,
                    job_id=job, resource_id=resourceId), timeout=5)
            assert expired.value.code() == grpc.StatusCode.NOT_FOUND
        else:
            assert displayClient.ReadAsset(pb.DisplayAssetRequest(runtime_instance_id=runtime.runtimeInstanceId,
                job_id=job, resource_id=resourceId), timeout=5).content
    for lease in leases:
        displayClient.ReleaseLease(lease, timeout=5)
    assert channel.assets.stats()['assets'] == 0


@pytest.mark.parametrize('budget', ['sources', 'scopes'])
def testQuotaDiagnosticsExplainMergedQtAndExternalDemandWithoutSaving(channel, tmp_path, budget):
    runtime = channel.runtime
    registry = runtime.pluginScanResult.activeOperators
    path, document = imageProject(tmp_path / 'project', registry)
    assert runtime.LoadProject(pb.LoadProjectRequest(project_path=str(path)), None).ok
    base = imageRequests(document, registry)
    source = next(iter(base['sources'].values()))
    scope = next(iter(base['scopes'].values()))
    config = dict(schemaVersion='1.0', sources={}, scopes={'external-main': scope})
    if budget == 'sources':
        # Fifteen external IDs plus the two required Qt IDs exceed sixteen;
        # aliases remain just two physical image outputs after merging.
        config['sources'] = {f'external-{index}': dict(source, resultScopeId='external-main') for index in range(15)}
    else:
        # Explicit result scopes count even when no component output uses them.
        config['scopes'] = {f'external-scope-{index}': scope for index in range(16)}
    submission = request(runtime, config)
    reply = runtime.SubmitOutputRequests(submission, None)
    assert not reply.ok and reply.code == 'INVALID_ARGUMENT'
    state = json.loads(reply.state_json)
    diagnostic = next(item for item in state['diagnostics'] if item['budget'] == budget)
    assert diagnostic['code'] == f'CAPTURE_{budget.upper()}_LIMIT'
    assert diagnostic['count'] == 17 and diagnostic['limit'] == 16
    assert diagnostic['workflowName'] == document.workflows['main'].name
    assert {item['sourceId'] for item in diagnostic['sources'] if 'qt' in item['origins']} == {'qt-original', 'qt-mask'}
    assert any('qt' in item['origins'] for item in diagnostic['scopes'])
    assert any('external' in item['origins'] for item in diagnostic['scopes'])
    assert state['config']['sources'] == {} and state['config']['scopes'] == {}
    assert runtime._outputStore().receipt(submission.request_id) is None
    assert not runtime.ListRuns(pb.ListRunsRequest(), None).runs


@pytest.mark.parametrize('differentAddress', ['callPath', 'fieldPath'])
def testImageDemandIdentityDistinguishesInvocationAndFieldPath(channel, tmp_path, differentAddress):
    from emo_master.core.presentation.capture_limits import outputCaptureLimits
    _path, document = imageProject(tmp_path / 'project', channel.runtime.pluginScanResult.activeOperators)
    config = imageRequests(document, channel.runtime.pluginScanResult.activeOperators)
    sources = deepcopy(config['sources'])
    scopes = deepcopy(config['scopes'])
    image = next(iter(sources.values()))
    if differentAddress == 'callPath':
        scopes['another-call'] = dict(entryWorkflowId='main', scopeWorkflowId='main', callPath=[dict(nodeId='other', relation='subflow')])
        sources['another-image'] = dict(image, resultScopeId='another-call', callPath=[dict(nodeId='other', relation='subflow')])
    else:
        sources['another-image'] = dict(image, fieldPath=['nestedImage'])
    with pytest.raises(ValueError, match='at most two distinct image sources'):
        outputCaptureLimits(sources, scopes)
