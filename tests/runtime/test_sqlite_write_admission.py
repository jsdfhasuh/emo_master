"""Write admission is bounded and cannot replay a body or uncertain commit."""
import sqlite3
from types import SimpleNamespace

import pytest

from emo_master.apps.runtime.context import sqlite_transactions as module
from emo_master.apps.runtime.context.global_variables import _beginWrite
from emo_master.apps.runtime.context.sqlite_store import SqliteStore
from emo_master.core.project.global_variables import VariableError


class AdmissionConnection:
    def __init__(self, errors, timeout=10):
        self.errors = iter(errors)
        self.timeout = timeout
        self.statements = []

    def execute(self, sql):
        self.statements.append(sql)
        if sql == "PRAGMA busy_timeout":
            return SimpleNamespace(fetchone=lambda: (self.timeout,))
        if sql == "BEGIN IMMEDIATE":
            error = next(self.errors, None)
            if error is not None:
                raise error


def fakeClock(monkeypatch):
    elapsed, pauses = [0.0], []

    def sleep(seconds):
        pauses.append(seconds)
        elapsed[0] += seconds

    monkeypatch.setattr(module, "time", SimpleNamespace(monotonic=lambda: elapsed[0], sleep=sleep))
    monkeypatch.setattr(module.random, "uniform", lambda *_: .003)
    return elapsed, pauses


def testAdmissionRetriesOnlyBeginAndRestoresTheOriginalBusyTimeout(monkeypatch):
    elapsed, pauses = fakeClock(monkeypatch)
    connection = AdmissionConnection([sqlite3.OperationalError("database is locked"), None])
    module.beginImmediate(connection)
    assert connection.statements == ["PRAGMA busy_timeout", "PRAGMA busy_timeout=0",
                                     "BEGIN IMMEDIATE", "BEGIN IMMEDIATE", "PRAGMA busy_timeout=10"]
    assert elapsed[0] == .003 and pauses == [.003]


def testAdmissionCannotExtendTheOriginalTotalBudget(monkeypatch):
    elapsed, pauses = fakeClock(monkeypatch)
    error = sqlite3.OperationalError("database is locked")
    connection = AdmissionConnection([error] * 10)
    with pytest.raises(sqlite3.OperationalError) as caught:
        module.beginImmediate(connection)
    assert caught.value is error
    assert elapsed[0] == pytest.approx(.010) and sum(pauses) == pytest.approx(.010)
    # The old fifth attempt began exactly at the expired deadline. A retry
    # budget may not admit that extra call, including after scheduler oversleep.
    assert connection.statements.count("BEGIN IMMEDIATE") == 4
    assert connection.statements[-1] == "PRAGMA busy_timeout=10"


@pytest.mark.parametrize("timeout", [0, 10])
def testNonBusyErrorsAreNotRetriedAndZeroBudgetStaysZero(monkeypatch, timeout):
    elapsed, pauses = fakeClock(monkeypatch)
    error = sqlite3.OperationalError("no such table") if timeout else sqlite3.OperationalError("database is locked")
    connection = AdmissionConnection([error], timeout)
    with pytest.raises(sqlite3.OperationalError) as caught:
        module.beginImmediate(connection)
    assert caught.value is error and elapsed[0] == 0 and not pauses
    assert connection.statements.count("BEGIN IMMEDIATE") == 1
    assert connection.statements[-1] == f"PRAGMA busy_timeout={timeout}"


def testVariablesKeepTheirStableBusyCodeWithoutRetryingTheMutation(monkeypatch):
    fakeClock(monkeypatch)
    connection = AdmissionConnection([sqlite3.OperationalError("database is locked")], 0)
    with pytest.raises(VariableError) as error:
        _beginWrite(connection)
    assert error.value.code == "E_VARIABLE_BUSY"
    assert connection.statements.count("BEGIN IMMEDIATE") == 1


