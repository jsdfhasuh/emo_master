"""Only transaction admission is retryable, never mutations or side effects."""
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from threading import Event

import pytest

from emo_master.apps.runtime.context import global_variables as variables
from emo_master.apps.runtime.context.global_counters import GlobalCounterError
from emo_master.core.project.global_variables import VariableError
from tests.runtime.test_global_variables import service, variable


@pytest.mark.parametrize('definitions', [{}, {'v': variable()}, {'v': variable(kind='constant')}])
def testJobInitializationWithoutJobValuesNeverRequestsWriter(tmp_path, monkeypatch, definitions):
    accessor = service(tmp_path)
    accessor.definitions = variables.definitions(definitions)
    lock = accessor.store._connect()
    lock.execute('BEGIN IMMEDIATE')
    def unexpectedConnection():
        pytest.fail('no job-scoped values: initialization must not request a database connection')
    monkeypatch.setattr(accessor.store, '_connect', unexpectedConnection)
    try:
        accessor.initializeJob()
    finally:
        lock.rollback()
        lock.close()


def testRealJobInitializationStillRequiresWriteAdmissionAndInitializesOnce(tmp_path, monkeypatch):
    accessor = service(tmp_path, {'v': variable(7, lifetime='job')})
    accessor.jobId = 'second'
    connect = accessor.store._connect
    @contextmanager
    def quickConnection():
        connection = connect()
        connection.execute('PRAGMA busy_timeout=30')
        try:
            with connection:
                yield connection
        finally:
            connection.close()
    monkeypatch.setattr(accessor.store, 'connection', quickConnection)
    lock = connect()
    lock.execute('BEGIN IMMEDIATE')
    try:
        with pytest.raises(VariableError) as caught:
            accessor.initializeJob()
        assert caught.value.code == 'E_VARIABLE_BUSY'
    finally:
        lock.rollback()
        lock.close()
    accessor.initializeJob()
    assert accessor.get('v') == 7
    accessor.set('v', 9)
    accessor.initializeJob()
    assert accessor.get('v') == 9


def testEmptyInitializationStillRequiresJobIdentity(tmp_path):
    accessor = service(tmp_path)
    accessor.jobId = ''
    accessor.definitions = {}
    with pytest.raises(VariableError) as caught:
        accessor.initializeJob()
    assert caught.value.code == 'E_VARIABLE_JOB_REQUIRED'


def testContendedAdmissionExecutesTransformExactlyOnce(tmp_path, monkeypatch):
    accessor = service(tmp_path)
    lock = accessor.store._connect()
    lock.execute('BEGIN IMMEDIATE')
    waiting = Event()
    sleep = variables.time.sleep
    def wait(seconds):
        waiting.set()
        sleep(seconds)
    monkeypatch.setattr(variables.time, 'sleep', wait)
    calls = []
    def transform(value):
        calls.append(value)
        return value + 1
    try:
        with ThreadPoolExecutor(1) as pool:
            result = pool.submit(accessor._mutate, 'v', transform)
            try:
                assert waiting.wait(2)
                assert not calls
            finally:
                lock.rollback()
            assert result.result(timeout=5).value == 1
        assert calls == [0] and accessor.get('v') == 1
    finally:
        lock.close()


def testAdmissionTimeoutRestoresBusyTimeoutAndDoesNotStartTransaction(tmp_path):
    path = tmp_path / 'busy.db'
    with sqlite3.connect(path) as lock, sqlite3.connect(path) as contender:
        lock.execute('CREATE TABLE value (n INTEGER)')
        lock.execute('BEGIN IMMEDIATE')
        contender.execute('PRAGMA busy_timeout=30')
        with pytest.raises(VariableError) as caught:
            variables._beginWrite(contender)
        assert caught.value.code == 'E_VARIABLE_BUSY'
        assert not contender.in_transaction
        assert contender.execute('PRAGMA busy_timeout').fetchone()[0] == 30
        lock.rollback()
        variables._beginWrite(contender)
        assert contender.in_transaction


def testTransactionBodyFailureIsNotReplayed(tmp_path):
    accessor = service(tmp_path)
    calls = []
    def transform(value):
        calls.append(value)
        raise sqlite3.OperationalError('database is locked')
    with pytest.raises(sqlite3.OperationalError):
        accessor._mutate('v', transform)
    assert calls == [0] and accessor.get('v') == 0


def testLegacyCounterKeepsBusyErrorContract(tmp_path, monkeypatch):
    accessor = service(tmp_path)
    def busy(connection):
        raise VariableError('E_VARIABLE_BUSY', 'busy')
    monkeypatch.setattr(variables, '_beginWrite', busy)
    with pytest.raises(GlobalCounterError) as caught:
        accessor.store.applyGlobalCounter('project', 'parts', increment=True)
    assert caught.value.code == 'E_COUNTER_BUSY'
