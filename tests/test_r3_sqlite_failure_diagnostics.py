"""Failure-boundary evidence preserves native SQL and pytest lifetimes."""
from concurrent.futures import ThreadPoolExecutor
import gc
import json
from pathlib import Path
import sqlite3
import threading
from types import SimpleNamespace
import weakref

import pytest

from scripts import r3_sqlite_failure_diagnostics as plugin
from scripts.r3_sqlite_phases import _Connection


def fixtureRun(monkeypatch, tmp_path, *, connect=None, details=None, metaclass=type,
               callDetails=False, snapshotType=None):
    class Store(metaclass=metaclass):
        def _connect(self):
            return connect(self) if connect is not None else sqlite3.connect(":memory:")

    class Runtime:
        def __init__(self, store):
            self.sqliteStore = store

    def originalDetails(runtimeService, jobId, status=None, result=None):
        return details(runtimeService, jobId, status, result) if details else "cached-only details"

    def wait(runtimeService, jobId):
        return helper.jobFailureDetails(runtimeService, jobId)

    helper = SimpleNamespace(jobFailureDetails=originalDetails, waitForTerminal=wait)
    module = SimpleNamespace(jobFailureDetails=originalDetails, waitForTerminal=wait)
    monkeypatch.setattr(plugin, "sourceHashes", lambda: {"test": "unchanged"})
    monkeypatch.setattr(plugin, "detailSourceHashes", lambda: {"detail": "unchanged"})
    run = plugin.FailureRun(tmp_path / "failure.json", Store, Runtime, helper,
                            callDetails=callDetails, snapshotType=snapshotType)
    run.selected = 1
    return run, module, Runtime, Store


def testDetailedPollsForwardOnceBoundedAndKeepNoOwners(monkeypatch, tmp_path):
    class Result:
        pass
    calls = []
    class Snapshots:
        def snapshot(self, jobId):
            calls.append(jobId)
            return Result()
    run, module, Runtime, Store = fixtureRun(monkeypatch, tmp_path, callDetails=True, snapshotType=Snapshots)
    original = Snapshots.snapshot
    owner = plugin.FailureOwner(run, module, plugin.DETAIL_TEST)
    owner.install()
    snapshots = Snapshots()
    runtime = Runtime(Store())
    try:
        runtime.sqliteStore._connect().close()
        assert owner.registry[runtime.sqliteStore][1].details
        item = SimpleNamespace(config=configFor(run), nodeid=plugin.DETAIL_TEST,
                               funcargs={"tmp_path": tmp_path})
        run.active = owner
        plugin.pytest_runtest_call(item)
        assert owner.allowedRoot == str(tmp_path)
        from scripts import r3_sqlite_wal_metadata as wal
        metadataCalls = []
        monkeypatch.setattr(wal, "captureWalMetadata", lambda path, root:
                            metadataCalls.append((path, root)) or {
                                "status": "UNAVAILABLE", "cleanup": {"retirement_confirmed": True}})
        result = snapshots.snapshot("job")
        resultRef, storeRef = weakref.ref(result), weakref.ref(snapshots)
        del result
        assert resultRef() is None and not metadataCalls and not run.path.exists()
        for _ in range(140):
            snapshots.snapshot("job")
        assert len(calls) == 141 and owner.pollCount == 141
        assert len(owner.pollRows) == 128 and owner.pollDropped == 13
        del snapshots
        assert storeRef() is None and not owner.pollStores
        module.jobFailureDetails(runtime, "job")
        assert metadataCalls == [(None, str(tmp_path))]
        row = run.records[0]
        assert row["existing_snapshot_polls"]["count"] == 141
        assert "not the original wait start" in row["existing_snapshot_polls"]["limitations"]
        assert row["wal_metadata"]["status"] == "UNAVAILABLE"
        assert row["status"] == "OBSERVED"
        json.dumps(row)
    finally:
        owner.retire()
    assert Snapshots.snapshot is original


def testDetailsAreExactTargetOnlyAndDoNotRequestTmpFixture(monkeypatch, tmp_path):
    class Snapshots:
        def snapshot(self, jobId):
            return jobId
    run, module, Runtime, Store = fixtureRun(monkeypatch, tmp_path, callDetails=True, snapshotType=Snapshots)
    original = Snapshots.snapshot
    owner = install(run, module)
    run.active = owner
    try:
        store = Store()
        store._connect().close()
        assert not owner.registry[store][1].details
        assert Snapshots.snapshot is original
        plugin.pytest_runtest_call(SimpleNamespace(config=configFor(run), nodeid=owner.nodeid))
        assert owner.allowedRoot is None
    finally:
        owner.retire()


