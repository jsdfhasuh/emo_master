"""Exact target selection and lifecycle of the optional capture-test observer."""
import gc
import json
from pathlib import Path
from types import SimpleNamespace
import weakref

import pytest

from scripts import r3_sqlite_capture_diagnostics as plugin
from scripts.r3_sqlite_phases import SqlitePhases


class Store:
    def _connect(self):
        raise AssertionError("the observer must not issue SQL or connect")


class Runtime:
    def __init__(self, closes):
        self.closes = closes
        self.sqliteStore = Store()
        self.jobSupervisor = SimpleNamespace(_handles={}, _bridges={}, _terminalEvents=set(),
            _heartbeat={}, _heartbeatMonotonic={}, _heartbeatCells={})
        self.jobRepository = SimpleNamespace(_jobs={})
        self.eventStore = SimpleNamespace(_sequences={}, _terminalSequences={})

    def start(self, jobId):
        self.jobRepository._jobs[jobId] = SimpleNamespace(status="RUNNING", errorCode="", endedAtMs=0)
        self.eventStore._sequences[jobId] = 1

    def close(self):
        self.closes.append("runtime")


class Channel:
    def __init__(self, runtime, closes):
        self.runtime, self.closes = runtime, closes

    def close(self):
        self.closes.append("channel")


def fixtureItem(monkeypatch, tmp_path, *, waitFailure=None, installFailure=False):
    closes, originalCalls, installs = [], [], []
    runtime = Runtime(closes)
    channel = Channel(runtime, closes)

    def install(self, selected, **kwargs):
        installs.append((selected is runtime, kwargs))
        self.runtime = selected
        self.sqlitePhases = SqlitePhases()
        self.sqlitePhases.install(selected.sqliteStore)
        if installFailure:
            raise RuntimeError("injected partial installation failure")
        def details(jobId):
            self.jobId = jobId
            return {}
        self.wrap(selected, "start", "supervisor.start", details=details)

    def wait(*args, **kwargs):
        originalCalls.append((args, kwargs))
        selected = kwargs.get("runtimeService", args[0] if args else None)
        jobId = kwargs.get("jobId", args[1] if len(args) > 1 else None)
        if waitFailure is not None:
            raise waitFailure
        selected.jobRepository._jobs[jobId].status = "COMPLETED"
        selected.eventStore._sequences[jobId] = 14
        return SimpleNamespace(status="COMPLETED")

    monkeypatch.setattr(plugin.CaptureDiagnostics, "install", install)
    monkeypatch.setattr(plugin.subprocess, "check_output", lambda *_args, **_kwargs: "test-head")
    item = SimpleNamespace(nodeid=plugin.TARGET, module=SimpleNamespace(waitForTerminal=wait),
        funcargs={"channel": channel}, stash={},
        config=SimpleNamespace(stash={}, getoption=lambda _name: str(tmp_path / "diagnostic.json")))
    return item, runtime, closes, originalCalls, installs, wait


def finish(hook):
    with pytest.raises(StopIteration):
        hook.send(None)


def finishFixtures(item):
    hook = plugin.pytest_runtest_teardown(item)
    next(hook)
    channel = item.funcargs["channel"]
    channel.runtime.close()
    channel.close()
    finish(hook)


def twoWaits(item, runtime):
    runtime.start("first")
    first = item.module.waitForTerminal(runtime, "first")
    runtime.start("second")
    second = item.module.waitForTerminal(runtimeService=runtime, jobId="second", timeoutSeconds=10.0)
    assert first.status == second.status == "COMPLETED"


