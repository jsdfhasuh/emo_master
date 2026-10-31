"""First publication of drafts uses Runtime preparation, not Designer Save/Run."""
from copy import deepcopy
import hashlib
from pathlib import Path
from threading import Event
import time

import cv2
import grpc
import numpy as np
import pytest

from emo_master.apps.designer.services.runtime_client import RuntimeClient, RuntimeClientError
from emo_master.apps.runtime.grpc_server.generated import runtime_pb2_grpc
from emo_master.apps.runtime.grpc_server.service import RuntimeService
from emo_master.apps.runtime.main import createRuntimeServer
from emo_master.core.project.migration import migrateProjectPayload
from emo_master.core.project.models import ProjectDocument
from tests.runtime.test_device_runtime import simulatedProject
from tests.runtime.runtime_test_utils import waitForTerminal


def waitForWrite(service, client, requestId):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        status = client.getProjectWriteStatus(requestId)
        if status['state'] in {'COMMITTED', 'FAILED', 'CANCELLED'} and service.projectConfiguration.active is None:
            return status
        time.sleep(.01)
    pytest.fail(f'configuration write did not retire: {status}')


def imageProject(root, *, bound=True):
    root.mkdir(parents=True, exist_ok=True)
    image = root / 'input.png'
    assert cv2.imwrite(str(image), np.full((9, 13, 3), 120, dtype=np.uint8))
    payload = simulatedProject()
    payload['devices']['definitions'] = {}
    payload['workflows']['main'].update(nodes=[
        dict(nodeId='input', kind='workflow_input'),
        dict(nodeId='load', operatorId='vision.io.image_loader', params=dict(imagePath='input.png')),
        dict(nodeId='blur', operatorId='vision.preprocess.blur', params=dict(mode='mean', kernelSize=3)),
        dict(nodeId='output', kind='workflow_output')], edges=[
        dict(fromNode='load', fromPort='image', toNode='blur', toPort='image'),
        dict(fromNode='blur', fromPort='image', toNode='output', toPort='image')])
    if bound:
        payload['resources']['items']['image'] = dict(path='input.png', size=image.stat().st_size,
            sha256=hashlib.sha256(image.read_bytes()).hexdigest(), purpose='input_image')
        payload['resources']['parameterBindings'] = [dict(resourceId='image', target=dict(
            workflowId='main', nodeId='load', parameterPath=['imagePath']))]
    return ProjectDocument.model_validate(migrateProjectPayload(payload)).model_dump(mode='json')


@pytest.fixture(params=['embedded', 'loopback'])
def initialRuntime(request, tmp_path):
    service = RuntimeService(dbPath=tmp_path / 'data/runtime.db', workspaceRoot=tmp_path / 'data/jobs')
    server = channel = None
    if request.param == 'loopback':
        server, port, _ = createRuntimeServer(port=0, runtimeService=service)
        server.start()
        channel = grpc.insecure_channel(f'127.0.0.1:{port}')
        grpc.channel_ready_future(channel).result(timeout=5)
        client = RuntimeClient(runtime_pb2_grpc.RuntimeServiceStub(channel), runtimeTarget=f'127.0.0.1:{port}')
    else:
        client = RuntimeClient(service)
    yield service, client
    client.close()
    if channel:
        channel.close()
    if server:
        server.stop(0).wait()
    service.close()


