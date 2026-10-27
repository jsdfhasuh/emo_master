from dataclasses import replace
from pathlib import Path

import pytest
from PySide2.QtCore import QSettings, QTimer

from emo_master.apps.designer.startup_settings import (
    defaultDataRoot, initialStartupOptions, recentValues, saveStartupOptions,
)
from emo_master.apps.designer.ui.startup_settings_dialog import DesignerStartupSettingsDialog


def preferences(tmp_path):
    result = QSettings(str(tmp_path / "settings.ini"), QSettings.IniFormat)
    result.setFallbacksEnabled(False)
    return result


def test_defaults_and_saved_mode_are_read_without_creating_runtime_data(tmp_path):
    store = preferences(tmp_path)
    options = initialStartupOptions(store, environment={})
    assert options.mode == "local" and options.dataRoot == defaultDataRoot()
    assert options.dbPath == options.dataRoot / "emo_master.db"
    assert options.workspaceRoot == options.dataRoot / "jobs"
    assert options.runtimeTarget == "127.0.0.1:50051"
    root = tmp_path / "中文目录 with spaces"
    options = options.withDataRoot(root)
    saveStartupOptions(store, options)
    remote = replace(options, mode="remote", runtimeTarget="localhost:50052")
    saveStartupOptions(store, remote)
    restored = initialStartupOptions(store, environment={})
    assert restored.mode == "remote" and restored.dataRoot == root
    assert restored.runtimeTarget == "localhost:50052"
    assert not root.exists()
    assert recentValues(store, "recentDataRoots") == [str(root)]
    assert recentValues(store, "recentRuntimeTargets") == ["localhost:50052"]


def test_environment_prefills_existing_paths_and_manual_choice_overrides_all(tmp_path):
    store = preferences(tmp_path)
    store.setValue("startup/mode", "remote")
    store.setValue("startup/dataRoot", str(tmp_path / "saved"))
    env = dict(EMO_RUNTIME_DATA_DIR="environment", EMO_RUNTIME_DB_PATH="db/custom.sqlite",
               EMO_MASTER_RUNTIME_DB_PATH="ignored.sqlite", EMO_RUNTIME_LOG_DIR="separate logs")
    options = initialStartupOptions(store, environment=env, cwd=tmp_path)
    assert options.mode == "local" and options.dataRoot == tmp_path / "environment"
    assert options.dbPath == tmp_path / "db/custom.sqlite"
    assert options.workspaceRoot == tmp_path / "environment/jobs"
    assert options.logDirectory == tmp_path / "separate logs"
    chosen = options.withDataRoot(tmp_path / "chosen")
    assert (chosen.dbPath, chosen.workspaceRoot, chosen.logDirectory) == (
        tmp_path / "chosen/emo_master.db", tmp_path / "chosen/jobs", tmp_path / "chosen/logs")
    assert env["EMO_RUNTIME_DB_PATH"] == "db/custom.sqlite"
    env["EMO_RUNTIME_TARGET"] = "127.0.0.1:50053"
    assert initialStartupOptions(store, environment=env, cwd=tmp_path).mode == "remote"
    del env["EMO_RUNTIME_DB_PATH"]
    assert initialStartupOptions(store, environment=env, cwd=tmp_path).dbPath == tmp_path / "ignored.sqlite"


def test_recent_history_is_unique_bounded_and_separate_between_modes(tmp_path):
    store = preferences(tmp_path)
    options = initialStartupOptions(store, environment={})
    for index in range(12):
        saveStartupOptions(store, options.withDataRoot(tmp_path / str(index)))
        saveStartupOptions(store, replace(options, mode="remote", runtimeTarget=f"localhost:{50000 + index}"))
    saveStartupOptions(store, options.withDataRoot(tmp_path / "10"))
    saveStartupOptions(store, replace(options, mode="remote", runtimeTarget="LOCALHOST:50010"))
    roots = recentValues(store, "recentDataRoots")
    targets = recentValues(store, "recentRuntimeTargets")
    assert len(roots) == len(targets) == 10
    assert roots[:2] == [str(tmp_path / "10"), str(tmp_path / "11")]
    assert targets[:2] == ["LOCALHOST:50010", "localhost:50011"]


