"""Exercise the executable entrypoint, not only direct window construction."""
import json

import pytest

from emo_master.apps.operator_runtime import main as entry
from emo_master.apps.operator_runtime.settings import DEFAULT_ROOT_KEY, SOURCE_LABELS
from tests.ui.operator_view.test_production_window import closeWindow, preferences
from tests.ui.operator_view.test_view import waitFor


def runEntrypoint(qtApp, monkeypatch, prefs, arguments, inspect):
    monkeypatch.setattr(entry, "QSettings", lambda *_args: prefs)

    def inspectWindow():
        window = next(widget for widget in qtApp.topLevelWidgets()
                      if isinstance(widget, entry.OperatorWindow) and not widget.done)
        try:
            inspect(window)
            return 0
        finally:
            closeWindow(window)

    # Use the real main parser, application writer, window, and Runtime. Drive Qt
    # with waitFor instead of entering an unbounded interactive event loop.
    monkeypatch.setattr(qtApp, "exec_", inspectWindow)
    assert entry.main(arguments) == 0


def assertStorage(window, root, source):
    assert window.resolution.path == root
    assert window.resolution.source == source
    assert window.startupPage.storage.sourceLabel.text() == "来源：" + SOURCE_LABELS[source]
    writer = window.applicationLogWriter
    assert writer.directory == root / "logs"
    assert writer.waitUntilProcessed("", writer.sequence)
    records = [json.loads(line) for line in writer.activePath.read_text(encoding="utf-8").splitlines()]
    storage = [record for record in records if record["eventType"] == "operator.storage.resolved"]
    assert storage and all(record["payload"]["source"] == source for record in storage)
    assert storage[0]["payload"]["dataRoot"] == str(root)
    if window.controller is not None:
        assert window.controller.dataRoot == root
        assert window.controller.runtime.operationalLogWriter.directory == root / "logs"
        assert len(storage) >= 2  # Target and ready diagnostics preserve the origin.


@pytest.mark.parametrize("source", ["explicit", "environment", "saved", "system"])
def testMainPreservesPriorityOriginAndNextStartupSettings(qtApp, tmp_path, monkeypatch, source):
    prefs = preferences(tmp_path)
    roots = {name: tmp_path / name for name in ("explicit", "environment", "saved")}
    roots["system"] = tmp_path / "local" / "EmoMaster" / "operator-runtime"
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.setenv("EMO_RUNTIME_LOG_DIR", str(tmp_path / "ignored-log-root"))
    monkeypatch.delenv("EMO_RUNTIME_DATA_DIR", raising=False)
    if source in {"explicit", "environment"}:
        monkeypatch.setenv("EMO_RUNTIME_DATA_DIR", str(roots["environment"]))
    if source != "system":
        prefs.setValue(DEFAULT_ROOT_KEY, str(roots["saved"]))
    arguments = ["--local-only"]
    if source == "explicit":
        arguments += ["--data-root", str(roots["explicit"])]
    target, nextRoot = roots[source], tmp_path / "下次 目录"

    def first(window):
        waitFor(lambda: not window.busy)
        assert not window.error
        assertStorage(window, target, source)
        assert window.resolution.systemDefault == roots["system"]
        assert window.resolution.ignoredVariables == ("EMO_RUNTIME_LOG_DIR",)
        resolution = window.resolution
        panel = window.startupPage.storage
        panel.nextEdit.setText(str(nextRoot))
        assert panel.save()
        assert "仍被" in panel.feedback.text() if source in {"explicit", "environment"} else "下次启动生效" in panel.feedback.text()
        assert window.resolution is resolution and window.controller.dataRoot == target
        assert not nextRoot.exists()

    runEntrypoint(qtApp, monkeypatch, prefs, arguments, first)
    previousFiles = set(target.rglob("*"))

    def restarted(window):
        waitFor(lambda: not window.busy)
        assert not window.error
        overridden = source in {"explicit", "environment"}
        assertStorage(window, target if overridden else nextRoot, source if overridden else "saved")
        assert previousFiles.issubset(set(target.rglob("*")))
        assert not (tmp_path / "ignored-log-root").exists()

    runEntrypoint(qtApp, monkeypatch, prefs, arguments, restarted)


def testMainFailedSavedRootRetryKeepsOriginAndNextLaunchAdoptsSavedChange(qtApp, tmp_path, monkeypatch):
    monkeypatch.delenv("EMO_RUNTIME_DATA_DIR", raising=False)
    prefs = preferences(tmp_path)
    target, nextRoot = tmp_path / "failed-root", tmp_path / "next-root"
    blocker = target / "runtime.sqlite3"
    blocker.mkdir(parents=True)
    prefs.setValue(DEFAULT_ROOT_KEY, str(target))

    def failedThenRetried(window):
        waitFor(lambda: window.initializationFailed and not window.busy)
        assert window.controller is None
        assertStorage(window, target, "saved")
        resolution = window.resolution
        panel = window.startupPage.storage
        panel.nextEdit.setText(str(nextRoot))
        assert panel.save() and "下次启动生效" in panel.feedback.text()
        assert not nextRoot.exists()
        blocker.rmdir()
        window.retryInitialization()
        waitFor(lambda: window.controller is not None and not window.busy)
        assert not window.error
        assert window.resolution is resolution
        assertStorage(window, target, "saved")
        assert not nextRoot.exists()

    runEntrypoint(qtApp, monkeypatch, prefs, ["--local-only"], failedThenRetried)

    def restarted(window):
        waitFor(lambda: not window.busy)
        assert not window.error
        assertStorage(window, nextRoot, "saved")
        assert (target / "runtime.sqlite3").is_file()

    runEntrypoint(qtApp, monkeypatch, prefs, ["--local-only"], restarted)
