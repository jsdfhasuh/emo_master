"""The optional pytest hook preserves failures and marks invalid observations."""
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import r3_sqlite_stop_diagnostics as plugin


def hookFixture(monkeypatch, tmp_path, *, brokenObserver=False):
    captures, closes = [], []
    source = {}
    probe = SimpleNamespace(errors=[], sqlitePhases=SimpleNamespace(snapshot=lambda: {"enabled": not brokenObserver}))

    def makeProbe(_path, *, source: dict, **_kwargs):
        probe.source = source
        return probe

    probe.install = lambda runtime, **kwargs: None
    probe.captureOptions = []
    def capture(reason, **kwargs):
        captures.append(reason)
        probe.captureOptions.append((reason, kwargs))
    probe.capture = capture
    probe.close = lambda: closes.append("observer")
    probe._error = lambda error: probe.errors.append(type(error).__name__)
    runtime = SimpleNamespace(close=lambda: closes.append("runtime"))
    def factory():
        return runtime
    waitFailure = RuntimeError("exact original wait failure")

    def wait(*args):
        raise waitFailure

    module = SimpleNamespace(RuntimeService=factory, waitForTerminal=wait)
    item = SimpleNamespace(nodeid=plugin.TARGET, module=module,
        config=SimpleNamespace(getoption=lambda _name: str(tmp_path / "diagnostic.json")))
    monkeypatch.setattr(plugin, "JobDiagnostics", makeProbe)
    monkeypatch.setattr(plugin.subprocess, "check_output", lambda *_args, **_kwargs: "test-head")
    return item, probe, runtime, captures, closes, factory, wait, waitFailure, source


def testHookCapturesBeforeCleanupWithoutChangingWaitFailure(monkeypatch, tmp_path):
    item, probe, runtime, captures, closes, factory, wait, failure, _ = hookFixture(monkeypatch, tmp_path)
    hook = plugin.pytest_runtest_call(item)
    next(hook)
    assert item.module.RuntimeService() is runtime
    with pytest.raises(RuntimeError) as raised:
        item.module.waitForTerminal(runtime, "job")
    assert raised.value is failure and "terminal_wait_failed" in captures
    options = dict(probe.captureOptions)
    assert options["terminal_wait_enter"]["save"] is False
    assert options["terminal_wait_return"]["save"] is False
    assert options["terminal_wait_failed"].get("save", True) is True
    runtime.close()
    assert captures.index("before_runtime_close") < captures.index("after_runtime_close")
    with pytest.raises(RuntimeError) as raised:
        hook.throw(failure)
    assert raised.value is failure
    assert item.module.RuntimeService is factory and item.module.waitForTerminal is wait
    assert closes == ["runtime", "observer"]
    assert probe.source["pytest_outcome"] == "failed" and probe.source["diagnostic_valid"]


def testInvalidObserverCannotProduceDiagnosticPass(monkeypatch, tmp_path):
    item, probe, *_ = hookFixture(monkeypatch, tmp_path, brokenObserver=True)
    hook = plugin.pytest_runtest_call(item)
    next(hook)
    item.module.RuntimeService()
    with pytest.raises(pytest.fail.Exception, match="observer failed"):
        hook.send(None)
    assert probe.source["pytest_outcome"] == "passed"
    assert probe.source["diagnostic_valid"] is False


def testInvalidObserverStillPreservesOriginalFailure(monkeypatch, tmp_path):
    item, probe, _, _, _, _, _, failure, _ = hookFixture(monkeypatch, tmp_path, brokenObserver=True)
    hook = plugin.pytest_runtest_call(item)
    next(hook)
    item.module.RuntimeService()
    with pytest.raises(RuntimeError) as raised:
        hook.throw(failure)
    assert raised.value is failure and not probe.source["diagnostic_valid"]


def testUnrequestedOrOtherTestIsNotInstrumented():
    module = SimpleNamespace(RuntimeService=object())
    for nodeid, option in ((plugin.TARGET, None), ("other.py::testOther", "new.json")):
        item = SimpleNamespace(nodeid=nodeid, module=module,
            config=SimpleNamespace(getoption=lambda _name: option))
        original = module.RuntimeService
        hook = plugin.pytest_runtest_call(item)
        next(hook)
        with pytest.raises(StopIteration):
            hook.send(None)
        assert module.RuntimeService is original


def testCollectionRejectsMissingTargetAndExistingOutput(tmp_path):
    path = tmp_path / "diagnostic.json"
    config = SimpleNamespace(getoption=lambda _name: str(path))
    with pytest.raises(pytest.UsageError, match="exactly one"):
        plugin.pytest_collection_modifyitems(config, [])
    path.write_text("keep", encoding="utf-8")
    with pytest.raises(pytest.UsageError, match="must be new"):
        plugin.pytest_collection_modifyitems(config, [SimpleNamespace(nodeid=plugin.TARGET)])
    assert Path(path).read_text() == "keep"