def test_dialog_modes_paths_and_cancel_do_not_create_or_save_data(tmp_path, monkeypatch):
    monkeypatch.setenv("EMO_RUNTIME_DB_PATH", str(tmp_path / "legacy/old.sqlite"))
    monkeypatch.setenv("EMO_RUNTIME_LOG_DIR", str(tmp_path / "legacy logs"))
    store = preferences(tmp_path)
    dialog = DesignerStartupSettingsDialog(store)
    assert "old.sqlite" in dialog.pathDetails.toPlainText()
    newRoot = tmp_path / "新目录 with spaces"
    dialog.selectDirectory(str(newRoot))
    assert "old.sqlite" not in dialog.pathDetails.toPlainText()
    assert str(newRoot / "logs") in dialog.pathDetails.toPlainText()
    dialog.remoteButton.click()
    assert dialog.pages.currentIndex() == 1
    dialog.targetCombo.setCurrentText("bad-address")
    dialog.accept()
    assert dialog._selection is None and "主机:端口" in dialog.errorLabel.text()
    dialog.localButton.click()
    assert not dialog.errorLabel.text()
    dialog.accept()
    assert dialog._selection.dataRoot == newRoot
    assert not newRoot.exists() and not store.contains("startup/mode")
    QTimer.singleShot(0, dialog.reject)
    assert dialog.execSelection() is None
    assert not newRoot.exists() and not store.contains("startup/mode")


@pytest.mark.parametrize("path", ["", "relative/path", "C:relative"])
def test_invalid_local_choice_stays_in_dialog(tmp_path, path):
    dialog = DesignerStartupSettingsDialog(preferences(tmp_path))
    dialog.selectDirectory(path)
    dialog.accept()
    assert dialog._selection is None and dialog.errorLabel.text()


def test_explicitly_selecting_same_root_discards_legacy_database_override(tmp_path, monkeypatch):
    monkeypatch.setenv("EMO_RUNTIME_DB_PATH", str(tmp_path / "legacy.sqlite"))
    dialog = DesignerStartupSettingsDialog(preferences(tmp_path))
    root = dialog.initialOptions.dataRoot
    dialog.selectDirectory(str(root))
    dialog.accept()
    assert dialog._selection.dbPath == root / "emo_master.db"
    assert dialog._selection.dbPath != Path(tmp_path / "legacy.sqlite")


def test_selecting_current_recent_directory_also_discards_environment_database_override(tmp_path, monkeypatch):
    monkeypatch.setenv("EMO_RUNTIME_DB_PATH", str(tmp_path / "legacy.sqlite"))
    store = preferences(tmp_path)
    root = initialStartupOptions(store).dataRoot
    store.setValue("startup/recentDataRoots", [str(root)])
    dialog = DesignerStartupSettingsDialog(store)
    dialog.directoryCombo.activated[str].emit(str(root))
    dialog.accept()
    assert dialog._selection.dbPath == root / "emo_master.db"


def test_browse_and_default_buttons_select_without_creating_directories(tmp_path, monkeypatch):
    from emo_master.apps.designer.ui import startup_settings_dialog
    chosen = tmp_path / "not-created"
    monkeypatch.setattr(startup_settings_dialog.QFileDialog, "getExistingDirectory", lambda *_: str(chosen))
    dialog = DesignerStartupSettingsDialog(preferences(tmp_path))
    dialog.browseButton.click()
    assert dialog.directoryCombo.currentText() == str(chosen) and not chosen.exists()
    dialog.defaultButton.click()
    assert Path(dialog.directoryCombo.currentText()) == defaultDataRoot()
    assert dialog._localOptions().dbPath == defaultDataRoot() / "emo_master.db"


