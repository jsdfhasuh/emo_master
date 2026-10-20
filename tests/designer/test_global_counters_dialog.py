import threading
import time

import emo_master.apps.designer.ui.global_counters_dialog as dialogModule
from emo_master.apps.designer.services.runtime_client import RuntimeClientError
from emo_master.apps.designer.services.runtime_client import GlobalCounterInfo
from emo_master.apps.designer.ui.global_counters_dialog import GlobalCountersDialog


def testGlobalCountersDialogRefreshIsNonBlockingAndSkipsOverlap(
    designerApplication,
) -> None:
    qapp = designerApplication
    callingThread = threading.get_ident()

    class Client:
        def __init__(self) -> None:
            self.started = threading.Event()
            self.release = threading.Event()
            self.calls = 0
            self.workerThread = 0

        def listGlobalCounters(self, projectId: str):
            assert projectId == "project"
            self.calls += 1
            self.workerThread = threading.get_ident()
            self.started.set()
            self.release.wait(timeout=3.0)
            return [GlobalCounterInfo("parts", 3, 123)]

    client = Client()
    dialog = GlobalCountersDialog(client)
    dialog.showForProject("project")
    assert client.started.wait(timeout=2.0)
    assert client.workerThread != callingThread
    assert dialog.isRequestInFlight()

    deadline = time.monotonic() + 1.2
    while time.monotonic() < deadline:
        if qapp is not None:
            qapp.processEvents()
        dialog.refreshCounters()
        time.sleep(0.01)
    assert client.calls == 1

    client.release.set()
    _waitUntil(qapp, lambda: dialog.getDisplayedCounters() != [])
    assert dialog.getDisplayedCounters()[0]["value"] == 3
    assert dialog.refreshIntervalMs() == 1000
    dialog.shutdown()
    dialog.close()


def testGlobalCountersDialogNewSetAndResetUseBackgroundClient(
    designerApplication,
) -> None:
    qapp = designerApplication

    class Client:
        def __init__(self) -> None:
            self.values = {"parts": 5}
            self.calls = []

        def listGlobalCounters(self, projectId: str):
            self.calls.append(("list", projectId, "", 0))
            return [
                GlobalCounterInfo(name, value, 1)
                for name, value in self.values.items()
            ]

        def getGlobalCounter(self, projectId: str, name: str):
            self.calls.append(("get", projectId, name, 0))
            self.values.setdefault(name, 0)
            return GlobalCounterInfo(name, self.values[name], 2)

        def setGlobalCounter(self, projectId: str, name: str, value: int):
            self.calls.append(("set", projectId, name, value))
            self.values[name] = value
            return GlobalCounterInfo(name, value, 3)

        def resetGlobalCounter(self, projectId: str, name: str):
            self.calls.append(("reset", projectId, name, 0))
            self.values[name] = 0
            return GlobalCounterInfo(name, 0, 4)

    client = Client()
    dialog = GlobalCountersDialog(client)
    dialog.showForProject("project")
    _waitUntil(qapp, lambda: len(dialog.getDisplayedCounters()) == 1)
    timer = getattr(dialog, "_timer", None)
    if timer is not None:
        timer.stop()

    assert dialog.createCounter("new-counter") is True
    _waitUntil(qapp, lambda: len(dialog.getDisplayedCounters()) == 2)
    assert dialog.setCounter("parts", 9) is True
    _waitUntil(qapp, lambda: client.values["parts"] == 9)
    _waitUntil(qapp, lambda: not dialog.isRequestInFlight())
    assert dialog.resetCounter("parts") is True
    _waitUntil(qapp, lambda: client.values["parts"] == 0)

    assert any(call[0] == "get" for call in client.calls)
    assert ("set", "project", "parts", 9) in client.calls
    assert ("reset", "project", "parts", 0) in client.calls
    dialog.shutdown()
    dialog.close()


def testGlobalCountersDialogIgnoresReplyFromPreviousProject(
    designerApplication,
) -> None:
    qapp = designerApplication

    class Client:
        def __init__(self) -> None:
            self.oldStarted = threading.Event()
            self.releaseOld = threading.Event()
            self.calls = []

        def listGlobalCounters(self, projectId: str):
            self.calls.append(projectId)
            if projectId == "old-project":
                self.oldStarted.set()
                self.releaseOld.wait(timeout=3.0)
                return [GlobalCounterInfo("old", 1, 1)]
            return [GlobalCounterInfo("new", 2, 2)]

    client = Client()
    dialog = GlobalCountersDialog(client)
    dialog.showForProject("old-project")
    assert client.oldStarted.wait(timeout=2.0)
    dialog.bindProject("new-project")
    client.releaseOld.set()

    _waitUntil(
        qapp,
        lambda: dialog.getDisplayedCounters()
        == [{"name": "new", "value": 2, "updatedAtMs": 2}],
    )
    assert client.calls[:2] == ["old-project", "new-project"]
    dialog.shutdown()
    dialog.close()


