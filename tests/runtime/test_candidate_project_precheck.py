"""New candidate public RPC: real registry/compiler and isolated request bytes."""
from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import threading
import time
import zipfile

import cv2
import grpc
import numpy as np
import pytest

from emo_panel_protocol.generated import runtime_pb2 as pb, runtime_pb2_grpc as rpc
from emo_master.apps.designer.state.project_store import loadProjectDocument, saveProject
from emo_master.apps.designer.state.workflow_store import WorkflowStore
from emo_master.apps.runtime.grpc_server.service import RuntimeService
from emo_master.apps.runtime.main import createRuntimeServer
from emo_master.apps.runtime.project_config import candidate
from emo_master.core.contracts.port_types import canonicalPortTypes
from emo_master.core.project.models import ProjectDocument
from tests.runtime.runtime_test_utils import waitForTerminal


@pytest.fixture
def runtime(tmp_path):
    service = RuntimeService(dbPath=tmp_path / 'runtime.db', workspaceRoot=tmp_path / 'jobs')
    try:
        yield service
    finally:
        service.close()


def imageProject(runtime):
    store = WorkflowStore()
    store.get('main').outputs = {'mask': 'image'}
    store.ensureBoundaryNodes()
    payload = store.toPayload('Candidate image project')
    graph = payload['workflows']['main']
    output = next(n['nodeId'] for n in graph['nodes'] if n['kind'] == 'workflow_output')
    for name, operatorId, params in [
        ('load', 'vision.io.image_loader', {'imagePath': '', 'colorMode': 'grayscale'}),
        ('threshold', 'vision.preprocess.threshold', {'mode': 'fixed', 'threshold': 127}),
    ]:
        manifest = runtime.pluginScanResult.activeOperators[operatorId].manifest
        graph['nodes'].append(dict(nodeId=name, operatorId=operatorId, kind='operator', params=params,
            inputPorts=canonicalPortTypes(manifest.inputPorts), outputPorts=canonicalPortTypes(manifest.outputPorts),
            paramSchema=deepcopy(manifest.paramSchema)))
        payload['dependencies']['operators'].append(dict(operatorId=operatorId, version=manifest.version))
    graph['edges'] = [dict(fromNode='load', fromPort='image', toNode='threshold', toPort='image'),
        dict(fromNode='threshold', fromPort='mask', toNode=output, toPort='mask')]
    ok, encoded = cv2.imencode('.png', np.arange(81, dtype=np.uint8).reshape(9, 9))
    assert ok
    image = encoded.tobytes()
    payload['resources']['items']['image'] = dict(path='resources/input.png', sha256=hashlib.sha256(image).hexdigest(),
                                               size=len(image), purpose='input_image')
    payload['resources']['parameterBindings'] = [dict(resourceId='image', target=dict(workflowId='main', nodeId='load',
                                                                                  parameterPath=['imagePath']))]
    ProjectDocument.model_validate(payload)
    return payload, image


def package(payload, image=None, *, entries=None):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('project.emoproj', json.dumps(payload))
        if image is not None:
            archive.writestr('resources/input.png', image)
        for name, content in (entries or {}).items():
            archive.writestr(name, content)
    return stream.getvalue()


def chunks(runtime, content, *, revision='r1', **metadata):
    fields = dict(draft_revision=revision, expected_runtime_instance_id=runtime.runtimeInstanceId,
                  size_bytes=len(content), sha256=hashlib.sha256(content).hexdigest())
    fields.update(metadata)
    yield pb.CandidateProjectChunk(metadata=pb.CandidateProjectMetadata(**fields))
    for offset in range(0, len(content), 4093):
        yield pb.CandidateProjectChunk(data=content[offset:offset + 4093])


def report(runtime, content, **kwargs):
    reply = runtime.ValidateCandidateProject(chunks(runtime, content, **kwargs), None)
    assert reply.ok, (reply.code, reply.message)
    return json.loads(reply.report_json)


