"""Post-failure attribution stays opt-in, bounded and subordinate to A18."""
import ctypes
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from scripts import r3_a18_thread_diagnostics as plugin
from scripts import r3_native_thread_origins as native


def hookFixture(tmp_path, original, *, nodeid=plugin.TARGET, enabled=True):
    output = tmp_path / "origins.json"
    module = SimpleNamespace(assertNoNewThreads=original, nativeCounts=lambda _app, **_ignored: None)
    item = SimpleNamespace(nodeid=nodeid, module=module,
        config=SimpleNamespace(getoption=lambda _name: str(output) if enabled else None, stash=pytest.Stash()))
    return item, output, plugin.pytest_runtest_call(item)


def finish(hook):
    with pytest.raises(StopIteration):
        hook.send(None)


def testPassingAssertionsNeverCollectOrWrite(monkeypatch, tmp_path):
    calls = []
    def original(before, after):
        calls.append((before, after))
        return "original return"
    monkeypatch.setattr(plugin, "collect", lambda _ids: pytest.fail("unexpected collection"))
    item, output, hook = hookFixture(tmp_path, original)
    next(hook)
    assert item.module.assertNoNewThreads("before", "after") == "original return"
    finish(hook)
    assert calls == [("before", "after")] and not output.exists()
    assert item.module.assertNoNewThreads is original


@pytest.mark.parametrize("diagnosticFailure", (False, True))
def testOriginalAssertionObjectSurvivesAndOnlyFailedDifferenceIsCaptured(monkeypatch, tmp_path, diagnosticFailure):
    failure = AssertionError(("native_thread_ids", frozenset((4,))))
    events = []
    before = {"native_thread_ids": frozenset((1, 2, 3))}
    after = {"native_thread_ids": frozenset((1, 4))}
    def original(left, right):
        assert left is before and right is after
        events.append("original")
        raise failure
    def collect(ids):
        events.append(ids)
        if diagnosticFailure:
            raise RuntimeError("private diagnostic error path must not be emitted")
        return {"status": "queried", "threads": [{"native_thread_id": 4, "status": "observed",
                                                "module": "test.dll", "module_offset": 12}]}
    monkeypatch.setattr(plugin, "collect", collect)
    item, output, hook = hookFixture(tmp_path, original)
    next(hook)
    for _ in range(2):
        with pytest.raises(AssertionError) as raised:
            item.module.assertNoNewThreads(before, after)
        assert raised.value is failure
    with pytest.raises(AssertionError) as raised:
        hook.throw(failure)
    assert raised.value is failure and item.module.assertNoNewThreads is original
    assert events == ["original", [4], "original"]
    if diagnosticFailure:
        assert not output.exists()
    else:
        record = json.loads(output.read_text())
        assert record["schema_version"] == 2 and record["observer_retired"] is True
        assert record["snapshot_match"] == "unknown"
        assert record["selected_native_thread_ids"] == [4]
        assert record["new_native_thread_count"] == 1
        assert record["evidence"]["threads"][0]["module"] == "test.dll"
        assert output.stat().st_size < native.MAX_BYTES


def testNewOutputIsNeverOverwrittenAndOriginalFailureSurvives(monkeypatch, tmp_path):
    failure = AssertionError(("native_thread_ids", frozenset((7,))))
    def original(*_args):
        raise failure
    monkeypatch.setattr(plugin, "collect", lambda _ids: {"status": "helper_timeout", "threads": []})
    item, output, hook = hookFixture(tmp_path, original)
    next(hook)
    output.write_text("preserve existing")
    with pytest.raises(AssertionError) as raised:
        item.module.assertNoNewThreads({"native_thread_ids": set()}, {"native_thread_ids": {7}})
    assert raised.value is failure and output.read_text() == "preserve existing"
    finish(hook)


