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
