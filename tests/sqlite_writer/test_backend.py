from copy import deepcopy
import json
from pathlib import Path
import sqlite3
import threading
import time
import zipfile
from types import SimpleNamespace

import pytest

from emo_master.apps.runtime.business_sqlite.backend import (
    inspect, initialize, insert, resolveTarget, freezeTargets, validateMappings, SqliteManagement)
from emo_master.apps.runtime.workflow.runner import WorkflowRunner, WorkflowExecutionError
from emo_master.apps.runtime.workflow.context import RunContext
from emo_master.apps.runtime.workflow.cancellation import CancellationToken, CancellationRequested
from emo_master.core.workflow.compiler import WorkflowCompiler
from emo_master.core.project.models import ProjectDocument
from emo_master.core.project.package_builder import buildPackage
from emo_master.core.contracts.sqlite_writer import OPERATOR_ID, SqliteWriterError, toStorage
from emo_master.plugins.builtins.sqlite_writer.operator import SqliteWriterOperator
from tests.sqlite_writer.test_dependencies import config, mapping, project


def makeDb(path, *, columns=None, table="records"):
    initialize(path, table, columns or [{"name": "value", "storageType": "JSON", "nullable": True}])
    return path


def actualRun(path, value, *, rows=None, policy="stop", registry=None, payload=None, token=None):
    payload = deepcopy(payload or project(rows))
    writer = next(n for n in payload["workflows"]["main"]["nodes"] if n.get("operatorId") == OPERATOR_ID)
    writer["params"] = config(path, rows=rows or writer["params"]["mappings"], failure=policy)
    operators = {OPERATOR_ID: SqliteWriterOperator, **(registry or {})}
    compiled = WorkflowCompiler(operators).compile(payload)
    events = []
    runner = WorkflowRunner(compiled, operators, eventPublisher=lambda **e: events.append(e))
    result = runner.run("main", {"value": value}, RunContext.root("job", "main"), token or CancellationToken())
    return next(e["payload"]["outputs"]["receipt"] for e in events if e["eventType"] == "node.completed" and e["context"].callerNodeId == "writer"), result, events


def testReadonlyMissingAndExplicitCreationPreserveOtherTable(tmp_path):
    path = tmp_path / "中文 空格.sqlite3"
    before = inspect(path)
    assert not before["exists"] and not path.exists()
    makeDb(path)
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE other (v TEXT)")
        connection.execute("INSERT INTO other VALUES ('keep')")
    makeDb(path)
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT v FROM other").fetchall() == [('keep',)]
    with pytest.raises(SqliteWriterError, match="不一致"):
        initialize(path, "records", [{"name": "different", "storageType": "TEXT"}])


@pytest.mark.parametrize("value", [0, False, "", None, {"items": [0, False, None, ""]}])
def testRealRunnerSingleRecordFullJsonFalsyAndReceipt(tmp_path, value):
    path = makeDb(tmp_path / "business.sqlite3")
    receipt, _, events = actualRun(path, value)
    assert receipt["status"] == "COMMITTED" and receipt["rowsAffected"] == 1 and receipt["primaryKey"] == 1
    assert receipt["execution"]["workflowRunId"] and receipt["execution"]["nodeRunId"]
    with sqlite3.connect(path) as connection:
        stored, writeId = connection.execute("SELECT value, write_id FROM records").fetchone()
        assert stored is None if value is None else json.loads(stored) == value
        assert writeId == receipt["writeId"]
    assert [e['eventType'] for e in events].count('sqlite.write.started') == 1


def testWriteIdMappingCannotDivergeFromCommittedReceipt(tmp_path):
    from emo_master.core.contracts.sqlite_writer import parseConfig
    path = makeDb(tmp_path / 'business.sqlite3')
    identity = {'column': 'WRITE_ID', 'source': {'kind': 'context', 'key': 'writeId'},
                'storageType': 'TEXT', 'missing': 'error'}
    rows = [mapping(), identity]
    parseConfig(config(path, rows))
    receipt, _, _ = actualRun(path, 7, rows=rows)
    with sqlite3.connect(path) as connection:
        assert connection.execute('SELECT write_id FROM records').fetchone()[0] == receipt['writeId']
    with pytest.raises(SqliteWriterError, match='write_id 为写入身份'):
        parseConfig(config(path, [mapping(), {**identity, 'source': {'kind': 'constant', 'value': 'wrong'}}]))
    with pytest.raises(SqliteWriterError, match='write_id 必须与本次回执'):
        insert(path, config(path), {'value': 'no write', 'write_id': 'wrong'}, 'expected')
    with sqlite3.connect(path) as connection:
        assert connection.execute('SELECT count(*) FROM records').fetchone()[0] == 1


