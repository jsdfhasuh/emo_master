"""Client-side failures remain searchable without changing operation outcomes."""
from concurrent.futures import Future
from types import SimpleNamespace
import threading

from PySide2.QtWidgets import QFileDialog, QMessageBox

from emo_master.apps.designer.services.runtime_client import RuntimeClientError
from emo_master.apps.designer.services.runtime_worker import RuntimeWorker, _followJobEvents
from emo_master.apps.designer.ui.runtime_connection import RuntimeConnectionDialog
from emo_master.apps.designer.page_designer import delivery
from tests.designer.test_project_configuration_ui import setupDialog
from tests.designer.test_runtime_qt_ownership import Client, makeController
from tests.designer.qt_wait import waitUntil
from tests.designer.runtime_feedback_fixture import messages  # noqa: F401


def capture(window, monkeypatch):
    events = []
    monkeypatch.setattr(window, 'appendLocalEvent',
                        lambda event, source='designer': events.append(dict(event, source=source)), raising=False)
    return events


def failOperation(dialog, kind, error):
    pending = Future()
    pending.set_exception(error)
    dialog.pending, dialog.kind = pending, kind
    dialog.submittedCheckpoint = dialog.session._checkpoint
    dialog.poll()


def testConfigurationFailureUnknownAndCancellationKeepCorrectLevels(
        ownedDesignerWindow, tmp_path, monkeypatch, messages):  # noqa: F811
    window, dialog, calls, path = setupDialog(ownedDesignerWindow, tmp_path)
    events = capture(window, monkeypatch)
    before = path.read_bytes()
    try:
        failOperation(dialog, 'snapshot', RuntimeClientError('UNAVAILABLE', 'network unreachable'))
        assert events[-1]['level'] == 'ERROR' and events[-1]['payload']['operation'] == 'snapshot'
        assert events[-1]['code'] == 'UNAVAILABLE'
        dialog.refresh()
        waitUntil(lambda: dialog.pending is None)
        failOperation(dialog, 'snapshot', RuntimeClientError('UNAVAILABLE', 'network unreachable'))
        assert len(events) == 2  # A new failure after a successful read is a new incident.
        dialog.requestId = 'original-write'
        dialog.writePending = True
        error = RuntimeClientError('UNAVAILABLE', 'lost write response')
        failOperation(dialog, 'write', error)
        failOperation(dialog, 'write', error)
        unknown = [row for row in events if row['eventType'] == 'designer.configuration.unknown']
        assert len(unknown) == 1 and unknown[0]['level'] == 'WARN'
        assert unknown[0]['payload']['requestId'] == 'original-write'
        assert dialog.writePending and not calls
        count = len(events)
        failOperation(dialog, 'write', RuntimeClientError('E_CONFIG_CANCELLED', 'cancelled'))
        assert len(events) == count and not dialog.writePending
        assert path.read_bytes() == before
    finally:
        dialog.reject()


def testUnknownWriteLogsConfirmationWithoutReplayingWrite(
        ownedDesignerWindow, tmp_path, monkeypatch, messages):  # noqa: F811
    window, dialog, calls, _path = setupDialog(ownedDesignerWindow, tmp_path)
    events = capture(window, monkeypatch)
    try:
        dialog.requestId, dialog.writePending = 'original-write', True
        failOperation(dialog, 'write', RuntimeClientError('UNAVAILABLE', 'lost response'))
        result = Future()
        result.set_result(dict(state='COMMITTED', documentRevision=9))
        dialog.pending, dialog.kind = result, 'write'
        dialog.submittedCheckpoint = dialog.session._checkpoint
        dialog.poll()
        waitUntil(lambda: dialog.pending is None)
        assert [row['level'] for row in events] == ['WARN', 'INFO']
        assert events[-1]['eventType'] == 'designer.configuration.confirmed'
        assert events[-1]['payload']['requestId'] == 'original-write'
        assert not calls and not dialog.writePending
    finally:
        dialog.reject()


def testConnectionObservationRetriesAreLoggedOncePerRequest(
        ownedDesignerWindow, tmp_path, monkeypatch):
    window, dialog, calls, _path = setupDialog(ownedDesignerWindow, tmp_path)
    events = capture(window, monkeypatch)
    try:
        dialog.requestId = 'committed-write'
        for _ in range(4):
            failOperation(dialog, 'connectionObserve', RuntimeClientError('UNAVAILABLE', 'temporary outage'))
        assert len(events) == 1 and events[0]['level'] == 'WARN'
        assert events[0]['payload']['requestId'] == 'committed-write' and not calls
        dialog.requestId = 'next-write'
        failOperation(dialog, 'connectionObserve', RuntimeClientError('UNAVAILABLE', 'temporary outage'))
        assert len(events) == 2 and events[1]['payload']['requestId'] == 'next-write'
    finally:
        dialog.reject()