def testDetailedPollAndWalErrorsPreserveOriginalFailures(monkeypatch, tmp_path):
    error = AssertionError("original snapshot")
    helperError = ValueError("original helper")
    calls = []
    class Snapshots:
        def snapshot(self, jobId):
            calls.append(jobId)
            raise error
    def details(*_args):
        raise helperError
    run, module, Runtime, Store = fixtureRun(monkeypatch, tmp_path, details=details,
                                            callDetails=True, snapshotType=Snapshots)
    owner = plugin.FailureOwner(run, module, plugin.DETAIL_TEST)
    owner.install()
    runtime = Runtime(Store())
    try:
        runtime.sqliteStore._connect().close()
        with pytest.raises(AssertionError) as raised:
            Snapshots().snapshot("job")
        assert raised.value is error and calls == ["job"] and owner.pollFailed == 1
        from scripts import r3_sqlite_wal_metadata as wal
        def fail(*_args):
            raise OSError("metadata unavailable")
        monkeypatch.setattr(wal, "captureWalMetadata", fail)
        with pytest.raises(ValueError) as raised:
            module.jobFailureDetails(runtime, "job")
        assert raised.value is helperError
        assert run.records[0]["wal_metadata"] == {"status": "UNAVAILABLE", "error_type": "OSError",
                                                    "cleanup": {"retirement_confirmed": False}}
        assert run.records[0]["helper_exception_type"] == "ValueError"
        monkeypatch.setattr(owner, "recordPoll", fail)
        with pytest.raises(AssertionError) as raised:
            Snapshots().snapshot("again")
        assert raised.value is error and calls == ["job", "again"]
        assert "existing_poll:OSError" in owner.errors
    finally:
        owner.retire()
    assert not run.records[0]["observer_retired"] and not run.metadataRetired
    assert "wal_metadata_cleanup_unconfirmed" in run.invalid


def testDetailedDormantSnapshotOnlyForwardsAfterRetirement(monkeypatch, tmp_path):
    calls = []
    class Snapshots:
        def snapshot(self, jobId):
            calls.append(jobId)
            return jobId
    run, module, Runtime, Store = fixtureRun(monkeypatch, tmp_path, callDetails=True, snapshotType=Snapshots)
    owner = plugin.FailureOwner(run, module, plugin.DETAIL_TEST)
    owner.install()
    wrapper = Snapshots.snapshot
    owner.retire()
    assert wrapper(Snapshots(), "later") == "later"
    assert calls == ["later"] and not owner.pollCount and not owner.pollStores


def testDetailedSnapshotReturningAfterRetirementAddsNoEndClock(monkeypatch, tmp_path):
    entered, release = threading.Event(), threading.Event()
    marker, clocks = object(), []
    class Snapshots:
        def snapshot(self, jobId):
            entered.set()
            assert release.wait(2)
            return marker
    run, module, Runtime, Store = fixtureRun(monkeypatch, tmp_path, callDetails=True, snapshotType=Snapshots)
    owner = plugin.FailureOwner(run, module, plugin.DETAIL_TEST)
    owner.install()
    monkeypatch.setattr(plugin, "_pollClock", lambda: clocks.append(1) or 1)
    def retire():
        assert entered.wait(2)
        owner.retire()
        release.set()
    with ThreadPoolExecutor(1) as executor:
        pending = executor.submit(retire)
        try:
            assert Snapshots().snapshot("job") is marker
            pending.result(2)
        finally:
            release.set()
            owner.retire()
    assert clocks == [1] and not owner.pollCount and not owner.pollRows


def testUnconfirmedWalCleanupInvalidatesRetirementWithoutRetry(monkeypatch, tmp_path):
    run, module, Runtime, Store = fixtureRun(monkeypatch, tmp_path, callDetails=True)
    owner = plugin.FailureOwner(run, module, plugin.DETAIL_TEST)
    owner.install()
    run.active = owner
    run.started = 1
    runtime = Runtime(Store())
    runtime.sqliteStore._connect().close()
    from scripts import r3_sqlite_wal_metadata as wal
    calls = []
    def failedCleanup(*_args):
        calls.append(1)
        return {"status": "UNAVAILABLE", "cleanup": {
            "opened": 2, "close_attempted": 2, "close_failures": 1, "retirement_confirmed": False}}
    monkeypatch.setattr(wal, "captureWalMetadata", failedCleanup)
    try:
        assert module.jobFailureDetails(runtime, "job") == "cached-only details"
    finally:
        assert owner.retire()  # Python patches retired; ambiguous native close is not retried.
        run.active = None
    session = SimpleNamespace(config=configFor(run), exitstatus=1)
    plugin.pytest_sessionfinish(session, 1)
    payload = json.loads(run.path.read_text())
    assert calls == [1] and session.exitstatus == 1
    assert payload["status"] == "INVALID" and not payload["observer_retired"]
    assert not payload["failures"][0]["observer_retired"] and run.pending is None


