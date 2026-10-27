"""Cross-process startup conflicts must preserve the first Runtime's ownership."""
from __future__ import annotations

import errno
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

from emo_master.apps.runtime.context import runtime_lock
from emo_master.apps.runtime.context.runtime_lock import RuntimeDataLock


ROOT = Path(__file__).resolve().parents[2]


def test_second_designer_explains_busy_directory_and_can_choose_another_without_exiting(tmp_path):
    directory = tmp_path / "runtime-data"
    lock = RuntimeDataLock(directory / ".jobs.runtime.lock")
    lock.acquire()
    # The dialog interception lets the real Qt bootstrap exit under an outer deadline.
    code = """
import sys
from pathlib import Path
from PySide2.QtCore import QSettings, QTimer
from emo_master.apps.designer import main
from emo_master.apps.designer.ui import startup_settings_dialog
from emo_master.apps.designer.ui.project_entry_dialog import ProjectEntryDialog
directory = Path(sys.argv[1])
startup_settings_dialog.QSettings = lambda *_: QSettings(str(directory.parent/'settings.ini'), QSettings.IniFormat)
attempts = []
def select_settings(self):
    attempts.append(True)
    if len(attempts) == 2:
        assert str(directory) in self.errorLabel.text()
        assert '不要删除锁文件' in self.errorLabel.text()
        print('BUSY_CHOOSER_SHOWN', flush=True)
        self.selectDirectory(str(directory.parent/'alternate'))
    assert len(attempts) <= 2
    self.accept()
    return self._selection
startup_settings_dialog.DesignerStartupSettingsDialog.execSelection = select_settings
original = ProjectEntryDialog.execSelection
def cancel_project(self):
    QTimer.singleShot(50, self.reject)
    return original(self)
ProjectEntryDialog.execSelection = cancel_project
main.runDesigner()
assert len(attempts) == 2
print('RETRY_CLOSED_CLEANLY', flush=True)
"""
    environment = dict(os.environ, PYTHONPATH=str(ROOT / "src"),
                       EMO_RUNTIME_DATA_DIR=str(directory), QT_QPA_PLATFORM="offscreen",
                       PYTHONIOENCODING="utf-8")
    for name in ("EMO_RUNTIME_TARGET", "EMO_RUNTIME_DB_PATH", "EMO_MASTER_RUNTIME_DB_PATH", "EMO_RUNTIME_LOG_DIR"):
        environment.pop(name, None)
    try:
        result = subprocess.run(
            [sys.executable, "-c", code, str(directory)], cwd=ROOT, env=environment,
            capture_output=True, text=True, encoding="utf-8", timeout=30,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert "BUSY_CHOOSER_SHOWN" in result.stdout
        assert "RETRY_CLOSED_CLEANLY" in result.stdout
        assert "Traceback" not in result.stderr
        assert not (directory / "emo_master.db").exists()
        assert (tmp_path / "alternate/emo_master.db").is_file()
        # A second independent probe must still be rejected: no lock stealing/deletion.
        probe = """
from pathlib import Path
import sys
from emo_master.apps.runtime.context.runtime_lock import RuntimeDataLock, RuntimeDataInUseError
lock = RuntimeDataLock(Path(sys.argv[1]))
try:
    lock.acquire()
except RuntimeDataInUseError:
    raise SystemExit(2)
else:
    lock.release()
"""
        held = subprocess.run([sys.executable, "-c", probe, str(lock.path)],
                              cwd=ROOT, env=environment, timeout=15)
        assert held.returncode == 2
    finally:
        lock.release()
    released = subprocess.run([sys.executable, "-c", probe, str(lock.path)],
                              cwd=ROOT, env=environment, timeout=15)
    assert released.returncode == 0


def test_two_real_designer_bootstraps_use_independent_roots_and_close_independently(tmp_path):
    code = r'''
import sys
from pathlib import Path
from PySide2.QtCore import QSettings, QTimer
from emo_master.apps.designer import main
from emo_master.apps.designer.ui import startup_settings_dialog
from emo_master.apps.designer.ui.project_entry_dialog import ProjectEntryDialog
root = Path(sys.argv[1])
startup_settings_dialog.QSettings = lambda *_: QSettings(str(root/'settings.ini'), QSettings.IniFormat)
def select_settings(self):
    self.accept()
    return self._selection
startup_settings_dialog.DesignerStartupSettingsDialog.execSelection = select_settings
original = ProjectEntryDialog.execSelection
def wait_project(self):
    timer = QTimer(self)
    timer.setInterval(30)
    def poll():
        if (root/'exit-requested').exists():
            self.reject()
    timer.timeout.connect(poll)
    timer.start()
    (root/'ready').touch()
    return original(self)
ProjectEntryDialog.execSelection = wait_project
main.runDesigner()
'''
    processes = []
    try:
        for name in ("first", "second"):
            root = tmp_path / name
            env = dict(os.environ, PYTHONPATH=str(ROOT / "src"), EMO_RUNTIME_DATA_DIR=str(root),
                       QT_QPA_PLATFORM="offscreen", PYTHONIOENCODING="utf-8")
            for key in ("EMO_RUNTIME_TARGET", "EMO_RUNTIME_DB_PATH", "EMO_MASTER_RUNTIME_DB_PATH", "EMO_RUNTIME_LOG_DIR"):
                env.pop(key, None)
            processes.append(subprocess.Popen([sys.executable, "-c", code, str(root)], cwd=ROOT,
                                              env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE))
        deadline = time.monotonic() + 30
        while not all((tmp_path / name / "ready").exists() for name in ("first", "second")):
            assert all(process.poll() is None for process in processes), "Designer exited before project entry"
            assert time.monotonic() < deadline, "Designer startup timed out"
            time.sleep(0.05)
        assert all((tmp_path / name / "emo_master.db").is_file() for name in ("first", "second"))
        (tmp_path / "first/exit-requested").touch()
        firstOutput = processes[0].communicate(timeout=15)
        assert processes[0].returncode == 0, firstOutput
        assert processes[1].poll() is None
        released = RuntimeDataLock(tmp_path / "first/.jobs.runtime.lock")
        assert released.acquire()
        released.release()
        (tmp_path / "second/exit-requested").touch()
        secondOutput = processes[1].communicate(timeout=15)
        assert processes[1].returncode == 0, secondOutput
    finally:
        for process in processes:
            if process.poll() is None:
                process.terminate()
            process.communicate(timeout=15)


def test_other_lock_errors_keep_their_original_cause(tmp_path, monkeypatch):
    failure = OSError(errno.EIO, "simulated I/O failure")

    def fail(_handle):
        raise failure

    monkeypatch.setattr(runtime_lock, "_lockHandle", fail)
    with pytest.raises(OSError) as caught:
        RuntimeDataLock(tmp_path / "runtime.lock").acquire()
    assert caught.value is failure