@pytest.mark.parametrize("nodeid,enabled", ((plugin.TARGET, False), ("other.py::testOther", True)))
def testOnlyExplicitExactTargetIsWrapped(tmp_path, nodeid, enabled):
    original = object()
    item, output, hook = hookFixture(tmp_path, original, nodeid=nodeid, enabled=enabled)
    next(hook)
    assert item.module.assertNoNewThreads is original
    finish(hook)
    assert not output.exists()


def testCollectionRejectsMissingTargetAndExistingOutput(tmp_path):
    path = tmp_path / "origins.json"
    config = SimpleNamespace(getoption=lambda _name: str(path))
    with pytest.raises(pytest.UsageError, match="exactly one"):
        plugin.pytest_collection_modifyitems(config, [])
    path.write_text("preserve")
    with pytest.raises(pytest.UsageError, match="must be new"):
        plugin.pytest_collection_modifyitems(config, [SimpleNamespace(nodeid=plugin.TARGET)])
    assert path.read_text() == "preserve"


def fakeWindows(monkeypatch):
    monkeypatch.setattr(native, "os", SimpleNamespace(name="nt", getpid=lambda: 200, getppid=lambda: 100))


def testHelperRejectsUnrelatedPidBeforeLoadingWindows(monkeypatch):
    fakeWindows(monkeypatch)
    monkeypatch.setattr(native, "WindowsQuery", lambda: pytest.fail("native call before validation"))
    for pid, ids in ((999, [7]), (200, [7]), (0, [7]), (True, [7]),
                     (100, [7] * 17), (100, [7, 7]), (100, [0]), (100, [True]),
                     (100, [0x100000000]), (100, [])):
        with pytest.raises(ValueError):
            native.queryParent(pid, ids)


def testHelperUsesOnlyParentModulesAndRequestedThreads(monkeypatch):
    fakeWindows(monkeypatch)
    calls = []
    def modules(pid):
        calls.append(("modules", pid))
        return [(1000, 100, "test.dll")], "complete"
    def thread(tid, pid, modules):
        calls.append(("thread", tid, pid))
        return {"native_thread_id": tid, "status": "observed", "module": modules[0][2]}
    monkeypatch.setattr(native, "WindowsQuery", lambda: SimpleNamespace(modules=modules, thread=thread))
    result = native.queryParent(100, [7, 8])
    assert calls == [("modules", 100), ("thread", 7, 100), ("thread", 8, 100)]
    assert result["module_snapshot_status"] == "complete" and len(result["threads"]) == 2


@pytest.mark.parametrize("outcome,status", (("timeout", "helper_timeout"),
    ("failed", "helper_failed"), ("oversize", "helper_output_limit"),
    ("invalid", "helper_unavailable"), ("success", "queried")))
def testHelperTimeoutAndOutputBounds(monkeypatch, outcome, status):
    fakeWindows(monkeypatch)
    def run(command, **options):
        assert command[:3] == [native.sys.executable, "-I", "-S"]
        assert Path(command[3]).name == "r3_native_thread_origins.py"
        assert command[4] == "200" and command[5:] == [str(value) for value in range(1, 17)]
        assert options == {"stdin": subprocess.DEVNULL, "stdout": subprocess.PIPE,
            "stderr": subprocess.DEVNULL, "timeout": native.HELPER_SECONDS, "check": False}
        if outcome == "timeout":
            raise subprocess.TimeoutExpired(command, native.HELPER_SECONDS)
        output = {"success": b'{"status":"queried","threads":[]}', "invalid": b'not json',
                  "oversize": b'x' * (native.MAX_BYTES + 1), "failed": b''}[outcome]
        return SimpleNamespace(returncode=1 if outcome == "failed" else 0, stdout=output)
    monkeypatch.setattr(native.subprocess, "run", run)
    assert native.collect(range(1, 30))["status"] == status


class NativeFunction:
    def __init__(self, call):
        self.call = call
    def __call__(self, *args):
        return self.call(*args)