def install(run, module):
    owner = plugin.FailureOwner(run, module, plugin.MODULES[0] + "::testExample")
    owner.install()
    return owner


def finish(hook):
    with pytest.raises(StopIteration):
        hook.send(None)


def configFor(run):
    return SimpleNamespace(stash={plugin._RUN: run},
                           pluginmanager=SimpleNamespace(getplugin=lambda _name: None))


def itemFor(run, module):
    return SimpleNamespace(config=configFor(run), module=module,
                           nodeid=plugin.MODULES[0] + "::testExample")


def testWeakStoresRetireImmediatelyWithoutGCAndTokensNeverReuse(monkeypatch, tmp_path):
    run, module, Runtime, Store = fixtureRun(monkeypatch, tmp_path)
    owner = install(run, module)
    wasEnabled = gc.isenabled()
    gc.disable()
    try:
        store = Store()
        runtime = Runtime(store)
        refs = weakref.ref(store), weakref.ref(runtime)
        connection = store._connect()
        assert isinstance(connection, _Connection)
        token = owner.registry[store][0]
        assert owner.registry[store][1].patch is None
        connection.close()
        del runtime, store, connection
        assert [ref() for ref in refs] == [None, None]
        assert not owner.registry
        replacement = Store()
        replacement._connect().close()
        assert owner.registry[replacement][0] > token
        assert owner.retire() and not owner.registry
    finally:
        owner.retire()
        if wasEnabled:
            gc.enable()


def testNativeCommitRollbackContextFailureAndNoImplicitClose(monkeypatch, tmp_path):
    native = sqlite3.connect(":memory:")
    calls = []
    run, module, Runtime, Store = fixtureRun(monkeypatch, tmp_path,
        connect=lambda store: (calls.append("connect"), native)[1])
    owner = install(run, module)
    store = Store()
    try:
        connection = store._connect()
        assert calls == ["connect"]
        with connection as entered:
            assert entered is connection
            cursor = entered.execute("CREATE TABLE test (value INTEGER)")
            assert type(cursor) is sqlite3.Cursor
            entered.execute("INSERT INTO test VALUES (1)")
        assert native.execute("SELECT * FROM test").fetchall() == [(1,)]
        failure = ValueError("same native context failure")
        with pytest.raises(ValueError) as raised:
            with connection:
                connection.execute("INSERT INTO test VALUES (2)")
                raise failure
        assert raised.value is failure
        assert native.execute("SELECT * FROM test").fetchall() == [(1,)]
        connection.execute("INSERT INTO test VALUES (3)")
        connection.rollback()
        connection.execute("INSERT INTO test VALUES (4)")
        connection.commit()
        assert native.execute("SELECT * FROM test").fetchall() == [(1,), (4,)]
        snapshot = owner.registry[store][1].snapshot()
        assert "explicit_close" not in snapshot["stages"]
        assert snapshot["stages"]["context_exit"]["entered"] == 2
        assert snapshot["stages"]["rollback"]["returned"] == 1
        assert owner.retire()
        assert native.execute("SELECT count(*) FROM test").fetchone() == (2,)
    finally:
        owner.retire()
        native.close()


def testConcurrentLiveQuotaNeverHoldsLockAcrossOrSkipsNativeCall(monkeypatch, tmp_path):
    barrier = threading.Barrier(2)
    calls = []
    def connect(store):
        assert not owner.lock.locked()
        calls.append(1)
        barrier.wait(timeout=2)
        return sqlite3.connect(":memory:", check_same_thread=False)
    run, module, Runtime, Store = fixtureRun(monkeypatch, tmp_path, connect=connect)
    owner = install(run, module)
    owner.MAX_LIVE = 1
    stores = [Store(), Store()]
    try:
        with ThreadPoolExecutor(2) as executor:
            connections = list(executor.map(lambda store: store._connect(), stores))
        assert calls == [1, 1] and len(owner.registry) == 1
        assert owner.gaps == {0: "store_registry_quota"}
        assert sum(type(connection) is _Connection for connection in connections) == 1
        for connection in connections:
            connection.close()
        module.jobFailureDetails(Runtime(stores[0]), "job")
        assert run.records[0]["status"] == "UNOBSERVED"
        assert "store_registry_quota" in run.records[0]["gaps"]
    finally:
        owner.retire()


