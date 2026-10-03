"""Native offline measurement counters; no device or field-SLA assertion."""
import os
import json
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from scripts import p2_resources, r3_resources
from scripts.r3_resources import resources


def testCurrentProcessResourceSnapshotHasNativeOwnershipCounters():
    result = resources()
    assert result["pid"] == os.getpid()
    if result["status"] == "NOT_RUN":
        assert result["reason"]
        return
    assert result["rss_bytes"] > 0
    assert result["peak_rss_bytes"] >= result["rss_bytes"]
    assert result["handles"] >= 0
    assert result["native_threads"] >= 1
    assert result["python_threads"] >= 1
    assert result["cpu_seconds"] >= 0
    assert "native_thread_ids" not in result


def testOptInNativeThreadInventoryTracksOwnedThreadLifetime():
    ready, stop = threading.Event(), threading.Event()

    def work():
        ready.set()
        stop.wait()

    thread = threading.Thread(target=work, name="r3-resource-inventory")
    thread.start()
    try:
        assert ready.wait(2)
        result = resources(includeThreadIds=True)
        if result["status"] == "NOT_RUN":
            assert result["reason"]
            return
        assert thread.native_id in result["native_thread_ids"]
        assert threading.get_native_id() in result["native_thread_ids"]
        assert result["native_threads"] == len(set(result["native_thread_ids"]))
        assert json.loads(json.dumps(result)) == result
    finally:
        stop.set()
        thread.join(2)
        assert not thread.is_alive()
    retired = resources(includeThreadIds=True)
    assert thread.native_id not in retired["native_thread_ids"]


@pytest.mark.parametrize(("entries", "lastError"), (
    ((), 5),  # Thread32First fails before returning an entry.
    (((101, 73),), 5),  # Thread32Next fails after a partial inventory.
    (((101, 73), (102, 99), (103, 73)), 18),  # Normal end, including another PID.
))
def testWindowsThreadInventoryRequiresSuccessfulEnumeration(monkeypatch, entries, lastError):
    import ctypes

    remaining = iter(entries)

    def nextEntry(snapshot, pointer):
        entry = next(remaining, None)
        if entry is None:
            return False
        pointer._obj.thread, pointer._obj.owner = entry
        return True

    kernel = SimpleNamespace(CreateToolhelp32Snapshot=Mock(return_value=42),
        Thread32First=Mock(side_effect=nextEntry), Thread32Next=Mock(side_effect=nextEntry),
        CloseHandle=Mock())
    monkeypatch.setattr(r3_resources, "os", SimpleNamespace(name="nt", getpid=lambda: 73))
    monkeypatch.setattr(p2_resources, "resources", lambda pid: {})
    monkeypatch.setattr(ctypes, "WinDLL", lambda *args, **kwargs: kernel, raising=False)
    monkeypatch.setattr(ctypes, "get_last_error", lambda: lastError, raising=False)
    monkeypatch.setattr(ctypes, "WinError", lambda code: OSError(code, "snapshot failure"), raising=False)
    if lastError != 18:
        with pytest.raises(OSError) as error:
            resources(includeThreadIds=True)
        assert error.value.errno == lastError
    else:
        result = resources(includeThreadIds=True)
        assert result["native_thread_ids"] == [101, 103]
        assert result["native_threads"] == 2
    kernel.CloseHandle.assert_called_once_with(42)
