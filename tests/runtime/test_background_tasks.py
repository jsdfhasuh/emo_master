from concurrent.futures import ThreadPoolExecutor
import threading
import weakref

import numpy as np
import pytest

from emo_master.apps.runtime.execution import background_tasks
from emo_master.apps.runtime.execution.background_tasks import BackgroundTaskService
from emo_master.apps.runtime.execution.errors import WorkflowExecutionError
from emo_master.apps.runtime.workflow.cancellation import CancellationRequested, CancellationToken
from emo_master.apps.runtime.workflow.context import RunContext


def _client(service, node="save", job="job"):
    context = RunContext.root(job, "flow").forNode(node)
    return service.client(context, node), context


def test_shared_fifo_submit_wait_and_job_isolation():
    firstJob, secondJob = BackgroundTaskService(), BackgroundTaskService()
    producer, _ = _client(firstJob)
    consumer, _ = _client(firstJob, "consumer")
    independent, _ = _client(secondJob, job="second-job")
    started, release = threading.Event(), threading.Event()
    order = []

    def record(control, value):
        if value == 1:
            started.set()
            assert release.wait(5)
        order.append(value)
        return threading.current_thread()

    try:
        first = producer.submit(record, 1, taskName="first", estimatedBytes=0)
        assert started.wait(5)
        second = consumer.submit(record, 2, taskName="second", estimatedBytes=0)
        assert consumer.status(first.id) == "RUNNING"
        assert consumer.status(second.id) == "QUEUED"
        other = independent.submit(record, 3, taskName="independent", estimatedBytes=0)
        otherThread = independent.wait(other.id)
        assert order == [3]
        with pytest.raises(WorkflowExecutionError) as unknown:
            independent.wait(first.id)
        assert unknown.value.code == "E_BACKGROUND_TASK_EXPIRED"
        release.set()
        worker = consumer.wait(first.id)
        assert producer.wait(second.id) is worker
        assert worker is not otherThread and worker is not threading.current_thread()
        assert producer.status(second.id) == "SUCCEEDED"
        assert order == [3, 1, 2]
    finally:
        release.set()
        firstJob.close()
        secondJob.close()


def test_failures_preserve_cause_source_and_observation_order():
    service = BackgroundTaskService(maxRecords=1)
    client, context = _client(service)
    diskError = OSError("disk full")
    diskError.code = -2  # Native plugin errors such as cv2.error have integer codes.

    def fail(control, error, payload=None):
        try:
            raise IOError("write failed")
        except IOError as cause:
            raise error from cause

    image = np.zeros((128, 128), dtype=np.uint8)
    imageRef = weakref.ref(image)
    bound = client.submit(fail, diskError, image, taskName="save", estimatedBytes=image.nbytes)
    service.pin(bound.id)
    del image
    other = client.submit(fail, ValueError("bad value"), taskName="other", estimatedBytes=0)
    client.submit(lambda control: None, taskName="next", estimatedBytes=0)
    service.close()
    assert imageRef() is None
    assert client.status(bound.id) == "FAILED"
    with pytest.raises(WorkflowExecutionError, match="bad value") as polled:
        service.raiseUnobservedFailure(exclude={bound.id})
    assert polled.value.diagnostics["backgroundTask"]["taskId"] == other.id
    with pytest.raises(WorkflowExecutionError, match="disk full") as waited:
        client.wait(bound.id)
    assert waited.value.__cause__ is diskError
    assert str(diskError.__cause__) == "write failed"
    assert waited.value.code == "E_BACKGROUND_TASK_FAILED"
    assert waited.value.diagnostics["backgroundTask"] == {
        "taskName": "save", "taskId": bound.id, "jobId": "job", "workflowId": "flow",
        "workflowRunId": context.workflowRunId, "nodeId": "save", "nodeRunId": context.nodeRunId,
    }
    service.release(bound.id)
    service.raiseUnobservedFailure()


