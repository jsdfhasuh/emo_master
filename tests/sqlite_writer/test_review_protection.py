"""All Runtime entry points reject internal targets and their file aliases."""
from copy import deepcopy
import json
import multiprocessing
from pathlib import Path
import sqlite3
import tempfile
import traceback

import grpc
import pytest


def protectionProbe(root, mode, sender):
    from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb, runtime_pb2_grpc as rpc
    from emo_master.apps.runtime.grpc_server.service import RuntimeService
    from emo_master.apps.runtime.main import createRuntimeServer
    from emo_master.core.contracts.sqlite_writer import SqliteWriterError
    from emo_master.core.project.migration import migrateProjectPayload
    from emo_master.core.project.models import ProjectDocument
    from tests.runtime.runtime_test_utils import waitForTerminal
    from tests.sqlite_writer.test_backend import makeDb
    from tests.sqlite_writer.test_dependencies import project

    root = Path(root)
    state = root / 'control-state.sqlite3'
    runtime = RuntimeService(dbPath=state, workspaceRoot=root / 'worker-space')
    server = channel = None
    try:
        server, port, _ = createRuntimeServer(port=0, runtimeService=runtime)
        server.start()
        channel = grpc.insecure_channel(f'127.0.0.1:{port}')
        grpc.channel_ready_future(channel).result(timeout=5)
        stub = rpc.RuntimeServiceStub(channel)
        alias = root / 'state-alias.sqlite3'
        alias.hardlink_to(state)
        mappings = [{'column': key, 'storageType': kind, 'missing': 'error',
                     'source': {'kind': 'constant', 'value': value}}
                    for key, kind, value in [('projectId', 'TEXT', 'review'), ('name', 'TEXT', 'probe'),
                                             ('value', 'INTEGER', 123), ('updatedAtMs', 'INTEGER', 1)]]
        payload = project(rows=mappings)
        payload['workflows']['main']['inputs'] = {}
        payload['workflows']['main']['nodes'][0]['params'].update(databasePath=str(state), table='globalCounters')
        options = {'mode': mode, **({'releaseRevision': 'temporary-test'} if mode == 'release' else {})}
        blocked = []
        for path in (state, alias):
            candidate = deepcopy(payload)
            candidate['workflows']['main']['nodes'][0]['params']['databasePath'] = str(path)
            (root / 'project.json').write_text(json.dumps(candidate), encoding='utf-8')
            assert stub.LoadProject(pb.LoadProjectRequest(project_path=str(root)), timeout=3).ok
            inspected = stub.InspectSqliteTarget(pb.InspectSqliteTargetRequest(database_path=str(path),
                project_directory=str(root), table='globalCounters'), timeout=3)
            assert not inspected.ok and inspected.code == 'E_SQLITE_TARGET'
            assert not stub.StartJob(pb.StartJobRequest(project_id='sqlite-test'), timeout=3).ok
            document = ProjectDocument.model_validate(migrateProjectPayload(candidate, enablePresentation=True))
            with pytest.raises(SqliteWriterError) as rejected:
                runtime._presentationOwner.prepare(document, root, **options)
            assert rejected.value.code == 'E_SQLITE_TARGET'
            blocked.append(path.name)

        # Arbitrary names underneath Runtime-owned directories cannot bypass the
        # checks by avoiding conventional "jobs" or "preview-cache" path parts.
        business = makeDb(root / 'business.sqlite3')
        simple = project()
        simple['workflows']['main']['inputs'] = {}
        params = simple['workflows']['main']['nodes'][0]['params']
        params['mappings'][0]['source'] = {'kind': 'constant', 'value': 'actual'}
        for parent in (runtime.workspaceRoot, runtime.previewAssetStore.root, runtime._presentationOwner.assets.root):
            parent.mkdir(parents=True, exist_ok=True)
            internal = parent / 'arbitrary-name.sqlite3'
            with sqlite3.connect(internal) as connection:
                connection.execute('CREATE TABLE records(value TEXT)')
            params['databasePath'] = str(internal)
            document = ProjectDocument.model_validate(migrateProjectPayload(simple, enablePresentation=True))
            with pytest.raises(SqliteWriterError) as rejected:
                runtime._presentationOwner.prepare(document, root, **options)
            assert rejected.value.code == 'E_SQLITE_TARGET'
            blocked.append(parent.name + '/' + internal.name)
        # Debug templates use the same protection list as business targets.
        if mode == 'debug':
            params.update(databasePath=str(business), debugDatabasePath=str(alias), table='globalCounters', mappings=mappings)
            with sqlite3.connect(business) as connection:
                connection.execute('CREATE TABLE globalCounters (projectId TEXT NOT NULL,name TEXT NOT NULL,value INTEGER NOT NULL,updatedAtMs INTEGER NOT NULL,PRIMARY KEY(projectId,name))')
            document = ProjectDocument.model_validate(migrateProjectPayload(simple, enablePresentation=True))
            with pytest.raises(SqliteWriterError) as rejected:
                runtime._presentationOwner.prepare(document, root, **options)
            assert rejected.value.code == 'E_SQLITE_TARGET'
            blocked.append('internal-template')
            params.pop('debugDatabasePath')
        assert not runtime._presentationOwner.prepared
        assert not runtime.jobRepository.all()
        with sqlite3.connect(state) as connection:
            assert connection.execute("SELECT count(*) FROM globalCounters WHERE projectId='review'").fetchone()[0] == 0
        # Valid business databases still use real debug/release execution.
        params.update(databasePath=str(business), table='records', mappings=[{
            'column': 'value', 'storageType': 'JSON', 'source': {'kind': 'constant', 'value': mode}}])
        with sqlite3.connect(business) as connection:
            connection.execute("INSERT INTO records(write_id,value) VALUES('seed','\"release-seed\"')")
        document = ProjectDocument.model_validate(migrateProjectPayload(simple, enablePresentation=True))
        prepared = runtime._presentationOwner.prepare(document, root, **options)
        frozen = ProjectDocument.model_validate_json(prepared.projectPath.read_text(encoding='utf-8'))
        target = Path(frozen.workflows['main'].nodes[0].params['databasePath'])
        assert target.samefile(business) == (mode == 'release')
        job = runtime._presentationOwner.start(prepared.snapshot.snapshotId)
        assert waitForTerminal(runtime, job, timeoutSeconds=12).status == 'COMPLETED'
        with sqlite3.connect(target) as connection:
            assert connection.execute('SELECT value FROM records ORDER BY id').fetchall() == ([('\"release-seed\"',), ('\"release\"',)] if mode == 'release' else [('\"debug\"',)])
        with sqlite3.connect(business) as connection:
            assert connection.execute('SELECT count(*) FROM records').fetchone()[0] == (2 if mode == 'release' else 1)
        sender.send({'mode': mode, 'blocked': blocked, 'validJob': job})
    except BaseException:
        sender.send({'error': traceback.format_exc()})
        raise
    finally:
        if channel is not None:
            channel.close()
        if server is not None:
            server.stop(0).wait()
        runtime.close()
        assert not multiprocessing.active_children()
        sender.close()


@pytest.mark.parametrize('mode', ['debug', 'release'])
def testPreparedTargetsShareManagementAndStartJobProtectionWithRealSpawn(mode):
    with tempfile.TemporaryDirectory(prefix='esq-') as root:
        context = multiprocessing.get_context('spawn')
        receive, send = context.Pipe(duplex=False)
        process = context.Process(target=protectionProbe, args=(root, mode, send))
        process.start()
        send.close()
        try:
            assert receive.poll(25), 'outer watchdog; unrelated errors are not protected-target rejection'
            result = receive.recv()
            process.join(5)
            assert 'error' not in result, result.get('error')
            assert not process.is_alive() and process.exitcode == 0
            assert len(result['blocked']) == (6 if mode == 'debug' else 5)
        finally:
            if process.is_alive():
                process.terminate()
                process.join(3)
            if process.is_alive():
                process.kill()
                process.join(3)
            receive.close()
            process.close()
