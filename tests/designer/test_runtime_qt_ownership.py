"""Runtime threads retire natively on the GUI thread and cannot outlive UI delivery."""
import gc
import threading
import weakref
from types import SimpleNamespace

import pytest

from emo_master.apps.designer.controllers.runtime_controller import RuntimeController
from emo_master.apps.designer.services.runtime_worker import RuntimeStopWorker, RuntimeWorker
from tests.designer.qt_wait import waitUntil


class Client:
    def __init__(self, active=False):
        self.active = active
        self.count = 0
        self.statuses = {}

    def startJob(self, *args, **kwargs):
        self.count += 1
        job = str(self.count)
        self.statuses[job] = 'RUNNING' if self.active else 'COMPLETED'
        return SimpleNamespace(ok=True, job_id=job, status='ACCEPTED', message='ok')

    def streamJobEvents(self, *args, **kwargs):
        return []

    def getJobStatus(self, job):
        return SimpleNamespace(status=self.statuses[job], message='status')

    def stopJob(self, job, **kwargs):
        self.statuses[job] = 'ABORTED'
        return SimpleNamespace(ok=True, status='ABORTED', message='stopped')

    def close(self):
        pass


class Panel:
    jobStatus = 'IDLE'

    def updateJob(self, status, message):
        self.jobStatus = status

    def applyEvent(self, event):
        pass


def makeController(client):
    state = SimpleNamespace(current=None, running=False, callbacks=[])
    controller = RuntimeController(
        runtimeClient=client, runtimePanelState=Panel(), appendLog=lambda *args: None,
        refreshRuntimePanelView=lambda: state.callbacks.append(('refresh', controller._closed)),
        updateToolbarState=lambda: state.callbacks.append(('toolbar', controller._closed)),
        syncRuntimeProjectBeforeRun=lambda: True, applyRuntimeEventToNode=lambda event: None,
        setCurrentJobId=lambda job: setattr(state, 'current', job),
        setIsJobRunning=lambda running: setattr(state, 'running', running),
        getLoadedProjectPath=lambda: 'project', getCurrentJobId=lambda: state.current,
    )
    return controller, state


def testCompletedRuntimeAndStopWorkersRetireBeforeBackgroundGc(designerApplication, monkeypatch):
    if designerApplication is None:
        pytest.skip('native QObject ownership requires Qt')
    from PySide2.QtCore import QCoreApplication, QEvent, Qt
    from shiboken2 import isValid

    references = []
    destroyedOn = []
    for cls in (RuntimeWorker, RuntimeStopWorker):
        original = cls.__init__
        def record(worker, *args, _original=original, **kwargs):
            _original(worker, *args, **kwargs)
            references.append(weakref.ref(worker))
            worker.destroyed.connect(lambda *_args: destroyedOn.append(threading.get_ident()),
                                     Qt.DirectConnection)
        monkeypatch.setattr(cls, '__init__', record)
    controller, state = makeController(Client(active=True))
    try:
        for index in range(4):
            controller.startJob()
            waitUntil(lambda: state.current == str(index + 1))
            controller.stopJob()
            waitUntil(lambda: not controller._jobActive and controller._worker is None
                      and controller._stopWorker is None)
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        assert len(references) == 8
        assert all(ref() is None or not isValid(ref()) for ref in references)
        assert destroyedOn == [threading.get_ident()] * 8
        collector = threading.Thread(target=gc.collect)
        collector.start()
        collector.join(3)
        assert not collector.is_alive()
        assert all(ref() is None for ref in references)
        assert destroyedOn == [threading.get_ident()] * 8
    finally:
        controller.close()


