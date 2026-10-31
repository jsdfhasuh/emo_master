"""Remote first import over real gRPC, with local temporary resources only."""
from copy import deepcopy
import json
from pathlib import Path

import grpc
import pytest

from emo_master.apps.designer.services.runtime_client import RuntimeClient, RuntimeClientError
from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb, runtime_pb2_grpc
from emo_master.apps.runtime.grpc_server.service import RuntimeService
from emo_master.apps.runtime.main import createRuntimeServer
from tests.runtime.current_production_fixture import currentProductionProject, node
from tests.runtime.runtime_test_utils import waitForTerminal
from tests.runtime.test_initial_project_configuration import waitForWrite


@pytest.fixture
def remoteImport(tmp_path):
    service = RuntimeService(dbPath=tmp_path / 'runtime/data/runtime.db',
        workspaceRoot=tmp_path / 'runtime/data/jobs')
    server, port, _ = createRuntimeServer(port=0, runtimeService=service)
    server.start()
    channel = grpc.insecure_channel(f'127.0.0.1:{port}')
    grpc.channel_ready_future(channel).result(timeout=5)
    # Exercise remote path semantics while keeping all sockets on the test host.
    client = RuntimeClient(runtime_pb2_grpc.RuntimeServiceStub(channel), runtimeTarget='192.0.2.20:50051')
    yield service, client
    client.close()
    channel.close()
    server.stop(0).wait()
    service.close()


def candidate(tmp_path):
    path, document = currentProductionProject(tmp_path / 'engineering', declared=True)
    return path, document.model_dump(mode='json')


def testDeclareUploadValidateCommitAndRunWithoutSendingClientPaths(remoteImport, tmp_path, monkeypatch):
    service, client = remoteImport
    path, payload = candidate(tmp_path)
    before = {str(p.relative_to(path.parent)): p.read_bytes() for p in path.parent.rglob('*') if p.is_file()}
    sent = []
    originalCall = client._call
    def call(method, request, **kwargs):
        if method == 'InitializeProjectConfiguration':
            sent.append(request)
        return originalCall(method, request, **kwargs)
    monkeypatch.setattr(client, '_call', call)

    staged = client.prepareProjectImport(payload, 'remote-import')
    assert client.getProjectWriteStatus('remote-import')['state'] == 'AWAITING_RESOURCES'
    with pytest.raises(RuntimeClientError) as missing:
        client.initializeProjectConfiguration(payload, path.parent, 'validation-only', validate=True, projectPath=path)
    assert missing.value.code == 'E_CONFIG_INVALID'
    assert 'resource' in str(missing.value).lower()
    assert not service.loadedDocument and not service.jobRepository.all()
    assert not list(service.projectResourceTransfers.root.glob('*.png'))
    uploaded = client.uploadInitialProjectResources(payload, path.parent)
    assert len(uploaded['uploaded']) == 1 and not uploaded['errors']
    assert service.projectConfiguration.current is None
    checked = client.initializeProjectConfiguration(payload, path.parent, 'validation', validate=True, projectPath=path)
    assert checked['state'] == 'VALIDATED' and checked['importId'] == staged['importId']
    client.initializeProjectConfiguration(payload, path.parent, checked['importId'],
        runtimeInstanceId=checked['runtimeInstanceId'], projectPath=path)
    receipt = waitForWrite(service, client, checked['importId'])
    assert receipt['state'] == 'COMMITTED', receipt
    assert all(not item.resource_root and not item.project_path for item in sent)
    snapshot = client.getOnlineProjectSnapshot()
    assert Path(snapshot['sourcePath']).is_relative_to(service.workspaceRoot.parent / 'online-projects')
    assert snapshot['payload'] == payload
    assert not service.jobRepository.all()
    assert {str(p.relative_to(path.parent)): p.read_bytes() for p in path.parent.rglob('*') if p.is_file()} == before
    loaded = Path(node(service.loadedDocument, 'load').params['imagePath'])
    assert loaded != path.parent / 'input.png' and loaded.read_bytes() == before['input.png']
    assert client.initializeProjectConfiguration(payload, None, checked['importId'],
        runtimeInstanceId=checked['runtimeInstanceId']) == receipt
    started = client.startJob(service.loadedProjectId, 'main')
    assert started.ok, started.message
    terminal = waitForTerminal(service, started.job_id, timeoutSeconds=30)
    assert terminal.status == 'COMPLETED', terminal


