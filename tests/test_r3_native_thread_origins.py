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
    module = SimpleNamespace(assertNoNewThreads=original)
    item = SimpleNamespace(nodeid=nodeid, module=module,
        config=SimpleNamespace(getoption=lambda _name: str(output) if enabled else None))
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
    failure = AssertionError("original exact predicate")
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
        assert record["selected_native_thread_ids"] == [4]
        assert record["new_native_thread_count"] == 1
        assert record["evidence"]["threads"][0]["module"] == "test.dll"
        assert output.stat().st_size < native.MAX_BYTES


def testNewOutputIsNeverOverwrittenAndOriginalFailureSurvives(monkeypatch, tmp_path):
    failure = AssertionError("original")
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
