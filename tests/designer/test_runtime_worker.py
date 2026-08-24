from __future__ import annotations

import threading
import time

try:
    from PySide2.QtCore import QCoreApplication
except ImportError:
    QCoreApplication = None

from emo_master.apps.designer.services.runtime_worker import RuntimeWorker, _runWorker


class _RuntimeStub:
    def __init__(self) -> None:
        self.release = threading.Event()

    def startJob(self, projectId: str, workflowId: str = "", inputs=None):
        _ = projectId
        _ = workflowId
        _ = inputs
        return type("Reply", (), {"ok": True, "job_id": "job-worker", "status": "ACCEPTED"})()

    def streamJobEvents(self, jobId: str, follow: bool = False):
        assert jobId == "job-worker"
        assert follow is True
        self.release.wait(timeout=2.0)
        return [
            type(
                "Event",
                (),
                {
                    "event_type": "job.completed",
                    "message": "done",
                    "level": "INFO",
                    "node_id": "",
                    "payload": {},
                },
            )()
        ]

    def getJobStatus(self, jobId: str):
        assert jobId == "job-worker"
        return type("Status", (), {"status": "COMPLETED", "message": "done"})()


def _waitForEvent(event: threading.Event, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    application = QCoreApplication.instance() if QCoreApplication is not None else None
    while not event.is_set():
        if application is not None:
            application.processEvents()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        event.wait(min(0.01, remaining))
    return True


def testRuntimeWorkerDoesNotBlockCallerWhileFollowingJob() -> None:
    client = _RuntimeStub()
    accepted = threading.Event()
    events = []
    worker = RuntimeWorker(client, "project", "main")
    worker.jobAccepted.connect(lambda reply: accepted.set())
    worker.eventReceived.connect(events.append)
    worker.start()
    assert _waitForEvent(accepted, timeout=2.0)
    assert worker.isRunning() is True
    client.release.set()
    worker.wait(3000)
    if QCoreApplication is not None:
        application = QCoreApplication.instance()
        if application is not None:
            application.processEvents()
    assert len(events) == 1


def testRuntimeWorkerRequestStopCancelsActiveStreamAndClientSubscription() -> None:
    class Client:
        def __init__(self) -> None:
            self.cancelled = []

        def cancelEventStream(self, jobId: str) -> None:
            self.cancelled.append(jobId)

    class Stream:
        def __init__(self) -> None:
            self.cancelled = 0

        def cancel(self) -> None:
            self.cancelled += 1

    client = Client()
    stream = Stream()
    worker = RuntimeWorker(client, "project", "main")
    worker.setJobId("job-stop")
    worker.setActiveStream(stream)

    worker.requestStop()

    assert worker.stopRequested() is True
    assert stream.cancelled == 1
    assert client.cancelled == ["job-stop"]


def testRuntimeWorkerClearsStreamAndReportsFailureAfterStreamError() -> None:
    class Client:
        def startJob(self, projectId: str, workflowId: str = "", inputs=None):
            _ = projectId
            _ = workflowId
            _ = inputs
            return type("Reply", (), {"ok": True, "job_id": "job-error"})()

        def streamJobEvents(self, jobId: str, follow: bool = False):
            assert jobId == "job-error"
            assert follow is True
            return Stream()

    class Stream:
        def __iter__(self):
            return self

        def __next__(self):
            raise RuntimeError("stream failed")

        def close(self) -> None:
            self.closed = True

    worker = RuntimeWorker(Client(), "project", "main")
    failures = []
    worker.failed.connect(failures.append)

    _runWorker(worker)

    assert failures == ["stream failed"]
    assert worker._stream is None


def testRuntimeWorkerStopBeforeStartDoesNotCreateAJob() -> None:
    class Client:
        def __init__(self) -> None:
            self.started = False

        def startJob(self, projectId: str, workflowId: str = "", inputs=None):
            _ = projectId
            _ = workflowId
            _ = inputs
            self.started = True
            return type("Reply", (), {"ok": True, "job_id": "job-never-started"})()

    client = Client()
    worker = RuntimeWorker(client, "project", "main")
    worker.requestStop()

    _runWorker(worker)

    assert client.started is False
