import threading

from emo_master.apps.designer.services.global_counter_worker import GlobalCounterWorker
from emo_master.apps.designer.services.runtime_client import GlobalCounterInfo


def testGlobalCounterWorkerRunsBlockingRpcOffCallingThread() -> None:
    callingThread = threading.get_ident()

    class Client:
        def __init__(self) -> None:
            self.started = threading.Event()
            self.release = threading.Event()
            self.workerThread = 0

        def listGlobalCounters(self, projectId: str):
            assert projectId == "project"
            self.workerThread = threading.get_ident()
            self.started.set()
            self.release.wait(timeout=2.0)
            return [GlobalCounterInfo("parts", 3, 1)]

    client = Client()
    worker = GlobalCounterWorker(client, "list", "project", 5)
    # Native Qt startup must not query the PySide metaobject for a thread name.
    assert worker.objectName() == "GlobalCounterWorker"
    worker.start()

    assert client.started.wait(timeout=2.0)
    assert client.workerThread != callingThread
    assert worker.isRunning()
    client.release.set()
    assert worker.wait(2000)
    assert worker.result is not None
    assert worker.result.generation == 5
    assert worker.result.payload == [GlobalCounterInfo("parts", 3, 1)]


def testGlobalCounterWorkerDispatchesMutationMethods() -> None:
    class Client:
        def __init__(self) -> None:
            self.calls = []

        def setGlobalCounter(self, projectId: str, name: str, value: int):
            self.calls.append((projectId, name, value))
            return GlobalCounterInfo(name, value, 2)

    client = Client()
    worker = GlobalCounterWorker(
        client,
        "set",
        "project",
        1,
        name="parts",
        value=12,
    )
    # Native Qt startup must not query the PySide metaobject for a thread name.
    assert worker.objectName() == "GlobalCounterWorker"
    worker.start()
    assert worker.wait(2000)
    assert client.calls == [("project", "parts", 12)]
    assert worker.result is not None
    assert getattr(worker.result.payload, "value", -1) == 12