def windowsApi(monkeypatch, *, owner=100, queryStatus=0, address=1012, openHandle=77,
               queryRaises=False, modulesAvailable=True):
    closed, queries = [], []
    def query(handle, infoClass, buffer, size, returned):
        queries.append(handle)
        assert infoClass == 9 and size == ctypes.sizeof(ctypes.c_void_p) and returned is None
        if queryRaises:
            raise OSError("private path")
        buffer._obj.value = address
        return queryStatus
    def opened(access, inherited, tid):
        assert access == 0x0040 and inherited is False and tid == 7
        return openHandle
    def times(_handle, creation, *_unused):
        ticks = 116444736000000000 + 123
        creation._obj.high, creation._obj.low = ticks >> 32, ticks & 0xffffffff
        return 1
    def first(_snapshot, entry):
        assert entry._obj.dwSize == ctypes.sizeof(native.ModuleEntry)
        if not modulesAvailable:
            return 0
        entry._obj.th32ProcessID = 100
        entry._obj.modBaseAddr, entry._obj.modBaseSize = 1000, 100
        encoded = "test.dll\0".encode("utf-16-le")
        ctypes.memmove(entry._obj.szModule, encoded, len(encoded))
        return 1
    functions = {"OpenThread": opened, "GetProcessIdOfThread": lambda _h: owner,
        "GetThreadTimes": times, "CloseHandle": lambda handle: closed.append(handle) or 1,
        "CreateToolhelp32Snapshot": lambda flags, pid: 88 if flags == 0x18 and pid == 100 else -1,
        "Module32FirstW": first, "Module32NextW": lambda *_a: 0,
        "NtQueryInformationThread": query}
    api = SimpleNamespace(**{name: NativeFunction(call) for name, call in functions.items()})
    monkeypatch.setattr(ctypes, "WinDLL", lambda *_a, **_kw: api, raising=False)
    monkeypatch.setattr(ctypes, "get_last_error", lambda: 18, raising=False)
    return native.WindowsQuery(), closed, queries


def testWindowsAbiAndOnlyBasenameOffsetLeaveNativeCalls(monkeypatch):
    query, closed, queries = windowsApi(monkeypatch)
    assert ctypes.sizeof(native.ModuleEntry) == (1080 if ctypes.sizeof(ctypes.c_void_p) == 8 else 1064)
    assert ctypes.sizeof(native.FileTime) == 8
    modules, status = query.modules(100)
    row = query.thread(7, 100, modules)
    assert closed == [88, 77] and queries == [77] and status == "complete"
    assert row["module"] == "test.dll" and row["module_offset"] == 12
    assert row["creation_unix_ns"] == 12300
    assert set(row) == {"native_thread_id", "query_unix_ns", "creation_unix_ns", "status", "module", "module_offset"}
    assert query.kernel.OpenThread.restype is ctypes.c_void_p
    assert query.nt.NtQueryInformationThread.restype is ctypes.c_int32
    assert native.moduleBasename("C:\\private\\module.dll") == "module.dll"


@pytest.mark.parametrize("options,status,queried,closed", (
    ({"owner": 999}, "owner_mismatch", [], [77]),
    ({"owner": 0}, "owner_query_failed", [], [77]),
    ({"openHandle": 0}, "thread_unavailable", [], []),
    ({"queryStatus": -1}, "start_query_failed", [77], [77]),
    ({"address": 9000}, "module_not_found", [77], [77]),
))
def testThreadRetirementReuseAndMissingModuleDoNotLeakHandles(monkeypatch, options, status, queried, closed):
    query, handles, queries = windowsApi(monkeypatch, **options)
    row = query.thread(7, 100, [(1000, 100, "test.dll")])
    assert row["status"] == status and queries == queried and handles == closed
    assert "module" not in row


def testNativeExceptionStillClosesAcquiredThreadHandle(monkeypatch):
    query, closed, _ = windowsApi(monkeypatch, queryRaises=True)
    with pytest.raises(OSError):
        query.thread(7, 100, [])
    assert closed == [77]


