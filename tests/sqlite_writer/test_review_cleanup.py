"""Commit truth survives cleanup faults; running cleanup retains ownership."""
import sqlite3
import threading

import pytest

from emo_master.apps.designer.presenters.node_run_presenter import sqliteReceiptText
from emo_master.apps.runtime.business_sqlite import backend
from emo_master.apps.runtime.workflow.cancellation import CancellationRequested, CancellationToken
from emo_master.apps.runtime.workflow.runner import WorkflowExecutionError
from emo_master.core.contracts.sqlite_writer import receiptSummary
from tests.sqlite_writer.test_backend import actualRun, makeDb
from tests.sqlite_writer.test_dependencies import config


@pytest.mark.parametrize('policy', ['stop', 'continue'])
def testCleanupFailureAfterActualCloseRetainsConfirmedCommitAndDiagnostic(tmp_path, monkeypatch, policy):
    path = makeDb(tmp_path / 'business.sqlite3')
    original = backend.OperationGuard.close
    def failAfterActualClose(self):
        original(self)
        raise OSError('injected cleanup failure after real close')
    with monkeypatch.context() as injection:
        injection.setattr(backend.OperationGuard, 'close', failAfterActualClose)
        if policy == 'stop':
            with pytest.raises(WorkflowExecutionError) as failed:
                actualRun(path, 'actual', policy=policy)
            receipt = failed.value.diagnostics['sqliteReceipt']
            assert failed.value.code == 'E_SQLITE_CLEANUP'
        else:
            receipt, _, events = actualRun(path, 'actual', policy=policy)
            assert any(e['eventType'] == 'node.log' and e['level'] == 'ERROR' for e in events)
    assert receipt['status'] == 'COMMITTED' and receipt['rowsAffected'] == 1 and receipt['primaryKey'] == 1
    assert receipt['error'] is None and receipt['cleanupError']['code'] == 'E_SQLITE_CLEANUP'
    normalized = receiptSummary(receipt, receipt['execution'])
    assert normalized['cleanupError'] == receipt['cleanupError']
    assert '清理异常' in sqliteReceiptText(normalized) and 'COMMITTED' in sqliteReceiptText(normalized)
    with sqlite3.connect(path) as connection:
        assert connection.execute('SELECT value,write_id FROM records').fetchall() == [('\"actual\"', receipt['writeId'])]
    assert not any(t.name == 'sqlite-interrupt' for t in threading.enumerate())


@pytest.mark.parametrize('policy', ['stop', 'continue'])
def testCancelDuringFaultyPostCommitCleanupKeepsReceiptAndPropagatesCancel(tmp_path, monkeypatch, policy):
    path = makeDb(tmp_path / 'cancel.sqlite3')
    token = CancellationToken()
    original = backend.OperationGuard.close
    def cancelAfterClose(self):
        original(self)
        token.cancel()
        raise OSError('cleanup acknowledgement failed')
    with monkeypatch.context() as injection:
        injection.setattr(backend.OperationGuard, 'close', cancelAfterClose)
        with pytest.raises(CancellationRequested) as cancelled:
            actualRun(path, 'committed', policy=policy, token=token)
    receipt = cancelled.value.diagnostics['sqliteReceipt']
    assert receipt['status'] == 'COMMITTED' and receipt['rowsAffected'] == 1
    assert receipt['cleanupError']['code'] == 'E_SQLITE_CLEANUP'
    with sqlite3.connect(path) as connection:
        assert connection.execute('SELECT write_id FROM records').fetchall() == [(receipt['writeId'],)]


def testProgressHandlerCleanupFailureStillClosesRealConnection(tmp_path, monkeypatch):
    path = makeDb(tmp_path / 'handler.sqlite3')
    original = backend.sqlite3.connect
    connections = []
    class HandlerCleanupFault:
        def __init__(self, connection):
            self.connection = connection
        def __getattr__(self, name):
            return getattr(self.connection, name)
        def set_progress_handler(self, handler, steps):
            if handler is None:
                raise RuntimeError('injected handler cleanup failure')
            return self.connection.set_progress_handler(handler, steps)
    def connect(*args, **kwargs):
        connection = original(*args, **kwargs)
        connections.append(connection)
        return HandlerCleanupFault(connection)
    with monkeypatch.context() as injection:
        injection.setattr(backend.sqlite3, 'connect', connect)
        receipt, _, _ = actualRun(path, 7, policy='continue')
    assert receipt['status'] == 'COMMITTED' and receipt['cleanupError']['code'] == 'E_SQLITE_CLEANUP'
    with pytest.raises(sqlite3.ProgrammingError, match='closed'):
        connections[0].execute('SELECT 1')


def testBlockedCleanupKeepsManagementAdmissionUntilItReallyExits(tmp_path, monkeypatch):
    path = makeDb(tmp_path / 'blocked.sqlite3')
    manager = backend.SqliteManagement()
    entered, release = threading.Event(), threading.Event()
    original = backend.OperationGuard.close
    def blocked(self):
        entered.set()
        try:
            assert release.wait(3), 'outer bounded release'
        finally:
            original(self)
    monkeypatch.setattr(backend.OperationGuard, 'close', blocked)
    result = []
    def work():
        result.append(manager.run(lambda cancelled: backend.insert(path, config(path), {'value': '7'}, 'write', cancelled)))
    thread = threading.Thread(target=work)
    thread.start()
    try:
        assert entered.wait(3)
        assert thread.is_alive() and manager.active == 1 and not result
        with pytest.raises(RuntimeError, match='尚未释放'):
            manager.close()
        assert manager.active == 1
    finally:
        release.set()
        thread.join(3)
        assert not thread.is_alive()
        manager.close()
    assert result == [1] and manager.active == 0


@pytest.mark.parametrize('outcome', ['ignored', 'unknown'])
def testSecondCleanupFaultDoesNotOverwriteRollbackOrUnconfirmedCommit(tmp_path, monkeypatch, outcome):
    path = makeDb(tmp_path / 'double-fault.sqlite3')
    originalClose, originalConnect = backend.OperationGuard.close, backend.sqlite3.connect
    if outcome == 'ignored':
        with sqlite3.connect(path) as connection:
            connection.execute('CREATE TRIGGER ignore_record BEFORE INSERT ON records BEGIN SELECT RAISE(IGNORE); END')
    class LostCommitAck:
        def __init__(self, connection):
            self.connection = connection
        def __getattr__(self, name):
            return getattr(self.connection, name)
        def commit(self):
            self.connection.commit()
            raise OSError('commit acknowledgement lost')
    def failCleanup(self):
        originalClose(self)
        raise OSError('second cleanup failure')
    with monkeypatch.context() as injection:
        injection.setattr(backend.OperationGuard, 'close', failCleanup)
        if outcome == 'unknown':
            injection.setattr(backend.sqlite3, 'connect', lambda *a, **kw: LostCommitAck(originalConnect(*a, **kw)))
        receipt, _, _ = actualRun(path, 7, policy='continue')
    assert receipt['status'] == ('UNKNOWN' if outcome == 'unknown' else 'FAILED')
    assert receipt['error']['code'] == ('E_SQLITE_UNKNOWN' if outcome == 'unknown' else 'E_SQLITE_NO_INSERT')
    assert receipt['cleanupError']['code'] == 'E_SQLITE_CLEANUP'
    with sqlite3.connect(path) as connection:
        assert connection.execute('SELECT count(*) FROM records').fetchone()[0] == (1 if outcome == 'unknown' else 0)
