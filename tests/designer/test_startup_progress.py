from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
from threading import Event, get_ident
from time import perf_counter
from types import SimpleNamespace

import pytest
from PySide2.QtCore import QTimer
from PySide2.QtWidgets import QMessageBox

from emo_master.apps.designer import main, startup
from emo_master.apps.designer.ui.startup_dialog import DesignerStartupDialog


ROOT = Path(__file__).resolve().parents[2]


def test_timing_includes_launcher_and_reports_failed_stage(monkeypatch, capsys):
    clock = [12.0]
    monkeypatch.setattr(startup, "perf_counter", lambda: clock[0])
    monkeypatch.setenv("EMO_DESIGNER_LAUNCH_STARTED", "2")
    timing = startup.StartupTiming(10)
    assert "EMO_DESIGNER_LAUNCH_STARTED" not in os.environ
    timing.mark("entry")
    timing.begin("runtime-plugins")
    clock[0] = 14
    timing.begin("runtime-online-configuration")
    clock[0] = 17
    timing.finish("failed")
    lines = capsys.readouterr().out
    assert "entry total=10.000s" in lines
    assert "runtime-plugins done elapsed=2.000s total=12.000s" in lines
    assert "runtime-online-configuration failed elapsed=3.000s total=15.000s" in lines


@pytest.mark.parametrize("raw", ["", "invalid", "nan", "inf", "-1", "100"])
def test_invalid_or_future_launcher_time_uses_entry_time(raw, monkeypatch):
    monkeypatch.setenv("EMO_DESIGNER_LAUNCH_STARTED", raw)
    assert startup.StartupTiming(10).started == 10


@pytest.mark.parametrize("cancelWith", ["button", "close", "escape"])
def test_startup_paints_before_work_and_cancel_remains_responsive(cancelWith):
    timing = startup.StartupTiming(perf_counter())
    timing.begin("startup-window")
    dialog = DesignerStartupDialog(timing)
    started, release = Event(), Event()
    ticks, workers = [], []
    client = object()
    guiThread = get_ident()

    def prepare(cancelled):
        workers.append((get_ident(), dialog._painted))
        timing.begin("runtime-plugins")
        started.set()
        assert release.wait(3), "GUI did not respond during initialization"
        assert cancelled.is_set()
        return client

    def tick():
        if not started.is_set():
            return
        dialog.refreshStatus()
        ticks.append((dialog.isVisible(), dialog.statusLabel.text(), dialog.elapsedLabel.text()))
        if cancelWith == "button":
            dialog.cancelButton.click()
        elif cancelWith == "close":
            dialog.close()
        else:
            dialog.reject()
        assert dialog.isVisible()
        release.set()
        timer.stop()

    timer = QTimer(dialog)
    timer.setInterval(10)
    timer.timeout.connect(tick)
    timer.start()
    try:
        assert dialog.prepare(prepare) is client
        assert dialog.cancelled.is_set()
        assert workers == [(dialog._thread.ident, True)]
        assert workers[0][0] != guiThread
        assert ticks and ticks[0][0] and "扫描" in ticks[0][1] and "秒" in ticks[0][2]
        assert not dialog._thread.is_alive()
    finally:
        release.set()
        dialog.finish()


def test_cancel_before_first_paint_does_not_start_preparation():
    dialog = DesignerStartupDialog(startup.StartupTiming(perf_counter()))
    dialog.reject()
    try:
        assert dialog.prepare(lambda _: pytest.fail("cancelled preparation ran")) is None
        assert dialog._thread is None
    finally:
        dialog.finish()


def test_prepare_failure_returns_to_gui_thread_and_finishes_worker():
    dialog = DesignerStartupDialog(startup.StartupTiming(perf_counter()))
    error = OSError("startup test failure")

    def fail(_cancelled):
        raise error

    try:
        with pytest.raises(OSError) as caught:
            dialog.prepare(fail)
        assert caught.value is error
        assert not dialog._thread.is_alive()
    finally:
        dialog.finish()


