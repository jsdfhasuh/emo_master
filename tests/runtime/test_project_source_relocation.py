"""Relocating an existing online project preserves relative paths and business data."""
from contextlib import closing
from copy import deepcopy
import json
import os
from pathlib import Path
import shutil
import sqlite3
from threading import Event

import pytest

from emo_master.apps.designer.services.runtime_client import RuntimeClient, RuntimeClientError
from emo_master.apps.designer.state.project_store import saveProject
from emo_master.apps.runtime.business_sqlite.backend import initialize
from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb
from emo_master.apps.runtime.grpc_server.service import RuntimeService
from emo_master.core.project.configuration import projectConfigurationChanges
from emo_master.core.project.models import ProjectDocument
from tests.runtime.current_production_fixture import currentProductionProject, imageBytes, savedPixels
from tests.runtime.test_initial_project_configuration import waitForWrite
from tests.runtime.runtime_test_utils import waitForTerminal
from tests.runtime.test_device_production_outcomes import waitFor

DB = 'outputs/records.sqlite3'


def relocatedProjects(tmp_path):
    first, document = currentProductionProject(tmp_path / '旧工程')
    payload = document.model_dump(mode='json')
    payload['workflows']['main']['nodes'].insert(-1, dict(nodeId='record', operatorId='vision.io.sqlite_writer', params=dict(
        configVersion=1, databasePath=DB, table='records', failurePolicy='stop', mappings=[dict(
            column='image_path', storageType='FILE_REFERENCE', missing='error',
            source=dict(kind='node_output', nodeId='save', port='result'))])))
    payload['dependencies']['operators'].append(dict(operatorId='vision.io.sqlite_writer', version='1.0.0'))
    document = ProjectDocument.model_validate(payload)
    initialize(first.parent / DB, 'records', [dict(name='image_path', storageType='FILE_REFERENCE', nullable=False)])
    saveProject(first, payload)
    second = tmp_path / '迁移后 中文 空格' / first.name
    shutil.copytree(first.parent, second.parent)
    (second.parent / 'input.png').write_bytes(imageBytes(193))
    return first, second, document


def loadOnline(client, service, path, document):
    client.initializeProjectConfiguration(document.model_dump(mode='json'), path.parent, 'initial',
        runtimeInstanceId=service.runtimeInstanceId, projectPath=path)
    assert waitForWrite(service, client, 'initial')['state'] == 'COMMITTED'
    return client.getOnlineProjectSnapshot()


def runImage(client, service, projectId):
    reply = client.startJob(projectId, 'main')
    assert reply.ok, reply.message
    record = waitForTerminal(service, reply.job_id, timeoutSeconds=30)
    assert record.status == 'COMPLETED', record
    service.jobSupervisor.waitForRetirement(timeoutSeconds=10)
    assert not service.jobSupervisor.ownsJobResources(reply.job_id)


@pytest.mark.parametrize('changeOutput', [False, True])
def testRelocationCommitRunsNewImagesAndDatabaseAndSurvivesRestart(tmp_path, changeOutput):
    first, second, document = relocatedProjects(tmp_path)
    service = RuntimeService(dbPath=tmp_path / 'runtime/runtime.db', workspaceRoot=tmp_path / 'runtime/jobs')
    client = RuntimeClient(service)
    try:
        snapshot = loadOnline(client, service, first, document)
        runImage(client, service, document.project.projectId)
        oldDatabase = (first.parent / DB).read_bytes()
        oldImage = (first.parent / 'outputs/result.png').read_bytes()
        payload = document.model_dump(mode='json')
        output = 'outputs/moved.png' if changeOutput else 'outputs/result.png'
        if changeOutput:
            next(n for n in payload['workflows']['main']['nodes'] if n['nodeId'] == 'save')['params']['outputPath'] = output
        paths = [c['path'] for c in projectConfigurationChanges(snapshot['payload'], payload)]
        preview = client.sqliteTarget('inspect', DB, str(second.parent), 'records')
        assert preview.exists and Path(preview.resolved_path) == second.parent / DB
        checked = client.writeProjectConfiguration(snapshot, payload, paths, 'check', validate=True, localProjectPath=second)
        assert checked['sourceChanged'] and not checked['cycleSafe']
        assert service.loadedProjectFile == str(first) and client.getOnlineProjectSnapshot() == snapshot
        client.writeProjectConfiguration(snapshot, payload, paths, 'move', localProjectPath=second)
        receipt = waitForWrite(service, client, 'move')
        assert receipt['state'] == 'COMMITTED', receipt
        assert receipt['executionRevision'] == snapshot['executionRevision'] + 1
        assert service.loadedProjectFile == str(second)
        assert client.getOnlineProjectSnapshot()['payload'] == payload
        runImage(client, service, document.project.projectId)
        assert (savedPixels(second.parent / output) == 193).all()
        with closing(sqlite3.connect(second.parent / DB)) as connection:
            assert connection.execute('SELECT image_path FROM records').fetchall() == [(str(second.parent / output),)]
        assert (first.parent / DB).read_bytes() == oldDatabase
        assert (first.parent / 'outputs/result.png').read_bytes() == oldImage
        assert client.writeProjectConfiguration(snapshot, payload, paths, 'move', localProjectPath=second)['state'] == 'COMMITTED'
    finally:
        client.close()
        service.close()
    restored = RuntimeService(dbPath=tmp_path / 'runtime/runtime.db', workspaceRoot=tmp_path / 'runtime/jobs')
    try:
        assert restored.loadedProjectFile == str(second) and not restored.onlineRecoveryError
        assert restored.projectConfiguration.current['executionRevision'] == receipt['executionRevision']
        assert restored.projectConfiguration.current['payload'] == payload
    finally:
        restored.close()


