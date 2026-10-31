import hashlib
from threading import Event

import pytest

from emo_master.apps.designer.services.runtime_connection import connectCandidate
from emo_master.apps.designer.state.project_store import saveProject
from emo_master.apps.operator_runtime.controller import ProductionRuntime
from emo_master.clients.runtime.project_resources import CHUNK_BYTES, downloadProjectResources
from emo_master.core.project.resources import Resource
from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb
from tests.runtime.current_production_fixture import currentProductionProject


def snapshotFor(data, path='models/model.bin'):
    return dict(runtimeInstanceId='instance', documentRevision=3, payload=dict(project=dict(projectId='project'),
        resources=dict(schemaVersion='1.0', items=dict(model=dict(path=path, size=len(data),
            sha256=hashlib.sha256(data).hexdigest(), purpose='model')))))


def chunks(data, snapshot, requests, cancelled=None, *, corrupt=False):
    def call(name, request):
        assert name == 'ReadProjectResource'
        requests.append(request)
        part = data[request.offset:request.offset + request.max_bytes]
        if corrupt:
            part = bytes(byte ^ 1 for byte in part)
        if cancelled:
            cancelled.set()
        return pb.ReadProjectResourceReply(ok=True, data=part, offset=request.offset,
            total_size=len(data), sha256=snapshot['payload']['resources']['items']['model']['sha256'],
            eof=request.offset + len(part) == len(data))
    return call


def testRealGrpcDownloadsOnlyDeclaredResourcesAndCanReadBackToAnotherRoot(tmp_path):
    path, document = currentProductionProject(tmp_path / 'source', declared=True)
    data = b'large declared model fixture\n' * 45000
    (path.parent / 'models').mkdir()
    (path.parent / 'models/model.bin').write_bytes(data)
    document.resources.items['model'] = Resource(path='models/model.bin', size=len(data),
        sha256=hashlib.sha256(data).hexdigest(), purpose='model')
    saveProject(path, document.model_dump(mode='json'))
    (path.parent / 'production.db').write_bytes(b'not a declared resource')
    (path.parent / 'outputs').mkdir(exist_ok=True)
    (path.parent / 'outputs/report.txt').write_text('business output')
    owner = ProductionRuntime(tmp_path / 'runtime')
    client = None
    try:
        owner.load(path)
        client, _info, snapshot, _types = connectCandidate(owner.connectionProfile)
        assert snapshot['sourcePath'] != str(tmp_path / 'designer')
        result = client.downloadProjectResources(snapshot, tmp_path / 'designer')
        assert not result['errors'] and len(result['downloaded']) == 2
        assert (tmp_path / 'designer/models/model.bin').read_bytes() == data
        assert (tmp_path / 'designer/input.png').read_bytes() == (path.parent / 'input.png').read_bytes()
        assert not (tmp_path / 'designer/production.db').exists() and not (tmp_path / 'designer/outputs').exists()
        again = client.downloadProjectResources(snapshot, tmp_path / 'designer')
        assert len(again['unchanged']) == 2 and not again['downloaded']
        assert not owner.runtime.jobRepository.all()
    finally:
        if client:
            client.close()
        owner.close()


@pytest.mark.parametrize('field,value,code', [
    ('runtime_instance_id', 'other', 'E_CONFIG_INSTANCE'),
    ('project_id', 'other', 'E_CONFIG_PROJECT'),
    ('expected_document_revision', 99999, 'E_CONFIG_CONFLICT'),
    ('resource_id', '../production.db', 'E_RESOURCE_DECLARATION'),
    ('sha256', '0' * 64, 'E_RESOURCE_DECLARATION'),
])
def testResourceRpcRequiresOnlineIdentityAndExactDeclaration(tmp_path, field, value, code):
    path, document = currentProductionProject(tmp_path / 'source', declared=True)
    owner = ProductionRuntime(tmp_path / 'runtime')
    client = None
    try:
        owner.load(path)
        client, _info, snapshot, _types = connectCandidate(owner.connectionProfile)
        request = pb.ReadProjectResourceRequest(runtime_instance_id=owner.runtime.runtimeInstanceId,
            project_id=document.project.projectId, resource_id='input', sha256=document.resources.items['input'].sha256,
            expected_document_revision=snapshot['documentRevision'], max_bytes=CHUNK_BYTES)
        setattr(request, field, value)
        reply = client._call('ReadProjectResource', request)
        assert not reply.ok and reply.code == code and not reply.data
    finally:
        if client:
            client.close()
        owner.close()


def testFinalDigestFailureLeavesExistingFileAndCleansStaging(tmp_path):
    data = b'expected contents'
    snapshot, requests = snapshotFor(data), []
    destination = tmp_path / 'models/model.bin'
    destination.parent.mkdir()
    destination.write_bytes(b'previous contents')
    result = downloadProjectResources(chunks(data, snapshot, requests, corrupt=True), snapshot, tmp_path, overwrite=True)
    assert result['errors'] and not result['downloaded']
    assert destination.read_bytes() == b'previous contents'
    assert not list(tmp_path.rglob('*.part'))


def testCancellationCleansUnfinishedFileAndDoesNotFetchNextRange(tmp_path):
    data = b'x' * (CHUNK_BYTES + 20)
    snapshot, requests, cancelled = snapshotFor(data), [], Event()
    result = downloadProjectResources(chunks(data, snapshot, requests, cancelled), snapshot, tmp_path, cancelled=cancelled)
    assert result['cancelled'] and len(requests) == 1 and not result['downloaded']
    assert not (tmp_path / 'models/model.bin').exists() and not list(tmp_path.rglob('*.part'))


def testDifferentLocalContentIsNotOverwrittenByDefault(tmp_path):
    data = b'new data'
    destination = tmp_path / 'models/model.bin'
    destination.parent.mkdir()
    destination.write_bytes(b'local work')
    result = downloadProjectResources(lambda *_: pytest.fail('conflict should be resolved before RPC'),
        snapshotFor(data), tmp_path)
    assert result['errors'] and destination.read_bytes() == b'local work'


@pytest.mark.parametrize('path', ['../outside.bin', '/outside.bin', 'C:/outside.bin', 'sub\\outside.bin'])
def testUnsafeDestinationDeclarationIsRejectedBeforeWriting(tmp_path, path):
    target = tmp_path / 'new directory'
    with pytest.raises(ValueError):
        downloadProjectResources(lambda *_: pytest.fail('unsafe declaration reached RPC'), snapshotFor(b'x', path), target)
    assert not target.exists()