class MissingSource:
    meta = SimpleNamespace(inputPorts={}, outputPorts={"value": {"type": "string", "required": False, "nullable": True}})
    def executeNode(self, inputs, params, context):
        return {"status": "ok", "outputs": {}}


def testMissingNullAndOmissionDefaultAreDifferent(tmp_path):
    path = makeDb(tmp_path / "business.sqlite3", columns=[{"name": "value", "storageType": "TEXT", "nullable": True, "default": "db default"}])
    payload = project()
    payload['workflows']['main']['nodes'].append({'nodeId': 'optional', 'operatorId': 'test.optional',
        'outputPorts': {'value': 'string'}})
    for missing in ('default', 'null'):
        row = mapping(node='optional', storage='TEXT', missing=missing)
        receipt, _, _ = actualRun(path, 'ignored', rows=[row], payload=payload, registry={'test.optional': MissingSource})
        assert receipt['status'] == 'COMMITTED'
    with sqlite3.connect(path) as connection:
        assert connection.execute('SELECT value FROM records ORDER BY id').fetchall() == [('db default',), (None,)]
    with pytest.raises(WorkflowExecutionError, match='来源被跳过'):
        actualRun(path, 1, rows=[mapping(node='optional', storage='TEXT')], payload=payload, registry={'test.optional': MissingSource})


def testSchemaRequiredGeneratedAffinityAndTransactionRecheck(tmp_path):
    path = tmp_path / 'business.sqlite3'
    with sqlite3.connect(path) as connection:
        connection.execute('CREATE TABLE records (value TEXT, optional INTEGER, derived TEXT GENERATED ALWAYS AS (value))')
    spec = config(path)
    validateMappings(spec, inspect(path, 'records')['structure'])
    insert(path, spec, {'value': 'A'}, 'a')
    with sqlite3.connect(path) as connection:
        connection.execute("ALTER TABLE records ADD COLUMN mandatory TEXT NOT NULL DEFAULT 'default'")
    insert(path, spec, {'value': 'B'}, 'b')
    with sqlite3.connect(path) as connection:
        connection.execute('ALTER TABLE records DROP COLUMN mandatory')
        connection.execute('DELETE FROM records')
        connection.execute('ALTER TABLE records ADD COLUMN mandatory TEXT NOT NULL')
    with pytest.raises(SqliteWriterError, match='未映射必填列'):
        insert(path, spec, {'value': 'C'}, 'c')
    bad = config(path, rows=[mapping(column='derived')])
    with pytest.raises(SqliteWriterError, match='生成列'):
        validateMappings(bad, inspect(path, 'records')['structure'])
    bad = config(path, rows=[mapping(storage='INTEGER')])
    with pytest.raises(SqliteWriterError, match='亲和性'):
        validateMappings(bad, inspect(path, 'records')['structure'])
    with sqlite3.connect(path) as connection:
        connection.execute('ALTER TABLE records DROP COLUMN derived')
        connection.execute('ALTER TABLE records DROP COLUMN value')
    with pytest.raises((SqliteWriterError, sqlite3.OperationalError)):
        insert(path, spec, {'value': 'D'}, 'd')


def testConstraintFailureStopContinueAndNoRetry(tmp_path):
    path = makeDb(tmp_path / 'business.sqlite3', columns=[{'name': 'value', 'storageType': 'JSON', 'nullable': True}])
    with sqlite3.connect(path) as connection:
        connection.execute('CREATE UNIQUE INDEX value_unique ON records(value)')
    actualRun(path, 'A')
    with pytest.raises(WorkflowExecutionError) as error:
        actualRun(path, 'A')
    assert error.value.diagnostics['sqliteReceipt']['status'] == 'FAILED'
    receipt, _, events = actualRun(path, 'A', policy='continue')
    assert receipt['status'] == 'FAILED' and receipt['error']['code'] == 'E_SQLITE_CONSTRAINT'
    assert any(e['eventType'] == 'node.log' and e['level'] == 'ERROR' for e in events)
    with sqlite3.connect(path) as connection:
        assert connection.execute('SELECT COUNT(*) FROM records').fetchone()[0] == 1


def testCommittedButLostAcknowledgementIsUnknownAndNeverRetried(tmp_path, monkeypatch):
    import emo_master.apps.runtime.business_sqlite.backend as backend
    path = makeDb(tmp_path / 'business.sqlite3')
    original = backend.sqlite3.connect
    class LostAcknowledgement:
        def __init__(self, connection):
            self.connection = connection
        def __getattr__(self, name):
            return getattr(self.connection, name)
        def commit(self):
            self.connection.commit()
            raise OSError('injected lost commit acknowledgement')
    with monkeypatch.context() as injection:
        injection.setattr(backend.sqlite3, 'connect', lambda *a, **kw: LostAcknowledgement(original(*a, **kw)))
        receipt, _, _ = actualRun(path, 'committed once', policy='continue')
    assert receipt['status'] == 'UNKNOWN' and receipt['rowsAffected'] is None
    with sqlite3.connect(path) as connection:
        assert connection.execute('SELECT COUNT(*) FROM records').fetchone()[0] == 1