def test_file_instead_of_directory_is_rejected_before_initialization(tmp_path):
    path = tmp_path / "existing-file"
    path.write_text("unchanged", encoding="utf-8")
    dialog = DesignerStartupSettingsDialog(preferences(tmp_path))
    dialog.selectDirectory(str(path))
    dialog.accept()
    assert dialog._selection is None and "不是目录" in dialog.errorLabel.text()
    assert path.read_text(encoding="utf-8") == "unchanged"


def test_failed_preference_save_does_not_block_this_launch(tmp_path, monkeypatch):
    from emo_master.apps.designer.ui import startup_settings_dialog
    dialog = DesignerStartupSettingsDialog(preferences(tmp_path))
    def fail(*_args):
        raise OSError("read-only settings")
    monkeypatch.setattr(startup_settings_dialog, "saveStartupOptions", fail)
    notice = dialog.rememberSuccessful(dialog.initialOptions)
    assert "本次启动成功" in notice and "未记住" in notice and "read-only settings" in notice
    from emo_master.apps.designer.ui.project_entry_dialog import ProjectEntryDialog
    entry = ProjectEntryDialog()
    entry.setStartupNotice(notice)
    assert entry._startupNotice.text() == notice and not entry._startupNotice.isHidden()
    assert entry._loadErrorEdit.isHidden()


def test_preference_write_failure_does_not_replace_previous_in_memory_choice(tmp_path):
    blocked = tmp_path / "not-a-directory"
    blocked.write_text("file", encoding="utf-8")
    store = QSettings(str(blocked / "settings.ini"), QSettings.IniFormat)
    options = initialStartupOptions(store, environment={}).withDataRoot(tmp_path / "chosen")
    with pytest.raises(OSError, match="无法保存"):
        saveStartupOptions(store, options)
    assert not store.contains("startup/mode")
    assert not store.contains("startup/dataRoot")
    assert not options.dataRoot.exists()


def test_isolated_ini_override_keeps_startup_preferences_out_of_user_registry(tmp_path, monkeypatch):
    path = tmp_path / "isolated.ini"
    monkeypatch.setenv("EMO_DESIGNER_SETTINGS_PATH", str(path))
    # Use the real constructor, rather than the per-test registry-isolation factory.
    from emo_master.apps.designer.ui import startup_settings_dialog
    monkeypatch.setattr(startup_settings_dialog, "QSettings", QSettings)
    dialog = DesignerStartupSettingsDialog()
    assert Path(dialog.preferences.fileName()) == path
    assert dialog.rememberSuccessful(dialog.initialOptions) == ""
    assert path.is_file()


@pytest.mark.parametrize("width", [480, 640])
def test_settings_layout_keeps_long_paths_and_buttons_inside_dialog(tmp_path, width, designerApplication):
    dialog = DesignerStartupSettingsDialog(preferences(tmp_path))
    dialog.selectDirectory(str(tmp_path / ("中文长目录 with spaces " * 5)))
    dialog.resize(width, 420)
    dialog.show()
    designerApplication.processEvents()
    for button in (dialog.localButton, dialog.remoteButton, dialog.startButton, dialog.cancelButton):
        assert button.fontMetrics().horizontalAdvance(button.text()) < button.width() - 20
    for widget in (dialog.directoryCombo, dialog.pathDetails, dialog.browseButton, dialog.startButton):
        assert widget.mapTo(dialog, widget.rect().bottomRight()).x() < dialog.width()
        assert widget.mapTo(dialog, widget.rect().bottomRight()).y() < dialog.height()
    assert dialog.grab().save(str(tmp_path / f"startup-settings-{width}.png"))