def testRuntimeProbeFailureRecordsTargetAndDoesNotLogInvalidAddress(
        ownedDesignerWindow, tmp_path, monkeypatch, messages):  # noqa: F811
    from emo_master.apps.designer.ui import runtime_connection
    window, configuration, _calls, _path = setupDialog(ownedDesignerWindow, tmp_path)
    configuration.reject()
    events = capture(window, monkeypatch)
    monkeypatch.setattr(runtime_connection, 'readCurrentConnection', lambda _client:
                        (dict(projectId='', runtimeInstanceId='fake'), None, ''))
    def failed(_target):
        raise RuntimeClientError('UNAVAILABLE', 'probe timed out')
    monkeypatch.setattr(runtime_connection, 'connectCandidate', failed)
    dialog = RuntimeConnectionDialog(window.pageCoordinator)
    try:
        waitUntil(lambda: dialog.currentPending is None)
        dialog.target.setText('not an address')
        dialog.connect()
        assert not events
        dialog.target.setText('127.0.0.1:40000')
        dialog.connect()
        waitUntil(lambda: dialog.pending is None)
        assert len(events) == 1
        assert events[0]['level'] == 'ERROR' and events[0]['code'] == 'UNAVAILABLE'
        assert events[0]['payload']['target'] == '127.0.0.1:40000'
        assert dialog.candidate is None
    finally:
        dialog.reject()


def testPageExportAndDeviceTypeFailuresUsePageErrorLog(
        ownedDesignerWindow, tmp_path, monkeypatch):
    window, configuration, _calls, _path = setupDialog(ownedDesignerWindow, tmp_path)
    configuration.reject()
    events = capture(window, monkeypatch)
    c = window.pageCoordinator
    output = str(tmp_path / 'export')
    monkeypatch.setattr(QFileDialog, 'getExistingDirectory', lambda *_args: output)
    monkeypatch.setattr(QMessageBox, 'warning', lambda *_args: QMessageBox.Ok)
    def failed(*_args):
        raise OSError('output directory denied')
    monkeypatch.setattr(delivery, 'exportTestPackage', failed)
    delivery.exportDialog(c)
    assert events[0]['level'] == 'ERROR' and events[0]['source'] == 'page_designer'
    assert events[0]['payload']['outputDirectory'] == output
    monkeypatch.setattr(QFileDialog, 'getExistingDirectory', lambda *_args: '')
    delivery.exportDialog(c)
    assert len(events) == 1
    monkeypatch.setattr(window.runtimeClient, 'listDeviceTypes', failed, raising=False)
    window._clearRuntimeLogCache()
    c.resourceBindings()
    entry, = window.logEntries
    assert entry.level == 'ERROR' and entry.source == 'page_designer'


def testRepeatedLogDisconnectsAreSummarizedAndUserStopIsQuiet():
    class StreamClient:
        def __init__(self):
            self.calls = 0

        def iterJobEvents(self, _job, afterSequence=0, follow=True):
            self.calls += 1
            if self.calls <= 3:
                raise RuntimeClientError('UNAVAILABLE', 'temporary outage')
            return [] if afterSequence else [SimpleNamespace(sequence=1, event_type='job.completed')]

        def getJobStatus(self, _job):
            return SimpleNamespace(status='RUNNING' if self.calls <= 3 else 'COMPLETED')

    client = StreamClient()
    worker = RuntimeWorker(client, 'project', 'main')
    events = []
    worker.diagnosticReceived.connect(events.append)
    _followJobEvents(worker, 'job')
    assert [row['level'] for row in events] == ['WARN', 'INFO']
    assert events[0]['code'] == 'UNAVAILABLE'
    worker.requestStop()
    _followJobEvents(worker, 'job')
    assert len(events) == 2


def testNativeLogDiagnosticsArriveOnGuiThread(designerApplication):
    class DisconnectClient(Client):
        def __init__(self):
            super().__init__()
            self.reads = 0

        def streamJobEvents(self, *args, **kwargs):
            self.reads += 1
            if self.reads == 1:
                raise RuntimeClientError('UNAVAILABLE', 'synthetic log outage')
            return [SimpleNamespace(sequence=1, event_type='job.completed')]

        def getJobStatus(self, _job):
            return SimpleNamespace(status='RUNNING' if self.reads == 1 else 'COMPLETED', message='status')

    controller, _state = makeController(DisconnectClient())
    events = []
    controller.appendLocalEvent = lambda event: events.append((threading.get_ident(), event))
    try:
        controller.startJob()
        waitUntil(lambda: controller._worker is None)
        assert [row['level'] for _, row in events] == ['WARN', 'INFO']
        assert all(thread == threading.get_ident() for thread, _ in events)
    finally:
        controller.close()