def testCandidateMembershipAndCancellationUseExistingRequestOwner(remoteImport, tmp_path):
    service, client = remoteImport
    path, payload = candidate(tmp_path)
    staged = client.prepareProjectImport(payload, 'candidate-one')
    declared = payload['resources']['items']['input']
    undeclared = dict(declared, path='other.png')
    with pytest.raises(RuntimeClientError) as error:
        client.transferProjectResource(staged, 'undeclared', 'BEGIN', resource=undeclared)
    assert error.value.code == 'E_RESOURCE_DECLARATION'
    client.transferProjectResource(staged, 'owned-transfer', 'BEGIN', resource=declared)
    other = deepcopy(payload)
    other['project']['name'] = 'Other candidate'
    second = client.prepareProjectImport(other, 'candidate-two')
    for action in ('CHUNK', 'BEGIN'):
        with pytest.raises(RuntimeClientError) as error:
            client.transferProjectResource(second, 'owned-transfer', action,
                resource=declared if action == 'BEGIN' else None, data=b'x' if action == 'CHUNK' else b'')
        assert error.value.code == 'E_RESOURCE_PROJECT'
    assert client.cancelProjectWrite('candidate-one')['state'] == 'CANCELLED'
    with pytest.raises(RuntimeClientError) as error:
        client.transferProjectResource(staged, 'owned-transfer', 'CHUNK', data=b'x')
    assert error.value.code == 'E_CONFIG_IMPORT_STATE'
    assert client.transferProjectResource(staged, 'owned-transfer', 'CANCEL')['state'] == 'CANCELLED'
    request = pb.InitialProjectConfigurationRequest(request_id='candidate-one', import_id='candidate-one',
        expected_runtime_instance_id=service.runtimeInstanceId)
    assert client._projectConfigurationCall('InitializeProjectConfiguration', request)['state'] == 'CANCELLED'
    assert service.projectConfiguration.current is None
    assert client.cancelProjectWrite('candidate-two')['state'] == 'CANCELLED'
    assert client.prepareProjectImport(payload, 'candidate-three')['importId'] == 'candidate-three'
    assert not service.jobRepository.all()


def testRemoteImportRejectsClientPathsAndChangedCandidate(remoteImport, tmp_path):
    service, client = remoteImport
    path, payload = candidate(tmp_path)
    staged = client.prepareProjectImport(payload, 'candidate')
    request = pb.InitialProjectConfigurationRequest(request_id='candidate', import_id='candidate',
        expected_runtime_instance_id=staged['runtimeInstanceId'], resource_root=str(path.parent))
    with pytest.raises(RuntimeClientError) as error:
        client._projectConfigurationCall('InitializeProjectConfiguration', request)
    assert error.value.code == 'E_CONFIG_IMPORT_PATH'
    request.resource_root = ''
    altered = deepcopy(payload)
    altered['project']['name'] = 'Changed'
    request.local_payload_json = json.dumps(altered)
    with pytest.raises(RuntimeClientError) as error:
        client._projectConfigurationCall('InitializeProjectConfiguration', request)
    assert error.value.code == 'E_CONFIG_REQUEST_MISMATCH'
    assert service.projectConfiguration.current is None and not service.jobRepository.all()


def testCommittedRemoteImportIsRecoverableAfterRuntimeRestart(remoteImport, tmp_path):
    service, client = remoteImport
    path, payload = candidate(tmp_path)
    staged = client.prepareProjectImport(payload, 'durable')
    client.uploadInitialProjectResources(payload, path.parent)
    client.initializeProjectConfiguration(payload, None, 'durable', runtimeInstanceId=staged['runtimeInstanceId'])
    receipt = waitForWrite(service, client, 'durable')
    assert receipt['state'] == 'COMMITTED', receipt
    service.close()
    restarted = RuntimeService(dbPath=tmp_path / 'runtime/data/runtime.db',
        workspaceRoot=tmp_path / 'runtime/data/jobs')
    reader = RuntimeClient(restarted)
    try:
        assert reader.getProjectWriteStatus('durable')['state'] == 'COMMITTED'
        assert reader.getOnlineProjectSnapshot()['payload'] == payload
        assert not restarted.onlineRecoveryError and not restarted.jobRepository.all()
        assert Path(node(restarted.loadedDocument, 'load').params['imagePath']).is_file()
    finally:
        reader.close()
        restarted.close()


def testDesktopAdoptsFirstRemotelyPublishedProjectWithoutStarting(tmp_path):
    from emo_master.apps.operator_runtime.controller import ProductionRuntime
    owner = ProductionRuntime(tmp_path / 'runtime')
    path, payload = candidate(tmp_path)
    try:
        assert owner.status()['state'] == 'EMPTY'
        with grpc.insecure_channel(owner.address) as channel:
            client = RuntimeClient(runtime_pb2_grpc.RuntimeServiceStub(channel), runtimeTarget='192.0.2.20:50051')
            try:
                staged = client.prepareProjectImport(payload, 'desktop-import')
                client.uploadInitialProjectResources(payload, path.parent)
                client.initializeProjectConfiguration(payload, None, 'desktop-import',
                    runtimeInstanceId=staged['runtimeInstanceId'])
                assert waitForWrite(owner.runtime, client, 'desktop-import')['state'] == 'COMMITTED'
                assert owner.document is None
                state = owner.status()
                assert state['state'] == 'READY' and state['canStart']
                assert owner.document is owner.runtime.loadedDocument
                assert owner.projectOwner.root == Path(owner.runtime.loadedProjectFile).parent
                assert not owner.runtime.jobRepository.all()
            finally:
                client.close()
    finally:
        owner.close()
