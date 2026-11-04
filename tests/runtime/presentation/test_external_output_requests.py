"""Real software operators, ordinary StartJob and persistent external requests."""
from copy import deepcopy
import hashlib
import json
import time
from uuid import uuid4

import grpc
import pytest

from scripts.independent_web_editor_demo import create_demo_project
from emo_master.apps.runtime.grpc_server.service import RuntimeService
from emo_master.apps.runtime.grpc_server.aio_entry import AioRuntimeServer
from emo_master.apps.runtime.presentation.service import PresentationService
from emo_master.apps.runtime.presentation.output_capture import freezeMergedCapture, outputCatalog
from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb, runtime_pb2_grpc as rpc
from emo_master.core.project.models import ProjectDocument
from emo_panel_protocol.output_requests import OutputRequestConfig
from tests.runtime.runtime_test_utils import jobFailureDetails, waitForTerminal
from tests.runtime.presentation.test_normal_capture import release
from tests.runtime.test_initial_project_configuration import waitForWrite
from emo_master.apps.designer.services.runtime_client import RuntimeClient
from emo_master.core.project.configuration import projectChanges


def request(runtime, config=None):
    return pb.OutputRequestsRequest(runtime_instance_id=runtime.runtimeInstanceId,
        project_id=runtime.loadedDocument.project.projectId,
        expected_document_revision=runtime.projectConfiguration.current['documentRevision'],
        expected_config_revision=runtime._outputStore().read(runtime.loadedDocument.project.projectId).revision,
        request_id=uuid4().hex, config_json=json.dumps(config or OutputRequestConfig().model_dump()))


def selectedConfig(choices, *, rows=False):
    ports = {('blob', 'overlay'), ('count', 'count'), ('judge', 'result')}
    if rows:
        ports.add(('blob', 'blobs'))
    selected = [c for c in choices if (c['source']['nodeId'], c['source']['port']) in ports
                and not c['source']['fieldPath'] and not c['scope']['callPath']]
    return dict(schemaVersion='1.0', sources={c['sourceId']: c['source'] for c in selected},
                scopes={c['scopeId']: c['scope'] for c in selected})


def result(display, job):
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        results = display.store.snapshot(job)['results']
        if results:
            return results[0]
        time.sleep(.02)
    pytest.fail('No DisplayService result: ' + jobFailureDetails(display.runtime, job))


def start(runtime, *, qt=False):
    reply = runtime.StartJob(pb.StartJobRequest(project_id=runtime.loadedDocument.project.projectId,
        workflow_id='main', capture_presentation=qt, start_request_id=uuid4().hex,
        expected_runtime_instance_id=runtime.runtimeInstanceId), None)
    assert reply.ok, reply.message
    return reply.job_id


def testRpcResultsCasLookupAndRestartWithoutWeb(tmp_path):
    db, workspace = tmp_path / 'runtime.sqlite3', tmp_path / 'jobs'
    runtime = RuntimeService(dbPath=db, workspaceRoot=workspace)
    display = PresentationService(runtime, tmp_path / 'display')
    server = AioRuntimeServer(runtime, display)
    channel = grpc.insecure_channel(f'127.0.0.1:{server.port}')
    stub = rpc.RuntimeServiceStub(channel)
    try:
        path, document = create_demo_project(tmp_path / 'project', runtime.pluginScanResult.activeOperators, continuous=False)
        loaded = runtime.LoadProject(pb.LoadProjectRequest(project_path=str(path)), None)
        assert loaded.ok, loaded.message
        catalog = json.loads(stub.GetOutputCatalog(request(runtime), timeout=5).state_json)
        config = selectedConfig(catalog['choices'])
        submission = request(runtime, config)
        assert stub.ValidateOutputRequests(submission, timeout=5).ok
        saved = stub.SubmitOutputRequests(submission, timeout=5)
        assert saved.ok and json.loads(saved.state_json)['state'] == 'SAVED'
        assert stub.GetOutputRequestStatus(submission, timeout=5).state_json == saved.state_json
        assert stub.SubmitOutputRequests(submission, timeout=5).state_json == saved.state_json
        job = start(runtime)  # Designer/local callers do not need a Qt capture flag.
        assert waitForTerminal(runtime, job).status == 'COMPLETED', jobFailureDetails(runtime, job)
        frame = result(display, job)
        values = {config['sources'][s.sourceId]['nodeId']: s for s in frame.sources}
        assert values['count'].valueJson == '2' and values['judge'].valueJson == 'true'
        assert values['blob'].image.ownerResultKey == frame.identity.resultKey
        data, digest = display.assets.read(job, values['blob'].image.resourceId)
        assert data and digest == hashlib.sha256(data).hexdigest()
        assert frame.status == 'COMPLETE' and len(frame.expectedSourceIds) == 3
        assert frame.identity.capturePlanRevision != json.loads(saved.state_json)['configRevision']
        release(display, job)
        bad = deepcopy(config)
        next(iter(bad['sources'].values()))['expectedType'] = 'string'
        rejected = stub.ValidateOutputRequests(request(runtime, bad), timeout=5)
        assert not rejected.ok and json.loads(rejected.state_json)['diagnostics']
        stale = request(runtime, config)
        stale.expected_config_revision = '0' * 64
        assert stub.SubmitOutputRequests(stale, timeout=5).code == 'CONFIG_CONFLICT'
        assert stub.GetOutputRequestStatus(request(runtime), timeout=5).code == 'NOT_FOUND'
        assert len(runtime.jobRepository.all()) == 1
    finally:
        channel.close()
        server.close()
        runtime.close()
        display.close()
    runtime = RuntimeService(dbPath=db, workspaceRoot=workspace)
    try:
        assert runtime.loadedDocument.project.projectId == document.project.projectId
        assert runtime._outputStore().read(document.project.projectId).model_dump() == OutputRequestConfig.model_validate(config).model_dump()
        assert getattr(runtime, '_presentationOwner', None) is None
        client = RuntimeClient(runtime)
        started = client.startJob(document.project.projectId, 'main', capturePresentation=False,
                                  startRequestId=uuid4().hex, expectedRuntimeInstanceId=runtime.runtimeInstanceId)
        assert started.ok, started.message
        job = started.job_id
        display = runtime._presentationOwner
        assert waitForTerminal(runtime, job).status == 'COMPLETED'
        assert len(result(display, job).sources) == 3
        release(display, job)
    finally:
        runtime.close()
    assert display.closed and not display.monitor.is_alive()
    # The local production controller uses this same start path after autoStart.
    from emo_master.apps.operator_runtime.controller import ProductionRuntime
    production = ProductionRuntime(dataRoot=tmp_path)
    try:
        production.load(path, restoreOnline=True)
        job = production.start()
        assert waitForTerminal(production.runtime, job).status == 'COMPLETED'
        assert len(result(production.presentation, job).sources) == 3
    finally:
        production.close()


