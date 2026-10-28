import json
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from scripts import r3_a18_creation_trace as observer


@pytest.mark.parametrize("fault", [None, "start_timeout", "stop_failure"])
def testOnlyOwnedWprInstanceIsStoppedOrCancelled(tmp_path, monkeypatch, fault):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SystemRoot", str(tmp_path / "Windows"))
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        assert command[-2:] == ["-instancename", trace.instance]
        assert kwargs["timeout"] == 30
        if command[1] == "-start" and fault == "start_timeout":
            raise subprocess.TimeoutExpired(command, 30)
        if command[1] == "-stop":
            if fault == "stop_failure":
                return SimpleNamespace(returncode=1)
            Path(command[2]).write_bytes(b"synthetic ETL for lifecycle test")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(observer.subprocess, "run", run)
    trace = observer.Trace()
    trace.start()
    trace.finish()
    operations = [command[1] for command in calls]
    assert operations == ({None: ["-start", "-stop"],
                           "start_timeout": ["-start", "-cancel"],
                           "stop_failure": ["-start", "-stop", "-cancel"]}[fault])
    record = json.loads((trace.root / "metadata.json").read_text(encoding="utf-8"))
    assert record["retired"] is True
    assert bool(record["errors"]) == bool(fault)


def testTraceFailureDoesNotReplaceOriginalAssertion(monkeypatch):
    calls = []

    class BrokenTrace:
        root = Path("unused")
        record = {"errors": [], "retired": False}

        def start(self):
            calls.append("start")
            raise RuntimeError("trace start failed")

        def finish(self):
            calls.append("finish")
            raise RuntimeError("trace stop failed")

    monkeypatch.setattr(observer, "enabled", lambda: True)
    monkeypatch.setattr(observer, "Trace", BrokenTrace)
    config = SimpleNamespace(stash={})
    hook = observer.pytest_runtest_call(SimpleNamespace(nodeid=observer.NATIVE_OWNER_TEST, config=config))
    next(hook)
    original = AssertionError("original handles failure")
    with pytest.raises(AssertionError) as caught:
        hook.throw(original)
    assert caught.value is original and calls == ["start", "finish"]
    assert config.stash[observer.INVALID]
    failed = SimpleNamespace(config=config, exitstatus=1)
    observer.pytest_sessionfinish(failed)
    assert failed.exitstatus == 1
    successful = SimpleNamespace(config=config, exitstatus=0)
    observer.pytest_sessionfinish(successful)
    assert successful.exitstatus == 1


@pytest.mark.parametrize("target,active", [(False, True), (True, False)])
def testOtherCallsAndDisabledCaptureNeverStartWpr(monkeypatch, target, active):
    monkeypatch.setattr(observer, "enabled", lambda: active)
    monkeypatch.setattr(observer, "Trace", lambda: pytest.fail("unexpected WPR capture"))
    hook = observer.pytest_runtest_call(SimpleNamespace(
        nodeid=observer.NATIVE_OWNER_TEST if target else "other.py::testOther"))
    next(hook)
    with pytest.raises(StopIteration) as returned:
        hook.send("original return")
    assert returned.value.value == "original return"
