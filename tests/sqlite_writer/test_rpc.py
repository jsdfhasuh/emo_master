import json
from pathlib import Path
import sqlite3
import threading
import time

import grpc
import pytest

from emo_master.apps.runtime.grpc_server.service import RuntimeService
from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb, runtime_pb2_grpc as rpc
from emo_master.apps.runtime.main import createRuntimeServer
from emo_master.apps.designer.services.runtime_client import RuntimeClient, RuntimeClientError
from emo_master.core.project.migration import migrateProjectPayload
from emo_master.core.project.models import ProjectDocument
from tests.runtime.runtime_test_utils import waitForTerminal
from tests.sqlite_writer.test_dependencies import project
from tests.sqlite_writer.test_backend import makeDb


@pytest.fixture
def network(tmp_path):
    service = RuntimeService(dbPath=tmp_path / 'runtime.db', workspaceRoot=tmp_path / 'jobs')
    server, port, _ = createRuntimeServer(port=0, runtimeService=service)
    server.start()
    channel = grpc.insecure_channel(f'127.0.0.1:{port}')
    grpc.channel_ready_future(channel).result(timeout=5)
    client = RuntimeClient(rpc.RuntimeServiceStub(channel))
    try:
        yield service, server, client, rpc.RuntimeServiceStub(channel)
    finally:
        channel.close()
        server.stop(0).wait()
        service.close()


def testTypedReadonlyInitializeNormalSpawnTwoRunsAndPreflight(network, tmp_path):
    service, _, client, _ = network
    root = tmp_path / '工程 中文 空格'
    root.mkdir()
    path = root / '业务.sqlite3'
    columns = [{'name': 'value', 'storageType': 'JSON', 'nullable': True}]
    preview = client.sqliteTarget('inspect', str(path), str(root), 'records', columns)
    assert not preview.exists and preview.preview_sql.startswith('CREATE TABLE') and not path.exists()
    assert preview.runtime_host and preview.resolved_path == str(path)
    with pytest.raises(RuntimeClientError, match='明确确认'):
        client.sqliteTarget('initialize', str(path), str(root), 'records', columns)
    initialized = client.sqliteTarget('initialize', str(path), str(root), 'records', columns, confirmed=True)
    assert initialized.exists and len(initialized.columns) == 3
    payload = project()
    payload['workflows']['main']['nodes'][0]['params']['databasePath'] = path.name
    (root / 'project.json').write_text(json.dumps(payload), encoding='utf-8')
    assert client.loadProject(str(root)).ok
    identities = []
    for value in ['A 实际数据', 'B 不同数据']:
        started = client.startJob('sqlite-test', inputs={'value': value})
        assert started.ok, started.message
        assert waitForTerminal(service, started.job_id, timeoutSeconds=15).status == 'COMPLETED'
        done = next(e for e in service.eventStore.readMerged(started.job_id) if e.eventType == 'sqlite.write.finished')
        receipt = json.loads(done.payloadJson)['receipt']
        assert receipt['status'] == 'COMMITTED'
        identities.append(receipt['execution']['workflowRunId'])
    assert identities[0] != identities[1]
    with sqlite3.connect(path) as connection:
        assert connection.execute('SELECT value FROM records ORDER BY id').fetchall() == [('"A 实际数据"',), ('"B 不同数据"',)]
        connection.execute('ALTER TABLE records DROP COLUMN value')
    rejected = client.startJob('sqlite-test', inputs={'value': 'C'})
    assert not rejected.ok and rejected.status == 'REJECTED' and '列不存在' in rejected.message


def testDraftPrepareUsesActualDebugNamespaceNoBusinessData(network, tmp_path):
    service, _, _, _ = network
    root = tmp_path / 'project'
    root.mkdir()
    actual = makeDb(root / 'actual.sqlite3')
    with sqlite3.connect(actual) as connection:
        connection.execute('INSERT INTO records (write_id,value) VALUES (?,?)', ('production', '"release"'))
    before = actual.read_bytes()
    document = ProjectDocument.model_validate(migrateProjectPayload(project(), enablePresentation=True))
    document.workflows['main'].nodes[0].params['databasePath'] = 'actual.sqlite3'
    document.workflows['main'].inputs = {}
    document.workflows['main'].nodes[0].params['mappings'][0]['source'] = {'kind': 'constant', 'value': 'actual debug run'}
    prepared = service._presentationOwner.prepare(document, root)
    frozen = ProjectDocument.model_validate_json(prepared.projectPath.read_text(encoding='utf-8'))
    target = Path(frozen.workflows['main'].nodes[0].params['databasePath'])
    assert target != actual and 'debug' in target.parts and 'business-sqlite' in target.parts
    assert actual.read_bytes() == before
    with sqlite3.connect(target) as connection:
        assert connection.execute('SELECT COUNT(*) FROM records').fetchone()[0] == 0
    job = service._presentationOwner.start(prepared.snapshot.snapshotId)
    assert waitForTerminal(service, job, timeoutSeconds=15).status == 'COMPLETED'
    with sqlite3.connect(target) as connection:
        assert connection.execute('SELECT value FROM records').fetchall() == [('"actual debug run"',)]
    assert actual.read_bytes() == before


def testAioCancelledManagementKeepsQuotaUntilRealWorkExits(network, tmp_path, monkeypatch):
    service, server, _, stub = network
    entered = threading.Barrier(3)
    release = threading.Event()
    import emo_master.apps.runtime.business_sqlite.rpc as implementation
    original = implementation.inspect
    def blocked(*args):
        entered.wait(3)
        release.wait(4)
        return original(*args)
    monkeypatch.setattr(implementation, 'inspect', blocked)
    request = pb.InspectSqliteTargetRequest(database_path=str(tmp_path / 'unused.sqlite3'), project_directory=str(tmp_path))
    calls = [stub.InspectSqliteTarget.future(request, timeout=5) for _ in range(2)]
    try:
        entered.wait(3)
        calls[0].cancel()
        time.sleep(.05)
        assert service.sqliteManagement.active == 2
        assert server.transport.active['sqlite'] == 2
        with pytest.raises(grpc.RpcError) as error:
            stub.InspectSqliteTarget(request, timeout=1)
        assert error.value.code() == grpc.StatusCode.RESOURCE_EXHAUSTED
        # A blocked management category cannot block ordinary control responses.
        assert stub.ListOperators(pb.ListOperatorsRequest(), timeout=1).operators
    finally:
        release.set()
        for call in calls:
            try:
                call.result(timeout=5)
            except grpc.FutureCancelledError:
                pass
    deadline = time.monotonic() + 3
    while service.sqliteManagement.active and time.monotonic() < deadline:
        time.sleep(.01)
    assert service.sqliteManagement.active == 0
    assert service.sqliteManagement.peak == 2


def testOldRuntimeCapabilityIsExplicit():
    client = RuntimeClient(object())
    with pytest.raises(RuntimeClientError) as error:
        client.sqliteTarget('inspect', 'path', 'root')
    assert error.value.code == 'E_SQLITE_UNSUPPORTED'