def testEmptyModuleSnapshotClosesHandle(monkeypatch):
    query, closed, _ = windowsApi(monkeypatch, modulesAvailable=False)
    assert query.modules(100) == ([], "complete")
    assert closed == [88]


def countSnapshot(ids, **changes):
    return {"handles": 3, "native_threads": len(ids), "python_threads": 1,
            "widgets": 2, "windows": 1, "native_thread_ids": frozenset(ids),
            "python_thread_objects": frozenset(), **changes}


def nativeAssertion(before, after):
    assert after["native_thread_ids"] <= before["native_thread_ids"], (
        "native_thread_ids", after["native_thread_ids"] - before["native_thread_ids"])
    assert after["python_thread_objects"] <= before["python_thread_objects"], (
        "python_thread_objects", after["python_thread_objects"] - before["python_thread_objects"])


def checkpointSchedule():
    return [("pre_fixture_baseline", None), ("warmed_fixture_baseline", None),
        ("stable_initial", None)] + [("navigation", value) for value in range(99, 1000, 100)] + [
        ("floating", value) for value in range(30)] + [("hidden_observers", None),
        ("owners_closed", None), ("fixture_released", None)]


def testExistingCheckpointScheduleAndFailureSamplesAreExact(monkeypatch, tmp_path):
    item, output, hook = hookFixture(tmp_path, nativeAssertion)
    calls = []
    snapshots = []
    def counts(app, **keywords):
        calls.append((app, keywords))
        snapshot = countSnapshot((1,) if len(calls) in (1, 45) else (1, 2))
        if len(calls) == 46:
            snapshot = countSnapshot((1, 7))
        snapshots.append(snapshot)
        return snapshot
    item.module.nativeCounts = counts
    ticks = iter(range(1000, 2000))
    monkeypatch.setattr(plugin, "reading", lambda: (next(ticks), next(ticks)))
    collection = []
    monkeypatch.setattr(plugin, "collect", lambda ids: collection.append(ids) or {
        "status": "queried", "threads": [{"native_thread_id": 7,
        "creation_unix_ns": 123, "query_unix_ns": 456, "module": "observed.dll"}]})
    next(hook)
    schedule = checkpointSchedule()
    for number, (phase, index) in enumerate(schedule):
        result = item.module.nativeCounts("app", phase=phase, index=index)
        assert result is snapshots[-1]
        if number < 2:
            continue
        before = snapshots[0 if number >= 44 else 1]
        if number == 45:
            with pytest.raises(AssertionError) as raised:
                item.module.assertNoNewThreads(before, result)
            failure = raised.value
        else:
            item.module.assertNoNewThreads(before, result)
    with pytest.raises(AssertionError) as raised:
        hook.throw(failure)
    assert raised.value is failure and item.module.nativeCounts is counts
    record = json.loads(output.read_text())
    assert collection == [[7]] and len(calls) == 46
    assert [(row["phase"], row["index"]) for row in record["checkpoints"]] == schedule
    assert record["before_sample"] == 0 and record["after_sample"] == 45
    assert record["snapshot_match"] == "live_argument_dict_id"
    assert record["checkpoint_calls"] == 46 and record["omitted_checkpoint_count"] == 0
    assert record["first_observations"] == [{"native_thread_id": 7, "first_observed_sample": 45,
        "earlier_checkpoint_absence": "observed", "checkpoint_identity": "unverified"}]
    assert record["evidence"]["threads"][0]["creation_unix_ns"] == 123
    assert record["evidence"]["threads"][0]["query_unix_ns"] == 456
    assert record["observer_retired"] is True and record["errors"] == []
    assert record["checkpoints"][0]["entry_monotonic_ns"] == 1000
    assert record["checkpoints"][0]["exit_unix_ns"] == 1003