def testRunningPublishNewTaskQtMergeAndStaleSource(tmp_path):
    runtime = RuntimeService(dbPath=tmp_path / 'runtime.db', workspaceRoot=tmp_path / 'jobs', productionMode=True)
    display = PresentationService(runtime, tmp_path / 'display')
    try:
        path, document = create_demo_project(tmp_path / 'project', runtime.pluginScanResult.activeOperators)
        assert runtime.LoadProject(pb.LoadProjectRequest(project_path=str(path)), None).ok
        choices = outputCatalog(runtime.loadedDocument, runtime.pluginScanResult.activeOperators)
        config = selectedConfig(choices)
        assert runtime.SubmitOutputRequests(request(runtime, config), None).ok
        job = start(runtime)
        first = result(display, job)
        # The ordinary observing client's connection and subscription own no Job.
        server = AioRuntimeServer(runtime, display)
        observer = grpc.insecure_channel(f'127.0.0.1:{server.port}')
        try:
            stream = rpc.DisplayServiceStub(observer).Subscribe(pb.DisplayRequest(
                runtime_instance_id=runtime.runtimeInstanceId, job_id=job), timeout=5)
            assert next(stream).results
            stream.cancel()
            observer.close()
            deadline = time.monotonic() + 5
            while max(frame.identity.resultOrdinal for frame in display.store.snapshot(job)['results']) <= first.identity.resultOrdinal and time.monotonic() < deadline:
                time.sleep(.02)
            assert runtime.jobRepository.get(job).status == 'RUNNING'
            assert max(frame.identity.resultOrdinal for frame in display.store.snapshot(job)['results']) > first.identity.resultOrdinal
            with grpc.insecure_channel(f'127.0.0.1:{server.port}') as reconnected:
                snapshot = rpc.DisplayServiceStub(reconnected).Snapshot(pb.DisplayRequest(
                    runtime_instance_id=runtime.runtimeInstanceId, job_id=job), timeout=5)
                assert snapshot.results and all(frame.identity.job_id == job for frame in snapshot.results)
        finally:
            observer.close()
            server.close()
        frozen = display.jobs[job]['plan']
        expanded = selectedConfig(choices, rows=True)
        assert runtime.SubmitOutputRequests(request(runtime, expanded), None).ok
        assert display.jobs[job]['plan'] == frozen
        assert runtime.jobRepository.get(job).status == 'RUNNING'
        assert len(runtime.jobRepository.all()) == 1
        assert runtime.StopJob(pb.StopJobRequest(job_id=job, mode='graceful'), None).ok
        assert waitForTerminal(runtime, job).status == 'ABORTED'
        release(display, job)
        runtime.productionMode = False
        job = start(runtime)
        assert waitForTerminal(runtime, job).status == 'COMPLETED'
        assert len(result(display, job).sources) == 4
        release(display, job)
        raw = document.model_dump(mode='json')
        raw['presentation'].update(defaultPageId='qt', pageOrder=['qt'],
            resultScopes={'qt-scope': dict(entryWorkflowId='main', scopeWorkflowId='main')},
            dataSources={'qt-count': dict(kind='node_output', workflowId='main', nodeId='count', port='count', expectedType='integer', resultScopeId='qt-scope')},
            pages={'qt': dict(name='Qt', targets=['desktop'], components=[dict(componentId='qt-view', type='number', bindings={'value': 'qt-count'})])})
        qt = ProjectDocument.model_validate(raw)
        merged = freezeMergedCapture(qt, runtime.pluginScanResult.activeOperators, path.parent, 'main',
                                    OutputRequestConfig.model_validate(expanded), qt=True)
        assert len(json.loads(merged.sourceJson)['sources']) == 5
        client = RuntimeClient(runtime)
        snapshot = client.getOnlineProjectSnapshot()
        qtPayload = qt.model_dump(mode='json')
        changes = [change['path'] for change in projectChanges(snapshot['payload'], qtPayload)]
        writeId = uuid4().hex
        client.writeProjectConfiguration(snapshot, qtPayload, changes, writeId)
        assert waitForWrite(runtime, client, writeId)['state'] == 'COMMITTED'
        job = start(runtime, qt=True)  # The Designer's Qt flag adds Qt demand; external remains.
        assert waitForTerminal(runtime, job).status == 'COMPLETED'
        result(display, job)
        deadline = time.monotonic() + 5
        while len(display.store.snapshot(job)['results']) < 2 and time.monotonic() < deadline:
            time.sleep(.02)
        assert sum(len(frame.sources) for frame in display.store.snapshot(job)['results']) == 5
        release(display, job)
        changed = runtime.loadedDocument.model_copy(deep=True)
        changed.workflows['main'].nodes = [n for n in changed.workflows['main'].nodes if n.nodeId != 'judge']
        changed.workflows['main'].edges = [e for e in changed.workflows['main'].edges if e.toNode != 'judge']
        snapshot = client.getOnlineProjectSnapshot()
        payload = changed.model_dump(mode='json')
        changes = [change['path'] for change in projectChanges(snapshot['payload'], payload)]
        writeId = uuid4().hex
        client.writeProjectConfiguration(snapshot, payload, changes, writeId)
        assert waitForWrite(runtime, client, writeId)['state'] == 'COMMITTED'
        state = json.loads(runtime.GetOutputRequests(request(runtime), None).state_json)
        assert any(d.get('sourceId') and 'missing' in d['message'] for d in state['diagnostics'])
        job = start(runtime)
        assert waitForTerminal(runtime, job).status == 'COMPLETED'
        frame = result(display, job)
        assert sum(s.state == 'AVAILABLE' for s in frame.sources) == 3
        missing = next(s for s in frame.sources if s.state == 'UNAVAILABLE')
        assert 'missing' in missing.reason
        release(display, job)
        # Repair the workflow through the same normal project-write path. The
        # retained request can then be validated/published without rebinding IDs.
        snapshot = client.getOnlineProjectSnapshot()
        repairedPayload = qt.model_dump(mode='json')
        changes = [change['path'] for change in projectChanges(snapshot['payload'], repairedPayload)]
        writeId = uuid4().hex
        client.writeProjectConfiguration(snapshot, repairedPayload, changes, writeId)
        assert waitForWrite(runtime, client, writeId)['state'] == 'COMMITTED'
        repaired = json.loads(runtime.GetOutputRequests(request(runtime), None).state_json)
        assert not repaired['diagnostics']
        assert repaired['config'] == OutputRequestConfig.model_validate(expanded).model_dump(mode='json')
        publication = request(runtime, expanded)
        assert runtime.ValidateOutputRequests(publication, None).ok
        assert runtime.SubmitOutputRequests(publication, None).ok
        job = start(runtime)
        assert waitForTerminal(runtime, job).status == 'COMPLETED'
        frame = result(display, job)
        assert len(frame.sources) == 4 and all(s.state == 'AVAILABLE' for s in frame.sources)
        values = {expanded['sources'][s.sourceId]['nodeId']: s for s in frame.sources}
        assert values['count'].valueJson == '2' and values['judge'].valueJson == 'true'
        assert set(frame.expectedSourceIds) == set(expanded['sources'])
        release(display, job)
        # Renaming display labels leaves source identities unchanged.
        oldIds = {choice['sourceId'] for choice in outputCatalog(changed, runtime.pluginScanResult.activeOperators)}
        changed.workflows['main'].nodes[1].displayName = 'Renamed node'
        assert {choice['sourceId'] for choice in outputCatalog(changed, runtime.pluginScanResult.activeOperators)} == oldIds
        # A stale scope has no real execution boundary and must not produce a result.
        absent = deepcopy(expanded)
        next(iter(absent['scopes'].values()))['entryWorkflowId'] = 'removed-entry'
        assert freezeMergedCapture(changed, runtime.pluginScanResult.activeOperators, path.parent, 'main',
                                   OutputRequestConfig.model_validate(absent)) is None
    finally:
        runtime.close()
        display.close()
