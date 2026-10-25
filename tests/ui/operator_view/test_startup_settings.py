from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide2.QtCore import QSettings, Qt
from PySide2.QtTest import QTest

from emo_master.apps.operator_runtime.main import OperatorWindow
from emo_master.apps.operator_runtime.settings import DEFAULT_ROOT_KEY, resolveDataRoot
from emo_master.apps.operator_runtime.startup_page import StorageSettings
from tests.ui.operator_view.test_production_window import closeWindow, preferences
from tests.ui.operator_view.test_view import waitFor


@pytest.mark.parametrize("source", ["explicit", "environment", "saved", "system"])
def testStoragePreferenceDraftPersistenceClearAndOverrides(qtApp, tmp_path, source):
    prefs = preferences(tmp_path)
    saved = tmp_path / "saved"
    prefs.setValue(DEFAULT_ROOT_KEY, str(saved))
    resolution = resolveDataRoot(tmp_path / "explicit" if source == "explicit" else None,
        environment={"EMO_RUNTIME_DATA_DIR": str(tmp_path / "environment")} if source == "environment" else {},
        saved=saved if source != "system" else None, platformDefault=tmp_path / "system")
    panel = StorageSettings(prefs, resolution)
    panel.show()
    target = tmp_path / "中文 下次目录"
    panel.nextEdit.setText(str(target))
    assert "未保存" in panel.feedback.text()
    assert panel.save()
    assert prefs.value(DEFAULT_ROOT_KEY) == str(target)
    assert "仍被" in panel.feedback.text() if source in {"explicit", "environment"} else "下次启动生效" in panel.feedback.text()
    assert not target.exists()
    QTest.mouseClick(panel.defaultButton, Qt.LeftButton)
    assert "未保存" in panel.feedback.text() and prefs.contains(DEFAULT_ROOT_KEY)
    assert panel.save() and not prefs.contains(DEFAULT_ROOT_KEY)
    for invalid in ("relative", str(tmp_path / "file")):
        (tmp_path / "file").write_text("not a directory")
        panel.nextEdit.setText(invalid)
        assert not panel.save() and "保存失败" in panel.feedback.text()
        assert not prefs.contains(DEFAULT_ROOT_KEY)


def testPreferenceSyncFailureIsNotReportedAsSuccess(qtApp, tmp_path):
    blocked = tmp_path / "settings-location"
    blocked.write_text("ordinary path collision")
    prefs = QSettings(str(blocked / "preferences.ini"), QSettings.IniFormat)
    panel = StorageSettings(prefs, resolveDataRoot(environment={}, platformDefault=tmp_path))
    panel.nextEdit.setText(str(tmp_path / "target"))
    assert not panel.save()
    assert panel.savedDefault == "" and "无法写入" in panel.feedback.text()
    assert "已保存" not in panel.feedback.text()
    assert not prefs.contains(DEFAULT_ROOT_KEY)


@pytest.mark.parametrize("clearDefault", [False, True], ids=["set-directory", "system-default"])
def testPreferenceSyncFailureCanRetryInSamePanel(qtApp, tmp_path, clearDefault):
    prefs = preferences(tmp_path)
    previous = str(tmp_path / "previous")
    prefs.setValue(DEFAULT_ROOT_KEY, previous)
    prefs.setValue("projectPath", "remembered.emoproj")
    prefs.sync()
    path = Path(prefs.fileName())
    original = path.read_bytes()
    panel = StorageSettings(prefs, resolveDataRoot(environment={}, saved=previous))
    panel.show()
    target = "" if clearDefault else str(tmp_path / "next")
    panel.nextEdit.setText(target)
    # A real sync failure, without depending on the test user's ACL privileges.
    path.unlink()
    path.mkdir()
    try:
        assert not panel.save()
        assert "保存失败" in panel.feedback.text() and "已保存" not in panel.feedback.text()
        assert panel.savedDefault == previous and panel.nextEdit.text() == target
        assert prefs.value(DEFAULT_ROOT_KEY) == previous
        assert preferences(tmp_path).value(DEFAULT_ROOT_KEY) == previous
    finally:
        path.rmdir()
        path.write_bytes(original)

    assert panel.save(), panel.feedback.text()
    assert "已保存" in panel.feedback.text() and "下次启动生效" in panel.feedback.text()
    assert panel.savedDefault == target and prefs.value(DEFAULT_ROOT_KEY, "") == target
    assert path.is_file() and path.read_bytes() != original
    persisted = preferences(tmp_path)
    persisted.sync()
    assert persisted.status() == QSettings.NoError
    assert persisted.value(DEFAULT_ROOT_KEY, "") == target
    assert persisted.contains(DEFAULT_ROOT_KEY) is not clearDefault
    assert persisted.value("projectPath") == "remembered.emoproj"
    assert not (tmp_path / "next").exists()
    assert panel.save()  # A successful retry must also leave later saves usable.


