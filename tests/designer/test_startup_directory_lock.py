"""Cross-process startup conflicts must preserve the first Runtime's ownership."""
from __future__ import annotations

import errno
import os
from pathlib import Path
import subprocess
import sys

import pytest

from emo_master.apps.runtime.context import runtime_lock
from emo_master.apps.runtime.context.runtime_lock import RuntimeDataLock


ROOT = Path(__file__).resolve().parents[2]


def test_second_designer_explains_busy_directory_and_can_retry_after_release(tmp_path):
    directory = tmp_path / "runtime-data"
    lock = RuntimeDataLock(directory / ".jobs.runtime.lock")
    lock.acquire()
    # The dialog interception lets the real Qt bootstrap exit under an outer deadline.
    code = """
import sys
from pathlib import Path
import emo_master
from PySide2.QtWidgets import QMessageBox
from emo_master.apps.designer import main
def show_warning(self):
    title, message = self.windowTitle(), self.text()
    assert 'Designer' in title
    assert sys.argv[1] in message
    assert '不要删除锁文件' in message
    print('BUSY_DIALOG_SHOWN', flush=True)
    return QMessageBox.Ok
QMessageBox.exec_ = show_warning
main.runDesigner()
raise AssertionError('busy startup unexpectedly continued')
"""
    environment = dict(os.environ, PYTHONPATH=str(ROOT / "src"),
                       EMO_RUNTIME_DATA_DIR=str(directory), QT_QPA_PLATFORM="offscreen",
                       PYTHONIOENCODING="utf-8")
    for name in ("EMO_RUNTIME_TARGET", "EMO_RUNTIME_DB_PATH", "EMO_MASTER_RUNTIME_DB_PATH"):
        environment.pop(name, None)
    try:
        result = subprocess.run(
            [sys.executable, "-c", code, str(directory)], cwd=ROOT, env=environment,
            capture_output=True, text=True, encoding="utf-8", timeout=30,
        )
        assert result.returncode == 2, result.stdout + result.stderr
        assert "BUSY_DIALOG_SHOWN" in result.stdout
        assert "Traceback" not in result.stderr
        assert not (directory / "emo_master.db").exists()
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


def test_other_lock_errors_keep_their_original_cause(tmp_path, monkeypatch):
    failure = OSError(errno.EIO, "simulated I/O failure")

    def fail(_handle):
        raise failure

    monkeypatch.setattr(runtime_lock, "_lockHandle", fail)
    with pytest.raises(OSError) as caught:
        RuntimeDataLock(tmp_path / "runtime.lock").acquire()
    assert caught.value is failure
