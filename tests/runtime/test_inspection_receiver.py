"""Inspection backpressure belongs to preview publishers, not the event bridge."""
import queue
import threading
from types import SimpleNamespace

import numpy as np
import pytest

from emo_master.apps.runtime.jobs.worker_main import _eventPublisher
from emo_master.apps.runtime.preview.inspection_receiver import InspectionReceiver
from emo_master.apps.runtime.preview.async_writer import AsyncPreviewSnapshotWriter
from emo_master.apps.runtime.workflow.cancellation import CancellationRequested, CancellationToken
from emo_master.apps.runtime.workflow.context import RunContext


def testSlowReceiverKeepsOrdinaryEventsMovingAndBoundsPreviewAdmission():
    entered, release, published = (threading.Event() for _ in range(3))
    received, errors = [], []

    def adopt(event):
        entered.set()
        assert release.wait(5)
        received.append(event['payload']['index'])

    receiver = InspectionReceiver('bounded', adopt, errors.append, capacity=2)
    events = queue.Queue()
    publish = _eventPublisher('job', 'project', events, inspectionCredits=receiver.credits)
    context = RunContext.root('job', 'main', '').forNode('source')

    def preview(index):
        publish('inspection.snapshot', context, '', payload={'index': index})

    worker = threading.Thread(target=lambda: (preview(3), published.set()))
    try:
        preview(1)
        receiver.submit(events.get_nowait())
        assert entered.wait(1)
        preview(2)
        receiver.submit(events.get_nowait())
        worker.start()
        assert not published.wait(.1)
        publish('node.completed', context, '')
        assert events.get(timeout=1)['eventType'] == 'node.completed'
        assert not receiver.credits.acquire(block=False)
        release.set()
        assert published.wait(2)
        receiver.submit(events.get(timeout=1))
    finally:
        release.set()
        worker.join(2)
        receiver.close()
    assert received == [1, 2, 3] and not errors
    assert not receiver.thread.is_alive()
    assert receiver.credits.acquire(block=False)
    assert receiver.credits.acquire(block=False)
    assert not receiver.credits.acquire(block=False)


def testCancelledPublisherReturnsWithoutWaitingForReceiver():
    receiver = InspectionReceiver('cancel', lambda event: None, lambda error: None, capacity=1)
    events, token = queue.Queue(), CancellationToken()
    assert receiver.credits.acquire(block=False)
    publish = _eventPublisher('job', 'project', events, inspectionCredits=receiver.credits,
                              cancellation=token)
    context = RunContext.root('job', 'main', '')
    errors = []

    def waiting():
        try:
            publish('inspection.snapshot', context, '')
        except Exception as error:
            errors.append(error)

    worker = threading.Thread(target=waiting)
    worker.start()
    token.cancel()
    worker.join(2)
    receiver.credits.release()
    receiver.close()
    assert not worker.is_alive() and events.empty()
    assert len(errors) == 1 and isinstance(errors[0], CancellationRequested)


def testFailedAdoptionReleasesCreditAndDoesNotKillReceiver():
    received, errors = [], []

    def adopt(event):
        received.append(event)
        if event == 'first':
            raise OSError('controlled copy failure')

    receiver = InspectionReceiver('failure', adopt, errors.append, capacity=2)
    for event in ('first', 'second'):
        assert receiver.credits.acquire(timeout=1)
        receiver.submit(event)
    receiver.close()
    receiver.close()
    assert received == ['first', 'second'] and len(errors) == 1
    assert receiver.credits.acquire(block=False)
    assert receiver.credits.acquire(block=False)


def testFailedEventTransportReturnsReservedCredit():
    receiver = InspectionReceiver('transport', lambda event: None, lambda error: None, capacity=1)

    class FailedQueue:
        def put(self, event):
            raise OSError('closed pipe')

    publish = _eventPublisher('job', 'project', FailedQueue(), inspectionCredits=receiver.credits)
    try:
        with pytest.raises(OSError, match='closed pipe'):
            publish('inspection.snapshot', RunContext.root('job', 'main', ''), '')
        assert receiver.credits.acquire(block=False)
    finally:
        receiver.close()


def testCancelledWriterReleasesActiveCaptureWhileReceiverCreditsAreExhausted(tmp_path):
    receiver = InspectionReceiver('writer-cancel', lambda event: None, lambda error: None, capacity=1)
    assert receiver.credits.acquire(block=False)
    waiting, token, events, errors = threading.Event(), CancellationToken(), queue.Queue(), []

    class Credits:
        def acquire(self, **kwargs):
            waiting.set()
            return receiver.credits.acquire(**kwargs)

        def release(self):
            receiver.credits.release()

    publish = _eventPublisher('job', 'project', events, inspectionCredits=Credits(), cancellation=token)
    writer = AsyncPreviewSnapshotWriter(tmp_path, jobId='job', inspectionPublisher=publish,
        errorPublisher=lambda **event: errors.append(event), cancellation=token)
    try:
        writer.capture(SimpleNamespace(nodeId='source', outputPorts={'image': 'image'}),
                       {'image': np.zeros((20, 20, 3), np.uint8)}, RunContext.root('job', 'main').forNode('source'))
        assert waiting.wait(2)
    finally:
        token.cancel()
        writer.close(drain=False)
        receiver.credits.release()
        receiver.close()
    assert not writer._thread.is_alive() and writer._bufferedBytes == 0
    assert writer.statistics()['discarded'] == 1
    assert writer.statistics()['failed'] == writer.statistics()['inspectionFailed'] == 0
    assert not errors and events.empty()
