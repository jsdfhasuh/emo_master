from types import SimpleNamespace

import pytest
from PySide2.QtCore import QTimer
from PySide2.QtGui import QTextCursor

from tests.designer.qt_wait import waitForCatalog, waitUntil
from tests.designer.test_node_run_inspection import event
from tests.designer.test_run_inspector_ui import windowWithNode


@pytest.fixture
def runningWindow():
    window, node = windowWithNode()
    waitForCatalog(window)
    manager = window.runtimeController
    manager.select('')
    controller = manager.runs[''].controller
    controller._onJobAccepted(SimpleNamespace(
        ok=True, job_id='job', project_revision=1, status='RUNNING', message='running'))
    controller._onJobStatus(SimpleNamespace(status='RUNNING', message='running'))
    try:
        yield window, node, controller
    finally:
        # These Jobs have no Runtime transport or worker to stop.
        for run in manager.runs.values():
            run.controller._jobActive = False
            run.state.updateJob('COMPLETED')
        window._setIsJobRunning(False)
        window.close()


def send(controller, node, sequence=2, *, kind='node.completed', job='job', value=0):
    controller._onRuntimeEvent(SimpleNamespace(**event(
        'node.started', sequence * 2 - 1, node=node, job=job, execution=f'node-{sequence}')))
    controller._onRuntimeEvent(SimpleNamespace(**event(
        kind, sequence * 2, node=node, job=job, execution=f'node-{sequence}', value=value)))


def testBurstRetainsEveryEventAndRendersLatestOnce(runningWindow, monkeypatch):
    window, node, controller = runningWindow
    coordinator = window.nodeResultCoordinator
    renders, applied, logged, signatures, blueprints = [], [], [], [], []
    render = coordinator.panel.render
    apply = coordinator.history.applyEvent
    append = controller.appendEvent
    signature = window.pageCoordinator.session._signature
    blueprint = coordinator.blueprint
    monkeypatch.setattr(coordinator.panel, 'render', lambda *a: (renders.append(a), render(*a))[1])
    monkeypatch.setattr(coordinator.history, 'applyEvent', lambda e: (applied.append(e['sequence']), apply(e))[1])
    controller.appendEvent = lambda e: (logged.append(e['sequence']), append(e))[1]
    monkeypatch.setattr(window.pageCoordinator.session, '_signature',
                        lambda: (signatures.append(True), signature())[1])
    monkeypatch.setattr(coordinator, 'blueprint', lambda: (blueprints.append(True), blueprint())[1])
    before = window.pageCoordinator.session.payload()
    for sequence in range(1, 101):
        send(controller, node, sequence, value=sequence)
    assert applied == logged == list(range(1, 201))
    assert not renders and not signatures and not blueprints
    assert window._runtimeEventRefreshTimer.isActive()
    assert coordinator.history.view('main', node)['record']['nodeRunId'] == 'node-100'

    waitUntil(lambda: bool(renders))
    assert len(renders) == 1
    assert 'count = 100' in coordinator.panel.values.toPlainText()
    assert not window._runtimeEventRefreshTimer.isActive()
    assert not signatures and not blueprints
    assert window.pageCoordinator.session.payload() == before


def testContinuousEventsDoNotPostponeRefreshOrQtHeartbeat(runningWindow, monkeypatch):
    window, node, controller = runningWindow
    renders, heartbeats = [], []
    render = window.nodeResultCoordinator.panel.render
    monkeypatch.setattr(window.nodeResultCoordinator.panel, 'render',
                        lambda *a: (renders.append(True), render(*a))[1])
    producer, heartbeat = QTimer(window), QTimer(window)
    sequence = [0]

    def produce():
        sequence[0] += 1
        send(controller, node, sequence[0], value=sequence[0])

    producer.timeout.connect(produce)
    heartbeat.timeout.connect(lambda: heartbeats.append(True))
    producer.start(5)
    heartbeat.start(10)
    try:
        waitUntil(lambda: len(renders) >= 3)
        assert producer.isActive() and sequence[0] > len(renders)
        assert len(heartbeats) >= 3
    finally:
        producer.stop()
        heartbeat.stop()


