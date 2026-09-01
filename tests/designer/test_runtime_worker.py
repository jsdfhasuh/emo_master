from __future__ import annotations

import threading
import time

try:
    from PySide2.QtCore import QCoreApplication
except ImportError:
    QCoreApplication = None

from emo_master.apps.designer.services.runtime_worker import (
    RuntimeWorker,
    _runWorker,
)


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


def testRuntimeWorkerReconnectsFromLastSequenceWithoutDuplicatingEvents() -> None:
    class Stream:
        def __init__(self, values, error: Exception | None = None) -> None:
            self.values = iter(values)
            self.error = error

        def __iter__(self):
            return self

        def __next__(self):
            try:
                return next(self.values)
            except StopIteration:
                if self.error is not None:
                    error = self.error
                    self.error = None
                    raise error
                raise

        def close(self) -> None:
            return None

    def event(sequence: int, eventType: str):
        return type(
            "Event",
            (),
            {"sequence": sequence, "event_type": eventType},
        )()

    class Client:
        def __init__(self) -> None:
            self.streamCalls = []
            self.statusCalls = 0

        def startJob(self, projectId: str, workflowId: str = "", inputs=None):
            _ = projectId, workflowId, inputs
            return type("Reply", (), {"ok": True, "job_id": "job-reconnect"})()

        def iterJobEvents(
            self,
            jobId: str,
            afterSequence: int = 0,
            follow: bool = False,
        ):
            assert jobId == "job-reconnect"
            self.streamCalls.append((afterSequence, follow))
            if len(self.streamCalls) == 1:
                return Stream(
                    [event(1, "node.log")],
                    RuntimeError("temporary disconnect"),
                )
            if len(self.streamCalls) == 2:
                return Stream(
                    [
                        event(1, "node.log"),
                        event(2, "job.completed"),
                    ]
                )
            assert follow is True
            return Stream([event(3, "runtime.logfile.failed")])

        def getJobStatus(self, jobId: str):
            assert jobId == "job-reconnect"
            self.statusCalls += 1
            status = "RUNNING" if self.statusCalls == 1 else "COMPLETED"
            return type("Status", (), {"status": status})()

    client = Client()
    worker = RuntimeWorker(client, "project", "main")
    received = []
    failures = []
    statuses = []
    worker.eventReceived.connect(received.append)
    worker.failed.connect(failures.append)
    worker.statusChanged.connect(statuses.append)

    _runWorker(worker)

    assert [item.sequence for item in received] == [1, 2, 3]
    assert client.streamCalls == [(0, True), (1, True), (2, True)]
    assert failures == []
    assert [item.status for item in statuses] == ["COMPLETED"]


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


def testRuntimeWorkerStartJobCompatPassesOnlySupportedKeywords() -> None:
    calls = []

    class WorkflowOnlyClient:
        def startJob(self, projectId: str, workflowId: str = ""):
            calls.append((projectId, workflowId))
            return type("Reply", (), {"ok": True, "job_id": ""})()

    worker = RuntimeWorker(WorkflowOnlyClient(), "project", "body", {"value": 1})
    _runWorker(worker)

    assert calls == [("project", "body")]


def testRuntimeWorkerStartJobCompatSupportsInputsJsonOnlyClient() -> None:
    calls = []

    class InputsJsonClient:
        def startJob(self, projectId: str, inputs_json: str = "{}"):  # noqa: N803
            calls.append((projectId, inputs_json))
            return type("Reply", (), {"ok": True, "job_id": ""})()

    worker = RuntimeWorker(InputsJsonClient(), "project", "main", {"value": 1})
    _runWorker(worker)

    assert calls == [("project", '{"value": 1}')]


def testRuntimeWorkerPrefersIncrementalIteratorWhenAvailable() -> None:
    calls: list[str] = []

    class Client:
        def startJob(self, projectId: str, workflowId: str = "", inputs=None):
            _ = projectId
            _ = workflowId
            _ = inputs
            return type("Reply", (), {"ok": True, "job_id": "job-iter"})()

        def iterJobEvents(self, jobId: str, follow: bool = False):
            calls.append("iter")
            assert jobId == "job-iter"
            assert follow is True
            return iter(())

        def streamJobEvents(self, jobId: str, follow: bool = False):
            _ = jobId
            _ = follow
            calls.append("stream")
            raise AssertionError("worker used the materializing compatibility API")

        def getJobStatus(self, jobId: str):
            assert jobId == "job-iter"
            return type("Status", (), {"status": "COMPLETED"})()

    _runWorker(RuntimeWorker(Client(), "project", "main"))

    assert calls == ["iter"]