def testSuccessfulCountsDoNotRetainThreadOrOtherSnapshotObjects(monkeypatch, tmp_path):
    import threading
    import weakref
    item, output, hook = hookFixture(tmp_path, nativeAssertion)
    thread = threading.Thread()
    reference = weakref.ref(thread)
    snapshot = countSnapshot((1,), python_thread_objects=frozenset((thread,)))
    def counts(_app, **_ignored):
        return snapshot
    item.module.nativeCounts = counts
    monkeypatch.setattr(plugin, "collect", lambda _ids: pytest.fail("successful capture"))
    next(hook)
    result = item.module.nativeCounts(None, phase="pre_fixture_baseline")
    assert result is snapshot
    snapshot = None
    del thread, result
    assert reference() is None  # No GC rescue and no retained original snapshot.
    finish(hook)
    assert not output.exists()


@pytest.mark.parametrize("detail", (("python_thread_objects", frozenset()),
    ("native_threads", 1, 2), "arbitrary original failure"))
def testNonNativeIdentityFailureDoesNotCapture(monkeypatch, tmp_path, detail):
    failure = AssertionError(detail)
    def original(*_args):
        raise failure
    monkeypatch.setattr(plugin, "collect", lambda _ids: pytest.fail("wrong failure kind"))
    item, output, hook = hookFixture(tmp_path, original)
    next(hook)
    with pytest.raises(AssertionError) as raised:
        item.module.assertNoNewThreads(countSnapshot((1,)), countSnapshot((1,)))
    assert raised.value is failure
    finish(hook)
    assert not output.exists()


def testOnlyLatestTemporarySnapshotMayMatchFailure(monkeypatch, tmp_path):
    item, output, hook = hookFixture(tmp_path, nativeAssertion)
    snapshots = iter((countSnapshot((1,)), countSnapshot((1, 7)), countSnapshot((1, 8))))
    item.module.nativeCounts = lambda _app, **_ignored: next(snapshots)
    monkeypatch.setattr(plugin, "collect", lambda _ids: {"status": "queried", "threads": []})
    next(hook)
    baseline = item.module.nativeCounts(None, phase="pre_fixture_baseline")
    old = item.module.nativeCounts(None, phase="stable_initial")
    item.module.nativeCounts(None, phase="navigation", index=99)
    with pytest.raises(AssertionError):
        item.module.assertNoNewThreads(baseline, old)
    finish(hook)
    record = json.loads(output.read_text())
    assert record["before_sample"] == 0 and record["after_sample"] is None
    assert record["snapshot_match"] == "unknown"


def testCheckpointAndIdentityBudgetsKeepBaselinesAndMarkUnknown(monkeypatch, tmp_path):
    module = SimpleNamespace()
    owner = plugin.PhaseOwner(module, str(tmp_path / "bounded.json"))
    baseline = countSnapshot(range(1, 140))
    owner.sample(baseline, "pre_fixture_baseline", None, (1, 2), (3, 4))
    for index in range(1, 65):
        last = countSnapshot((1, 500))
        owner.sample(last, "floating", index, (5, 6), (7, 8))
    monkeypatch.setattr(plugin, "collect", lambda _ids: {"status": "queried", "threads": []})
    owner.capture(baseline, last)
    owner.retire()
    owner.save()
    record = json.loads(Path(owner.output).read_text())
    assert len(record["checkpoints"]) == 64 and record["checkpoint_calls"] == 65
    assert record["omitted_checkpoint_count"] == 1 and record["before_sample"] == 0
    assert record["after_sample"] is None and record["snapshot_match"] == "unknown"
    first = record["checkpoints"][0]
    assert first["phase"] == "pre_fixture_baseline"
    assert len(first["native_thread_ids"]) == 128 and first["omitted_native_thread_count"] == 11
    assert first["native_ids_status"] == "truncated"
    assert record["first_observations"][0]["earlier_checkpoint_absence"] == "unknown"
    assert "checkpoint_limit" in record["errors"]