def testGlobalCountersDialogSurfacesRpcFailure(
    designerApplication,
    monkeypatch,
) -> None:
    qapp = designerApplication
    warnings = []
    messageBox = getattr(dialogModule, "QMessageBox", None)
    if messageBox is not None:
        monkeypatch.setattr(
            messageBox,
            "warning",
            staticmethod(lambda parent, title, message: warnings.append((title, message))),
        )

    class Client:
        def listGlobalCounters(self, projectId: str):
            _ = projectId
            raise RuntimeClientError("E_COUNTER_BUSY", "busy")

    dialog = GlobalCountersDialog(Client())
    dialog.showForProject("project")
    _waitUntil(qapp, lambda: not dialog.isRequestInFlight())

    if messageBox is not None:
        assert warnings == [("全局计数器请求失败", "E_COUNTER_BUSY: busy")]
    else:
        assert getattr(dialog, "lastError", "") == "E_COUNTER_BUSY: busy"
    dialog.shutdown()
    dialog.close()


def _waitUntil(qapp, predicate, timeoutSeconds: float = 3.0) -> None:
    deadline = time.monotonic() + timeoutSeconds
    while time.monotonic() < deadline:
        if qapp is not None:
            qapp.processEvents()
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("condition was not reached before timeout")


def testCompletedCounterWorkersRetireOnGuiThread(
    designerApplication, monkeypatch,
) -> None:
    import gc
    import weakref

    import pytest

    if designerApplication is None:
        pytest.skip("Qt ownership requires QApplication")
    from PySide2.QtCore import QCoreApplication, QEvent, Qt
    from shiboken2 import isValid

    references = []
    destroyedOn = []
    originalInit = dialogModule.GlobalCounterWorker.__init__

    def recordWorker(worker, *args, **kwargs):
        originalInit(worker, *args, **kwargs)
        references.append(weakref.ref(worker))
        worker.destroyed.connect(
            lambda *_args: destroyedOn.append(threading.get_ident()),
            Qt.DirectConnection,
        )

    monkeypatch.setattr(dialogModule.GlobalCounterWorker, "__init__", recordWorker)

    class Client:
        def listGlobalCounters(self, projectId):
            return [GlobalCounterInfo("parts", 1, 1)]

    dialog = GlobalCountersDialog(Client())
    dialog.bindProject("project")
    try:
        for _ in range(20):
            dialog.refreshCounters()
            _waitUntil(designerApplication, lambda: not dialog.isRequestInFlight())
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        assert len(references) == 20
        assert destroyedOn == [threading.get_ident()] * 20
        assert all(reference() is None or not isValid(reference()) for reference in references)
        # Retired QObject natives must not be left for a later RPC thread's GC.
        collector = threading.Thread(target=gc.collect)
        collector.start()
        collector.join(3)
        assert not collector.is_alive()
        assert all(reference() is None for reference in references)
        assert destroyedOn == [threading.get_ident()] * 20
    finally:
        dialog.shutdown()
        dialog.close()


def testQueuedCounterCallbacksAreDroppedAfterDialogDeletion(
    designerApplication, monkeypatch,
) -> None:
    import sys

    import pytest

    if designerApplication is None:
        pytest.skip("Qt ownership requires QApplication")
    from PySide2.QtCore import QCoreApplication, QEvent, Slot
    from shiboken2 import isValid

    calls = []
    errors = []
    monkeypatch.setattr(sys, "excepthook", lambda *error: errors.append(error))

    class ObservedDialog(GlobalCountersDialog):
        @Slot(object)
        def _onWorkerResult(self, result):
            calls.append("result")
            super()._onWorkerResult(result)

        @Slot(object)
        def _onWorkerFailure(self, failure):
            calls.append("failure")
            super()._onWorkerFailure(failure)

        @Slot()
        def _onWorkerFinished(self):
            calls.append("finished")
            super()._onWorkerFinished()

    class Client:
        fail = False

        def listGlobalCounters(self, projectId):
            if self.fail:
                raise RuntimeClientError("E_COUNTER_BUSY", "synthetic failure")
            return [GlobalCounterInfo("parts", 1, 1)]

    # A completed request can have result/finished MetaCalls queued when the
    # owner closes. Cover normal shutdown and QObject parent-driven deletion.
    for fail in (False, True):
        for shutdown in (False, True):
            client = Client()
            client.fail = fail
            dialog = ObservedDialog(client)
            dialog.bindProject("project")
            dialog.refreshCounters()
            worker = dialog._worker
            assert worker is not None
            assert worker.wait(2000)
            assert calls == []
            if shutdown:
                dialog.shutdown()
            dialog.close()
            dialog.deleteLater()
            QCoreApplication.sendPostedEvents(dialog, QEvent.DeferredDelete)
            assert not isValid(dialog)
            _waitUntil(designerApplication, lambda: not isValid(worker))
            assert calls == []
            assert errors == []


