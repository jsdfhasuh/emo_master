"""GUI-owned delivery and native retirement for Designer runtime threads."""
import weakref

from PySide2.QtCore import QCoreApplication, QObject, QThread, Qt, SIGNAL, SLOT, Slot


def _connect(sender, signal, receiver, slot, connection=Qt.QueuedConnection):
    # PySide2's explicit receiver form avoids Python proxy/lambda ownership.
    if not QObject.connect(sender, SIGNAL(signal), receiver, SLOT(slot), connection):
        raise RuntimeError(f"could not connect runtime signal {signal} to {slot}")


class _RuntimeWorkerDelivery(QObject):
    def __init__(self, worker, controller, stopWorker, context):
        application = QCoreApplication.instance()
        super().__init__(application)
        self.worker = worker
        self.controller = controller
        self._lifecycleController = weakref.ref(controller)
        self.stopWorker = stopWorker
        worker.setParent(self)
        worker._qtDelivery = self
        if context is not None:
            _connect(context, 'destroyed(QObject*)', self, 'detach()', Qt.DirectConnection)
        _connect(application, 'aboutToQuit()', self, 'shutdown()', Qt.DirectConnection)
        if stopWorker:
            _connect(worker, 'replyReceived(PyObject)', self, 'replyReceived(PyObject)')
        else:
            _connect(worker, 'jobAccepted(PyObject)', self, 'jobAccepted(PyObject)')
            _connect(worker, 'eventReceived(PyObject)', self, 'eventReceived(PyObject)')
            _connect(worker, 'statusChanged(PyObject)', self, 'statusChanged(PyObject)')
            _connect(worker, 'uncertain(QString)', self, 'uncertain(QString)')
        _connect(worker, 'failed(QString)', self, 'failed(QString)')
        _connect(worker, 'finished()', self, 'finished()')

    def _currentController(self):
        controller = self.controller
        if (self.sender() is not self.worker or controller is None
                or controller._closing or controller._closed):
            return None
        current = controller._stopWorker if self.stopWorker else controller._worker
        return controller if current is self.worker else None

    @Slot()  # type: ignore[operator]
    def detach(self):
        # Native window deletion may bypass closeEvent. Keep the thread owned,
        # but sever its ability to deliver into the disposed widget tree.
        self.controller = None

    @Slot(object)  # type: ignore[operator]
    def jobAccepted(self, reply):
        controller = self._currentController()
        if controller is not None:
            controller._onJobAccepted(reply)

    @Slot(object)  # type: ignore[operator]
    def eventReceived(self, event):
        controller = self._currentController()
        if controller is not None:
            controller._onRuntimeEvent(event)

    @Slot(object)  # type: ignore[operator]
    def statusChanged(self, reply):
        controller = self._currentController()
        if controller is not None:
            controller._onJobStatus(reply)

    @Slot(str)  # type: ignore[operator]
    def uncertain(self, message):
        controller = self._currentController()
        if controller is not None:
            controller._onStartUncertain(message)

    @Slot(str)  # type: ignore[operator]
    def failed(self, message):
        controller = self._currentController()
        if controller is not None:
            if self.stopWorker:
                controller._onStopFailed(self.worker, message)
            else:
                controller._onWorkerFailed(message)

    @Slot(object)  # type: ignore[operator]
    def replyReceived(self, reply):
        controller = self._currentController()
        if controller is not None:
            controller._onStopReply(self.worker, reply)

    @Slot()  # type: ignore[operator]
    def finished(self):
        controller = self._currentController()
        try:
            if controller is not None:
                if self.stopWorker:
                    controller._onStopWorkerFinished(self.worker)
                else:
                    controller._onWorkerFinished(self.worker)
        finally:
            self._retire()

    def _retire(self):
        self.detach()
        controller = self._lifecycleController()
        if controller is not None:
            # A failed close may retry after detached signals have drained.
            # Clear native ownership references without UI updates or Stop
            # escalation, including when the delivery context was destroyed.
            controller._retireWorkerReference(self.worker, stopWorker=self.stopWorker)
        self.deleteLater()

    @Slot()  # type: ignore[operator]
    def shutdown(self):
        self.detach()
        requestStop = getattr(self.worker, 'requestStop', None)
        try:
            if callable(requestStop):
                requestStop()
        finally:
            # Even a transport cancellation error cannot let QApplication
            # destroy a child whose native thread is still running.
            if self.worker.isRunning():
                self.worker.wait(12000)
                if self.worker.isRunning():
                    self.worker.wait(-1)
            self._retire()


def bindRuntimeWorker(worker, controller, *, stopWorker=False, context=None):
    """Return false for headless/fake workers, preserving their existing API."""
    if not isinstance(worker, QThread) or QCoreApplication.instance() is None:
        return False
    _RuntimeWorkerDelivery(worker, controller, stopWorker, context)
    return True