@pytest.mark.parametrize("maxPending, maxBytes, cancel", [(2, 100, False), (8, 10, True)])
def test_queue_backpressure_accounts_for_running_and_queued_tasks(maxPending, maxBytes, cancel, monkeypatch):
    token = CancellationToken()
    service = BackgroundTaskService(token, maxPending=maxPending, maxBytes=maxBytes)
    client, _ = _client(service)
    started, release = threading.Event(), threading.Event()
    waiting, submitThread = threading.Event(), []

    def block(control):
        started.set()
        assert release.wait(5)
        control.raiseIfCancelled()

    # Signal precisely when the submitting thread reaches the capacity wait.
    originalWait = service._condition.wait

    def observedWait(timeout=None):
        if threading.current_thread() in submitThread:
            waiting.set()
        return originalWait(timeout)

    monkeypatch.setattr(service._condition, "wait", observedWait)

    def third():
        submitThread.append(threading.current_thread())
        return client.submit(lambda control: 3, taskName="third", estimatedBytes=2)

    try:
        with pytest.raises(WorkflowExecutionError) as oversized:
            client.submit(block, taskName="too large", estimatedBytes=maxBytes + 1)
        assert oversized.value.code == "E_BACKGROUND_TASK_TOO_LARGE"
        first = client.submit(block, taskName="one", estimatedBytes=7)
        assert started.wait(5)
        second = client.submit(lambda control: 2, taskName="two", estimatedBytes=2)
        with ThreadPoolExecutor(max_workers=1) as callers:
            thirdResult = callers.submit(third)
            assert waiting.wait(5)
            assert not thirdResult.done()
            if cancel:
                token.cancel()
                with pytest.raises(CancellationRequested):
                    thirdResult.result(2)
                closed = callers.submit(service.close, drain=False, waitForActive=True)
                with service._condition:
                    assert service._condition.wait_for(lambda: client.status(second.id) == "CANCELLED", 2)
                assert not closed.done()
                with pytest.raises(CancellationRequested):
                    client.wait(second.id)
                release.set()
                closed.result(2)
                assert client.status(first.id) == "CANCELLED"
            else:
                release.set()
                assert client.wait(thirdResult.result(2).id) == 3
                assert client.wait(second.id) == 2
                assert client.status(first.id) == "SUCCEEDED"
    finally:
        release.set()
        service.close(drain=False)
        service._thread.join(5)
    assert service._outstanding == 0 and service._bytes == 0


def test_recent_history_pins_and_unobserved_error_survive_eviction():
    service = BackgroundTaskService(maxRecords=1)
    client, _ = _client(service)

    def fail(control):
        raise OSError("unobserved")

    try:
        failed = client.submit(fail, taskName="unread", estimatedBytes=0)
        pinned = client.submit(lambda control: "file", taskName="pinned", estimatedBytes=0)
        service.pin(pinned.id)  # artifact
        service.pin(pinned.id)  # FILE_REFERENCE
        recent = client.submit(lambda control: None, taskName="recent", estimatedBytes=0)
        client.wait(recent.id)
        with pytest.raises(WorkflowExecutionError) as expired:
            client.status(failed.id)
        assert expired.value.code == "E_BACKGROUND_TASK_EXPIRED"
        with pytest.raises(WorkflowExecutionError, match="unobserved") as error:
            service.raiseUnobservedFailure()
        assert error.value.diagnostics["backgroundTask"]["taskId"] == failed.id
        service.raiseUnobservedFailure()
        service.release(pinned.id)
        assert client.wait(pinned.id) == "file"
        service.release(pinned.id)
        with pytest.raises(WorkflowExecutionError) as expired:
            client.wait(pinned.id)
        assert expired.value.code == "E_BACKGROUND_TASK_EXPIRED"
    finally:
        service.close()


def test_close_deadline_seals_publication_and_rejects_late_work(tmp_path, monkeypatch):
    service = BackgroundTaskService()
    client, _ = _client(service)
    started, release, rejected = threading.Event(), threading.Event(), threading.Event()
    final = tmp_path / "final"

    def late(control):
        started.set()
        assert release.wait(5)
        try:
            control.publish(lambda: final.touch())
        except CancellationRequested:
            rejected.set()
            raise

    task = client.submit(late, taskName="slow", estimatedBytes=0)
    try:
        assert started.wait(5)
        monkeypatch.setattr(background_tasks, "_DRAIN_TIMEOUT_SECONDS", .02)
        with pytest.raises(WorkflowExecutionError) as timeout:
            service.close()
        assert timeout.value.code == "E_BACKGROUND_TASK_DRAIN_TIMEOUT"
        release.set()
        assert rejected.wait(5)
        assert not final.exists()
        with pytest.raises(WorkflowExecutionError) as closed:
            client.submit(late, taskName="late", estimatedBytes=0)
        assert closed.value.code == "E_BACKGROUND_TASK_CLOSED"
    finally:
        release.set()
        service.close(drain=False)
        service._thread.join(5)
    assert client.status(task.id) == "CANCELLED"