@pytest.mark.parametrize('status', ['COMPLETED', 'FAILED', 'ABORTED', 'STOPPING'])
def testJobStatusFlushesImmediately(runningWindow, status):
    window, node, controller = runningWindow
    controller.onRunFailed = lambda failure: None
    send(controller, node, value=17)
    assert window._runtimeEventRefreshTimer.isActive()
    controller._onJobStatus(SimpleNamespace(status=status, message='status changed'))
    assert not window._runtimeEventRefreshTimer.isActive()
    assert window.runtimePanelState.jobStatus == status
    assert window.isJobRunning is (status == 'STOPPING')
    assert 'count = 17' in window.nodeResultCoordinator.panel.values.toPlainText()


def testNodeFailureIsVisibleBeforeNextRefreshTick(runningWindow):
    window, node, controller = runningWindow
    send(controller, node, value=17)
    assert window._runtimeEventRefreshTimer.isActive()
    send(controller, node, 3, kind='node.failed')
    panel = window.nodeResultCoordinator.panel
    assert not window._runtimeEventRefreshTimer.isActive()
    assert 'actual failure' in panel.execution.toPlainText()
    assert 'count = 17' not in panel.values.toPlainText()


def testPendingRefreshUsesNewSelectedJobAndKeepsOtherHistory(runningWindow):
    window, node, first = runningWindow
    manager = window.runtimeController
    send(first, node, value=11)
    second = manager._create('second').controller
    second._onJobAccepted(SimpleNamespace(
        ok=True, job_id='second-job', project_revision=1, status='RUNNING', message='running'))
    send(second, node, job='second-job', value=22)
    assert window._runtimeEventRefreshTimer.isActive()
    manager.select('second')
    assert not window._runtimeEventRefreshTimer.isActive()
    assert window.currentJobId == 'second-job'
    assert 'count = 22' in window.nodeResultCoordinator.panel.values.toPlainText()
    manager.select('')
    assert window.currentJobId == 'job'
    assert 'count = 11' in window.nodeResultCoordinator.panel.values.toPlainText()


def testClosingWindowCancelsPendingRefresh(runningWindow, monkeypatch, designerApplication):
    window, node, controller = runningWindow
    send(controller, node)
    assert window._runtimeEventRefreshTimer.isActive()
    controller._jobActive = False
    controller.runtimePanelState.updateJob('COMPLETED')
    window.close()
    assert window.runtimeController._closed
    assert not window._runtimeEventRefreshTimer.isActive()
    rendered = []
    monkeypatch.setattr(window, '_refreshRuntimePanelView', lambda: rendered.append(True))
    window._flushRuntimeEventViews()
    designerApplication.processEvents()
    assert not rendered


def testUnchangedTextPreservesDocumentsAndSelection(runningWindow):
    window, node, controller = runningWindow
    send(controller, node, value=17)
    window._flushRuntimeEventViews()
    panel = window.nodeResultCoordinator.panel
    widgets = (panel.values, panel.inputs, panel.execution)
    changes = []
    for widget in widgets:
        widget.textChanged.connect(lambda: changes.append(True))
    cursor = panel.values.textCursor()
    cursor.select(QTextCursor.Document)
    panel.values.setTextCursor(cursor)
    selected = panel.values.textCursor().selectedText()
    for _ in range(10):
        window.nodeResultCoordinator.refresh()
    assert not changes
    assert selected and panel.values.textCursor().selectedText() == selected


def testRuntimeRefreshSkipsDraftChecksButEditsStillUpdateTitle(runningWindow, monkeypatch):
    window, node, controller = runningWindow
    coordinator = window.pageCoordinator
    coordinator.sync()
    coordinator.session.markSaved()
    coordinator.chrome.update()
    assert not window.windowTitle().endswith(' *')
    signatures = []
    signature = coordinator.session._signature
    monkeypatch.setattr(coordinator.session, '_signature',
                        lambda: (signatures.append(True), signature())[1])
    for sequence in range(1, 11):
        send(controller, node, sequence)
    window._flushRuntimeEventViews()
    assert not signatures
    with coordinator.session.transaction():
        coordinator.session.workflows.project['name'] = 'Edited project'
    coordinator.chrome.update()
    assert signatures and window.windowTitle().endswith('Edited project *')
    assert coordinator.session.undo()
    coordinator.chrome.update()
    assert not window.windowTitle().endswith(' *')