def testExistingChannelAndTwoJobsRemainDistinctThroughFixtureTeardown(monkeypatch, tmp_path):
    item, runtime, closes, calls, installs, wait = fixtureItem(monkeypatch, tmp_path)
    hook = plugin.pytest_runtest_call(item)
    next(hook)
    probe = item.stash[plugin._SESSION]
    assert installs == [(True, {"sqlitePhases": True, "workerCounters": False})]
    assert item.funcargs["channel"].runtime is runtime
    # Before/after normal waits and cleanup only collect memory, including a
    # periodic sample. Even a forbidden save must not be attempted here.
    with monkeypatch.context() as patch:
        patch.setattr(probe, "_save", lambda: pytest.fail("pre-retirement disk write"))
        twoWaits(item, runtime)
        probe.capture("periodic", save=False)
        finish(hook)
        assert not probe.closed and "close" in vars(runtime)
        teardown = plugin.pytest_runtest_teardown(item)
        next(teardown)
        runtime.close()
        item.funcargs["channel"].close()
        assert not probe.closed and closes == ["runtime", "channel"]
    finish(teardown)
    assert calls == [((runtime, "first"), {}),
                     ((), {"runtimeService": runtime, "jobId": "second", "timeoutSeconds": 10.0})]
    assert item.module.waitForTerminal is wait and not item.stash
    assert not {"close", "start"} & vars(runtime).keys()
    assert "_connect" not in vars(runtime.sqliteStore)
    record = json.loads((tmp_path / "diagnostic.json").read_text())
    assert record["observer_retired"] and not record["worker_instrumented"] and not record["errors"]
    assert record["source"]["diagnostic_valid"] and record["source"]["fixture_teardown_outcome"] == "passed"
    assert record["capture_cost"]["disk_save_requests"] == 0
    waits = record["source"]["waits"]
    assert [(row["ordinal"], row["job_id"], row["outcome"]) for row in waits] == [
        (1, "first", "returned"), (2, "second", "returned")]
    assert all(row["exit_ns"] >= row["enter_ns"] for row in waits)
    boundaries = [row for row in record["snapshots"] if row["reason"].startswith("terminal_wait_")]
    assert [(row["state"]["job_id_before"], row["state"]["job_id_after"],
             row["frontiers"]["terminal_wait"]["ordinal"], row["state"]["event_store_sequence"])
            for row in boundaries] == [("first", "first", 1, 1), ("first", "first", 1, 14),
                                       ("second", "second", 2, 1), ("second", "second", 2, 14)]
    reasons = [row["reason"] for row in record["snapshots"]]
    assert reasons.index("test_call_finished") < reasons.index("before_runtime_close") < reasons.index("after_runtime_close") < reasons.index("fixture_teardown_finished")


def testUnrequestedOrOtherTestDoesNotResolveFixtureOrCreateEvidence():
    for nodeid, option in ((plugin.TARGET, None), ("other.py::testOther", "new.json")):
        item = SimpleNamespace(nodeid=nodeid, stash={},
            config=SimpleNamespace(getoption=lambda _name: option))
        hook = plugin.pytest_runtest_call(item)
        next(hook)
        finish(hook)
        teardown = plugin.pytest_runtest_teardown(item)
        next(teardown)
        finish(teardown)
        assert not item.stash


def testCollectionRejectsWrongSelectionWithoutChangingOriginalOrder(tmp_path):
    path = tmp_path / "new.json"
    config = SimpleNamespace(getoption=lambda _name: str(path))
    target = SimpleNamespace(nodeid=plugin.TARGET)
    unrelated = SimpleNamespace(nodeid="test_else.py::testOther")
    for items in ([], [unrelated], [target, target]):
        with pytest.raises(pytest.UsageError, match="exactly one"):
            plugin.pytest_collection_modifyitems(config, items)
    items = [unrelated, target]
    plugin.pytest_collection_modifyitems(config, items)
    assert items == [unrelated, target] and not path.exists()
    path.write_text("keep", encoding="utf-8")
    with pytest.raises(pytest.UsageError, match="must be new"):
        plugin.pytest_collection_modifyitems(config, items)
    assert path.read_text() == "keep"
    config.getoption = lambda _name: str(tmp_path / "missing" / "new.json")
    with pytest.raises(pytest.UsageError, match="existing directory"):
        plugin.pytest_collection_modifyitems(config, items)
    config.getoption = lambda _name: None
    plugin.pytest_collection_modifyitems(config, [])


@pytest.mark.parametrize("brokenObserver", (False, True))
def testExactWaitFailureSurvivesObserverFailureAndFixtureCleanup(monkeypatch, tmp_path, brokenObserver):
    failure = AssertionError("exact original wait failure: PRIVATE-PAYLOAD")
    item, runtime, closes, calls, _, wait = fixtureItem(monkeypatch, tmp_path, waitFailure=failure)
    hook = plugin.pytest_runtest_call(item)
    next(hook)
    probe = item.stash[plugin._SESSION]
    if brokenObserver:
        probe.sqlitePhases.enabled = False
    runtime.start("first")
    with pytest.raises(AssertionError) as raised:
        item.module.waitForTerminal(runtime, "first")
    assert raised.value is failure and calls == [((runtime, "first"), {})]
    assert probe.rows[-1]["reason"] == "terminal_wait_failed"
    with pytest.raises(AssertionError) as raised:
        hook.throw(failure)
    assert raised.value is failure
    finishFixtures(item)
    content = (tmp_path / "diagnostic.json").read_text()
    record = json.loads(content)
    assert "PRIVATE-PAYLOAD" not in content
    assert item.module.waitForTerminal is wait and closes == ["runtime", "channel"]
    assert record["source"]["pytest_outcome"] == "failed"
    assert record["source"]["waits"][0]["outcome"] == "failed"
    assert record["source"]["diagnostic_valid"] is not brokenObserver