def testQuotedNamesAndBooleanUtcFileReference(tmp_path):
    path = tmp_path / 'business.sqlite3'
    table = '表 "quoted'
    initialize(path, table, [{'name': '列 "quoted', 'storageType': 'BOOLEAN'}])
    spec = config(path, rows=[{'column': '列 "quoted', 'storageType': 'BOOLEAN', 'source': {'kind': 'constant', 'value': False}}])
    spec['table'] = table
    insert(path, spec, {'列 "quoted': 0}, 'id')
    assert toStorage('2026-10-05T08:00:00+08:00', 'UTC_TIME') == '2026-10-05T00:00:00Z'
    persistent = tmp_path / 'saved.png'
    persistent.write_bytes(b'persisted')
    assert toStorage({'saved': True, 'path': str(persistent)}, 'FILE_REFERENCE') == str(persistent)
    with pytest.raises(SqliteWriterError):
        toStorage({'saved': False, 'path': str(persistent)}, 'FILE_REFERENCE')


@pytest.mark.parametrize('raw', [':memory:', 'file:test.db?mode=rw', '//server/share/db', '\\\\server\\share\\db'])
def testUnsupportedTargets(raw, tmp_path):
    with pytest.raises(SqliteWriterError):
        resolveTarget(raw, tmp_path)


@pytest.mark.parametrize('platform,driveType', [('linux', None), ('darwin', None), ('win32', 3), ('win32', 4)])
def testDriveCheckIsWindowsOnlyAndStillRejectsMappedDrives(tmp_path, monkeypatch, platform, driveType):
    from emo_master.apps.runtime.business_sqlite import backend
    calls = []
    def getDriveType(anchor):
        calls.append(anchor)
        assert platform == 'win32', 'Non-Windows targets must not access ctypes.windll'
        return driveType
    monkeypatch.setattr(backend, 'sys', SimpleNamespace(platform=platform), raising=False)
    monkeypatch.setattr(backend, 'ctypes', SimpleNamespace(
        windll=SimpleNamespace(kernel32=SimpleNamespace(GetDriveTypeW=getDriveType))))
    path = (tmp_path / '业务 数据.sqlite3').resolve()
    if driveType == 4:
        with pytest.raises(SqliteWriterError, match='映射网络盘'):
            resolveTarget(str(path), tmp_path)
    else:
        assert resolveTarget(str(path), tmp_path) == path
    assert calls == ([path.anchor] if platform == 'win32' else [])


def testOriginalRootFreezeAndDebugDoesNotChangeActualDatabase(tmp_path, monkeypatch):
    original = tmp_path / '工程 空格'
    original.mkdir()
    path = makeDb(original / 'actual.sqlite3')
    actualRun(path, 'release')
    before = path.read_bytes()
    payload = ProjectDocument.model_validate(project())
    payload.workflows['main'].nodes[0].params['databasePath'] = 'actual.sqlite3'
    monkeypatch.chdir(tmp_path)
    release = freezeTargets(payload, original)
    assert release.workflows['main'].nodes[0].params['databasePath'] == str(path)
    isolated = freezeTargets(payload, original, debugRoot=tmp_path / 'debug' / 'business-sqlite')
    target = Path(isolated.workflows['main'].nodes[0].params['databasePath'])
    actualRun(target, 'debug')
    assert path.read_bytes() == before
    with sqlite3.connect(target) as connection:
        assert connection.execute('SELECT value FROM records').fetchall() == [('"debug"',)]
    with sqlite3.connect(path) as connection:
        connection.execute('CREATE TRIGGER keep_data AFTER INSERT ON records BEGIN SELECT 1; END')
    with pytest.raises(SqliteWriterError, match='专用测试库'):
        freezeTargets(payload, original, debugRoot=tmp_path / 'other-debug')
    template = makeDb(original / 'test-template.sqlite3')
    with sqlite3.connect(template) as connection:
        connection.execute("INSERT INTO records (write_id,value) VALUES ('test seed','test data')")
    payload.workflows['main'].nodes[0].params['debugDatabasePath'] = 'test-template.sqlite3'
    isolated = freezeTargets(payload, original, debugRoot=tmp_path / 'explicit-debug')
    with sqlite3.connect(isolated.workflows['main'].nodes[0].params['databasePath']) as connection:
        assert connection.execute('SELECT value FROM records').fetchall() == [('test data',)]
    payload.workflows['main'].nodes[0].params['debugDatabasePath'] = 'actual.sqlite3'
    with pytest.raises(SqliteWriterError):
        freezeTargets(payload, original, debugRoot=tmp_path / 'forbidden-debug')
    with pytest.raises(SqliteWriterError):
        resolveTarget(str(path), original, protected=(path,))


