from __future__ import annotations

import threading
import time

try:
    from PySide2.QtCore import QCoreApplication
except ImportError:
    QCoreApplication = None

from emo_master.apps.designer.services.runtime_worker import RuntimeWorker


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
