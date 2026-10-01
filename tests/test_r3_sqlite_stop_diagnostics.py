"""The optional pytest hook preserves failures and marks invalid observations."""
from pathlib import Path
from types import SimpleNamespace
import gc
import weakref

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
    ownerPatch = pytest.MonkeyPatch()
    probe._patch = ownerPatch.setattr
    def closeObserver():
        ownerPatch.undo()
        closes.append("observer")
    probe.close = closeObserver
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


def testRealHookRetiresAllInstanceWrappersWithoutGC(monkeypatch, tmp_path):
    from emo_master.apps.runtime.context.sqlite_store import SqliteStore
    from scripts.r3_job_diagnostics import JobDiagnostics
    from scripts.r3_sqlite_phases import SqlitePhases

    class Runtime:
        def __init__(self):
            self.sqliteStore = SqliteStore(tmp_path / "unused.db")
        def operation(self):
            return "original"
        def close(self):
            pass

    # Use the actual plugin, capture, _patch and close paths, without starting
    # processes or testing the production Runtime's independent ownership.
    def install(self, runtime, **_kwargs):
        self.runtime = runtime
        self.sqlitePhases = SqlitePhases()
        self.sqlitePhases.install(runtime.sqliteStore)
        self.wrap(runtime, "operation", "test.operation")

    monkeypatch.setattr(JobDiagnostics, "install", install)
    monkeypatch.setattr(JobDiagnostics, "_state", lambda self: {})
    monkeypatch.setattr(plugin.subprocess, "check_output", lambda *_args, **_kwargs: "test-head")
    module = SimpleNamespace(RuntimeService=Runtime, waitForTerminal=lambda *_args: None)
    item = SimpleNamespace(nodeid=plugin.TARGET, module=module,
        config=SimpleNamespace(getoption=lambda _name: str(tmp_path / "diagnostic.json")))
    wasEnabled = gc.isenabled()
    gc.disable()
    try:
        plain = Runtime()
        plainRef = weakref.ref(plain)
        plain.close()
        del plain
        assert plainRef() is None
        hook = plugin.pytest_runtest_call(item)
        next(hook)
        observed = module.RuntimeService()
        observedRef = weakref.ref(observed)
        storeRef = weakref.ref(observed.sqliteStore)
        assert observed.operation() == "original"
        observed.close()
        with pytest.raises(StopIteration):
            hook.send(None)
        assert "close" not in vars(observed) and "operation" not in vars(observed)
        assert "_connect" not in vars(observed.sqliteStore)
        del observed
        assert observedRef() is None and storeRef() is None
    finally:
        if wasEnabled:
            gc.enable()