def testObserverFailureCannotTurnSuccessfulOriginalTestIntoDiagnosticPass(monkeypatch, tmp_path):
    item, runtime, *_ = fixtureItem(monkeypatch, tmp_path)
    hook = plugin.pytest_runtest_call(item)
    next(hook)
    twoWaits(item, runtime)
    item.stash[plugin._SESSION].sqlitePhases.enabled = False
    finish(hook)
    with pytest.raises(pytest.fail.Exception, match="observer failed"):
        finishFixtures(item)
    source = json.loads((tmp_path / "diagnostic.json").read_text())["source"]
    assert source["pytest_outcome"] == "passed" and source["diagnostic_valid"] is False
    assert not item.stash and "close" not in vars(runtime)


def testTeardownFailureSurvivesObserverRetirementFailure(monkeypatch, tmp_path):
    item, runtime, *_ = fixtureItem(monkeypatch, tmp_path)
    hook = plugin.pytest_runtest_call(item)
    next(hook)
    probe = item.stash[plugin._SESSION]
    twoWaits(item, runtime)
    finish(hook)
    teardown = plugin.pytest_runtest_teardown(item)
    next(teardown)
    failure = RuntimeError("exact original fixture teardown failure")
    def brokenSave():
        raise OSError("observer output unavailable")
    monkeypatch.setattr(probe, "_save", brokenSave)
    with pytest.raises(RuntimeError) as raised:
        teardown.throw(failure)
    assert raised.value is failure
    assert probe.source["fixture_teardown_outcome"] == "failed"
    assert not probe.source["diagnostic_valid"] and item.stash[plugin._SESSION] is probe
    assert "close" not in vars(runtime) and "_connect" not in vars(runtime.sqliteStore)
    assert probe.runtime is None
    monkeypatch.undo()
    session = SimpleNamespace(config=item.config, exitstatus=1)
    plugin.pytest_sessionfinish(session)
    assert not item.stash and not item.config.stash and session.exitstatus == 1
    assert not probe.source["diagnostic_valid"]


def testPartialInstallFailureRetiresOriginalStoreAndDoesNotSkipTest(monkeypatch, tmp_path):
    item, runtime, closes, _, _, wait = fixtureItem(monkeypatch, tmp_path, installFailure=True)
    hook = plugin.pytest_runtest_call(item)
    next(hook)
    assert item.module.waitForTerminal is wait
    twoWaits(item, runtime)
    finish(hook)
    with pytest.raises(pytest.fail.Exception, match="observer failed"):
        finishFixtures(item)
    assert closes == ["runtime", "channel"] and "_connect" not in vars(runtime.sqliteStore)
    assert not item.stash


@pytest.mark.parametrize("ownClose", (False, True))
def testFixtureOwnersAndRawAttributesRetireWithoutGC(monkeypatch, tmp_path, ownClose):
    item, runtime, _, calls, *_ = fixtureItem(monkeypatch, tmp_path)
    # Retire the fixture helper's install closure too: it intentionally records
    # identity by referring to this Runtime while installation takes place.
    wasEnabled = gc.isenabled()
    gc.disable()
    try:
        if ownClose:
            runtime.close = lambda: None
        rawClose = vars(runtime).get("close")
        runtimeRef = weakref.ref(runtime)
        storeRef = weakref.ref(runtime.sqliteStore)
        channelRef = weakref.ref(item.funcargs["channel"])
        hook = plugin.pytest_runtest_call(item)
        next(hook)
        twoWaits(item, runtime)
        finish(hook)
        finishFixtures(item)
        assert ("close" in vars(runtime)) is ownClose
        assert vars(runtime).get("close") is rawClose
        assert "_connect" not in vars(runtime.sqliteStore)
        item.funcargs.clear()
        calls.clear()
        monkeypatch.undo()
        del runtime
        assert runtimeRef() is None and storeRef() is None and channelRef() is None
    finally:
        if wasEnabled:
            gc.enable()