def testQueuedRuntimeCallbacksDoNotTouchClosedController(designerApplication):
    if designerApplication is None:
        pytest.skip('native QObject ownership requires Qt')
    from PySide2.QtCore import QCoreApplication, QEvent
    from shiboken2 import isValid

    controller, state = makeController(Client())
    controller.startJob()
    worker = controller._worker
    assert worker is not None and worker.wait(5000)
    # Accepted/status/finished signals are queued, but not delivered, when close
    # joins the worker. Closing must prevent these from touching disposed UI.
    controller.close()
    current = state.current
    designerApplication.processEvents()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    assert not [call for call in state.callbacks if call[1]]
    assert state.current == current
    assert not isValid(worker)


@pytest.mark.parametrize('outcome', ['event', 'failed', 'uncertain'])
def testNativeRuntimeSignalsDeliverOnGuiThread(designerApplication, outcome):
    if designerApplication is None:
        pytest.skip('native QObject ownership requires Qt')
    from PySide2.QtCore import QCoreApplication, QEvent

    class SignalClient(Client):
        def startJob(self, *args, **kwargs):
            if outcome == 'failed':
                return SimpleNamespace(ok=False, status='FAILED', message='synthetic rejection')
            if outcome == 'uncertain':
                raise RuntimeError('synthetic lost start reply')
            return super().startJob(*args, **kwargs)

        def streamJobEvents(self, *args, **kwargs):
            return [SimpleNamespace(event_type='node.completed', message='event')]

    controller, state = makeController(SignalClient())
    calls = []
    controller.appendLog = lambda level, message: calls.append((threading.get_ident(), message))
    controller.applyRuntimeEventToNode = lambda event: calls.append((threading.get_ident(), 'node event'))
    try:
        controller.startJob()
        waitUntil(lambda: controller._worker is None)
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        assert calls and all(thread == threading.get_ident() for thread, _ in calls)
        if outcome == 'event':
            assert any(message == 'node event' for _, message in calls)
            assert not state.running
        elif outcome == 'failed':
            assert any('启动作业失败' in message for _, message in calls)
            assert not state.running
        else:
            assert controller._startUncertain and state.running
            assert any('启动结果尚未确认' in message for _, message in calls)
    finally:
        controller.close()


def testQueuedOldRuntimeSignalsCannotMutateReplacementWorker(designerApplication):
    if designerApplication is None:
        pytest.skip('native QObject ownership requires Qt')
    from PySide2.QtCore import QCoreApplication, QEvent
    from shiboken2 import isValid

    controller, state = makeController(Client())
    controller.startJob()
    worker = controller._worker
    assert worker is not None and worker.wait(5000)
    replacement = object()
    controller._worker = replacement
    state.current = 'new-job'
    state.callbacks.clear()
    try:
        designerApplication.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        assert controller._worker is replacement
        assert state.current == 'new-job'
        assert controller._jobActive
        assert state.callbacks == []
        assert not isValid(worker)
    finally:
        controller._worker = None
        controller._jobActive = False
        controller.runtimePanelState.jobStatus = 'COMPLETED'
        controller.close()


def testNativeContextDeletionDropsRuntimeDeliveryButRetainsRunningThread(designerApplication):
    if designerApplication is None:
        pytest.skip('native QObject ownership requires Qt')
    from PySide2.QtCore import QCoreApplication, QEvent
    from PySide2.QtWidgets import QWidget
    from shiboken2 import isValid

    entered, release = threading.Event(), threading.Event()
    class DelayedClient(Client):
        def streamJobEvents(self, *args, **kwargs):
            entered.set()
            assert release.wait(3)
            return []

    context = QWidget()
    controller, state = makeController(DelayedClient())
    controller.deliveryContext = context
    controller.startJob()
    worker = controller._worker
    try:
        assert entered.wait(3)
        state.callbacks.clear()
        context.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        assert not isValid(context)
        assert isValid(worker) and worker.isRunning()
        release.set()
        assert worker.wait(3000)
        designerApplication.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        assert state.callbacks == []
        assert state.current is None
        assert not isValid(worker)
    finally:
        release.set()
        if isValid(worker):
            worker.requestStop()
            worker.wait(3000)
        if isValid(context):
            context.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)