def testLockWaitAndCancellationDoNotLeakConnections(tmp_path):
    path = makeDb(tmp_path / 'business.sqlite3')
    locker = sqlite3.connect(path)
    locker.execute('BEGIN IMMEDIATE')
    token = CancellationToken()
    timer = threading.Timer(.1, token.cancel)
    timer.start()
    start = time.monotonic()
    try:
        with pytest.raises(CancellationRequested):
            actualRun(path, 'A', policy='continue', token=token)
        assert time.monotonic() - start < 3
    finally:
        timer.join()
        locker.rollback()
        locker.close()
    assert actualRun(path, 'B')[0]['status'] == 'COMMITTED'
    assert not any(t.name == 'sqlite-interrupt' for t in threading.enumerate())


def testManagementQuotaStaysOwnedUntilWorkActuallyExits():
    manager = SqliteManagement()
    entered = threading.Barrier(3)
    release = threading.Event()
    def operation(_cancelled):
        entered.wait(3)
        release.wait(3)
    threads = [threading.Thread(target=lambda: manager.run(operation)) for _ in range(2)]
    try:
        for thread in threads:
            thread.start()
        entered.wait(3)
        with pytest.raises(SqliteWriterError, match='额度'):
            manager.run(lambda _: None)
        with pytest.raises(RuntimeError, match='尚未释放'):
            manager.close()
        assert manager.active == 2
    finally:
        release.set()
        for thread in threads:
            thread.join(4)
        manager.close()
    assert manager.active == 0 and manager.peak == 2


def testOversizeFailsWithoutTruncationOrDatabaseWrite(tmp_path):
    path = makeDb(tmp_path / 'business.sqlite3')
    with pytest.raises(WorkflowExecutionError, match='1 MiB'):
        actualRun(path, 'X' * (1024 * 1024 + 1))
    with sqlite3.connect(path) as connection:
        assert connection.execute('SELECT COUNT(*) FROM records').fetchone()[0] == 0


def testBusinessDatabaseAndSidecarsExcludedFromLegacyPackage(tmp_path):
    root = tmp_path / 'project'
    root.mkdir()
    payload = project()
    path = root / 'business-data'
    payload['workflows']['main']['nodes'][0]['params']['databasePath'] = str(path.name)
    (root / 'project.json').write_text(json.dumps(payload), encoding='utf-8')
    makeDb(path)
    for ending in ('-wal', '-shm'):
        Path(str(path) + ending).write_bytes(b'private data')
    archive = buildPackage(root, tmp_path / 'packages')
    with zipfile.ZipFile(archive) as package:
        assert not any('business-data' in p for p in package.namelist())


def testDescendingIntegerPrimaryKeyIsNotAutomatic(tmp_path):
    path = tmp_path / 'descending.sqlite3'
    with sqlite3.connect(path) as connection:
        connection.execute('CREATE TABLE records (id INTEGER PRIMARY KEY DESC, value TEXT)')
    structure = inspect(path, 'records')['structure']
    assert not structure['columns'][0]['autoPrimaryKey']
    with pytest.raises(SqliteWriterError, match='未映射必填列：id'):
        validateMappings(config(path), structure)


def testFinalRecordBudgetIncludesGeneratedWriteId(tmp_path):
    path = makeDb(tmp_path / 'bounded.sqlite3')
    values = {'value': 'X' * (1024 * 1024 - 30)}
    assert len(json.dumps(values).encode()) < 1024 * 1024
    with pytest.raises(SqliteWriterError, match='含 write_id'):
        insert(path, config(path), values, 'w' * 36)
    with sqlite3.connect(path) as connection:
        assert connection.execute('SELECT COUNT(*) FROM records').fetchone()[0] == 0


def testAttachFailureStillClosesOwnedConnection(tmp_path, monkeypatch):
    import emo_master.apps.runtime.business_sqlite.backend as backend
    path = makeDb(tmp_path / 'attach.sqlite3')
    connections = []
    original = backend.sqlite3.connect
    def connect(*args, **kwargs):
        connection = original(*args, **kwargs)
        connections.append(connection)
        return connection
    def failStart(_self):
        raise RuntimeError('injected monitor start failure')
    with monkeypatch.context() as injection:
        injection.setattr(backend.sqlite3, 'connect', connect)
        injection.setattr(backend.threading.Thread, 'start', failStart)
        with pytest.raises(RuntimeError, match='monitor start'):
            inspect(path)
    with pytest.raises(sqlite3.ProgrammingError, match='closed'):
        connections[0].execute('SELECT 1')