def testNativeContextCommitFailureRollsBackAndLeavesConnectionOpen(monkeypatch, tmp_path):
    native = sqlite3.connect(":memory:")
    native.execute("PRAGMA foreign_keys=ON")
    native.execute("CREATE TABLE parent (id INTEGER PRIMARY KEY)")
    native.execute("CREATE TABLE child (parent_id INTEGER REFERENCES parent(id) DEFERRABLE INITIALLY DEFERRED)")
    run, module, Runtime, Store = fixtureRun(monkeypatch, tmp_path, connect=lambda store: native)
    owner = install(run, module)
    store = Store()
    try:
        connection = store._connect()
        with pytest.raises(sqlite3.IntegrityError):
            with connection:
                connection.execute("INSERT INTO child VALUES (9)")
        assert not native.in_transaction
        assert native.execute("SELECT * FROM child").fetchall() == []
        phases = owner.registry[store][1].snapshot()
        assert phases["stages"]["context_exit"]["failed"] == 1
        assert "explicit_close" not in phases["stages"]
    finally:
        owner.retire()
        native.close()


def testLifetimeQuotaAndNativeExceptionAreNeverRetried(monkeypatch, tmp_path):
    failure = sqlite3.OperationalError("same failure")
    calls = []
    def connect(store):
        calls.append(1)
        raise failure
    run, module, Runtime, Store = fixtureRun(monkeypatch, tmp_path, connect=connect)
    owner = install(run, module)
    owner.MAX_LIFETIMES = 1
    try:
        for _ in range(3):
            store = Store()
            with pytest.raises(sqlite3.OperationalError) as raised:
                store._connect()
            assert raised.value is failure
            del store
        assert len(calls) == 3 and owner.lifetimes == 1
        assert owner.gaps[0] == "store_registry_quota"
    finally:
        owner.retire()


@pytest.mark.parametrize("kind", ["subtype", "proxy", "unsupported"])
def testUnsupportedConnectionsPassThroughUnchanged(monkeypatch, tmp_path, kind):
    class Subtype(sqlite3.Connection):
        pass
    native = sqlite3.connect(":memory:", factory=Subtype)
    result = native if kind == "subtype" else SimpleNamespace(native=native) if kind == "proxy" else None
    run, module, Runtime, Store = fixtureRun(monkeypatch, tmp_path, connect=lambda store: result)
    owner = install(run, module)
    store = Store()
    try:
        assert store._connect() is result
        assert module.jobFailureDetails(Runtime(store), "job") == "cached-only details"
        assert run.records[0]["status"] == "UNOBSERVED"
        assert run.records[0]["gaps"] == ["unsupported_connection_type"]
    finally:
        owner.retire()
        native.close()


def testInstanceOverrideIsNotBypassedOrMisreported(monkeypatch, tmp_path):
    run, module, Runtime, Store = fixtureRun(monkeypatch, tmp_path)
    owner = install(run, module)
    store = Store()
    store._connect = lambda: "instance override"
    try:
        assert store._connect() == "instance override" and not owner.registry
        module.jobFailureDetails(Runtime(store), "job")
        assert run.records[0]["gaps"] == ["instance_connect_override"]
        assert run.records[0]["status"] == "UNOBSERVED"
        assert owner.retire()
        assert store._connect() == "instance override"
    finally:
        owner.retire()


def testNoFailureHasZeroWritesIncludingRetirementAndSessionFinish(monkeypatch, tmp_path):
    run, module, Runtime, Store = fixtureRun(monkeypatch, tmp_path)
    run.started = run.selected
    with monkeypatch.context() as patch:
        patch.setattr(Path, "open", lambda *_args, **_kwargs: pytest.fail("unexpected file I/O"))
        owner = install(run, module)
        Store()._connect().close()
        assert owner.retire()
        session = SimpleNamespace(config=configFor(run), exitstatus=0)
        plugin.pytest_sessionfinish(session, 0)
        assert session.exitstatus == 0
    assert not run.path.exists() and not run.records
    assert run.payload()["status"] == "NO_FAILURE_OBSERVED"


def testBothHelperAliasesPreserveReturnAndSameException(monkeypatch, tmp_path):
    sentinel = object()
    failure = AssertionError("same helper failure")
    calls = []
    def details(runtime, job, status, result):
        calls.append(job)
        if job == "raises":
            raise failure
        return sentinel
    run, module, Runtime, Store = fixtureRun(monkeypatch, tmp_path, details=details)
    raw = run.rawConnect
    owner = install(run, module)
    runtime = Runtime(Store())
    runtime.sqliteStore._connect().close()
    assert module.jobFailureDetails is run.helper.jobFailureDetails
    try:
        assert module.jobFailureDetails(runtime, "returns") is sentinel
        with pytest.raises(AssertionError) as raised:
            module.waitForTerminal(runtime, "raises")
        assert raised.value is failure and calls == ["returns", "raises"]
        assert len(run.records) == 2 and run.path.is_file()
        assert run.records[1]["helper_exception_type"] == "AssertionError"
    finally:
        owner.retire()
    assert vars(Store)["_connect"] is raw
    assert module.jobFailureDetails is run.helper.jobFailureDetails is run.originalDetails


