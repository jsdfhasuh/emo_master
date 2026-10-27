"""Already queued ordinary events share one durable, ordered transaction."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import sqlite3
from threading import Event, Thread

import pytest

from emo_master.apps.runtime.context.sqlite_store import SqliteStore
from emo_master.apps.runtime.events.event_store import EventStore
from emo_master.apps.runtime.events.models import RuntimeEvent


def events(count=3, job='job'):
    return [RuntimeEvent(job, 'node.completed', str(index), timestampMs=1000 + index,
                         workflowId='workflow', nodeRunId=f'node-{index}') for index in range(count)]


def stores(tmp_path, retention=10000):
    persistence = SqliteStore(tmp_path / 'events.db')
    persistence.initialize()
    return persistence, EventStore(persistence, retentionPerJob=retention)


class Connection:
    def __init__(self, native, *, beforeCommit=None, afterCommit=None, failInsert=0):
        self.native = native
        self.beforeCommit, self.afterCommit = beforeCommit, afterCommit
        self.failInsert = failInsert
        self.inserts = self.commits = self.closes = 0

    def execute(self, sql, *args):
        if 'INSERT INTO jobEvents' in sql:
            self.inserts += 1
            if self.inserts == self.failInsert:
                raise sqlite3.IntegrityError('injected INSERT failure')
        return self.native.execute(sql, *args)

    def commit(self):
        self.commits += 1
        if self.beforeCommit:
            self.beforeCommit()
        self.native.commit()
        if self.afterCommit:
            self.afterCommit()

    def __enter__(self):
        self.native.__enter__()
        return self

    def __exit__(self, *args):
        return self.native.__exit__(*args)

    def close(self):
        self.closes += 1
        self.native.close()


def instrument(monkeypatch, persistence, **kwargs):
    connect = persistence._connect
    connections = []
    def wrapped():
        connection = Connection(connect(), **kwargs)
        connections.append(connection)
        return connection
    monkeypatch.setattr(persistence, '_connect', wrapped)
    return connections


def testBatchUsesOneFullCommitAndPersistsCompleteContext(tmp_path, monkeypatch):
    persistence, store = stores(tmp_path)
    settings = []
    connect = persistence._connect
    def configured():
        native = connect()
        settings.append(native.execute('PRAGMA synchronous').fetchone()[0])
        return native
    monkeypatch.setattr(persistence, '_connect', configured)
    connections = instrument(monkeypatch, persistence)
    observed = []
    store.addSink(observed.append)
    result = store.appendBatch(events())
    assert [e.sequence for e in result] == [1, 2, 3]
    assert observed == result and settings == [2]
    assert (connections[0].inserts, connections[0].commits, connections[0].closes) == (3, 1, 1)
    assert persistence.listJobEventsAfter('job') == result
    assert EventStore(persistence).readMerged('job') == result


def testCommittedCohortTrimsAndNotifiesOnceWithoutDroppingSinkEvents(monkeypatch):
    class Bucket(list):
        trims = 0

        def __delitem__(self, key):
            self.trims += 1
            return super().__delitem__(key)

    store = EventStore(retentionPerJob=4)
    store._events['job'] = bucket = Bucket()
    received, notifications = [], []
    store.addSink(received.append)
    monkeypatch.setattr(store._condition, 'notify_all', lambda: notifications.append(True))
    batch = store.appendBatch(events(32))
    assert received == batch
    assert store.read('job') == batch[-4:]
    assert bucket.trims == 1 and notifications == [True]
    terminal = store.append('job', 'job.completed', 'done')
    assert terminal.sequence == 33
    assert received == [*batch, terminal]


def testNothingPublishedOrAcknowledgedBeforeCommit(tmp_path, monkeypatch):
    persistence, store = stores(tmp_path)
    entered, release, finished = Event(), Event(), Event()
    observed, errors = [], []
    store.addSink(observed.append)
    def blocked():
        entered.set()
        assert release.wait(5)
    instrument(monkeypatch, persistence, beforeCommit=blocked)
    def append():
        try:
            store.appendBatch(events())
            finished.set()
        except BaseException as error:
            errors.append(error)
    thread = Thread(target=append)
    thread.start()
    try:
        assert entered.wait(5)
        assert not observed and not store._events and not store._sequences and not finished.is_set()
        with sqlite3.connect(persistence.dbPath) as connection:
            assert connection.execute('SELECT count(*) FROM jobEvents').fetchone() == (0,)
    finally:
        release.set()
        thread.join(5)
    assert not errors and not thread.is_alive() and finished.is_set()
    assert [e.sequence for e in observed] == [1, 2, 3]


@pytest.mark.parametrize('failure', ['insert', 'before_commit', 'after_commit'])
def testFailureNeverPublishesOrReplaysAndRollbackIsAtomic(tmp_path, monkeypatch, failure):
    persistence, store = stores(tmp_path)
    observed = []
    store.addSink(observed.append)
    def fail():
        raise sqlite3.OperationalError('injected commit outcome')
    kwargs = {'failInsert': 2} if failure == 'insert' else {
        'beforeCommit' if failure == 'before_commit' else 'afterCommit': fail}
    connections = instrument(monkeypatch, persistence, **kwargs)
    with pytest.raises(sqlite3.Error, match='injected'):
        store.appendBatch(events())
    assert not observed and not store._events and not store._sequences
    assert len(connections) == 1 and connections[0].closes == 1
    assert connections[0].commits == (0 if failure == 'insert' else 1)
    with sqlite3.connect(persistence.dbPath) as connection:
        rows = connection.execute('SELECT sequence FROM jobEvents ORDER BY sequence').fetchall()
    assert rows == ([(1,), (2,), (3,)] if failure == 'after_commit' else [])


def testRetentionMatchesSingletonCursorAndMemory(tmp_path):
    persistence, store = stores(tmp_path, retention=2)
    persistence.jobEventRetention = 3
    result = store.appendBatch(events(8))
    assert [e.sequence for e in store.read('job')] == [7, 8]
    assert [e.sequence for e in persistence.listJobEventsAfter('job')] == [4, 5, 6, 7, 8]
    terminal = store.append('job', 'job.completed', 'done')
    assert terminal.sequence == 9
    assert [e.sequence for e in persistence.listJobEventsAfter('job')] == [7, 8, 9]
    assert result[-1].sequence == 8
    assert EventStore(persistence).terminalSequence('job') == 9


def testConcurrentStoresAndSingletonsKeepUniqueSequences(tmp_path):
    persistence, first = stores(tmp_path)
    second = EventStore(SqliteStore(persistence.dbPath))
    def write(index):
        store = (first, second)[index % 2]
        if index % 3 == 0:
            return [store.append('job', 'node.started', str(index))]
        return store.appendBatch(events(4))
    with ThreadPoolExecutor(max_workers=4) as pool:
        written = [event for batch in pool.map(write, range(30)) for event in batch]
    assert sorted(event.sequence for event in written) == list(range(1, 91))
    assert [event.sequence for event in persistence.listJobEventsAfter('job')] == list(range(1, 91))


def testConcurrentSingleAndBatchSinksKeepPersistenceOrder(tmp_path):
    persistence, store = stores(tmp_path)
    received = []
    store.addSink(lambda event: received.append(event.sequence))
    def write(index):
        if index % 2:
            store.append('job', 'node.started', str(index))
        else:
            store.appendBatch(events(3))
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(write, range(20)))
    assert received == list(range(1, 41))
    assert [event.sequence for event in persistence.listJobEventsAfter('job')] == received


@pytest.mark.parametrize('eventType', ['job.started', 'job.completed', 'job.failed', 'job.aborted',
                                     'display.result', 'process.heartbeat', 'sqlite.write.started'])
def testBarriersCannotEnterBatch(tmp_path, eventType):
    persistence, store = stores(tmp_path)
    batch = [events()[0], replace(events()[1], eventType=eventType)]
    for append in (store.appendBatch, persistence.appendJobEvents):
        with pytest.raises(ValueError, match='barrier'):
            append(batch)
    assert not store.read('job') and not persistence.listJobEventsAfter('job')


def testBatchLimitsAndJobIsolation(tmp_path):
    persistence, store = stores(tmp_path)
    for batch in ([], events(33), events(1) + events(1, 'other')):
        for append in (store.appendBatch, persistence.appendJobEvents):
            with pytest.raises(ValueError):
                append(batch)
    assert not persistence.listJobEventsAfter('job')


def testTerminalHookRemainsAfterCommittedBatchAndBeforeTerminal(tmp_path):
    persistence, store = stores(tmp_path)
    seen = []
    store.addSink(lambda event: seen.append((event.sequence, event.eventType)))
    store.appendBatch(events())
    def before(job):
        assert len(persistence.listJobEventsAfter(job)) == 3
        store.append(job, 'node.failed', 'unknown outcome')
    store.beforeTerminal = before
    terminal = store.append('job', 'job.failed', 'failed')
    assert terminal.sequence == 5
    assert seen == [(1, 'node.completed'), (2, 'node.completed'), (3, 'node.completed'),
                    (4, 'node.failed'), (5, 'job.failed')]


def testAcknowledgedBatchSurvivesProcessExitWithoutCleanup(tmp_path):
    import os
    from pathlib import Path
    import subprocess
    import sys
    db = tmp_path / 'crash.db'
    source = '''import os, sys
from pathlib import Path
from emo_master.apps.runtime.context.sqlite_store import SqliteStore
from emo_master.apps.runtime.events.event_store import EventStore
from emo_master.apps.runtime.events.models import RuntimeEvent
persistence = SqliteStore(Path(sys.argv[1]))
persistence.initialize()
persistence.retainIdleConnection()
written = EventStore(persistence).appendBatch([RuntimeEvent('job', 'node.completed', str(i)) for i in range(4)])
assert [event.sequence for event in written] == [1, 2, 3, 4]
os._exit(0)
'''
    environment = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[2] / 'src'))
    result = subprocess.run([sys.executable, '-c', source, str(db)], env=environment,
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    persistence = SqliteStore(db)
    assert [event.sequence for event in EventStore(persistence).readMerged('job')] == [1, 2, 3, 4]


def testCancellationDuringCommitPreservesConfirmedBatchBeforeTerminal(tmp_path, monkeypatch):
    from tests.runtime.test_job_supervisor_contracts import _supervisor
    persistence, store = stores(tmp_path)
    supervisor, repository, _, process = _supervisor()
    supervisor.eventStore = store
    entered, release, cancelling, cancelled = Event(), Event(), Event(), Event()
    acknowledged, errors = [], []
    def blocked():
        entered.set()
        assert release.wait(5)
    instrument(monkeypatch, persistence, beforeCommit=blocked)
    def append():
        try:
            supervisor.consumeWorkerEvents('job-stop', [
                {'eventType': 'node.completed', 'message': str(i)} for i in range(3)])
            acknowledged.append(True)
        except BaseException as error:
            errors.append(error)
    def cancel():
        try:
            cancelling.set()
            supervisor.stopJob('job-stop', mode='force')
            cancelled.set()
        except BaseException as error:
            errors.append(error)
    writer, stopper = Thread(target=append), Thread(target=cancel)
    writer.start()
    try:
        assert entered.wait(5)
        stopper.start()
        assert cancelling.wait(5)
        assert not acknowledged and not cancelled.is_set()
    finally:
        release.set()
        writer.join(5)
        if stopper.ident is not None:
            stopper.join(5)
    assert not errors and acknowledged == [True] and cancelled.is_set()
    assert not writer.is_alive() and not stopper.is_alive()
    assert repository.get('job-stop').status == 'ABORTED' and process.terminated
    replayed = EventStore(SqliteStore(persistence.dbPath)).readMerged('job-stop')
    assert [event.sequence for event in replayed] == list(range(1, len(replayed) + 1))
    assert [event.eventType for event in replayed[:3]] == ['node.completed'] * 3
    assert replayed[-1].eventType == 'job.aborted'
    assert 'job-stop' not in supervisor._handles
