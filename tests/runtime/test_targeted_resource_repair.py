import pytest

from emo_master.apps.designer.services.runtime_client import RuntimeClient
from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb
from emo_master.apps.runtime.grpc_server.service import RuntimeService
from tests.runtime.test_review_resource_lifecycle import resourceProject, INPUT
from emo_master.core.project.models import ProjectDocument


def uploadCandidate(snapshot, root, names=('first', 'second', 'third'), size=32):
    from copy import deepcopy
    from tests.runtime.test_project_resource_transfer import manifest
    candidate = deepcopy(snapshot['payload'])
    candidate['resources'] = dict(items={}, parameterBindings=[])
    for index, name in enumerate(names):
        data = bytes([index + 1]) * size
        (root / (name + '.bin')).write_bytes(data)
        candidate['resources']['items'][name] = manifest(data, name + '.bin')
    return candidate


@pytest.mark.parametrize('size', [32, 600 * 1024])
def testUploadCancelledAfterFinalChunkDoesNotCommit(normalUploadRuntime, monkeypatch, size):
    from threading import Event
    from emo_master.apps.designer.services.runtime_client import RuntimeClientError
    runtime, client, project = normalUploadRuntime
    snapshot = client.getOnlineProjectSnapshot()
    candidate = uploadCandidate(snapshot, project.parent, names=('first',), size=size)
    disk = project.read_bytes()
    original, calls = client.transferProjectResource, []
    cancelled = Event()

    def transfer(snap, tid, action, **kwargs):
        calls.append((tid, action))
        result = original(snap, tid, action, **kwargs)
        if action == 'CHUNK' and kwargs['offset'] + len(kwargs['data']) == size:
            # Runtime accepted the last bytes, but the client has not received
            # the reply yet. Closing the dialog here requests cancellation.
            assert result['state'] == 'PENDING' and result['offset'] == size
            cancelled.set()
        return result

    with monkeypatch.context() as patch:
        patch.setattr(client, 'transferProjectResource', transfer)
        try:
            with pytest.raises(RuntimeClientError) as caught:
                client.uploadProjectResources(snapshot, candidate, project.parent, cancelled)
        finally:
            print('CANCEL_AFTER_FINAL_CHUNK', size, 'CALLS', calls)
    assert caught.value.code == 'E_CONFIG_CANCELLED'
    result = caught.value.resourceResult
    tid = calls[0][0]
    assert result['aborted'] and not result['uploaded'] and not result['pending']
    assert result['errors'][0]['stage'] == 'CHECK_CANCELLED'
    assert [item['requestTransferId'] for item in result['cancelled']] == [tid]
    assert [action for _, action in calls] == ['LOOKUP', 'BEGIN'] + ['CHUNK'] * (1 if size == 32 else 2) + ['CANCEL']
    assert all(transferId == tid for transferId, _ in calls)
    assert runtime.projectResourceTransfers._read(tid)[3] == 'CANCELLED'
    assert not client.pendingResourceTransfers
    # A later explicit upload can proceed; cancellation never commits for us.
    assert client.uploadProjectResources(snapshot, candidate, project.parent)['uploaded'][0]['state'] == 'READY'
    assert client.getOnlineProjectSnapshot() == snapshot and not runtime.jobRepository.all()
    assert project.read_bytes() == disk


@pytest.mark.parametrize('lostReply', [False, True])
def testUploadCancelledAfterCommitKeepsOutcomeWithoutReplay(normalUploadRuntime, monkeypatch, lostReply):
    from threading import Event
    runtime, client, project = normalUploadRuntime
    snapshot = client.getOnlineProjectSnapshot()
    candidate = uploadCandidate(snapshot, project.parent, names=('first',))
    original, calls = client.transferProjectResource, []
    cancelled = Event()

    def transfer(snap, tid, action, **kwargs):
        calls.append((tid, action))
        result = original(snap, tid, action, **kwargs)
        if action == 'COMMIT':
            cancelled.set()
            if lostReply:
                raise TimeoutError('commit reply lost after cancellation')
        return result

    monkeypatch.setattr(client, 'transferProjectResource', transfer)
    result = client.uploadProjectResources(snapshot, candidate, project.parent, cancelled)
    assert cancelled.is_set() and len(result['uploaded']) == 1
    assert not result['cancelled'] and not result['pending'] and not result['errors']
    assert [action for _, action in calls] == ['LOOKUP', 'BEGIN', 'CHUNK', 'COMMIT'] + (['STATUS'] if lostReply else [])
    assert all(tid == calls[0][0] for tid, _ in calls)
    assert not client.pendingResourceTransfers
    assert client.getOnlineProjectSnapshot() == snapshot and not runtime.jobRepository.all()