def testFirstStartupHasRealPathsNoJobsAndNextPreferenceDoesNotSwitchInstance(qtApp, tmp_path, monkeypatch):
    monkeypatch.delenv("EMO_RUNTIME_DATA_DIR")
    prefs = preferences(tmp_path)
    first, second = tmp_path / "first", tmp_path / "下次 空间"
    prefs.setValue(DEFAULT_ROOT_KEY, str(first))
    window = OperatorWindow(preferences=prefs)
    window.show()
    try:
        waitFor(lambda: not window.busy)
        assert not window.error and window.contentStack.currentWidget() is window.startupPage
        assert window.resolution.source == "saved" and window.controller.dataRoot == first
        panel = window.startupPage.storage
        assert "当前有效目录" in panel.currentLabel.text()
        assert str(first / "runtime.sqlite3") in panel.details.toPlainText()
        assert "未加载工程" in window.startupPage.archiveLabel.text()
        assert window.settingsButton.isEnabled() and window.logsButton.isEnabled()
        writer = window.controller.runtime.operationalLogWriter
        panel.nextEdit.setText(str(second))
        assert panel.save() and not second.exists()
        assert window.controller.runtime.operationalLogWriter is writer
        assert writer.directory == first / "logs" and window.controller.runtime.workspaceRoot == first / "jobs"
        assert not window.controller.runtime.jobRepository.all()
        waitFor(lambda: bool(window.logFeed.records))
        assert all(not record.data["jobId"] for record in window.logFeed.records)
    finally:
        closeWindow(window)
    oldFiles = set(first.rglob("*"))
    restarted = OperatorWindow(preferences=prefs)
    restarted.show()
    try:
        waitFor(lambda: not restarted.busy)
        assert not restarted.error and restarted.controller.dataRoot == second
        assert oldFiles.issubset(set(first.rglob("*")))
        assert not (second / "logs").joinpath("copied-log.jsonl").exists()
    finally:
        closeWindow(restarted)


@pytest.mark.parametrize("failureAt", ["root", "database", "display", "preview-cache"])
def testFailedInitializationKeepsToolsAndCanRetrySameTarget(qtApp, tmp_path, failureAt):
    import threading
    target = tmp_path / "target"
    if failureAt == "root":
        blocker = target
    else:
        target.mkdir()
        blocker = target / {"database": "runtime.sqlite3", "display": "display", "preview-cache": "preview-cache"}[failureAt]
    if failureAt == "database":
        blocker.mkdir()
    else:
        blocker.write_text("ordinary path collision")
    before = {thread.ident for thread in threading.enumerate() if thread.name.startswith(("runtime-", "display-"))}
    window = OperatorWindow(dataRoot=target, preferences=preferences(tmp_path))
    window.show()
    try:
        waitFor(lambda: window.initializationFailed and not window.busy)
        assert window.controller is None and not window.startButton.isEnabled()
        assert window.startupPage.retryButton.isVisible()
        assert str(target) in window.startupPage.failureLabel.text()
        assert "原因" in window.error
        assert "本次启动目标目录" in window.startupPage.storage.currentLabel.text()
        window.showLogs()
        assert window.contentStack.currentWidget() is window.logView
        assert "未写入日志文件" in window.logView.diagnostics.toPlainText()
        window.backFromTool()
        panel = window.startupPage.storage
        panel.nextEdit.setText(str(tmp_path / "next"))
        assert panel.save()
        if blocker.is_dir():
            blocker.rmdir()
        else:
            blocker.unlink()
        window.retryInitialization()
        waitFor(lambda: window.controller is not None and not window.busy)
        assert not window.error and window.controller.dataRoot == target
        assert "当前有效目录" in panel.currentLabel.text()
    finally:
        closeWindow(window)
    waitFor(lambda: {thread.ident for thread in threading.enumerate() if thread.name.startswith(("runtime-", "display-"))}.issubset(before))


def testStorageDisplaysOwnersNotDraftAndUnusedEnvironmentValues(qtApp, tmp_path):
    resolution = resolveDataRoot(tmp_path / "effective", environment={"EMO_RUNTIME_LOG_DIR": "ignored"})
    panel = StorageSettings(preferences(tmp_path), resolution)
    runtime = SimpleNamespace(workspaceRoot=tmp_path / "real-jobs",
        sqliteStore=SimpleNamespace(dbPath=tmp_path / "real.sqlite3"),
        operationalLogWriter=SimpleNamespace(directory=tmp_path / "真实 logs"))
    panel.setCurrent(SimpleNamespace(dataRoot=resolution.path, runtime=runtime,
                                     presentation=SimpleNamespace(root=tmp_path / "real-display")))
    panel.nextEdit.setText(str(tmp_path / "draft"))
    text = panel.details.toPlainText()
    assert str(runtime.sqliteStore.dbPath) in text and str(runtime.operationalLogWriter.directory) in text
    assert "EMO_RUNTIME_LOG_DIR：不用于当前独立入口" in text
    assert str(Path(panel.nextEdit.text())) not in text