@pytest.mark.parametrize('bound', [False, True])
def testInitialDraftKeepsDiskAndResourcesAndRunsImageGraph(initialRuntime, tmp_path, bound):
    service, client = initialRuntime
    root = tmp_path / 'engineering'
    payload = imageProject(root, bound=bound)
    draft = deepcopy(payload)
    sentinel = root / 'original.emoproj'
    sentinel.write_bytes(b'original on-disk project must not change')
    before = {p.name: p.read_bytes() for p in root.iterdir()}
    checked = client.initializeProjectConfiguration(payload, root, 'check', validate=True)
    assert checked['state'] == 'VALIDATED'
    assert service.loadedDocument is None and service.projectConfiguration.current is None
    assert not service.jobRepository.all() and not service.deviceManager.drivers
    assert {p.name: p.read_bytes() for p in root.iterdir()} == before
    pending = client.initializeProjectConfiguration(payload, root, 'first', runtimeInstanceId=checked['runtimeInstanceId'])
    assert pending['state'] == 'VALIDATING'
    receipt = waitForWrite(service, client, 'first')
    assert receipt['state'] == 'COMMITTED'
    assert service.projectConfiguration.store.receipt('first')[1] == receipt
    assert client.getProjectWriteStatus('first')['state'] == 'COMMITTED'
    assert client.getOnlineProjectSnapshot()['payload']['workflows'] == payload['workflows']
    assert client.initializeProjectConfiguration(payload, root, 'first', runtimeInstanceId=checked['runtimeInstanceId']) == receipt
    assert payload == draft and {p.name: p.read_bytes() for p in root.iterdir()} == before
    assert not service.jobRepository.all()
    prepared = next(n for n in service.loadedDocument.workflows['main'].nodes if n.nodeId == 'load')
    owned = Path(prepared.params['imagePath'])
    assert owned != root / 'input.png' and owned.is_file()
    assert owned.read_bytes() == before['input.png']
    # A mean-blur graph is not one of the old four-operator image-test hosts.
    started = client.startJob(service.loadedProjectId, 'main')
    assert started.ok, started.message
    terminal = waitForTerminal(service, started.job_id, timeoutSeconds=30)
    assert terminal.status == 'COMPLETED', terminal


def testInvalidInitialInputCanBeFixedWithoutLoadingOrStarting(initialRuntime, tmp_path):
    service, client = initialRuntime
    payload = imageProject(tmp_path / 'engineering', bound=False)
    payload['workflows']['main']['nodes'][1]['params']['imagePath'] = 'missing.png'
    with pytest.raises(RuntimeClientError, match='input file not found'):
        client.initializeProjectConfiguration(payload, tmp_path / 'engineering', 'invalid', validate=True)
    assert service.loadedDocument is None and not service.jobRepository.all()
    payload['workflows']['main']['nodes'][1]['params']['imagePath'] = 'input.png'
    checked = client.initializeProjectConfiguration(payload, tmp_path / 'engineering', 'fixed', validate=True)
    assert checked['state'] == 'VALIDATED' and service.projectConfiguration.current is None


def testInitialFailedReceiptRetainsCompilerIssueLocations(initialRuntime, tmp_path):
    service, client = initialRuntime
    root = tmp_path / 'engineering'
    payload = imageProject(root)
    payload['workflows']['main']['nodes'][2]['operatorId'] = 'missing.operator'
    client.initializeProjectConfiguration(payload, root, 'bad-operator', runtimeInstanceId=service.runtimeInstanceId)
    receipt = waitForWrite(service, client, 'bad-operator')
    assert receipt['state'] == 'FAILED'
    issue, = receipt['issues']
    assert (issue['code'], issue['workflowId'], issue['nodeId'], issue['fieldPath']) == (
        'E_OPERATOR_UNKNOWN', 'main', 'blur', 'operatorId')
    assert not service.loadedDocument and not service.jobRepository.all() and not service.deviceManager.drivers


def testInitialPublicationCannotOverwriteAnInterveningOnlineProject(initialRuntime, tmp_path):
    service, client = initialRuntime
    payload = simulatedProject()
    checked = client.initializeProjectConfiguration(payload, tmp_path, 'check', validate=True)
    other = deepcopy(payload)
    other['project']['projectId'] = 'other-project'
    client.initializeProjectConfiguration(other, tmp_path, 'other', runtimeInstanceId=checked['runtimeInstanceId'])
    assert waitForWrite(service, client, 'other')['state'] == 'COMMITTED'
    with pytest.raises(RuntimeClientError) as error:
        client.initializeProjectConfiguration(payload, tmp_path, 'stale', runtimeInstanceId=checked['runtimeInstanceId'])
    assert error.value.code == 'E_CONFIG_ACTIVE'
    assert service.loadedProjectId == 'other-project' and not service.jobRepository.all()


