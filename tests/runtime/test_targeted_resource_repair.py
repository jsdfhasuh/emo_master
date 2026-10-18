import pytest

from emo_master.apps.designer.services.runtime_client import RuntimeClient
from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb
from emo_master.apps.runtime.grpc_server.service import RuntimeService
from tests.runtime.test_review_resource_lifecycle import resourceProject, INPUT
from emo_master.core.project.models import ProjectDocument


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
        result = client.uploadProjectResources(snap, snap['payload'], project.parent, repairConfirmed=True)
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
