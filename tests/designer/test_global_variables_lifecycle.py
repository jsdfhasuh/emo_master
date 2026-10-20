"""Original Qt slots must not run after their dialog's native parent retires."""

import sys
import threading

import pytest
from PySide2.QtCore import QCoreApplication, QEvent, Qt
from PySide2.QtWidgets import QWidget
from shiboken2 import isValid

from emo_master.apps.designer.ui.global_variables_dialog import GlobalVariablesDialog
from tests.designer.test_global_counters_dialog import _waitUntil


class _Client:
    definition = dict(name="enabled", type="boolean", initialValue=True,
                      lifetime="persistent", kind="variable")

    def __init__(self, active):
        self.active = active
        self.started = threading.Event()
        self.release = threading.Event()

    def globalVariableState(self, _project, _job):
        self.started.set()
        if self.active:
            assert self.release.wait(3)
        return {"variables": [{"variableId": "v", **self.definition, "state": "current",
                               "value": True, "revision": 1, "updatedAtMs": 1}], "jobs": []}


@pytest.mark.parametrize("active", [False, True], ids=["queued-completion", "active-request"])
@pytest.mark.parametrize("close", [False, True], ids=["parent-deletion", "close-then-parent-deletion"])
def testParentDeletionDropsOriginalVariableFinishedSlot(designerApplication, monkeypatch, active, close):
    errors = []
    monkeypatch.setattr(sys, "excepthook", lambda *error: errors.append(error))
    client = _Client(active)
    parent = QWidget()
    dialog = GlobalVariablesDialog(client, lambda: {"v": client.definition}, lambda _values: None,
                                   lambda: False, lambda: "", parent)
    dialog.bindProject("project")
    dialog.refreshVariables()
    worker = dialog._worker
    destroyedOn = []
    worker.destroyed.connect(lambda *_args: destroyedOn.append(threading.get_ident()), Qt.DirectConnection)
    guiThread = threading.get_ident()
    try:
        assert client.started.wait(2)
        if not active:
            assert worker.wait(2000)
        assert dialog._worker is worker
        if close:
            assert dialog.close()
        # Parent-driven native deletion does not call the child's closeEvent.
        # Keep its Python wrapper to verify that the original @Slot was dropped.
        parent.deleteLater()
        QCoreApplication.sendPostedEvents(parent, QEvent.DeferredDelete)
        assert not isValid(parent) and not isValid(dialog)
        assert isValid(worker) and worker.parent().parent() is designerApplication
        if active:
            assert worker.isRunning()
        client.release.set()

        def retired():
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
            return not isValid(worker)

        _waitUntil(designerApplication, retired)
        # This Python attribute remains readable after native deletion. The
        # original slot clears it on entry, so identity detects even a callback
        # that would exit early because close() set _closing.
        assert dialog._worker is worker
        assert errors == []
        assert destroyedOn == [guiThread]
    finally:
        client.release.set()
        if isValid(worker):
            worker.wait(3000)
        if isValid(parent):
            dialog.shutdown()
            parent.deleteLater()
            QCoreApplication.sendPostedEvents(parent, QEvent.DeferredDelete)
        designerApplication.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