def testActiveCounterWorkerOutlivesDeletedDialogUntilFinished(
    designerApplication, monkeypatch,
) -> None:
    import gc
    import sys
    import weakref

    import pytest

    if designerApplication is None:
        pytest.skip("Qt ownership requires QApplication")
    from PySide2.QtCore import QCoreApplication, QEvent, Qt
    from shiboken2 import isValid

    class Client:
        def __init__(self):
            self.started = threading.Event()
            self.release = threading.Event()

        def listGlobalCounters(self, projectId):
            self.started.set()
            assert self.release.wait(3)
            return [GlobalCounterInfo("parts", 1, 1)]

    errors = []
    destroyedOn = []
    monkeypatch.setattr(sys, "excepthook", lambda *error: errors.append(error))
    client = Client()
    dialog = GlobalCountersDialog(client)
    dialog.bindProject("project")
    dialog.refreshCounters()
    reference = weakref.ref(dialog._worker)
    dialog._worker.destroyed.connect(
        lambda *_args: destroyedOn.append(threading.get_ident()), Qt.DirectConnection,
    )
    try:
        assert client.started.wait(2)
        # Delete the native dialog without shutdown: the application must retain
        # its running thread until finished; no slot may address deleted widgets.
        dialog.deleteLater()
        QCoreApplication.sendPostedEvents(dialog, QEvent.DeferredDelete)
        assert not isValid(dialog)
        del dialog
        gc.collect()
        assert reference() is not None and isValid(reference())
        assert reference().parent().parent() is designerApplication
        assert reference().isRunning()
        client.release.set()
        _waitUntil(
            designerApplication,
            lambda: reference() is None or not isValid(reference()),
        )
        assert destroyedOn == [threading.get_ident()]
        assert errors == []
    finally:
        client.release.set()
        worker = reference()
        if worker is not None and isValid(worker):
            worker.wait(2000)
        designerApplication.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)


def testApplicationExitJoinsCounterWorkerWhoseDialogWasDeleted(
    designerApplication,
) -> None:
    from pathlib import Path
    import subprocess
    import sys
    import textwrap

    import pytest

    if designerApplication is None:
        pytest.skip("Qt ownership requires QApplication")
    source = textwrap.dedent('''
        import threading
        import emo_master
        from PySide2.QtCore import QCoreApplication, QEvent, QTimer, Qt
        from PySide2.QtWidgets import QApplication
        from shiboken2 import delete, isValid
        from emo_master.apps.designer.ui.global_counters_dialog import GlobalCountersDialog

        application = QApplication([])
        application.setQuitOnLastWindowClosed(False)
        guiThread = threading.get_ident()
        started = threading.Event()
        release = threading.Event()
        destroyedOn = []

        class Client:
            def listGlobalCounters(self, projectId):
                started.set()
                assert release.wait(3)
                return []

        dialog = GlobalCountersDialog(Client())
        dialog.bindProject("synthetic-project")
        dialog.refreshCounters()
        worker = dialog._worker
        worker.destroyed.connect(
            lambda *_args: destroyedOn.append(threading.get_ident()),
            Qt.DirectConnection,
        )
        assert started.wait(2)
        dialog.deleteLater()
        QCoreApplication.sendPostedEvents(dialog, QEvent.DeferredDelete)
        assert not isValid(dialog)
        del dialog
        # A Python timer can release the RPC while GUI-thread shutdown joins it.
        # A Qt timer cannot run while that wait blocks the event loop.
        releaser = threading.Timer(0.05, release.set)
        releaser.start()
        QTimer.singleShot(0, application.quit)
        application.exec_()
        releaser.join(2)
        assert not isValid(worker) or not worker.isRunning()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        delete(application)
        assert not isValid(worker)
        assert destroyedOn == [guiThread]
        print("application exit joined and retired the counter worker")
    ''')
    result = subprocess.run(
        [sys.executable, "-c", source],
        cwd=Path(__file__).resolve().parents[2] / "src",
        capture_output=True, text=True, encoding="utf-8", timeout=10,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "application exit joined and retired the counter worker" in result.stdout
    assert "QThread: Destroyed while thread is still running" not in result.stderr
    assert "already deleted" not in result.stderr
    assert "Timers cannot be stopped from another thread" not in result.stderr