def test_cancelled_bootstrap_closes_prepared_client_without_building_editor(monkeypatch, designerApplication):
    from PySide2 import QtWidgets
    from emo_master.apps.designer.ui import main_window

    started, release = Event(), Event()
    closed = []
    client = SimpleNamespace(close=lambda: closed.append("client"))
    originalQuit = designerApplication.quitOnLastWindowClosed()

    def prepare(_timing, cancelled):
        started.set()
        assert release.wait(3)
        assert cancelled.is_set()
        return client

    def cancel():
        if not started.is_set():
            return
        for widget in designerApplication.topLevelWidgets():
            if isinstance(widget, DesignerStartupDialog) and widget.isVisible():
                widget.close()
                release.set()
                timer.stop()

    timer = QTimer()
    timer.setInterval(10)
    timer.timeout.connect(cancel)
    monkeypatch.setattr(QtWidgets, "QApplication", lambda _: designerApplication)
    monkeypatch.setattr(startup, "prepareRuntime", prepare)
    monkeypatch.setattr(main_window, "MainWindow", lambda *_a, **_k: pytest.fail("editor built after cancel"))
    timer.start()
    try:
        main.runDesigner()
    finally:
        release.set()
        timer.stop()
    assert closed == ["client"]
    assert designerApplication.quitOnLastWindowClosed() is originalQuit


def test_bootstrap_error_is_visible_and_restores_quit_policy(monkeypatch, designerApplication, capsys):
    from PySide2 import QtWidgets

    originalQuit = designerApplication.quitOnLastWindowClosed()
    messages = []

    def fail(timing, _cancelled):
        timing.begin("runtime-plugins")
        raise OSError("startup test failure")

    monkeypatch.setattr(QtWidgets, "QApplication", lambda _: designerApplication)
    monkeypatch.setattr(startup, "prepareRuntime", fail)
    monkeypatch.setattr(QMessageBox, "exec_", lambda self: messages.append(self.text()) or QMessageBox.Ok)
    with pytest.raises(SystemExit) as error:
        main.runDesigner()
    assert error.value.code == 1
    assert messages and "startup test failure" in messages[0]
    assert designerApplication.quitOnLastWindowClosed() is originalQuit
    output = capsys.readouterr()
    assert "runtime-plugins failed" in output.out
    assert "Traceback" in output.err


def test_real_bootstrap_paints_before_runtime_import_and_reports_ready_before_selection(tmp_path):
    code = r'''
import sys
from pathlib import Path
from emo_master.apps.designer import main, startup
from emo_master.apps.designer.ui.startup_dialog import DesignerStartupDialog
from emo_master.apps.designer.ui.project_entry_dialog import ProjectEntryDialog
from PySide2 import QtCore

assert 'emo_master.apps.runtime.grpc_server.service' not in sys.modules
assert 'emo_master.apps.designer.ui.main_window' not in sys.modules
settingsType = QtCore.QSettings
QtCore.QSettings = lambda *_: settingsType(str(Path(sys.argv[1])/'designer.ini'), settingsType.IniFormat)
originalPaint = DesignerStartupDialog.paintEvent
def paint(self, event):
    if not self._painted:
        assert 'emo_master.apps.runtime.grpc_server.service' not in sys.modules
        assert 'emo_master.apps.designer.ui.main_window' not in sys.modules
        print('PAINT_BEFORE_HEAVY_IMPORTS', flush=True)
    originalPaint(self, event)
DesignerStartupDialog.paintEvent = paint
ready = []
originalMark = startup.StartupTiming.mark
def mark(self, stage):
    if stage == 'ready':
        ready.append(stage)
    originalMark(self, stage)
startup.StartupTiming.mark = mark
originalExec = ProjectEntryDialog.execSelection
def select(self):
    def cancel():
        assert ready == ['ready'], 'ready must precede the user selection'
        self.reject()
    QtCore.QTimer.singleShot(250, cancel)
    return originalExec(self)
ProjectEntryDialog.execSelection = select
main.runDesigner()
assert ready == ['ready']
from emo_master.apps.runtime.context.runtime_lock import RuntimeDataLock
lock = RuntimeDataLock(Path(sys.argv[1])/'.jobs.runtime.lock')
assert lock._key not in lock._held
assert lock.acquire()
lock.release()
print('STARTUP_CLEANLY_CLOSED', flush=True)
'''
    environment = dict(os.environ, PYTHONPATH=str(ROOT / "src"),
                       EMO_RUNTIME_DATA_DIR=str(tmp_path), QT_QPA_PLATFORM="offscreen", PYTHONIOENCODING="utf-8")
    for name in ("EMO_RUNTIME_TARGET", "EMO_RUNTIME_DB_PATH", "EMO_MASTER_RUNTIME_DB_PATH", "EMO_RUNTIME_LOG_DIR"):
        environment.pop(name, None)
    result = subprocess.run([sys.executable, "-c", code, str(tmp_path)], cwd=ROOT,
                            env=environment, capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "PAINT_BEFORE_HEAVY_IMPORTS" in result.stdout
    assert "runtime-online-configuration done" in result.stdout
    assert "STARTUP_CLEANLY_CLOSED" in result.stdout
    assert result.stdout.count("[designer:start] ready total=") == 1