def testUploadChunkDisconnectRetainsRealBeginIdentity(normalUploadRuntime, monkeypatch):
    from emo_master.apps.designer.services.runtime_client import RuntimeClientError
    runtime, client, project = normalUploadRuntime
    snapshot = client.getOnlineProjectSnapshot()
    candidate = uploadCandidate(snapshot, project.parent, names=('first',), size=600 * 1024)
    original, calls = client.transferProjectResource, []
    disconnected = False
    def transfer(snap, tid, action, **kwargs):
        nonlocal disconnected
        calls.append((snap, tid, action))
        if action == 'CHUNK' and kwargs['offset']:
            disconnected = True
        if disconnected:
            raise TimeoutError('one connection loss')
        return original(snap, tid, action, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(client, 'transferProjectResource', transfer)
        with pytest.raises(Exception) as caught:
            client.uploadProjectResources(snapshot, candidate, project.parent)
    tid = calls[1][1]
    assert original(calls[1][0], tid, 'STATUS')['offset'] == 512 * 1024
    assert client.pendingResourceTransfers == {tid: (calls[1][0], 'first')}
    assert isinstance(caught.value, RuntimeClientError)
    assert [action for _, _, action in calls] == ['LOOKUP', 'BEGIN', 'CHUNK', 'CHUNK', 'CANCEL']
    assert caught.value.__cause__ is not None
    assert {e['stage'] for e in caught.value.resourceResult['errors']} == {'CHUNK', 'CANCEL'}
    assert client.reconcileProjectResources()['pending'][0]['requestTransferId'] == tid
    assert client.cancelProjectResourceTransfers([tid])['cancelled'][0]['requestTransferId'] == tid
    assert not client.pendingResourceTransfers
    assert client.uploadProjectResources(snapshot, candidate, project.parent)['uploaded'][0]['state'] == 'READY'
    assert client.getOnlineProjectSnapshot() == snapshot and not runtime.jobRepository.all()


@pytest.mark.parametrize('phase', ['LOOKUP', 'BEGIN', 'CHUNK', 'CHECK_CANCELLED'])
@pytest.mark.parametrize('cleanupLost', [False, True])
def testUploadBatchFailurePreservesEarlierReady(normalUploadRuntime, monkeypatch, phase, cleanupLost):
    from threading import Event
    from emo_master.apps.designer.services.runtime_client import RuntimeClientError
    runtime, client, project = normalUploadRuntime
    snapshot = client.getOnlineProjectSnapshot()
    candidate = uploadCandidate(snapshot, project.parent)
    original, calls, ids = client.transferProjectResource, [], {}
    cancelled = Event()
    def transfer(snap, tid, action, **kwargs):
        calls.append((tid, action))
        if action == 'LOOKUP':
            ids[kwargs['resource']['path']] = tid
        if tid == ids.get('second.bin') and action == phase:
            if phase == 'BEGIN':
                original(snap, tid, action, **kwargs)
            raise TimeoutError('second failed at ' + phase)
        if action == 'CANCEL' and cleanupLost:
            raise TimeoutError('cleanup unavailable')
        result = original(snap, tid, action, **kwargs)
        if tid == ids.get('first.bin') and action == 'COMMIT' and phase == 'CHECK_CANCELLED':
            cancelled.set()
        return result
    with monkeypatch.context() as patch:
        patch.setattr(client, 'transferProjectResource', transfer)
        with pytest.raises(Exception) as caught:
            client.uploadProjectResources(snapshot, candidate, project.parent, cancelled)
    result = getattr(caught.value, 'resourceResult', None)
    assert result is not None
    assert result['aborted']
    assert isinstance(caught.value, RuntimeClientError)
    assert [v['resourceId'] for v in result['uploaded']] == ['first']
    assert any(v['resourceId'] == 'second' and v['stage'] == phase for v in result['errors'])
    assert 'third.bin' not in ids
    first = runtime.projectResourceTransfers._read(ids['first.bin'])
    assert first[3] == 'READY'
    assert runtime.projectResourceTransfers._path(first[2]).read_bytes() == (project.parent / 'first.bin').read_bytes()
    pending = phase == 'BEGIN' or (phase == 'CHUNK' and cleanupLost)
    assert bool(client.pendingResourceTransfers) == pending
    assert len(result['cancelled']) == int(phase == 'CHUNK' and not cleanupLost)
    if pending:
        assert client.cancelProjectResourceTransfers(list(client.pendingResourceTransfers))['cancelled']
    calls.clear()
    def retry(snap, tid, action, **kwargs):
        calls.append((tid, action))
        return original(snap, tid, action, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(client, 'transferProjectResource', retry)
        result = client.uploadProjectResources(snapshot, candidate, project.parent)
    assert len(result['uploaded']) == 3 and not result['errors']
    assert [action for _, action in calls].count('BEGIN') == 2
    assert calls[0][1] == 'LOOKUP' and calls[1][1] == 'LOOKUP'
    assert client.getOnlineProjectSnapshot() == snapshot and not runtime.jobRepository.all()


@pytest.mark.parametrize('mode', ['lost', 'instance', 'capacity', 'lookup', 'unknown'])
def testUploadBeginAdmissionBoundary(normalUploadRuntime, monkeypatch, mode):
    from emo_master.apps.designer.services.runtime_client import RuntimeClientError
    runtime, client, project = normalUploadRuntime
    snapshot = client.getOnlineProjectSnapshot()
    candidate = uploadCandidate(snapshot, project.parent, names=('first',))
    original, calls = client.transferProjectResource, []
    def transfer(snap, tid, action, **kwargs):
        calls.append((tid, action))
        if action == 'LOOKUP' and mode == 'lookup':
            raise TimeoutError('lookup unavailable')
        if action == 'BEGIN':
            if mode == 'instance':
                return original(dict(snap, runtimeInstanceId='stale'), tid, action, **kwargs)
            if mode == 'capacity':
                with monkeypatch.context() as patch:
                    patch.setattr('emo_master.apps.runtime.project_config.transfer.MAX_TRANSFER_BYTES', 0)
                    return original(snap, tid, action, **kwargs)
            original(snap, tid, action, **kwargs)
            if mode == 'unknown':
                raise RuntimeClientError('E_RESOURCE_TRANSFER_UNKNOWN', 'one unknown is not proof of rejection')
            raise TimeoutError('begin reply lost')
        return original(snap, tid, action, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(client, 'transferProjectResource', transfer)
        with pytest.raises(RuntimeClientError) as caught:
            client.uploadProjectResources(snapshot, candidate, project.parent)
    assert caught.value.resourceResult['errors']
    assert bool(client.pendingResourceTransfers) == (mode in {'lost', 'unknown'})
    assert [action for _, action in calls] == (['LOOKUP'] if mode == 'lookup' else ['LOOKUP', 'BEGIN'])
    if mode in {'lost', 'unknown'}:
        assert client.reconcileProjectResources()['pending'][0]['requestTransferId'] == calls[1][0]
        assert client.cancelProjectResourceTransfers([calls[1][0]])['cancelled']
    elif mode != 'lookup':
        assert caught.value.code == {'instance': 'E_CONFIG_INSTANCE', 'capacity': 'E_RESOURCE_TRANSFER_CAPACITY'}[mode]
        with pytest.raises(Exception, match='not found'):
            runtime.projectResourceTransfers._read(calls[1][0])


def testUploadLocalPreflightHasNoFakePending(normalUploadRuntime):
    from emo_master.apps.designer.services.runtime_client import RuntimeClientError
    _runtime, client, project = normalUploadRuntime
    snapshot = client.getOnlineProjectSnapshot()
    candidate = uploadCandidate(snapshot, project.parent, names=('first',))
    candidate['resources']['items']['first']['size'] += 1
    with pytest.raises(RuntimeClientError) as caught:
        client.uploadProjectResources(snapshot, candidate, project.parent)
    assert caught.value.resourceResult['errors'][0]['stage'] == 'VALIDATE'
    assert not getattr(client, 'pendingResourceTransfers', {})


@pytest.fixture
def normalUploadRuntime(tmp_path):
    project, _source = resourceProject(tmp_path / 'source')
    runtime = RuntimeService(dbPath=tmp_path / 'runtime.db', workspaceRoot=tmp_path / 'jobs')
    assert runtime.LoadProject(pb.LoadProjectRequest(project_path=str(project)), None).ok
    try:
        yield runtime, RuntimeClient(runtimeService=runtime), project
    finally:
        runtime.close()


def pendingUpload(client, snapshot, transferId, state='PENDING'):
    item = snapshot['payload']['resources']['items']['image']
    client.transferProjectResource(snapshot, transferId, 'BEGIN', resource=item)
    client.transferProjectResource(snapshot, transferId, 'CHUNK', data=INPUT)
    if state == 'READY':
        client.transferProjectResource(snapshot, transferId, 'COMMIT')
    elif state == 'CANCELLED':
        client.transferProjectResource(snapshot, transferId, 'CANCEL')
    if not hasattr(client, 'pendingResourceTransfers'):
        client.pendingResourceTransfers = {}
    client.pendingResourceTransfers[transferId] = (snapshot, 'image')


@pytest.mark.parametrize('state,category', [('READY', 'uploaded'), ('CANCELLED', 'cancelled'), ('PENDING', 'pending')])
def testNormalUploadQueryClassifiesTerminalAndPending(normalUploadRuntime, state, category):
    runtime, client, project = normalUploadRuntime
    snapshot = client.getOnlineProjectSnapshot()
    pendingUpload(client, snapshot, 'original-request', state)
    result = client.reconcileProjectResources()
    assert len(result[category]) == 1 and not result['errors']
    assert result[category][0]['requestTransferId'] == 'original-request'
    assert bool(client.pendingResourceTransfers) == (state == 'PENDING')
    if state != 'PENDING':
        assert client.reconcileProjectResources()['uploaded'] == []
        assert client.uploadProjectResources(snapshot, snapshot['payload'], project.parent)['uploaded']
    assert client.getOnlineProjectSnapshot() == snapshot
    assert not runtime.jobRepository.all()


def testNormalUploadMixedQueryKeepsConfirmedResultsDespiteAnotherError(normalUploadRuntime, monkeypatch):
    _runtime, client, _project = normalUploadRuntime
    snapshot = client.getOnlineProjectSnapshot()
    for name, state in [('unreachable', 'PENDING'), ('ready', 'READY'), ('cancelled', 'CANCELLED'), ('waiting', 'PENDING')]:
        pendingUpload(client, snapshot, name, state)
    original = client.transferProjectResource
    def query(snap, transferId, action, **kwargs):
        assert action == 'STATUS'
        if transferId == 'unreachable':
            raise TimeoutError('temporary disconnection')
        return original(snap, transferId, action, **kwargs)
    monkeypatch.setattr(client, 'transferProjectResource', query)
    result = client.reconcileProjectResources()
    assert [v['requestTransferId'] for v in result['uploaded']] == ['ready']
    assert [v['requestTransferId'] for v in result['cancelled']] == ['cancelled']
    assert [v['requestTransferId'] for v in result['pending']] == ['waiting']
    assert result['errors'][0]['message'] == 'temporary disconnection'
    assert set(client.pendingResourceTransfers) == {'unreachable', 'waiting'}


@pytest.mark.parametrize('action', ['query', 'cancel'])
def testNormalUploadUnavailableActionKeepsIdentityAndCanBeRetried(normalUploadRuntime, monkeypatch, action):
    _runtime, client, _project = normalUploadRuntime
    snapshot = client.getOnlineProjectSnapshot()
    pendingUpload(client, snapshot, 'original')
    saved = dict(client.pendingResourceTransfers)
    original = client.transferProjectResource
    calls = []
    def unavailable(snap, transferId, operation, **kwargs):
        calls.append((snap, transferId, operation))
        if operation == 'CANCEL':
            original(snap, transferId, operation, **kwargs)
        raise TimeoutError('lost response')
    with monkeypatch.context() as patch:
        patch.setattr(client, 'transferProjectResource', unavailable)
        result = (client.reconcileProjectResources() if action == 'query'
                  else client.cancelProjectResourceTransfers(['original']))
    assert result['errors'] and not result['uploaded'] and not result['cancelled']
    assert client.pendingResourceTransfers == saved
    assert calls == [(snapshot, 'original', 'STATUS' if action == 'query' else 'CANCEL')]
    result = client.reconcileProjectResources()
    assert result['pending' if action == 'query' else 'cancelled']


def testNormalUploadExplicitCancelHonorsSelectionIdentityAndOnlineProtection(normalUploadRuntime):
    runtime, client, _project = normalUploadRuntime
    snapshot = client.getOnlineProjectSnapshot()
    pendingUpload(client, snapshot, 'cancel-this')
    pendingUpload(client, snapshot, 'keep-this')
    online = client.transferProjectResource(snapshot, 'lookup', 'LOOKUP',
        resource=snapshot['payload']['resources']['items']['image'])['transferId']
    client.pendingResourceTransfers[online] = (snapshot, 'image')
    result = client.cancelProjectResourceTransfers(['cancel-this', online])
    assert [v['requestTransferId'] for v in result['cancelled']] == ['cancel-this']
    assert result['errors'][0]['code'] == 'E_RESOURCE_TRANSFER_READY'
    assert set(client.pendingResourceTransfers) == {'keep-this', online}
    client.pendingResourceTransfers['keep-this'] = (dict(snapshot, runtimeInstanceId='stale'), 'image')
    result = client.cancelProjectResourceTransfers(['keep-this'])
    assert result['errors'][0]['code'] == 'E_CONFIG_INSTANCE'
    assert 'keep-this' in client.pendingResourceTransfers
    assert client.transferProjectResource(snapshot, 'keep-this', 'STATUS')['state'] == 'PENDING'
    assert client.getOnlineProjectSnapshot() == snapshot and not runtime.jobRepository.all()


@pytest.mark.parametrize('state', ['PENDING', 'CANCELLED'])
def testUploadExceptionUsesSameStateClassificationWithoutImplicitCancel(normalUploadRuntime, monkeypatch, state):
    from copy import deepcopy
    _runtime, client, project = normalUploadRuntime
    snapshot = client.getOnlineProjectSnapshot()
    candidate = deepcopy(snapshot['payload'])
    candidate['resources']['items']['image']['path'] = 'replacement.png'
    (project.parent / 'replacement.png').write_bytes(INPUT)
    original, calls = client.transferProjectResource, []
    def interrupted(snap, transferId, action, **kwargs):
        calls.append((transferId, action))
        if action == 'COMMIT':
            if state == 'CANCELLED':
                original(snap, transferId, 'CANCEL')
            raise TimeoutError('commit unavailable')
        return original(snap, transferId, action, **kwargs)
    monkeypatch.setattr(client, 'transferProjectResource', interrupted)
    result = client.uploadProjectResources(snapshot, candidate, project.parent)
    assert result['pending' if state == 'PENDING' else 'cancelled']
    assert result['aborted']
    assert not result['uploaded']
    assert [action for _, action in calls] == ['LOOKUP', 'BEGIN', 'CHUNK', 'COMMIT', 'STATUS']
    assert bool(client.pendingResourceTransfers) == (state == 'PENDING')
    monkeypatch.setattr(client, 'transferProjectResource', original)
    if state == 'PENDING':
        assert client.cancelProjectResourceTransfers(list(client.pendingResourceTransfers))['cancelled']
    result = client.uploadProjectResources(snapshot, candidate, project.parent)
    assert result['uploaded'][0]['state'] == 'READY'
    assert result['uploaded'][0]['requestTransferId'] != calls[0][0]
    assert client.getOnlineProjectSnapshot() == snapshot


def brokenRuntime(tmp_path, missing=False):
    project, source = resourceProject(tmp_path / 'source')
    runtime = RuntimeService(dbPath=tmp_path / 'runtime.db', workspaceRoot=tmp_path / 'jobs')
    assert runtime.LoadProject(pb.LoadProjectRequest(project_path=str(project)), None).ok
    owned = runtime.projectResourceTransfers.resolve(runtime.loadedDocument)['image']
    runtime.close()
    if missing:
        owned.unlink()
    else:
        owned.write_bytes(b'corrupt')
    runtime = RuntimeService(dbPath=tmp_path / 'runtime.db', workspaceRoot=tmp_path / 'jobs')
    assert runtime.onlineRecoveryError
    return runtime, project, source, owned


@pytest.mark.parametrize('missing', [False, True])
def testNormalLookupUploadAndSameRevisionRecovery(tmp_path, missing):
    runtime, project, source, old = brokenRuntime(tmp_path, missing)
    repairRoot = tmp_path / 'provided-original'
    project.parent.rename(repairRoot)
    project = repairRoot / project.name
    import grpc
    from emo_master.apps.runtime.main import createRuntimeServer
    from emo_master.apps.runtime.grpc_server.generated.runtime_pb2_grpc import RuntimeServiceStub
    server, port, _ = createRuntimeServer(port=0, runtimeService=runtime)
    server.start()
    channel = grpc.insecure_channel(f'127.0.0.1:{port}')
    try:
        client = RuntimeClient(RuntimeServiceStub(channel))
        snapshot = client.getOnlineProjectSnapshot()
        payload = snapshot['payload']
        item = payload['resources']['items']['image']
        found = client.transferProjectResource(snapshot, 'lookup', 'LOOKUP', resource=item)
        assert found['state'] == 'CORRUPT'
        result = client.uploadProjectResources(snapshot, payload, project.parent, repairConfirmed=True)
        assert result['uploaded'][0]['state'] == 'READY'
        new = runtime.projectResourceTransfers.resolve(runtime.loadedDocument or ProjectDocument.model_validate(payload))['image']
        assert new != old and new.read_bytes() == INPUT
        assert runtime.onlineRecoveryError
        status = client.retryProjectResourceRecovery(snapshot, 'recover')
        runtime.projectConfiguration.thread.join(10)
        status = client.getProjectWriteStatus('recover')
        assert status['state'] == 'RECOVERED', status
        assert not runtime.onlineRecoveryError
        after = client.getOnlineProjectSnapshot()
        assert after['documentRevision'] == snapshot['documentRevision']
        assert after['executionRevision'] == snapshot['executionRevision']
        assert after['payload'] == payload
        assert not runtime.jobRepository.all() and not runtime.deviceManager.workers
        assert not any(v.connected for v in runtime.deviceManager.lifecycles.values())
        assert client.retryProjectResourceRecovery(snapshot, 'recover') == status
    finally:
        channel.close()
        server.stop(0).wait()
        runtime.close()


def beginRepair(client, snapshot, transferId, oldId):
    item = snapshot['payload']['resources']['items']['image']
    client.transferProjectResource(snapshot, transferId, 'BEGIN', resource=item, replacementOf=oldId)
    client.transferProjectResource(snapshot, transferId, 'CHUNK', data=INPUT)


def testPinnedCorruptObjectSurvivesRepairAndPublicCancelUntilReaderExits(tmp_path):
    project, source = resourceProject(tmp_path / 'source')
    runtime = RuntimeService(dbPath=tmp_path / 'runtime.db', workspaceRoot=tmp_path / 'jobs')
    try:
        assert runtime.LoadProject(pb.LoadProjectRequest(project_path=str(project)), None).ok
        transfers = runtime.projectResourceTransfers
        pin = transfers.pin(runtime.loadedDocument, project.parent)
        old = transfers.resolve(runtime.loadedDocument)['image']
        from tests.runtime.test_prepared_resources import imagePath
        materialized = imagePath(runtime)
        old.write_bytes(b'corrupt')
        client = RuntimeClient(runtimeService=runtime)
        snap = client.getOnlineProjectSnapshot()
        oldId = client.transferProjectResource(snap, 'lookup', 'LOOKUP', resource=snap['payload']['resources']['items']['image'])['transferId']
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        barrier = Barrier(2)
        def repair():
            barrier.wait(timeout=5)
            return client.uploadProjectResources(snap, snap['payload'], project.parent, repairConfirmed=True)
        def cancelPinned():
            barrier.wait(timeout=5)
            with pytest.raises(Exception, match='pinned'):
                client.transferProjectResource(snap, oldId, 'CANCEL')
        with ThreadPoolExecutor(2) as pool:
            repaired, cancelled = pool.submit(repair), pool.submit(cancelPinned)
            result = repaired.result(timeout=10)
            cancelled.result(timeout=10)
        assert old.exists() and old.read_bytes() == b'corrupt'
        assert materialized.read_bytes() == INPUT
        new = transfers.resolve(runtime.loadedDocument)['image']
        assert new != old and new.read_bytes() == INPUT
        for transferId in [oldId, result['uploaded'][0]['transferId']]:
            with pytest.raises(Exception, match='pinned|referenced'):
                client.transferProjectResource(snap, transferId, 'CANCEL')
        transfers.unpin(pin)
        assert not old.exists() and new.read_bytes() == INPUT
    finally:
        runtime.close()


def testCompetingRepairsReuseOneAuthorityAndLostCommitIsOnlyQueried(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    runtime, project, source, old = brokenRuntime(tmp_path)
    try:
        client = RuntimeClient(runtimeService=runtime)
        snap = client.getOnlineProjectSnapshot()
        item = snap['payload']['resources']['items']['image']
        oldId = client.transferProjectResource(snap, 'lookup', 'LOOKUP', resource=item)['transferId']
        for name in ['repair-a', 'repair-b']:
            beginRepair(client, snap, name, oldId)
        barrier = Barrier(2)
        def commit(name):
            barrier.wait(timeout=5)
            return client.transferProjectResource(snap, name, 'COMMIT')
        with ThreadPoolExecutor(2) as pool:
            results = list(pool.map(commit, ['repair-a', 'repair-b']))
        assert all(v['state'] == 'READY' for v in results)
        assert len({v['transferId'] for v in results}) == 1
        with runtime.sqliteStore.connection() as c:
            assert c.execute('SELECT count(*) FROM projectResourceTransfers WHERE active=1').fetchone()[0] == 1
        assert len(list(runtime.projectResourceTransfers.root.iterdir())) == 1
        current = runtime.projectResourceTransfers.resolve(ProjectDocument.model_validate(snap['payload']))['image']
        current.write_bytes(b'broken again')
        calls = []
        original = client.transferProjectResource
        def lost(snapshot, transferId, action, **kwargs):
            calls.append(action)
            result = original(snapshot, transferId, action, **kwargs)
            if action == 'COMMIT':
                raise TimeoutError('lost successful reply')
            return result
        monkeypatch.setattr(client, 'transferProjectResource', lost)
        result = client.uploadProjectResources(snap, snap['payload'], project.parent, repairConfirmed=True)
        assert result['uploaded'][0]['state'] == 'READY'
        assert calls.count('COMMIT') == 1 and calls.count('STATUS') == 1 and 'CANCEL' not in calls
        assert not client.pendingResourceTransfers
    finally:
        runtime.close()


@pytest.mark.parametrize('failure', ['digest', 'revision', 'transaction'])
def testFailedRepairNeverPromotesOrRemovesOldAuthority(tmp_path, monkeypatch, failure):
    from contextlib import contextmanager
    runtime, project, source, old = brokenRuntime(tmp_path)
    try:
        client = RuntimeClient(runtimeService=runtime)
        snap = client.getOnlineProjectSnapshot()
        item = snap['payload']['resources']['items']['image']
        oldId = client.transferProjectResource(snap, 'lookup', 'LOOKUP', resource=item)['transferId']
        beginRepair(client, snap, 'repair', oldId)
        owned = runtime.projectResourceTransfers._path(runtime.projectResourceTransfers._read('repair')[2])
        if failure == 'digest':
            owned.write_bytes(b'x' * len(INPUT))
        elif failure == 'revision':
            coordinator = runtime.projectConfiguration
            coordinator.current = coordinator.store.advanceRequestFence(snap['documentRevision'])
        else:
            original = runtime.sqliteStore.connection
            class Connection:
                def __init__(self, wrapped):
                    self.wrapped = wrapped
                def __getattr__(self, name):
                    return getattr(self.wrapped, name)
                def execute(self, sql, *args):
                    if "active=1,state='READY'" in sql:
                        raise OSError('injected promotion transaction failure')
                    return self.wrapped.execute(sql, *args)
            @contextmanager
            def connection():
                with original() as c:
                    yield Connection(c)
            monkeypatch.setattr(runtime.sqliteStore, 'connection', connection)
        with pytest.raises(Exception):
            client.transferProjectResource(snap, 'repair', 'COMMIT')
        found = client.transferProjectResource(snap, 'lookup', 'LOOKUP', resource=item)
        assert found['state'] == 'CORRUPT' and found['transferId'] == oldId
        assert old.read_bytes() == b'corrupt'
        client.transferProjectResource(snap, 'repair', 'CANCEL')
        assert not owned.exists()
    finally:
        runtime.close()


def testRecoveryRejectsOtherFailureAndConcurrentWriter(tmp_path):
    from threading import Event
    runtime, project, source, old = brokenRuntime(tmp_path)
    release, entered = Event(), Event()
    try:
        client = RuntimeClient(runtimeService=runtime)
        snapshot = client.getOnlineProjectSnapshot()
        client.uploadProjectResources(snapshot, snapshot['payload'], project.parent, repairConfirmed=True)
        runtime.onlineRecoveryKind = 'other'
        with pytest.raises(Exception, match='only resource'):
            client.retryProjectResourceRecovery(snapshot, 'other')
        runtime.onlineRecoveryKind = 'resource'
        original = runtime.projectConfiguration.prepare
        def prepare(payload, cancelled):
            entered.set()
            assert release.wait(10)
            return original(payload, cancelled)
        runtime.projectConfiguration.prepare = prepare
        client.retryProjectResourceRecovery(snapshot, 'recovery')
        assert entered.wait(5)
        with pytest.raises(Exception, match='pending'):
            client.retryProjectResourceRecovery(snapshot, 'second')
        release.set()
        runtime.projectConfiguration.thread.join(10)
        assert client.getProjectWriteStatus('recovery')['state'] == 'RECOVERED'
    finally:
        release.set()
        runtime.close()


def testCorruptNormalHelperRequiresExplicitConfirmation(tmp_path):
    runtime, project, source, old = brokenRuntime(tmp_path)
    try:
        client = RuntimeClient(runtimeService=runtime)
        snapshot = client.getOnlineProjectSnapshot()
        with pytest.raises(Exception, match='confirm'):
            client.uploadProjectResources(snapshot, snapshot['payload'], project.parent)
        assert old.read_bytes() == b'corrupt'
    finally:
        runtime.close()


def testUnknownCommitKeepsOriginalIdentityUntilReadOnlyReconciliation(tmp_path, monkeypatch):
    runtime, project, source, old = brokenRuntime(tmp_path)
    try:
        client = RuntimeClient(runtimeService=runtime)
        snap = client.getOnlineProjectSnapshot()
        original, calls = client.transferProjectResource, []
        def lost(snapshot, transferId, action, **kwargs):
            calls.append((transferId, action))
            if action == 'STATUS':
                raise TimeoutError('status transport unavailable')
            result = original(snapshot, transferId, action, **kwargs)
            if action == 'COMMIT':
                raise TimeoutError('commit reply lost')
            return result
        monkeypatch.setattr(client, 'transferProjectResource', lost)
        with pytest.raises(Exception, match='query original'):
            client.uploadProjectResources(snap, snap['payload'], project.parent, repairConfirmed=True)
        pending = dict(client.pendingResourceTransfers)
        assert len(pending) == 1
        with pytest.raises(Exception, match='pending resource transfer'):
            client.uploadProjectResources(snap, snap['payload'], project.parent, repairConfirmed=True)
        assert [action for _, action in calls] == ['LOOKUP', 'BEGIN', 'CHUNK', 'COMMIT', 'STATUS']
        def restored(snapshot, transferId, action, **kwargs):
            assert transferId in pending and action == 'STATUS'
            return original(snapshot, transferId, action, **kwargs)
        monkeypatch.setattr(client, 'transferProjectResource', restored)
        assert client.reconcileProjectResources()['uploaded'][0]['state'] == 'READY'
        assert not client.pendingResourceTransfers
    finally:
        runtime.close()


@pytest.mark.parametrize('validIds', [('a', 'b'), ('b',), ()])
def testLegacyDuplicateMigrationVerifiesBytesAndSelectsDeterministically(tmp_path, validIds):
    from emo_master.apps.runtime.context.sqlite_store import SqliteStore
    from emo_master.apps.runtime.project_config.transfer import ResourceTransfers
    from emo_master.core.project.resources import Resource
    from tests.runtime.test_project_resource_transfer import manifest, request
    store, root = SqliteStore(tmp_path / 'legacy.db'), tmp_path / 'uploads'
    root.mkdir()
    item = Resource.model_validate(manifest())
    with store.connection() as c:
        c.execute('CREATE TABLE projectResourceTransfers (transferId TEXT PRIMARY KEY, projectId TEXT, manifest TEXT, filename TEXT UNIQUE, state TEXT, offset INTEGER)')
        for index, transferId in enumerate(['b', 'a']):
            filename = str(index) * 32 + '.bin'
            (root / filename).write_bytes(b'model data' if transferId in validIds else b'broken')
            c.execute('INSERT INTO projectResourceTransfers VALUES (?,?,?,?,?,?)',
                      (transferId, 'project', item.model_dump_json(), filename, 'READY', item.size))
    transfers = ResourceTransfers(store, root)
    try:
        result = transfers.execute(request('LOOKUP'))
        assert result['transferId'] == (sorted(validIds)[0] if validIds else 'a')
        assert result['state'] == ('READY' if validIds else 'CORRUPT')
        with store.connection() as c:
            assert c.execute('SELECT count(*) FROM projectResourceTransfers WHERE active=1').fetchone()[0] == 1
        unknown = root / 'not-managed.txt'
        unknown.write_bytes(b'preserve')
        transfers.recover()
        assert unknown.read_bytes() == b'preserve'
        assert len(list(root.iterdir())) == 2
    finally:
        transfers.close()


def testLookupDoesNotMisreportPermissionFailureAsMissing(tmp_path, monkeypatch):
    runtime, project, source, old = brokenRuntime(tmp_path)
    try:
        from pathlib import Path
        original = Path.stat
        def denied(path, *args, **kwargs):
            if path == old:
                raise PermissionError('resource permission denied')
            return original(path, *args, **kwargs)
        with monkeypatch.context() as patch:
            patch.setattr(Path, 'stat', denied)
            client = RuntimeClient(runtimeService=runtime)
            snap = client.getOnlineProjectSnapshot()
            with pytest.raises(Exception, match='permission denied'):
                client.transferProjectResource(snap, 'lookup', 'LOOKUP', resource=snap['payload']['resources']['items']['image'])
    finally:
        runtime.close()


def testRecoveryCancellationAndFencesPreserveVariablesAndDeviceFaults(tmp_path):
    from threading import Event
    from emo_master.apps.runtime.context.global_variables import ProjectGlobalVariables
    runtime, project, source, old = brokenRuntime(tmp_path)
    release, entered = Event(), Event()
    try:
        client = RuntimeClient(runtimeService=runtime)
        snap = client.getOnlineProjectSnapshot()
        client.uploadProjectResources(snap, snap['payload'], project.parent, repairConfirmed=True)
        projectId = snap['payload']['project']['projectId']
        variables = ProjectGlobalVariables(runtime.sqliteStore, projectId, {
            'counter': dict(name='Counter', type='integer', kind='variable', lifetime='persistent', initialValue=27)})
        variables.synchronize()
        with runtime.sqliteStore.connection() as c:
            before = c.execute('SELECT * FROM variableValues').fetchall()
        for invalid in [dict(snap, runtimeInstanceId='stale'), dict(snap, documentRevision=999)]:
            with pytest.raises(Exception):
                client.retryProjectResourceRecovery(invalid, 'stale-' + str(invalid['documentRevision']))
        original = runtime.projectConfiguration.prepare
        def prepare(payload, cancelled):
            prepared = original(payload, cancelled)
            entered.set()
            assert release.wait(10)
            return prepared
        runtime.projectConfiguration.prepare = prepare
        client.retryProjectResourceRecovery(snap, 'cancel')
        assert entered.wait(5)
        client.cancelProjectWrite('cancel')
        release.set()
        runtime.projectConfiguration.thread.join(10)
        assert client.getProjectWriteStatus('cancel')['state'] == 'CANCELLED'
        assert runtime.onlineRecoveryError
        runtime.projectConfiguration.prepare = original
        client.retryProjectResourceRecovery(snap, 'recover')
        runtime.projectConfiguration.thread.join(10)
        assert client.getProjectWriteStatus('recover')['state'] == 'RECOVERED'
        with runtime.sqliteStore.connection() as c:
            assert c.execute('SELECT * FROM variableValues').fetchall() == before
        runtime.onlineRecoveryError, runtime.onlineRecoveryKind = 'resource broken', 'resource'
        executor = next(iter(runtime.deviceManager.executors.values()))
        executor.quarantined = True
        with pytest.raises(Exception, match='device recovery'):
            client.retryProjectResourceRecovery(snap, 'device-fault')
        assert executor.quarantined and runtime.onlineRecoveryError
    finally:
        release.set()
        runtime.close()