def testFakeRuntimeAndUntrackedStoreStayExplicitlyUnobserved(monkeypatch, tmp_path):
    run, module, Runtime, Store = fixtureRun(monkeypatch, tmp_path)
    owner = install(run, module)
    try:
        store = Store()
        store._connect().close()
        module.jobFailureDetails(SimpleNamespace(sqliteStore=store), "fake")
        module.jobFailureDetails(Runtime(Store()), "untracked")
        module.jobFailureDetails(Runtime(None), "missing")
        assert [row["gaps"] for row in run.records] == [
            ["not_exact_runtime_type"], ["untracked_store"], ["missing_or_unsupported_store"]]
        assert all(row["status"] == "UNOBSERVED" and "store_token" not in row for row in run.records)
    finally:
        owner.retire()


@pytest.mark.parametrize("target", ["class", "helper", "alias"])
def testThirdPartyReplacementsAreNeverOverwritten(monkeypatch, tmp_path, target):
    run, module, Runtime, Store = fixtureRun(monkeypatch, tmp_path)
    owner = install(run, module)
    destination, name = ((Store, "_connect") if target == "class" else
                         (run.helper if target == "helper" else module, "jobFailureDetails"))
    def replacement(*_args):
        return "third party"
    setattr(destination, name, replacement)
    assert owner.retire()
    assert vars(destination)[name] is replacement
    assert run.payload()["status"] == "INVALID"
    assert "third_party_replacement:" + name in owner.errors


@pytest.mark.parametrize("phase", ["setup", "call", "teardown"])
def testAllPytestFailurePhasesRetireAndKeepOriginalFailure(monkeypatch, tmp_path, phase):
    run, module, Runtime, Store = fixtureRun(monkeypatch, tmp_path)
    item = itemFor(run, module)
    hook = plugin.pytest_runtest_protocol(item, None)
    next(hook)
    owner = run.active
    assert vars(Store)["_connect"] is not run.rawConnect
    reportHook = plugin.pytest_runtest_makereport(item, None)
    next(reportHook)
    report = SimpleNamespace(when=phase, outcome="failed")
    with pytest.raises(StopIteration) as returned:
        reportHook.send(report)
    assert returned.value.value is report
    failure = RuntimeError("exact original " + phase)
    with pytest.raises(RuntimeError) as raised:
        hook.throw(failure)
    assert raised.value is failure
    assert not owner.patches and run.active is None and vars(Store)["_connect"] is run.rawConnect
    assert run.uncoveredFailures == 1 and not run.path.exists()
    session = SimpleNamespace(config=item.config, exitstatus=2)
    plugin.pytest_sessionfinish(session, 2)
    assert session.exitstatus == 2 and run.report["original_pytest_exit"] == 2


def testSetupSkipAndInterruptionRestoreRawAttributes(monkeypatch, tmp_path):
    for interrupt in (False, True):
        run, module, Runtime, Store = fixtureRun(monkeypatch, tmp_path)
        item = itemFor(run, module)
        hook = plugin.pytest_runtest_protocol(item, None)
        next(hook)
        run.active.outcomes["setup"] = "skipped"
        if interrupt:
            error = KeyboardInterrupt()
            with pytest.raises(KeyboardInterrupt) as raised:
                hook.throw(error)
            assert raised.value is error
        else:
            finish(hook)
        assert vars(Store)["_connect"] is run.rawConnect and not run.active
        assert module.jobFailureDetails is run.originalDetails and not run.path.exists()


@pytest.mark.parametrize("permanent", [False, True])
def testFailedRestorationKeepsOneOwnerForExactlyOneSessionRetry(monkeypatch, tmp_path, permanent):
    attempts, armed = [], [False]
    class Meta(type):
        def __setattr__(cls, name, value):
            if name == "_connect" and value is run.rawConnect and armed[0]:
                attempts.append(1)
                if permanent or len(attempts) == 1:
                    raise OSError("restore unavailable")
            super().__setattr__(name, value)
    run, module, Runtime, Store = fixtureRun(monkeypatch, tmp_path, metaclass=Meta)
    item = itemFor(run, module)
    hook = plugin.pytest_runtest_protocol(item, None)
    next(hook)
    owner = run.active
    runtime = Runtime(Store())
    runtime.sqliteStore._connect().close()
    module.jobFailureDetails(runtime, "job")
    armed[0] = True
    finish(hook)
    assert run.pending is owner and len(owner.patches) == 1 and len(attempts) == 1
    # While that patch still exists, do not stack another owner. The dormant
    # wrapper still calls the original exactly once without new observations.
    nextHook = plugin.pytest_runtest_protocol(item, None)
    next(nextHook)
    runtime.sqliteStore._connect().close()
    finish(nextHook)
    assert run.pending is owner and run.started == 1
    session = SimpleNamespace(config=item.config, exitstatus=0)
    plugin.pytest_sessionfinish(session, 0)
    assert len(attempts) == 2 and session.exitstatus == pytest.ExitCode.TESTS_FAILED
    assert (run.pending is owner) is permanent
    assert run.report["restoration_retry"] == ("failed" if permanent else "succeeded")
    plugin.pytest_sessionfinish(session, session.exitstatus)
    assert len(attempts) == 2
    report = json.loads(run.path.read_text())
    assert report["status"] == "INVALID" and report["failures"][0]["status"] == "INVALID"
    armed[0] = False
    owner.retire()