def testSnapshotBoundAndCrossJobRaceAreExplicit(monkeypatch, tmp_path):
    probe = plugin.CaptureDiagnostics(tmp_path / "bounded.json", source={}, plannedSeconds=1)
    def changingState(_self):
        probe.jobId = "second"
        return {"job_status": "RUNNING"}
    from scripts.r3_job_diagnostics import JobDiagnostics
    monkeypatch.setattr(JobDiagnostics, "_state", changingState)
    probe.jobId = "first"
    state = probe._state()
    assert state == {"job_status": "RUNNING", "job_id_before": "first", "job_id_after": "second", "job_id_stable": False}
    for _ in range(probe.MAX_SNAPSHOTS + 2):
        probe.capture("periodic", stacks=False, save=False)
    assert len(probe.rows) == probe.MAX_SNAPSHOTS == 64 and probe.evicted == 2
    probe.close()
    assert Path(probe.path).stat().st_size <= probe.MAX_BYTES


@pytest.mark.parametrize("failureKind", ("join", "restore", "save"))
@pytest.mark.parametrize("permanent", (False, True))
def testFailedRetirementKeepsOneOwnerForOneSessionRetry(monkeypatch, tmp_path, failureKind, permanent):
    item, runtime, _, calls, *_ = fixtureItem(monkeypatch, tmp_path)
    hook = plugin.pytest_runtest_call(item)
    next(hook)
    probe = item.stash[plugin._SESSION]
    twoWaits(item, runtime)
    finish(hook)
    attempts = []
    failing = [True]
    originalSave = plugin.CaptureDiagnostics._save

    class Join:
        def join(self, timeout):
            assert timeout == 2
            attempts.append("join")
        def is_alive(self):
            return failing[0] and (permanent or len(attempts) == 1)

    def delete(owner, name):
        if name == "start":
            attempts.append("restore")
            if failing[0] and (permanent or len(attempts) == 1):
                raise RuntimeError("restore unavailable")
        object.__delattr__(owner, name)

    def save(owner):
        attempts.append("save")
        if failing[0] and (permanent or len(attempts) == 1):
            raise OSError("report unavailable")
        return originalSave(owner)

    if failureKind == "join":
        probe.thread = Join()
    elif failureKind == "restore":
        monkeypatch.setattr(Runtime, "__delattr__", delete)
    else:
        monkeypatch.setattr(plugin.CaptureDiagnostics, "_save", save)
    with pytest.raises(pytest.fail.Exception, match="observer failed"):
        finishFixtures(item)
    assert item.stash[plugin._SESSION] is probe
    assert item.config.stash[plugin._RETIREMENT]["probe"] is probe
    assert not probe.source["diagnostic_valid"]
    assert probe.source["retirement_retry"] == "pending"
    if failureKind == "save":
        assert probe.closed and probe.runtime is None
    else:
        assert not probe.closed and probe.runtime is runtime
    session = SimpleNamespace(config=item.config, exitstatus=0)
    plugin.pytest_sessionfinish(session)
    assert session.exitstatus == pytest.ExitCode.TESTS_FAILED
    assert not probe.source["diagnostic_valid"]
    expected = "failed" if permanent else "succeeded"
    assert probe.source["retirement_retry"] == expected
    assert bool(item.stash) is permanent and bool(item.config.stash) is permanent
    recordedAttempts = len(attempts)
    plugin.pytest_sessionfinish(session)
    assert len(attempts) == recordedAttempts  # Never an unbounded retry loop.
    if not permanent or failureKind != "save":
        record = json.loads((tmp_path / "diagnostic.json").read_text())
        assert record["source"]["diagnostic_valid"] is False
        assert record["source"]["retirement_retry"] == expected
        assert record["observer_retired"] is (not permanent)
    # Tests clean up the deliberately permanent failure, after verifying the
    # plugin itself retained its retry owner instead of silently dropping it.
    failing[0] = False
    probe.retire()
    assert probe._ownersRetired() and probe.runtime is None
    wasEnabled = gc.isenabled()
    gc.disable()
    try:
        runtimeRef, storeRef = weakref.ref(runtime), weakref.ref(runtime.sqliteStore)
        channelRef = weakref.ref(item.funcargs["channel"])
        item.funcargs.clear()
        calls.clear()
        monkeypatch.undo()
        del runtime
        assert runtimeRef() is None and storeRef() is None and channelRef() is None
    finally:
        if wasEnabled:
            gc.enable()
