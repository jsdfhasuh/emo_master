"""The opt-in observer does not change durable operations or capture payloads."""
import json
import sqlite3
from types import SimpleNamespace

import pytest

from scripts.join_sqlite_diagnostic import Observers, TARGET, pytest_collection_modifyitems


def testExactTargetOnly():
    config = SimpleNamespace(_join_sqlite={})
    pytest_collection_modifyitems(config, [SimpleNamespace(nodeid=TARGET)])
    for nodeids in ([], [TARGET, TARGET], [TARGET + "Other"]):
        with pytest.raises(pytest.UsageError):
            pytest_collection_modifyitems(config, [SimpleNamespace(nodeid=n) for n in nodeids])


def testForwardDurableCommitRollbackCloseAndRetirement(tmp_path):
    from emo_master.apps.runtime.context.sqlite_store import SqliteStore
    store = SqliteStore(tmp_path / 'probe.db')
    probes = Observers()
    probes.attach(store)
    secret = 'payload-must-never-appear-in-observer-output'
    with store.connection() as connection:
        connection.execute('CREATE TABLE probe (value TEXT)')
        connection.execute('INSERT INTO probe VALUES (?)', (secret,))
        connection.commit()
    error = RuntimeError('original failure')
    with pytest.raises(RuntimeError) as caught:
        with store.connection() as connection:
            connection.execute('INSERT INTO probe VALUES (?)', ('rolled back',))
            raise error
    assert caught.value is error
    with sqlite3.connect(store.dbPath) as native:
        assert native.execute('SELECT value FROM probe').fetchall() == [(secret,)]
        assert native.execute('PRAGMA synchronous').fetchone() == (2,)
    rows = probes.retire()
    assert '_connect' not in vars(store)
    assert secret not in json.dumps(rows)
    assert probes.errors == [] and rows[0]['errors'] == []
    stages = rows[0]['stages']
    assert stages['connect_configure']['returned'] == 2
    assert stages['explicit_close']['returned'] == 2
    assert stages['commit']['returned'] == 1
    assert stages['context_exit']['failed'] == 0


def testObserverInstallFailureDoesNotReplaceConstructorResult():
    probes = Observers()
    probes.attach(object())
    assert probes.errors == ['AttributeError']
    assert probes.retire()[0]['errors'] == []


def testOriginalFailureIsValidButSkippedOrPartialRunIsNot():
    from scripts.join_sqlite_diagnostic import completeOutcomes
    passed = {phase: {'outcome': 'passed'} for phase in ('setup', 'call', 'teardown')}
    assert completeOutcomes(passed, 0)
    assert not completeOutcomes(passed, 1)
    failed = dict(passed, call={'outcome': 'failed'})
    assert completeOutcomes(failed, 1)
    assert not completeOutcomes(failed, 0)
    assert not completeOutcomes(dict(passed, call={'outcome': 'skipped'}), 0)
    assert not completeOutcomes({'setup': passed['setup']}, 1)
    assert not completeOutcomes(dict(passed, teardown={'outcome': 'failed'}), 1)


def testRetirementFailureIsRecordedWithoutReplacingTestOutcome():
    probes = Observers()
    def fail():
        raise RuntimeError('diagnostic cleanup only')
    probes.probes = [SimpleNamespace(snapshot=fail, close=fail)]
    assert probes.retire() == []
    assert probes.errors == ['RuntimeError', 'RuntimeError']


def testSessionReportPreservesFailedExitAndOnlyBoundedMetadata(tmp_path, monkeypatch):
    from scripts import join_sqlite_diagnostic as module
    report = tmp_path / 'report.json'
    monkeypatch.setattr(module, 'sourceHashes', lambda root: {'known.py': 'digest'})
    config = SimpleNamespace(rootpath=tmp_path, getoption=lambda name: str(report))
    module.pytest_configure(config)
    state = config._join_sqlite
    state['outcomes'] = {phase: {'outcome': outcome} for phase, outcome in
                         [('setup', 'passed'), ('call', 'failed'), ('teardown', 'passed')]}
    state['stores'] = [{'enabled': True, 'errors': [], 'active': []}]
    module.pytest_sessionfinish(SimpleNamespace(config=config), 1)
    row = json.loads(report.read_text())
    assert row['valid'] and row['original_pytest_exit'] == 1
    assert row['source_stable'] and row['observer_retired']
    assert row['outcomes']['call']['outcome'] == 'failed'
    assert 'observers' not in row and str(tmp_path) not in report.read_text()
