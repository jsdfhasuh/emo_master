import dataclasses
import threading
import time

from emo_master.apps.designer.services.operator_icon_cache import IconRequest
from emo_master.apps.designer.services.operator_icon_worker import OperatorIconWorker
from tests.designer.qt_wait import waitUntil
from tests.icon_fixtures import iconDefinition, iconReply


def testWorkersDeduplicateAndLimitConcurrencyAndPendingQueue():
    release = threading.Event()
    lock = threading.Lock()
    calls = []

    class Client:
        def getOperatorIconAsset(self, operatorId, *_, **kwargs):
            with lock:
                calls.append((operatorId, kwargs))
            release.wait(3)
            return iconReply()

    worker = OperatorIconWorker(Client())
    request = IconRequest.fromDefinition("s", 1, iconDefinition())
    try:
        for _ in range(30):
            assert worker.request(request)
        waitUntil(lambda: len(calls) == 1, pump=False)
        for i in range(200):
            worker.request(dataclasses.replace(request, operatorId=str(i)), priority=2)
        waitUntil(lambda: len(calls) == 4, pump=False)
        with worker._condition:
            assert len(worker._pending) <= 128
            assert len(worker._tasks) <= 132
        visible = dataclasses.replace(request, operatorId="visible")
        assert worker.request(visible, priority=0)
        assert all(call[1]["timeoutMs"] == 2000 for call in calls)
    finally:
        worker.beginClose()
        release.set()
        assert worker.wait(1)
    assert worker.poll() == []


def testCancelAndTimeoutRejectLateEmbeddedResults():
    release = threading.Event()
    started = threading.Event()
    contexts = []

    class Client:
        def getOperatorIconAsset(self, *_, cancellationToken=None, **kwargs):
            contexts.append(cancellationToken)
            started.set()
            release.wait(3)
            return iconReply()

    worker = OperatorIconWorker(Client(), workers=1)
    request = IconRequest.fromDefinition("s", 1, iconDefinition())
    try:
        worker.request(request, timeoutMs=30)
        assert started.wait(1)
        time.sleep(0.05)
        results = worker.poll()
        assert [r.code for r in results] == ["E_DISPLAY_TIMEOUT"]
        assert not contexts[0].is_active()
        assert worker.isRunning()  # Logical timeout did not terminate the embedded call.
        release.set()
    finally:
        worker.beginClose()
        release.set()
        assert worker.wait(1)
    assert worker.poll() == []


def testQueuedDeadlineStartsAtExecutionAndCancelledQueueNeverRuns():
    release = threading.Event()
    started = threading.Event()
    calls = []

    class Client:
        def getOperatorIconAsset(self, operatorId, *_, **kwargs):
            calls.append(operatorId)
            if operatorId == "first":
                started.set()
                release.wait(2)
            return iconReply()

    worker = OperatorIconWorker(Client(), workers=1)
    base = IconRequest.fromDefinition("s", 1, iconDefinition())
    first = dataclasses.replace(base, operatorId="first")
    second = dataclasses.replace(base, operatorId="second")
    removed = dataclasses.replace(base, operatorId="removed")
    try:
        worker.request(first)
        assert started.wait(1)
        worker.request(second, timeoutMs=50)
        worker.request(removed)
        worker.cancel(removed)
        time.sleep(0.08)
        assert worker.poll() == []
        release.set()
        results = []

        def completed():
            results.extend(worker.poll())
            return len(results) == 2

        waitUntil(completed, pump=False)
        assert all(not r.code for r in results)
        assert calls == ["first", "second"]
    finally:
        worker.beginClose()
        release.set()
        assert worker.wait(1)


def testMissingApiIsNotConfusedWithOtherFailures():
    worker = OperatorIconWorker(object(), workers=1)
    request = IconRequest.fromDefinition("s", 1, iconDefinition())
    results = []
    try:
        worker.request(request)

        def completed():
            results.extend(worker.poll())
            return bool(results)

        waitUntil(completed, pump=False)
        assert results[0].code == "UNIMPLEMENTED"
    finally:
        worker.beginClose()
        assert worker.wait(1)