def testListOperatorsMapsManifestAndRealInstance(runtime):
    reply = runtime.ListOperators(pb.ListOperatorsRequest(), None)
    assert reply.runtime_instance_id == runtime.runtimeInstanceId
    assert 'candidate_project_precheck_v1' in runtime.GetRuntimeInfo(pb.RuntimeInfoRequest(), None).capabilities
    for info in reply.operators:
        manifest = runtime.pluginScanResult.activeOperators[info.operator_id].manifest
        assert json.loads(info.next_cycle_parameter_paths_json) == list(manifest.nextCycleParameterPaths)
        assert info.min_core_version == manifest.minCoreVersion
        assert info.max_core_version == manifest.maxCoreVersion
        assert info.version == manifest.version
        assert json.loads(info.param_schema_json) == manifest.paramSchema


def testRealImageCompileAndMissingResourceRepairWithoutOnlineMutation(runtime, tmp_path):
    online = WorkflowStore().toPayload('Online A')
    projectFile = tmp_path / 'online' / 'online.emoproj'
    projectFile.parent.mkdir()
    saveProject(projectFile, online)
    assert loadProjectDocument(projectFile).schemaVersion == '2.9'
    assert runtime.LoadProject(pb.LoadProjectRequest(project_path=str(projectFile)), None).ok
    before = (runtime.loadedDocument.model_dump(), runtime.loadedProjectFile,
              deepcopy(runtime.projectConfiguration.current), runtime.jobRepository.all())
    payload, image = imageProject(runtime)
    missing = report(runtime, package(payload), revision='r1')
    assert missing['status'] == 'INCOMPLETE', missing
    assert missing['compileStatus'] == missing['executionStatus'] == 'not_run'
    assert missing['missingInputs'][0]['resourceId'] == 'image'
    assert missing['missingInputs'][0]['fieldPath'] == 'resources.items.image.path'
    fixed = report(runtime, package(payload, image), revision='r2')
    assert fixed['status'] == 'PASSED', fixed
    assert fixed['scope'] == 'target' and fixed['draftRevision'] == 'r2'
    assert fixed['compileStatus'] == 'passed' and fixed['executionStatus'] == 'not_run'
    assert fixed['runtimeInstanceId'] == runtime.runtimeInstanceId
    assert (runtime.loadedDocument.model_dump(), runtime.loadedProjectFile,
            runtime.projectConfiguration.current, runtime.jobRepository.all()) == before
    assert next(n for n in payload['workflows']['main']['nodes'] if n['nodeId'] == 'load')['params']['imagePath'] == ''


def testInvalidThresholdReturnsLocatedCompilerDiagnostic(runtime):
    payload, image = imageProject(runtime)
    node = next(n for n in payload['workflows']['main']['nodes'] if n['nodeId'] == 'threshold')
    node['params'] = {'mode': 'adaptiveMean', 'blockSize': 2}
    result = report(runtime, package(payload, image))
    assert result['status'] == 'FAILED'
    issue = next(i for i in result['errors'] if i['nodeId'] == 'threshold')
    assert issue['workflowId'] == 'main' and issue['code'] == 'E_PARAM_INVALID'


def testHostInputPathIsMissingAndNeverRead(runtime, tmp_path, monkeypatch):
    payload, image = imageProject(runtime)
    payload['resources'] = dict(schemaVersion='1.0', items={}, parameterBindings=[], siteBindings=[])
    host = tmp_path / 'not-uploaded.png'
    host.write_bytes(image)
    node = next(n for n in payload['workflows']['main']['nodes'] if n['nodeId'] == 'load')
    node['params']['imagePath'] = str(host)
    original = Path.open
    def guarded(path, *args, **kwargs):
        assert path != host, 'candidate must never open an arbitrary Runtime host file'
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'open', guarded)
    result = report(runtime, package(payload))
    assert result['status'] == 'INCOMPLETE', result
    assert result['missingInputs'][0]['nodeId'] == 'load'
    assert result['missingInputs'][0]['fieldPath'] == 'params.imagePath'
    assert result['compileStatus'] == 'not_run'


@pytest.mark.parametrize('change,code', [
    ({'sha256': '0' * 64}, 'E_CANDIDATE_TRANSFER'),
    ({'size_bytes': 1}, 'E_CANDIDATE_TRANSFER'),
    ({'expected_runtime_instance_id': 'other'}, 'E_CANDIDATE_INSTANCE'),
])
def testTransportFailuresAreNotCheckReports(runtime, change, code):
    payload, image = imageProject(runtime)
    reply = runtime.ValidateCandidateProject(chunks(runtime, package(payload, image), **change), None)
    assert not reply.ok and reply.code == code and not reply.report_json


