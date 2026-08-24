from __future__ import annotations

import threading

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


def testRuntimeWorkerDoesNotBlockCallerWhileFollowingJob() -> None:
    client = _RuntimeStub()
    accepted = threading.Event()
    events = []
    worker = RuntimeWorker(client, "project", "main")
    worker.jobAccepted.connect(lambda reply: accepted.set())
    worker.eventReceived.connect(events.append)
    worker.start()
    assert accepted.wait(timeout=2.0)
    assert worker.isRunning() is True
    client.release.set()
    worker.wait(3000)
    assert len(events) == 1