def testInstallationFailureStillRunsTestAndRetiresPartialPatches(monkeypatch, tmp_path):
    run, module, Runtime, Store = fixtureRun(monkeypatch, tmp_path)
    def replacement(*_args):
        return "existing alias"
    module.jobFailureDetails = replacement
    item = itemFor(run, module)
    hook = plugin.pytest_runtest_protocol(item, None)
    next(hook)
    assert not run.active.active
    Store()._connect().close()
    finish(hook)
    assert module.jobFailureDetails is replacement and run.helper.jobFailureDetails is run.originalDetails
    assert vars(Store)["_connect"] is run.rawConnect
    assert run.invalid and not run.path.exists()


def testProcessTestAndStoreLifetimesAndRawClockReadings(monkeypatch, tmp_path):
    run, module, Runtime, Store = fixtureRun(monkeypatch, tmp_path)
    for _ in range(2):
        owner = install(run, module)
        runtime = Runtime(Store())
        runtime.sqliteStore._connect().close()
        values = iter(({"monotonic_ns": 30, "perf_counter_ns": 100, "thread_cpu_ns": 2},
                       {"monotonic_ns": 10, "perf_counter_ns": 90, "thread_cpu_ns": 1},
                       {"monotonic_ns": 9, "perf_counter_ns": 80, "thread_cpu_ns": 0}))
        monkeypatch.setattr(plugin, "reading", lambda: next(values))
        module.jobFailureDetails(runtime, "job")
        assert owner.retire()
    first, second = run.records
    assert first["test_epoch"] != second["test_epoch"] and first["store_token"] == second["store_token"] == 1
    assert first["process_run_token"] == second["process_run_token"] == run.report["process_run_token"]
    assert first["after_sql_snapshot"]["monotonic_ns"] == 10
    assert first["after_cached_helper"]["thread_cpu_ns"] == 0
    other, *_ = fixtureRun(monkeypatch, tmp_path)
    assert other.report["process_run_token"] != run.report["process_run_token"]
    assert run.report["clocks"]["monotonic"]["resolution"] > 0


def testChangedSourceAndFailureQuotasAreExplicitAndBounded(monkeypatch, tmp_path):
    run, module, Runtime, Store = fixtureRun(monkeypatch, tmp_path)
    run.MAX_RECORDS = 2
    owner = install(run, module)
    runtime = Runtime(Store())
    runtime.sqliteStore._connect().close()
    monkeypatch.setattr(plugin, "sourceHashes", lambda: {"test": "changed"})
    try:
        for _ in range(5):
            module.jobFailureDetails(runtime, "job")
        assert len(run.records) == 2 and run.dropped == 3
        assert "changed_source" in run.invalid and "failure_record_quota" in run.invalid
        assert all(row["status"] == "INVALID" for row in run.records)
        assert run.path.stat().st_size <= run.MAX_BYTES
    finally:
        owner.retire()


def testWriteFailureNeverReplacesOriginalHelperException(monkeypatch, tmp_path):
    failure = ValueError("original helper")
    def details(*_args):
        raise failure
    run, module, Runtime, Store = fixtureRun(monkeypatch, tmp_path, details=details)
    owner = install(run, module)
    def failWrite(*_args, **_kwargs):
        raise OSError("no output")
    monkeypatch.setattr(Path, "open", failWrite)
    try:
        with pytest.raises(ValueError) as raised:
            module.jobFailureDetails(Runtime(Store()), "job")
        assert raised.value is failure and "report_write:OSError" in run.invalid
    finally:
        owner.retire()