@pytest.mark.parametrize('entry', ['../escape.png', '/absolute.png', 'C:/host.png'])
def testUnsafeZipIsRejected(runtime, entry):
    payload, _ = imageProject(runtime)
    reply = runtime.ValidateCandidateProject(chunks(runtime, package(payload, entries={entry: b'x'})), None)
    assert not reply.ok and reply.code == 'E_CANDIDATE_PACKAGE'


def testSuppliedCorruptResourceFailsWithoutCompiling(runtime):
    payload, _ = imageProject(runtime)
    result = report(runtime, package(payload, b'bad-image-bytes'))
    assert result['status'] == 'FAILED' and result['compileStatus'] == 'not_run'
    assert result['errors'][0]['resourceId'] == 'image'


def testRawBackslashZipNameIsRejectedBeforeWindowsNormalization(runtime):
    payload, _ = imageProject(runtime)
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w') as archive:
        archive.writestr('project.emoproj', json.dumps(payload))
        entry = zipfile.ZipInfo('placeholder')
        # Direct assignment preserves the original wire spelling on Windows.
        entry.filename = 'resources\\wire.png'
        archive.writestr(entry, b'wire')
    reply = runtime.ValidateCandidateProject(chunks(runtime, output.getvalue()), None)
    assert not reply.ok and reply.code == 'E_CANDIDATE_PACKAGE'


def testDottedResourceIdUsesLongestDeclaredKeyForLocatedDiagnostic(runtime):
    payload, _ = imageProject(runtime)
    item = payload['resources']['items'].pop('image')
    payload['resources']['items']['input.image'] = item
    payload['resources']['items']['input'] = dict(item, path='resources/other.png')
    payload['resources']['parameterBindings'][0]['resourceId'] = 'input.image'
    result = report(runtime, package(payload))
    assert result['status'] == 'INCOMPLETE'
    issue = next(item for item in result['missingInputs'] if item['fieldPath'] == 'resources.items.input.image.path')
    assert issue['resourceId'] == 'input.image'


def testPublicGrpcCandidateCancellationKeepsWorkUntilCompilerExits(runtime, monkeypatch):
    entered, release, exited = threading.Event(), threading.Event(), threading.Event()
    paths = []
    original = candidate.WorkflowCompiler.compile
    def blocking(compiler, document, **kwargs):
        # Hold the real compilation boundary, then run the actual compiler.
        paths.append(Path(next(n.params['imagePath'] for n in document.workflows['main'].nodes if n.nodeId == 'load')))
        entered.set()
        assert release.wait(10)
        try:
            return original(compiler, document, **kwargs)
        finally:
            exited.set()
    monkeypatch.setattr(candidate.WorkflowCompiler, 'compile', blocking)
    server, port, _ = createRuntimeServer(port=0, runtimeService=runtime)
    server.start()
    channel = grpc.insecure_channel(f'127.0.0.1:{port}')
    try:
        stub = rpc.RuntimeServiceStub(channel)
        payload, image = imageProject(runtime)
        future = stub.ValidateCandidateProject.future(chunks(runtime, package(payload, image)), timeout=15)
        assert entered.wait(5)
        assert paths[0].is_file()
        assert future.cancel()
        # Cancellation does not delete files while the compiler still owns them.
        assert paths[0].is_file() and not exited.is_set()
        assert server.transport.active['bulk'] == 1
        release.set()
        assert exited.wait(5)
        deadline = time.monotonic() + 5
        while server.transport.active['bulk'] and time.monotonic() < deadline:
            time.sleep(.01)
        assert server.transport.active['bulk'] == 0
        assert not paths[0].exists()
        # A fresh request after cancellation still uses the real public service.
        reply = stub.ListOperators(pb.ListOperatorsRequest(), timeout=5)
        assert reply.runtime_instance_id == runtime.runtimeInstanceId
        assert runtime.loadedDocument is None and runtime.jobRepository.all() == []
    finally:
        release.set()
        channel.close()
        server.stop(0).wait()


