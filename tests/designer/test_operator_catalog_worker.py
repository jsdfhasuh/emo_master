import threading

from emo_master.apps.designer.services.operator_catalog_worker import OperatorCatalogWorker
from tests.designer.qt_wait import waitUntil


def testCatalogRefreshMergesAndDiscardsPreviousScope():
    release = threading.Event()
    calls = []

    class Client:
        def listOperators(self, *, timeoutMs, cancellationToken, owner):
            calls.append((timeoutMs, cancellationToken, owner))
            if len(calls) == 1:
                release.wait(2)
            return [len(calls)]

    worker = OperatorCatalogWorker(Client())
    try:
        first = worker.refresh("old")
        for _ in range(20):
            assert worker.refresh("old") == first
        waitUntil(lambda: len(calls) == 1, pump=False)
        second = worker.refresh("new")
        assert second != first
        assert not calls[0][1].is_active()
        release.set()
        results = []

        def complete():
            results.extend(worker.poll())
            return bool(results)

        waitUntil(complete, pump=False)
        assert len(results) == 1 and results[0].key == second
        assert results[0].payload == [2]
        assert all(c[0] == 5000 for c in calls)
        assert worker.currentRequest is None
    finally:
        worker.beginClose()
        release.set()
        assert worker.wait(1)