def test_cancel_startup_selection_never_initializes_or_saves(monkeypatch, designerApplication, isolatedDesignerSettings):
    from PySide2 import QtWidgets
    from emo_master.apps.designer import main, startup
    monkeypatch.setattr(QtWidgets, "QApplication", lambda _: designerApplication)
    monkeypatch.setattr(DesignerStartupSettingsDialog, "execSelection", lambda self: None)
    monkeypatch.setattr(startup, "prepareRuntime", lambda *_a, **_k: pytest.fail("Runtime initialized after cancel"))
    quitPolicy = designerApplication.quitOnLastWindowClosed()
    main.runDesigner()
    store = QSettings(str(isolatedDesignerSettings), QSettings.IniFormat)
    assert not store.contains("startup/mode")
    assert designerApplication.quitOnLastWindowClosed() is quitPolicy


def test_failed_remote_startup_can_switch_to_local_and_only_remembers_success(
        tmp_path, monkeypatch, designerApplication, isolatedDesignerSettings):
    from types import SimpleNamespace
    from PySide2 import QtWidgets
    from emo_master.apps.designer import main, startup
    from emo_master.apps.designer.services.runtime_client import RuntimeClientError
    from emo_master.apps.designer.ui import main_window

    attempts, closed = [], []
    root = tmp_path / "new-local"
    store = QSettings(str(isolatedDesignerSettings), QSettings.IniFormat)
    store.setValue("startup/runtimeTarget", "previous:50051")
    client = SimpleNamespace(close=lambda: closed.append("client"))
    def select(dialog):
        if not attempts:
            dialog.remoteButton.click()
            dialog.targetCombo.setCurrentText("localhost:59999")
        else:
            assert dialog.targetCombo.currentText() == "localhost:59999"
            assert "offline" in dialog.errorLabel.text()
            assert not store.contains("startup/mode")
            assert store.value("startup/runtimeTarget") == "previous:50051"
            dialog.localButton.click()
            dialog.selectDirectory(str(root))
        dialog.accept()
        return dialog._selection
    def prepare(_timing, _cancelled, *, options):
        attempts.append(options.mode)
        if options.mode == "remote":
            raise RuntimeClientError("UNAVAILABLE", "offline")
        assert options.dataRoot == root and options.dbPath == root / "emo_master.db"
        return client
    class Window:
        def __init__(self, actualClient, **kwargs):
            assert actualClient is client
            assert Path(kwargs["settingsStore"].fileName()) == Path(isolatedDesignerSettings)
        def showStartupProjectEntry(self):
            assert store.value("startup/mode") == "local"
            assert store.value("startup/dataRoot") == str(root)
            return False
        def shutdownOperatorDisplay(self):
            closed.append("display")
    monkeypatch.setattr(QtWidgets, "QApplication", lambda _: designerApplication)
    monkeypatch.setattr(DesignerStartupSettingsDialog, "execSelection", select)
    monkeypatch.setattr(startup, "prepareRuntime", prepare)
    monkeypatch.setattr(main_window, "MainWindow", Window)
    main.runDesigner()
    assert attempts == ["remote", "local"] and closed == ["display", "client"]
    assert store.value("startup/runtimeTarget") == "previous:50051"


def test_permission_failure_then_cancel_keeps_previous_preferences(
        tmp_path, monkeypatch, designerApplication, isolatedDesignerSettings):
    from PySide2 import QtWidgets
    from emo_master.apps.designer import main, startup
    store = QSettings(str(isolatedDesignerSettings), QSettings.IniFormat)
    store.setValue("startup/dataRoot", str(tmp_path / "previous"))
    attempts = []
    def select(dialog):
        if attempts:
            assert "permission denied" in dialog.errorLabel.text()
            return None
        dialog.selectDirectory(str(tmp_path / "new"))
        dialog.accept()
        return dialog._selection
    def fail(*_args, **_kwargs):
        attempts.append(True)
        raise PermissionError("permission denied")
    monkeypatch.setattr(QtWidgets, "QApplication", lambda _: designerApplication)
    monkeypatch.setattr(DesignerStartupSettingsDialog, "execSelection", select)
    monkeypatch.setattr(startup, "prepareRuntime", fail)
    main.runDesigner()
    assert attempts == [True] and store.value("startup/dataRoot") == str(tmp_path / "previous")
    assert not store.contains("startup/mode")