def testByteBudgetMakesIdentityTruncationExplicitWithoutEvictingBaselines(monkeypatch, tmp_path):
    owner = plugin.PhaseOwner(SimpleNamespace(), str(tmp_path / "bounded.json"))
    baseline = countSnapshot(range(1000000000, 1000000128))
    owner.sample(baseline, "pre_fixture_baseline", None, (10**18, 10**18), (10**18, 10**18))
    for index in range(1, 64):
        after = countSnapshot(range(1000000001, 1000000129))
        owner.sample(after, "floating", index, (10**18, 10**18), (10**18, 10**18))
    monkeypatch.setattr(plugin, "collect", lambda _ids: {"status": "queried", "threads": []})
    owner.capture(baseline, after)
    owner.retire()
    owner.save()
    path = Path(owner.output)
    assert 0 < path.stat().st_size <= plugin.MAX_BYTES
    record = json.loads(path.read_text())
    assert record["output_truncated"] is True and len(record["checkpoints"]) == 64
    assert record["checkpoints"][0]["phase"] == "pre_fixture_baseline"
    assert all(row["native_ids_status"] == "unknown_output_byte_limit"
        and row["native_thread_ids"] == [] and row["omitted_native_thread_count"] == 128
        for row in record["checkpoints"])


def testNativeCountsExceptionObjectAndInheritedAttributeProvenanceSurvive(tmp_path):
    failure = RuntimeError("original native census failed")
    class Module:
        @staticmethod
        def nativeCounts(_app, **_ignored):
            raise failure
        assertNoNewThreads = staticmethod(nativeAssertion)
    module = Module()
    owner = plugin.PhaseOwner(module, str(tmp_path / "unused.json"))
    owner.install()
    with pytest.raises(RuntimeError) as raised:
        module.nativeCounts(None, phase="stable_initial")
    assert raised.value is failure
    assert owner.retire() is True and vars(module) == {}
    assert module.nativeCounts is Module.nativeCounts


def testPartialInstallationRestoresBothRawAttributes(tmp_path):
    class Module:
        nativeCounts = staticmethod(lambda _app, **_ignored: countSnapshot((1,)))
        assertNoNewThreads = staticmethod(nativeAssertion)
        def __setattr__(self, name, value):
            object.__setattr__(self, name, value)
            if name == "assertNoNewThreads":
                raise RuntimeError("setter mutated before failing")
    module = Module()
    owner = plugin.PhaseOwner(module, str(tmp_path / "unused.json"))
    with pytest.raises(RuntimeError):
        owner.install()
    assert owner.retire() is True and vars(module) == {}


def testFailedRestorationKeepsOwnerForSessionRetryAndNeverClaimsRetired(monkeypatch, tmp_path):
    item, output, hook = hookFixture(tmp_path, nativeAssertion)
    original = item.module.assertNoNewThreads
    class Module:
        failRestore = False
        def __setattr__(self, name, value):
            if self.failRestore and name == "assertNoNewThreads" and value is original:
                raise RuntimeError("restore temporarily unavailable")
            object.__setattr__(self, name, value)
    module = Module()
    module.nativeCounts = item.module.nativeCounts
    module.assertNoNewThreads = original
    item.module = module
    monkeypatch.setattr(plugin, "collect", lambda _ids: {"status": "queried", "threads": []})
    next(hook)
    with pytest.raises(AssertionError) as raised:
        module.assertNoNewThreads(countSnapshot((1,)), countSnapshot((1, 7)))
    failure = raised.value
    module.failRestore = True
    with pytest.raises(AssertionError) as raised:
        hook.throw(failure)
    assert raised.value is failure
    pending = item.config.stash[plugin._PENDING]
    assert pending.patches and json.loads(output.read_text())["observer_retired"] is False
    module.failRestore = False
    session = SimpleNamespace(config=item.config, exitstatus=0)
    plugin.pytest_sessionfinish(session)
    assert item.config.stash.get(plugin._PENDING, None) is None
    assert module.assertNoNewThreads is original
    assert session.exitstatus == pytest.ExitCode.TESTS_FAILED
    assert json.loads(output.read_text())["observer_retired"] is False