def testCollectionChecksAliasesScopeAndNoStackingWithoutFixtures(monkeypatch, tmp_path):
    from tests.runtime import runtime_test_utils as helper
    root = Path(plugin.__file__).resolve().parents[1]
    path = root / plugin.MODULES[0]
    module = SimpleNamespace(__file__=str(path), waitForTerminal=helper.waitForTerminal)
    item = SimpleNamespace(nodeid=plugin.MODULES[0] + "::testExample", path=path, module=module)
    options = {"sqlite_failure_diagnostics": str(tmp_path / "evidence.json")}
    plugins = set()
    config = SimpleNamespace(stash={}, getoption=lambda name, default=None: options.get(name, default),
                             pluginmanager=SimpleNamespace(hasplugin=lambda name: name in plugins,
                                                           get_plugins=lambda: ()))
    for name in plugin.OTHER_PLUGINS:
        plugins.add(name)
        with pytest.raises(pytest.UsageError, match="cannot stack"):
            plugin.pytest_collection_modifyitems(config, [item])
        plugins.clear()
    options[plugin.OTHER_OPTIONS[0]] = "existing.json"
    with pytest.raises(pytest.UsageError, match="cannot stack"):
        plugin.pytest_collection_modifyitems(config, [item])
    del options[plugin.OTHER_OPTIONS[0]]
    excluded = SimpleNamespace(nodeid="tests/runtime/test_failure_diagnostics.py::testFake")
    with pytest.raises(pytest.UsageError, match="allowlisted"):
        plugin.pytest_collection_modifyitems(config, [excluded])
    module.waitForTerminal = lambda: None
    with pytest.raises(pytest.UsageError, match="alias is overridden"):
        plugin.pytest_collection_modifyitems(config, [item])
    module.waitForTerminal = helper.waitForTerminal
    items = [excluded, item]
    plugin.pytest_collection_modifyitems(config, items)
    assert items == [excluded, item] and config.stash[plugin._RUN].selected == 1
    assert not Path(options["sqlite_failure_diagnostics"]).exists()
    with pytest.raises(pytest.UsageError, match="already installed"):
        plugin.pytest_collection_modifyitems(config, items)


def testUnselectedProtocolDoesNothing(monkeypatch, tmp_path):
    run, module, Runtime, Store = fixtureRun(monkeypatch, tmp_path)
    item = itemFor(run, module)
    item.nodeid = "tests/runtime/test_failure_diagnostics.py::testFake"
    hook = plugin.pytest_runtest_protocol(item, None)
    next(hook)
    assert run.active is None and vars(Store)["_connect"] is run.rawConnect
    finish(hook)


def testNativeConnectReturningAfterRetirementIsUnwrappedAndNotClosed(monkeypatch, tmp_path):
    entered, release = threading.Event(), threading.Event()
    native = sqlite3.connect(":memory:", check_same_thread=False)
    calls = []
    def connect(store):
        calls.append(1)
        entered.set()
        assert release.wait(2)
        return native
    run, module, Runtime, Store = fixtureRun(monkeypatch, tmp_path, connect=connect)
    owner = install(run, module)
    store = Store()
    with ThreadPoolExecutor(1) as executor:
        pending = executor.submit(store._connect)
        try:
            assert entered.wait(2)
            assert len(owner.registry) == 1
            assert owner.retire() and not owner.registry
            release.set()
            assert pending.result(2) is native
            assert not owner.registry and calls == [1]
            assert native.execute("SELECT 1").fetchone() == (1,)
        finally:
            release.set()
            owner.retire()
            native.close()


def testDormantUnrestoredHelperOnlyForwardsWithoutOldEpochWrites(monkeypatch, tmp_path):
    calls, armed = [], [False]
    def details(runtime, job, status, result):
        calls.append(job)
        return job
    run, module, Runtime, Store = fixtureRun(monkeypatch, tmp_path, details=details)
    class Alias(SimpleNamespace):
        def __setattr__(self, name, value):
            if armed[0] and name == "jobFailureDetails" and value is run.originalDetails:
                raise OSError("helper restoration unavailable")
            super().__setattr__(name, value)
    module = Alias(**vars(module))
    owner = install(run, module)
    armed[0] = True
    assert not owner.retire() and len(owner.patches) == 1
    monkeypatch.setattr(run, "save", lambda: pytest.fail("inactive helper wrote output"))
    assert module.jobFailureDetails(Runtime(Store()), "later test") == "later test"
    assert calls == ["later test"] and not run.records and not owner.helperCalls
    armed[0] = False
    assert owner.retire()


def testHelperReturningAfterRetirementDoesNotWriteOrExtendOldRecord(monkeypatch, tmp_path):
    entered, release = threading.Event(), threading.Event()
    def details(runtime, job, status, result):
        entered.set()
        assert release.wait(2)
        return "original return"
    run, module, Runtime, Store = fixtureRun(monkeypatch, tmp_path, details=details)
    owner = install(run, module)
    runtime = Runtime(Store())
    runtime.sqliteStore._connect().close()
    def retireInFlight():
        assert entered.wait(2)
        assert owner.retire()
        release.set()
    with ThreadPoolExecutor(1) as executor:
        pending = executor.submit(retireInFlight)
        try:
            monkeypatch.setattr(run, "save", lambda: pytest.fail("retired helper wrote output"))
            assert module.jobFailureDetails(runtime, "job") == "original return"
            pending.result(2)
            assert len(run.records) == 1 and run.records[0]["observer_retired"]
            assert "cached_job_details" not in run.records[0]
        finally:
            release.set()
            owner.retire()