def testSchedulerOversleepCannotStartAnotherBeginAfterTheBudget(monkeypatch):
    elapsed, _ = fakeClock(monkeypatch)
    monkeypatch.setattr(module.time, "sleep", lambda _: elapsed.__setitem__(0, .020))
    error = sqlite3.OperationalError("database is locked")
    connection = AdmissionConnection([error, None], 10)
    with pytest.raises(sqlite3.OperationalError) as caught:
        module.beginImmediate(connection)
    assert caught.value is error
    assert connection.statements.count("BEGIN IMMEDIATE") == 1
    assert connection.statements[-1] == "PRAGMA busy_timeout=10"


@pytest.mark.parametrize("beginFails", [False, True])
def testRestoreFailureIsVisibleAndCannotHideTheOriginalBeginFailure(monkeypatch, beginFails):
    fakeClock(monkeypatch)
    primary = sqlite3.OperationalError("database is locked")
    restore = sqlite3.OperationalError("injected timeout restore failure")

    class Connection(AdmissionConnection):
        def execute(self, sql):
            result = super().execute(sql)
            if sql == "PRAGMA busy_timeout=0" and self.statements.count(sql) == 2:
                raise restore
            return result

    connection = Connection([primary] if beginFails else [None], 0)
    with pytest.raises(sqlite3.OperationalError) as caught:
        module.beginImmediate(connection)
    assert caught.value is (primary if beginFails else restore)
    if beginFails:
        assert caught.value.__cause__ is restore
    assert connection.statements.count("BEGIN IMMEDIATE") == 1


def testSettingNonblockingModeFailureStillAttemptsRestore(monkeypatch):
    fakeClock(monkeypatch)
    error = sqlite3.OperationalError("injected mode switch failure")

    class Connection(AdmissionConnection):
        def execute(self, sql):
            result = super().execute(sql)
            if sql == "PRAGMA busy_timeout=0":
                raise error
            return result

    connection = Connection([], 10)
    with pytest.raises(sqlite3.OperationalError) as caught:
        module.beginImmediate(connection)
    assert caught.value is error and "BEGIN IMMEDIATE" not in connection.statements
    assert connection.statements[-1] == "PRAGMA busy_timeout=10"


@pytest.mark.parametrize("commitFailure", [None, "before", "after"])
def testDurableEventWritesDoNotReplayTheirBodyOrAnUncertainCommit(tmp_path, monkeypatch, commitFailure):
    store = SqliteStore(tmp_path / "runtime.db")
    store.initialize()
    native = store._connect
    statements, commits, closed = [], [], []

    class Connection:
        def __init__(self):
            self.connection = native()
            self.beginCalls = 0

        def execute(self, sql, *args):
            statements.append(sql)
            if sql == "BEGIN IMMEDIATE":
                self.beginCalls += 1
                if self.beginCalls == 1:
                    raise sqlite3.OperationalError("database is locked")
            return self.connection.execute(sql, *args)

        def __enter__(self):
            self.connection.__enter__()
            return self

        def __exit__(self, *args):
            return self.connection.__exit__(*args)

        def commit(self):
            commits.append(1)
            if commitFailure == "before":
                raise sqlite3.OperationalError("injected commit failure")
            self.connection.commit()
            if commitFailure == "after":
                raise sqlite3.OperationalError("injected commit failure after durable write")

        def close(self):
            self.connection.close()
            closed.append(1)

    monkeypatch.setattr(store, "_connect", Connection)
    if commitFailure:
        with pytest.raises(sqlite3.OperationalError, match="injected commit failure"):
            store.appendJobEvent("job", "node", "node.completed", "INFO", "", "", "{}")
    else:
        assert store.appendJobEvent("job", "node", "node.completed", "INFO", "", "", "{}") == 1
    assert statements.count("BEGIN IMMEDIATE") == 2
    assert len([sql for sql in statements if "INSERT INTO jobEvents" in sql]) == 1
    assert commits == [1] and closed == [1]
    with sqlite3.connect(store.dbPath) as observer:
        assert observer.execute("SELECT sequence FROM jobEvents").fetchall() == ([] if commitFailure == "before" else [(1,)])