@pytest.mark.parametrize('cancelFails', [False, True])
def testApplicationWorkerOwnerJoinsBeforeNativeDestruction(designerApplication, cancelFails):
    if designerApplication is None:
        pytest.skip('native QObject ownership requires Qt')
    from PySide2.QtCore import QCoreApplication, QEvent
    from shiboken2 import isValid

    entered, release = threading.Event(), threading.Event()
    class DelayedClient(Client):
        def streamJobEvents(self, *args, **kwargs):
            entered.set()
            assert release.wait(3)
            return []

        def cancelEventStream(self, job):
            if cancelFails:
                raise RuntimeError('synthetic cancellation error')

    controller, state = makeController(DelayedClient())
    controller.startJob()
    worker = controller._worker
    assert entered.wait(3)
    state.callbacks.clear()
    unblock = threading.Timer(.02, release.set)
    unblock.start()
    try:
        if cancelFails:
            with pytest.raises(RuntimeError, match='synthetic cancellation error'):
                worker._qtDelivery.shutdown()
        else:
            worker._qtDelivery.shutdown()
        assert not worker.isRunning()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        designerApplication.processEvents()
        assert not isValid(worker)
        assert state.callbacks == []
    finally:
        release.set()
        unblock.join(3)


def testCloseCanRetryAfterCancellationErrorAndNativeWorkerRetirement(designerApplication):
    if designerApplication is None:
        pytest.skip('native QObject ownership requires Qt')
    from PySide2.QtCore import QCoreApplication, QEvent
    from shiboken2 import isValid

    entered, release = threading.Event(), threading.Event()
    class CancelOnceClient(Client):
        cancellations = 0

        def streamJobEvents(self, *args, **kwargs):
            entered.set()
            assert release.wait(3)
            return []

        def cancelEventStream(self, job):
            self.cancellations += 1
            if self.cancellations == 1:
                raise RuntimeError('synthetic first cancellation error')

    controller, state = makeController(CancelOnceClient())
    controller.startJob()
    worker = controller._worker
    try:
        assert entered.wait(3)
        with pytest.raises(RuntimeError, match='synthetic first cancellation error'):
            controller.close()
        assert not controller._closed
        release.set()
        assert worker.wait(3000)
        designerApplication.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        assert not isValid(worker)
        controller.close()
        assert controller._closed and controller._worker is None
        assert not [call for call in state.callbacks if call[1]]
    finally:
        release.set()
        if isValid(worker):
            worker.requestStop()
            worker.wait(3000)


def testContextLossRetiresPendingStopWithoutStartingEscalation(designerApplication):
    if designerApplication is None:
        pytest.skip('native QObject ownership requires Qt')
    from PySide2.QtCore import QCoreApplication, QEvent
    from PySide2.QtWidgets import QWidget
    from shiboken2 import isValid

    entered, release = threading.Event(), threading.Event()
    class DelayedStopClient(Client):
        stops = 0

        def stopJob(self, job, **kwargs):
            self.stops += 1
            entered.set()
            assert release.wait(3)
            return super().stopJob(job, **kwargs)

    client = DelayedStopClient(active=True)
    controller, state = makeController(client)
    state.current = client.startJob().job_id
    controller._jobActive = True
    controller.runtimePanelState.jobStatus = 'RUNNING'
    context = QWidget()
    controller.deliveryContext = context
    controller.stopJob()
    worker = controller._stopWorker
    try:
        assert entered.wait(3)
        controller.stopJob(mode='force')
        assert controller._stopEscalateToForce
        state.callbacks.clear()
        context.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        assert not isValid(context) and worker.isRunning()
        release.set()
        assert worker.wait(3000)
        designerApplication.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        assert not isValid(worker)
        assert client.stops == 1 and state.callbacks == []
        assert controller._stopWorker is None and not controller._stopEscalateToForce
    finally:
        release.set()
        if isValid(worker):
            worker.wait(3000)
        if isValid(context):
            context.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