def testOnlyNewFailureRowsAndSessionFinishWriteAfterEarlierFailure(monkeypatch, tmp_path):
    run, module, Runtime, Store = fixtureRun(monkeypatch, tmp_path)
    run.MAX_RECORDS = 1
    writes = []
    save = run.save
    def countSave():
        writes.append(1)
        save()
    monkeypatch.setattr(run, "save", countSave)
    for index in range(4):
        item = itemFor(run, module)
        hook = plugin.pytest_runtest_protocol(item, None)
        next(hook)
        runtime = Runtime(Store())
        runtime.sqliteStore._connect().close()
        if not index:
            for _ in range(3):
                module.jobFailureDetails(runtime, "same failure")
        finish(hook)
        assert writes == [1]
    run.selected = 4
    session = SimpleNamespace(config=item.config, exitstatus=1)
    plugin.pytest_sessionfinish(session, 1)
    assert writes == [1, 1] and run.dropped == 2
    assert json.loads(run.path.read_text())["status"] == "INVALID"


def testConcurrentOffMainHelpersOnlyForwardAndInvalidateWithoutWriting(monkeypatch, tmp_path):
    barrier, calls = threading.Barrier(2), []
    def details(runtime, job, status, result):
        calls.append(job)
        barrier.wait(timeout=2)
        return job
    run, module, Runtime, Store = fixtureRun(monkeypatch, tmp_path, details=details)
    owner = install(run, module)
    runtime = Runtime(Store())
    try:
        monkeypatch.setattr(run, "save", lambda: pytest.fail("off-main helper wrote output"))
        with ThreadPoolExecutor(2) as executor:
            replies = list(executor.map(lambda job: module.jobFailureDetails(runtime, job), ["one", "two"]))
        assert replies == ["one", "two"] and sorted(calls) == ["one", "two"]
        assert not run.records and not owner.helperCalls and not run.path.exists()
        assert run.payload()["status"] == "INVALID"
        assert run.invalid == ["failure_helper_off_main_thread"]
    finally:
        owner.retire()


def testOffMainInstallationDoesNotPatchOrCapture(monkeypatch, tmp_path):
    run, module, Runtime, Store = fixtureRun(monkeypatch, tmp_path)
    with ThreadPoolExecutor(1) as executor:
        owner = executor.submit(install, run, module).result(2)
    assert not owner.active and not owner.patches
    assert vars(Store)["_connect"] is run.rawConnect and module.jobFailureDetails is run.originalDetails
    assert run.invalid == ["installation_off_main_thread"]


def testRealRuntimeTimeoutCapturesExactStoreAndValidatedFailureReport(tmp_path):
    from emo_master.apps.runtime.context.sqlite_store import SqliteStore
    from emo_master.apps.runtime.grpc_server.service import RuntimeService
    from tests.runtime import runtime_test_utils as helper
    module = SimpleNamespace(jobFailureDetails=helper.jobFailureDetails, waitForTerminal=helper.waitForTerminal)
    run = plugin.FailureRun(tmp_path / "actual-timeout.json", SqliteStore, RuntimeService, helper)
    run.selected = run.started = 1
    owner = install(run, module)
    runtime = RuntimeService(dbPath=tmp_path / "runtime.sqlite3", pluginRootPaths=())
    try:
        assert not run.path.exists()
        with pytest.raises(AssertionError, match="job did not reach a terminal state: absent"):
            module.waitForTerminal(runtime, "absent", timeoutSeconds=0)
        record = json.loads(run.path.read_text())
        assert record["status"] == "FAILURE_OBSERVED"
        row = record["failures"][0]
        assert row["status"] == "OBSERVED" and row["store_token"] == owner.registry[runtime.sqliteStore][0]
        assert json.loads(row["cached_job_details"])["job"] == "absent"
        assert row["sql"]["stages"]["connect_configure"]["returned"] > 0
        assert row["sql"]["enabled"] and not row["gaps"] and not row["errors"]
    finally:
        runtime.close()
        owner.outcomes.update(setup="passed", call="failed", teardown="passed")
        owner.retire()
    run.failedTests = 1
    session = SimpleNamespace(config=configFor(run), exitstatus=1)
    plugin.pytest_sessionfinish(session, 1)
    record = json.loads(run.path.read_text())
    assert record["status"] == "FAILURE_OBSERVED" and record["session_finished"] and record["observer_retired"]
    assert record["source_stable"] and record["original_pytest_exit"] == session.exitstatus == 1
    assert record["failures"][0]["outcomes"] == {"setup": "passed", "call": "failed", "teardown": "passed"}