def testRemoteFirstInstallDoesNotSendDesignerPath():
    class NoCall:
        def InitializeProjectConfiguration(self, *_args, **_kwargs):
            pytest.fail('a Designer filesystem path must not be sent to a remote Runtime')
    client = RuntimeClient(NoCall(), runtimeTarget='192.0.2.10:50051')
    try:
        with pytest.raises(RuntimeClientError) as error:
            client.initializeProjectConfiguration(simulatedProject(), 'C:/local/project', 'remote', validate=True)
        assert error.value.code == 'E_CONFIG_INITIAL_REMOTE'
    finally:
        client.close()


def testFirstWriteActivationErrorKeepsCommittedOutcome(initialRuntime, tmp_path, monkeypatch):
    service, client = initialRuntime
    payload = simulatedProject()
    checked = client.initializeProjectConfiguration(payload, tmp_path, 'check-activation', validate=True)
    calls = []
    def failedActivation():
        calls.append(True)
        raise RuntimeError('simulated post-commit publication error')
    monkeypatch.setattr(service.deviceManager, 'activate', failedActivation)
    client.initializeProjectConfiguration(payload, tmp_path, 'activation',
        runtimeInstanceId=checked['runtimeInstanceId'])
    receipt = waitForWrite(service, client, 'activation')
    assert receipt['state'] == 'COMMITTED' and 'publication error' in receipt['publicationError']
    assert service.projectConfiguration.store.receipt('activation')[1] == receipt
    assert client.getProjectWriteStatus('activation') == receipt
    assert client.getOnlineProjectSnapshot()['recoveryError'] == receipt['publicationError']
    assert client.initializeProjectConfiguration(payload, tmp_path, 'activation',
        runtimeInstanceId=checked['runtimeInstanceId']) == receipt
    assert calls == [True] and not service.jobRepository.all()


def testInitialOnlineWriteDoesNotPretendLegacySchemaWasPersisted(initialRuntime, tmp_path):
    service, client = initialRuntime
    legacy = dict(schemaVersion='2.1')
    with pytest.raises(RuntimeClientError, match='2.9'):
        client.initializeProjectConfiguration(legacy, tmp_path, 'legacy', validate=True)
    assert service.projectConfiguration.current is None and service.loadedDocument is None


