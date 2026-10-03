from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sqlite3

import pytest

from emo_master.apps.runtime.context.global_counters import (
    E_COUNTER_BUSY,
    E_COUNTER_NAME_INVALID,
    E_COUNTER_VALUE_RANGE,
    MAX_GLOBAL_COUNTER_VALUE,
    GlobalCounterError,
)
from emo_master.apps.runtime.context.sqlite_store import SqliteStore


def testGlobalCounterMigrationAndRuntimeRestartPersistence(tmp_path: Path) -> None:
    dbPath = tmp_path / "runtime.db"
    store = SqliteStore(dbPath)
    store.initialize()
    stored = store.setGlobalCounter("project-a", "总数", 41)

    reopened = SqliteStore(dbPath)
    reopened.initialize()
    loaded = reopened.getGlobalCounter("project-a", "总数")

    assert stored.value == 41
    assert loaded.value == 41
    with sqlite3.connect(dbPath) as connection:
        assert connection.execute(
            "SELECT version FROM schemaMigrations WHERE version = 4"
        ).fetchone() == (4,)
        assert connection.execute(
            "SELECT value FROM globalCounters WHERE projectId = ? AND name = ?",
            ("project-a", "总数"),
        ).fetchone() == (41,)


def testGlobalCounterMigrationUpgradesExistingDatabase(tmp_path: Path) -> None:
    dbPath = tmp_path / "runtime.db"
    store = SqliteStore(dbPath)
    store.initialize()
    with sqlite3.connect(dbPath) as connection:
        connection.execute("DROP TABLE globalCounters")
        connection.execute("DELETE FROM schemaMigrations WHERE version = 4")
        connection.commit()

    store.initialize()

    with sqlite3.connect(dbPath) as connection:
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'globalCounters'"
        ).fetchone() == ("globalCounters",)


def testGlobalCountersAreIsolatedByProjectAndCaseSensitiveName(tmp_path: Path) -> None:
    store = SqliteStore(tmp_path / "runtime.db")
    store.initialize()

    store.setGlobalCounter("project-a", "Count", 3)
    store.setGlobalCounter("project-a", "count", 4)
    store.setGlobalCounter("project-b", "Count", 9)

    assert store.getGlobalCounter("project-a", "Count").value == 3
    assert store.getGlobalCounter("project-a", "count").value == 4
    assert store.getGlobalCounter("project-b", "Count").value == 9
    assert [record.name for record in store.listGlobalCounters("project-a")] == [
        "Count",
        "count",
    ]


def testGlobalCounterApplyCreatesReadsIncrementsAndPrefersReset(tmp_path: Path) -> None:
    store = SqliteStore(tmp_path / "runtime.db")
    store.initialize()

    assert store.getGlobalCounter("project", "parts").value == 0
    assert store.applyGlobalCounter("project", "parts", increment=True).value == 1
    assert store.applyGlobalCounter("project", "parts").value == 1
    assert store.applyGlobalCounter(
        "project",
        "parts",
        increment=True,
        reset=True,
    ).value == 0
    assert store.resetGlobalCounter("project", "missing").value == 0


def testGlobalCounterRejectsInvalidNamesAndValuesWithoutWrapping(tmp_path: Path) -> None:
    store = SqliteStore(tmp_path / "runtime.db")
    store.initialize()

    for name in ("", " leading", "trailing ", "x" * 129):
        with pytest.raises(GlobalCounterError) as error:
            store.getGlobalCounter("project", name)
        assert error.value.code == E_COUNTER_NAME_INVALID

    for value in (-1, True, MAX_GLOBAL_COUNTER_VALUE + 1):
        with pytest.raises(GlobalCounterError) as error:
            store.setGlobalCounter("project", "parts", value)  # type: ignore[arg-type]
        assert error.value.code == E_COUNTER_VALUE_RANGE

    store.setGlobalCounter("project", "parts", MAX_GLOBAL_COUNTER_VALUE)
    with pytest.raises(GlobalCounterError) as overflow:
        store.applyGlobalCounter("project", "parts", increment=True)
    assert overflow.value.code == E_COUNTER_VALUE_RANGE
    assert store.getGlobalCounter("project", "parts").value == MAX_GLOBAL_COUNTER_VALUE
    assert store.applyGlobalCounter(
        "project", "parts", increment=True, reset=True
    ).value == 0


def testGlobalCounterConcurrentConnectionsDoNotLoseIncrements(tmp_path: Path) -> None:
    dbPath = tmp_path / "runtime.db"
    primary = SqliteStore(dbPath)
    primary.initialize()
    stores = [SqliteStore(dbPath) for _ in range(6)]

    def incrementMany(index: int) -> None:
        for _ in range(30):
            stores[index].applyGlobalCounter("project", "shared", increment=True)

    with ThreadPoolExecutor(max_workers=len(stores)) as executor:
        list(executor.map(incrementMany, range(len(stores))))

    assert primary.getGlobalCounter("project", "shared").value == 180


def testGlobalCounterMapsDatabaseLockToStableError(tmp_path: Path, monkeypatch) -> None:
    store = SqliteStore(tmp_path / "runtime.db")
    store.initialize()

    def lockedConnection():
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(store, "_connect", lockedConnection)
    with pytest.raises(GlobalCounterError) as error:
        store.listGlobalCounters("project")
    assert error.value.code == E_COUNTER_BUSY