def testMissingNewDatabaseRetainsOldOnlineProjectAndReportsActualTarget(tmp_path):
    first, second, document = relocatedProjects(tmp_path)
    (second.parent / DB).unlink()
    service = RuntimeService(dbPath=tmp_path / 'runtime/runtime.db', workspaceRoot=tmp_path / 'runtime/jobs')
    client = RuntimeClient(service)
    try:
        snapshot = loadOnline(client, service, first, document)
        with pytest.raises(RuntimeClientError) as failed:
            client.writeProjectConfiguration(snapshot, snapshot['payload'], [], 'check', validate=True, localProjectPath=second)
        assert failed.value.code == 'E_SQLITE_TARGET'
        assert str(second.parent / DB) in str(failed.value) and 'record' in str(failed.value)
        client.writeProjectConfiguration(snapshot, snapshot['payload'], [], 'move', localProjectPath=second)
        assert waitForWrite(service, client, 'move')['state'] == 'FAILED'
        assert client.getOnlineProjectSnapshot() == snapshot and service.loadedProjectFile == str(first)
        assert not (second.parent / DB).exists()
        runImage(client, service, document.project.projectId)
    finally:
        client.close()
        service.close()


def testCancelAndUnselectedDraftEditsCannotChangeTheOnlineSource(tmp_path, monkeypatch):
    first, second, document = relocatedProjects(tmp_path)
    service = RuntimeService(dbPath=tmp_path / 'runtime/runtime.db', workspaceRoot=tmp_path / 'runtime/jobs')
    client = RuntimeClient(service)
    release, entered = Event(), Event()
    try:
        snapshot = loadOnline(client, service, first, document)
        draft = deepcopy(snapshot['payload'])
        draft['workflows']['main']['name'] = 'Unselected draft change'
        assert client.writeProjectConfiguration(snapshot, draft, [], 'check', validate=True,
            localProjectPath=second)['candidate'] == snapshot['payload']
        original = service.projectConfiguration.prepare
        def waitForCancel(payload, cancelled, **options):
            entered.set()
            assert release.wait(10)
            return original(payload, cancelled, **options)
        monkeypatch.setattr(service.projectConfiguration, 'prepare', waitForCancel)
        client.writeProjectConfiguration(snapshot, draft, [], 'move', localProjectPath=second)
        assert entered.wait(10)
        client.cancelProjectWrite('move')
        release.set()
        assert waitForWrite(service, client, 'move')['state'] == 'CANCELLED'
        assert client.getOnlineProjectSnapshot() == snapshot
    finally:
        release.set()
        client.close()
        service.close()


def testRemoteAndOlderRuntimeCannotSilentlyIgnoreSourceChange(tmp_path):
    first, second, document = relocatedProjects(tmp_path)
    service = RuntimeService(dbPath=tmp_path / 'runtime/runtime.db', workspaceRoot=tmp_path / 'runtime/jobs')
    client = RuntimeClient(service)
    try:
        snapshot = loadOnline(client, service, first, document)
        with pytest.raises(RuntimeClientError, match='升级'):
            client.writeProjectConfiguration({**snapshot, 'projectSourceWrite': 0}, snapshot['payload'], [], 'old', localProjectPath=second)
        request = pb.ProjectConfigurationWriteRequest(request_id='remote',
            expected_runtime_instance_id=service.runtimeInstanceId, expected_revision=snapshot['documentRevision'],
            local_payload_json=json.dumps(snapshot['payload']), local_project_path=str(second))
        reply = service.WriteProjectConfiguration(request, object())
        assert not reply.ok and reply.code == 'E_CONFIG_SOURCE_REMOTE'
        remote = json.loads(service.GetOnlineProjectSnapshot(pb.OnlineProjectRequest(), object()).result_json)
        assert remote['projectSourceWrite'] == 0
        assert client.getOnlineProjectSnapshot() == snapshot
        # Ordinary remote field writes retain the execution-side source path.
        draft = deepcopy(snapshot['payload'])
        draft['workflows']['main']['name'] = 'Remote display name'
        request.ClearField('local_project_path')
        request.local_payload_json = json.dumps(draft)
        request.selected_paths.append('/workflows/main/name')
        assert service.WriteProjectConfiguration(request, object()).ok
        assert waitForWrite(service, client, 'remote')['state'] == 'COMMITTED'
        assert client.getOnlineProjectSnapshot()['payload'] == draft
        assert service.loadedProjectFile == str(first)
    finally:
        client.close()
        service.close()