def testCandidateBPrecheckDoesNotReplaceActuallyRunningA(tmp_path):
    """A uses the existing explicitly simulated cooperative blocking operator.

    The Runtime, spawned Job process, RPC transport and B's compiler are real;
    the controlled operator never accesses a camera, PLC or network device.
    """
    from tests.runtime.inspection_test_operator import InspectionTestOperator
    meta = InspectionTestOperator.meta
    plugin = tmp_path / 'controlled-plugin'
    plugin.mkdir()
    (plugin / 'manifest.json').write_text(json.dumps(dict(operatorId=meta.operatorId,
        displayName=meta.displayName, version=meta.version,
        entry='tests.runtime.inspection_test_operator:InspectionTestOperator',
        inputPorts=meta.inputPorts, outputPorts=meta.outputPorts, paramSchema=meta.paramSchema,
        minCoreVersion='0.1.0', maxCoreVersion='1.x')), encoding='utf-8')
    builtins = Path(__file__).resolve().parents[2] / 'src/emo_master/plugins/builtins'
    runtime = RuntimeService(dbPath=tmp_path / 'runtime.db', workspaceRoot=tmp_path / 'jobs',
                             pluginRootPaths=(str(builtins), str(plugin)))
    server, port, _ = createRuntimeServer(port=0, runtimeService=runtime)
    server.start()
    channel = grpc.insecure_channel(f'127.0.0.1:{port}')
    job = None
    try:
        stub = rpc.RuntimeServiceStub(channel)
        a = WorkflowStore().toPayload('Running simulated A')
        descriptor = runtime.pluginScanResult.activeOperators[meta.operatorId]
        a['workflows']['main']['nodes'].append(dict(nodeId='gate', kind='operator', operatorId=meta.operatorId,
            params=dict(block=True, cooperative=True), inputPorts=canonicalPortTypes(descriptor.manifest.inputPorts),
            outputPorts=canonicalPortTypes(descriptor.manifest.outputPorts)))
        a['dependencies']['operators'] = [dict(operatorId=meta.operatorId, version=descriptor.manifest.version)]
        projectFile = tmp_path / 'online' / 'a.emoproj'
        projectFile.parent.mkdir()
        saveProject(projectFile, a)
        assert loadProjectDocument(projectFile).schemaVersion == '2.9'
        loaded = stub.LoadProject(pb.LoadProjectRequest(project_path=str(projectFile)), timeout=10)
        assert loaded.ok, loaded.message
        started = stub.StartJob(pb.StartJobRequest(project_id=a['project']['projectId'], workflow_id='main'), timeout=10)
        assert started.ok, started.message
        job = started.job_id
        deadline = time.monotonic() + 10
        while not any(event.eventType == 'node.started' and event.nodeId == 'gate'
                      for event in runtime.eventStore.readMerged(job)):
            assert time.monotonic() < deadline, runtime.jobRepository.get(job)
            time.sleep(.01)
        before = stub.GetJobStatus(pb.GetJobStatusRequest(job_id=job), timeout=5)
        assert before.status == 'RUNNING' and before.pid > 0
        onlineBefore = deepcopy(runtime.loadedPayload)
        b, image = imageProject(runtime)
        checked = stub.ValidateCandidateProject(chunks(runtime, package(b, image), revision='b-r1'), timeout=10)
        assert checked.ok, (checked.code, checked.message)
        result = json.loads(checked.report_json)
        assert result['status'] == 'PASSED' and result['compileStatus'] == 'passed'
        assert result['projectId'] == b['project']['projectId'] != a['project']['projectId']
        after = stub.GetJobStatus(pb.GetJobStatusRequest(job_id=job), timeout=5)
        assert after.status == 'RUNNING' and after.pid == before.pid
        assert runtime.loadedPayload == onlineBefore and runtime.loadedDocument.project.projectId == a['project']['projectId']
        assert len(runtime.jobRepository.all()) == 1
        assert runtime.jobSupervisor.ownsJobResources(job)
    finally:
        if job is not None:
            runtime.StopJob(pb.StopJobRequest(job_id=job, mode='graceful'), None)
            assert waitForTerminal(runtime, job).status == 'ABORTED'
        channel.close()
        server.stop(0).wait()
        runtime.close()