@pytest.mark.parametrize('phase', ['copying', 'prepared'])
def testInitialWriteIsQueryableAndCancellableBeforeCommit(initialRuntime, tmp_path, monkeypatch, phase):
    from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb
    from emo_master.apps.runtime.project_config import resources

    service, client = initialRuntime
    root = tmp_path / 'engineering'
    payload = imageProject(root)
    checked = client.initializeProjectConfiguration(payload, root, 'check-cancel', validate=True)
    cache = service.workspaceRoot.parent / 'configuration-resources'
    beforeCache = {p for p in cache.rglob('*') if p.is_file()}
    entered, release = Event(), Event()
    prepare = resources.prepareResources
    calls = []
    def slowPrepare(*args, **kwargs):
        calls.append(True)
        result = prepare(*args, **kwargs) if phase == 'prepared' else None
        entered.set()
        assert release.wait(5)
        return result if result is not None else prepare(*args, **kwargs)
    monkeypatch.setattr(resources, 'prepareResources', slowPrepare)
    client.deadlineMs = 500
    try:
        pending = client.initializeProjectConfiguration(payload, root, 'cancel-first',
            runtimeInstanceId=checked['runtimeInstanceId'])
        assert pending['state'] == 'VALIDATING' and entered.wait(2)
        assert client.getProjectWriteStatus('cancel-first')['state'] == 'VALIDATING'
        assert client.initializeProjectConfiguration(payload, root, 'cancel-first',
            runtimeInstanceId=checked['runtimeInstanceId']) == pending
        with pytest.raises(RuntimeClientError) as error:
            client.initializeProjectConfiguration(payload, root, 'competing-first',
                runtimeInstanceId=checked['runtimeInstanceId'])
        assert error.value.code == 'E_CONFIG_WRITER_BUSY'
        legacy = service.LoadProject(pb.LoadProjectRequest(project_path=str(root)), None)
        assert not legacy.ok and 'E_CONFIG_WRITER_BUSY' in legacy.message
        cancelled = client.cancelProjectWrite('cancel-first')
        assert cancelled['state'] in {'VALIDATING', 'WAITING', 'CANCELLED'}
        assert service.projectConfiguration.current is None and service.loadedDocument is None
        assert not service.deviceManager.drivers
    finally:
        release.set()
        client.deadlineMs = 10000
    final = waitForWrite(service, client, 'cancel-first')
    assert final['state'] == 'CANCELLED'
    assert service.projectConfiguration.current is None and service.loadedDocument is None
    assert not service.jobRepository.all() and not service.deviceManager.drivers
    assert service.deviceManager.replacement is None
    assert {p for p in cache.rglob('*') if p.is_file()} == beforeCache
    assert client.initializeProjectConfiguration(payload, root, 'cancel-first',
        runtimeInstanceId=checked['runtimeInstanceId']) == final
    assert calls == [True]  # A cancelled request ID is never executed again.
    client.initializeProjectConfiguration(payload, root, 'explicit-new-write',
        runtimeInstanceId=checked['runtimeInstanceId'])
    assert waitForWrite(service, client, 'explicit-new-write')['state'] == 'COMMITTED'
    assert calls == [True, True] and not service.jobRepository.all()


def testFailedInitialWriteStaysQueryableWithoutReplay(initialRuntime, tmp_path, monkeypatch):
    from emo_master.apps.runtime.project_config import resources
    service, client = initialRuntime
    payload = imageProject(tmp_path / 'engineering')
    calls = []
    def failedPrepare(*_args, **_kwargs):
        calls.append(True)
        raise ValueError('simulated resource preparation failure')
    monkeypatch.setattr(resources, 'prepareResources', failedPrepare)
    client.initializeProjectConfiguration(payload, tmp_path / 'engineering', 'failed-first',
        runtimeInstanceId=service.runtimeInstanceId)
    status = waitForWrite(service, client, 'failed-first')
    assert status['state'] == 'FAILED' and 'preparation failure' in status['message']
    assert client.initializeProjectConfiguration(payload, tmp_path / 'engineering', 'failed-first',
        runtimeInstanceId=service.runtimeInstanceId) == status
    assert calls == [True] and service.loadedDocument is None and service.projectConfiguration.current is None
    assert not service.jobRepository.all() and not service.deviceManager.drivers


@pytest.mark.parametrize('policy', ['on_project_open', 'on_first_use'])
def testInitialWritePreservesConnectPolicyWithoutCapturing(initialRuntime, tmp_path, policy):
    service, client = initialRuntime
    payload = simulatedProject()
    payload['devices']['definitions']['camera']['policies'] = dict(connect=policy)
    checked = client.initializeProjectConfiguration(payload, tmp_path, 'check-policy', validate=True)
    assert not service.deviceManager.drivers
    client.initializeProjectConfiguration(payload, tmp_path, 'policy-first',
        runtimeInstanceId=checked['runtimeInstanceId'])
    receipt = waitForWrite(service, client, 'policy-first')
    assert receipt['state'] == 'COMMITTED' and not receipt.get('publicationError'), receipt
    lifecycle = service.deviceManager.lifecycles['camera']
    if policy == 'on_project_open':
        deadline = time.monotonic() + 5
        while not lifecycle.connected and time.monotonic() < deadline:
            time.sleep(.01)
        assert lifecycle.connected
    else:
        assert not lifecycle.connected
    assert service.deviceManager.drivers['camera'].sequence == 0 and not service.jobRepository.all()
    assert client.cancelProjectWrite('policy-first') == receipt