@pytest.mark.parametrize('policy', ['stopAffected', 'drainAffected'])
def testSourceOnlyChangeRetiresActiveJobWithoutDevices(tmp_path, policy):
    first, second, document = relocatedProjects(tmp_path)
    document.production.mode = 'continuous'
    document.production.cycleIntervalMs = 50
    service = RuntimeService(dbPath=tmp_path / 'runtime/runtime.db', workspaceRoot=tmp_path / 'runtime/jobs')
    client = RuntimeClient(service)
    try:
        snapshot = loadOnline(client, service, first, document)
        service.productionMode = True
        started = client.startJob(document.project.projectId, 'main')
        assert started.ok, started.message
        waitFor(lambda: (first.parent / 'outputs/result.png').is_file(), timeout=15)
        assert service.jobRepository.get(started.job_id).status == 'RUNNING'
        with pytest.raises(RuntimeClientError) as rejected:
            client.writeProjectConfiguration(snapshot, snapshot['payload'], [], 'check', validate=True, localProjectPath=second)
        assert rejected.value.code == 'E_CONFIG_REQUIRES_STOP'
        client.writeProjectConfiguration(snapshot, snapshot['payload'], [], 'reject', localProjectPath=second)
        assert waitForWrite(service, client, 'reject')['state'] == 'FAILED'
        assert client.getOnlineProjectSnapshot() == snapshot
        assert service.jobRepository.get(started.job_id).status == 'RUNNING'
        preview = client.writeProjectConfiguration(snapshot, snapshot['payload'], [], 'preview', validate=True,
            localProjectPath=second, replacementPolicy=policy)
        assert preview['affectedJobs'] == [started.job_id] and not preview['cycleSafe']
        client.writeProjectConfiguration(snapshot, snapshot['payload'], [], 'move', localProjectPath=second, replacementPolicy=policy)
        receipt = waitForWrite(service, client, 'move')
        assert receipt['state'] == 'COMMITTED', receipt
        assert started.job_id in receipt['affectedJobs']
        assert service.jobRepository.get(started.job_id).isTerminal
        assert not service.jobSupervisor.ownsJobResources(started.job_id)
        assert not service.projectConfiguration.cycles and not service.projectConfiguration.childCycles
        assert service.loadedProjectFile == str(second)
        assert not (second.parent / 'outputs/result.png').exists()  # No implicit restart.
        service.productionMode = False
        runImage(client, service, document.project.projectId)
        assert (savedPixels(second.parent / 'outputs/result.png') == 193).all()
    finally:
        client.close()
        service.close()


def testCancelDuringManagedSqliteRelocationPreflight(tmp_path, monkeypatch):
    from emo_master.apps.runtime.business_sqlite import backend
    first, second, document = relocatedProjects(tmp_path)
    service = RuntimeService(dbPath=tmp_path / 'runtime/runtime.db', workspaceRoot=tmp_path / 'runtime/jobs')
    client = RuntimeClient(service)
    entered, release = Event(), Event()
    try:
        snapshot = loadOnline(client, service, first, document)
        original = backend.inspect
        def pending(path, table='', cancelled=lambda: False):
            assert service.sqliteManagement.active == 1
            entered.set()
            assert release.wait(10)
            assert cancelled(), 'configuration cancellation must reach SQLite I/O'
            return original(path, table, cancelled)
        monkeypatch.setattr(backend, 'inspect', pending)
        client.writeProjectConfiguration(snapshot, snapshot['payload'], [], 'cancel-io', localProjectPath=second)
        assert entered.wait(10)
        client.cancelProjectWrite('cancel-io')
        release.set()
        assert waitForWrite(service, client, 'cancel-io')['state'] == 'CANCELLED'
        assert service.sqliteManagement.active == 0
        assert client.getOnlineProjectSnapshot() == snapshot
        monkeypatch.setattr(backend, 'inspect', original)
        runImage(client, service, document.project.projectId)
    finally:
        release.set()
        client.close()
        service.close()


def testEquivalentSourceSpellingDoesNotAdvanceExecutionRevision(tmp_path):
    from emo_master.apps.runtime.context.sqlite_store import SqliteStore
    from emo_master.apps.runtime.project_config.store import ConfigurationStore
    first, document = currentProductionProject(tmp_path / 'project')
    store = ConfigurationStore(SqliteStore(tmp_path / 'runtime.db'))
    payload = document.model_dump(mode='json')
    store.initialize(payload, str(first))
    equivalent = str(first.parent / 'unused' / '..' / first.name)
    if os.name == 'nt':
        equivalent = equivalent.upper().replace('\\', '/')
    receipt = store.commit(1, payload, 'same-source', 'same', 'instance', sourcePath=equivalent)
    assert receipt['documentRevision'] == 2 and receipt['executionRevision'] == 1