def testClockFailurePreservesSuccessButMarksSessionInvalidWithoutFile(monkeypatch, tmp_path, capsys):
    item, output, hook = hookFixture(tmp_path, nativeAssertion)
    expected = countSnapshot((1,))
    item.module.nativeCounts = lambda _app, **_ignored: expected
    monkeypatch.setattr(plugin, "reading", lambda: (_ for _ in ()).throw(RuntimeError("clock")))
    monkeypatch.setattr(plugin, "collect", lambda _ids: pytest.fail("successful collection"))
    next(hook)
    assert item.module.nativeCounts(None, phase="pre_fixture_baseline") is expected
    assert item.module.assertNoNewThreads(expected, expected) is None
    finish(hook)
    assert not output.exists()
    assert all(type(error) is str for error in item.config.stash[plugin._INVALID])
    session = SimpleNamespace(config=item.config, exitstatus=0)
    plugin.pytest_sessionfinish(session)
    assert session.exitstatus == pytest.ExitCode.TESTS_FAILED
    assert "A18_DIAGNOSTIC_INVALID" in capsys.readouterr().out


def testSessionRestorationRetryOccursOnlyOnceAndFailedOwnerRemains(tmp_path):
    item, _output, hook = hookFixture(tmp_path, nativeAssertion)
    next(hook)
    finish(hook)
    class Pending:
        retryAttempted = False
        attempts = 0
        def retire(self):
            self.attempts += 1
            return False
    pending = Pending()
    item.config.stash[plugin._PENDING] = pending
    session = SimpleNamespace(config=item.config, exitstatus=1)
    plugin.pytest_sessionfinish(session)
    plugin.pytest_sessionfinish(session)
    assert pending.attempts == 1 and item.config.stash[plugin._PENDING] is pending
    assert session.exitstatus == 1


def testClosingInterruptedHookRestoresOriginalFunctions(tmp_path):
    item, output, hook = hookFixture(tmp_path, nativeAssertion)
    originalCounts = item.module.nativeCounts
    next(hook)
    hook.close()
    assert item.module.nativeCounts is originalCounts
    assert item.module.assertNoNewThreads is nativeAssertion
    assert not output.exists()


def testUnknownPhaseAndFirstSampleDoNotClaimKnownEarlierAbsence(monkeypatch, tmp_path):
    owner = plugin.PhaseOwner(SimpleNamespace(), str(tmp_path / "unknown.json"))
    snapshot = countSnapshot((1, 7))
    owner.sample(snapshot, "unrecognized", None, (1, 4), (2, 3))
    monkeypatch.setattr(plugin, "collect", lambda _ids: {"status": "queried", "threads": []})
    owner.capture(countSnapshot((1,)), snapshot)
    owner.retire()
    owner.save()
    record = json.loads(Path(owner.output).read_text())
    assert record["checkpoints"][0]["phase"] is None
    assert "checkpoint_sequence_unknown" in record["errors"]
    assert record["first_observations"][0]["earlier_checkpoint_absence"] == "unknown"
    assert record["checkpoints"][0]["exit_unix_ns"] < record["checkpoints"][0]["entry_unix_ns"]


def testInstallFailureDoesNotReplaceOriginalTestOutcome(tmp_path):
    item, output, hook = hookFixture(tmp_path, nativeAssertion)
    class Module:
        nativeCounts = staticmethod(lambda _app, **_ignored: countSnapshot((1,)))
        assertNoNewThreads = staticmethod(nativeAssertion)
        def __setattr__(self, name, value):
            object.__setattr__(self, name, value)
            if name == "assertNoNewThreads":
                raise RuntimeError("mutated before raising")
    item.module = Module()
    next(hook)
    assert vars(item.module) == {}
    assert item.module.assertNoNewThreads(countSnapshot((1,)), countSnapshot((1,))) is None
    finish(hook)
    assert not output.exists()
    session = SimpleNamespace(config=item.config, exitstatus=0)
    plugin.pytest_sessionfinish(session)
    assert session.exitstatus == pytest.ExitCode.TESTS_FAILED
