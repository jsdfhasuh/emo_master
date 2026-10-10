"""Current-project read-only selection and no implicit execution regressions."""
import json
import time
from contextlib import nullcontext
from dataclasses import replace
from types import MappingProxyType, SimpleNamespace

import pytest
from PySide2.QtWidgets import QApplication, QInputDialog, QMessageBox
from PySide2.QtCore import QCoreApplication, QEvent
from shiboken2 import isValid

from emo_master.apps.designer.ui.main_window import MainWindow
from emo_master.apps.designer.page_designer import preview as preview_module
from emo_master.clients.runtime.view_state import JobView, SessionView
from emo_master.core.project.snapshots import captureDefinition, revisionOf
from emo_master.core.presentation.models import walkComponents
from emo_master.apps.designer.state.project_store import saveProject
from examples.runtime_pages_p3 import sampleProjectP3


def waitFor(predicate):
    end = time.monotonic() + 5
    while time.monotonic() < end:
        QApplication.processEvents()
        if predicate():
            return
        time.sleep(.005)
    raise AssertionError('bounded Qt wait')


def metadata(document, job='current', *, capture=True, project=None):
    presentation = document.presentation
    used = {key for page in presentation.pages.values() for component in walkComponents(page.components)
            for key in component.bindings.values()}
    definition = captureDefinition(presentation)
    return SimpleNamespace(job_id=job, project_id=project or document.project.projectId,
        workflow_id=document.entryWorkflowId, status='COMPLETED', mode='runtime',
        runtime_instance_id='runtime', capture_enabled=capture, source_ids=sorted(used) if capture else [],
        sources_json=json.dumps({key: presentation.dataSources[key].model_dump() for key in used}) if capture else '{}',
        capture_definition_json=json.dumps(definition) if capture else '',
        capture_plan_revision=revisionOf(definition) if capture else '', execution_revision='a'*64,
        resources_released=False)


class Client:
    def __init__(self):
        self.calls = []
        self.jobs = []
        self.capabilities = SimpleNamespace(protocol_version='1.0', runtime_instance_id='runtime',
            capabilities=['snapshot', 'subscribe', 'project_jobs', 'source_coverage'])

    def listOperators(self):
        return []

    def loadProject(self, path):
        return SimpleNamespace(ok=True, message='ok')

    def getDisplayCapabilities(self):
        self.calls.append('capabilities')
        return self.capabilities

    def displayAddress(self):
        self.calls.append('address')
        return '127.0.0.1:51001'

    def listDisplayJobs(self, projectId):
        self.calls.append(('list', projectId))
        return self.jobs


class Session:
    instances = []

    def __init__(self, address, job, *, imageDemand=False, expectedRuntimeInstanceId='', projectId='', readResults=True):
        self.address, self.jobId, self.closed = address, job, False
        self.imageDemand = imageDemand
        self.expectedRuntimeInstanceId, self.projectId = expectedRuntimeInstanceId, projectId
        self.readResults = readResults
        self.job = JobView(expectedRuntimeInstanceId, projectId, job, status='RUNNING',
            availability='AVAILABLE', captureEnabled=readResults, resourcesReleased=False)
        self.imageConsumers = {}
        self.instances.append(self)

    def setImageDemand(self, owner, sources):
        self.imageConsumers[owner] = sources

    def removeImageDemand(self, owner):
        self.imageConsumers.pop(owner, None)

    def readSnapshot(self):
        empty = MappingProxyType({})
        return SessionView(0, 0, 'runtime', self.jobId, 'CONNECTED' if self.readResults else 'NOT_CAPTURED',
            '' if self.readResults else '任务未采集页面来源；下一次明确启动才生效', empty, empty, empty, job=self.job)

    def close(self):
        self.closed = True


@pytest.fixture
def designer(qtApp, tmp_path, monkeypatch):
    monkeypatch.delenv('EMO_PAGE_DESIGNER', raising=False)
    monkeypatch.setattr(QMessageBox, 'question', lambda *a, **k: QMessageBox.Discard)
    Session.instances = []
    monkeypatch.setattr(preview_module, 'DisplaySession', Session)
    client = Client()
    window = MainWindow(client)
    document = sampleProjectP3(tmp_path)
    saveProject(tmp_path, document.model_dump())
    assert window.loadProjectDirectory(str(tmp_path))
    window.pageCoordinator.showPages()
    window.show()
    yield window, client, document
    preview = window.pageCoordinator.preview
    if preview.active():
        preview.closeAsync()
        waitFor(lambda: not preview.active())
    window.close()


def testNormalEntryDoesNotMigrateWithoutExplicitEnable(qtApp, monkeypatch):
    monkeypatch.delenv('EMO_PAGE_DESIGNER', raising=False)
    monkeypatch.setattr(QMessageBox, 'question', lambda *a, **k: QMessageBox.No)
    window = MainWindow(Client())
    coordinator = window.pageCoordinator
    assert coordinator is not None
    assert window.pageCoordinator.chrome.selector.text() == '当前：流程设计'
    assert [a.text() for a in window.pageCoordinator.chrome.workspaceActions] == ['流程设计', '页面设计']
    assert coordinator.session.document().schemaVersion == '2.1'
    coordinator.showPages()
    assert coordinator.editor is None
    assert not coordinator.session.dirty
    assert coordinator.session.document().schemaVersion == '2.1'
    window.close()


def testCurrentSelectionFiltersProjectAndShowsStatusChoice(designer, monkeypatch):
    window, client, document = designer
    client.jobs = [metadata(document, 'foreign', project='other-project'),
                   metadata(document, 'older'), metadata(document, 'current')]
    window.currentJobId = 'current'
    choices = []
    def select(parent, title, label, items, index, editable):
        choices.append((items, index))
        return items[index], True
    monkeypatch.setattr(QInputDialog, 'getItem', select)
    preview = window.pageCoordinator.preview
    preview.watchCurrent()
    waitFor(lambda: preview.hub is not None or preview.error)
    assert preview.error is None
    assert preview.session.jobId == 'current'
    assert choices[0][1] == 1
    assert all('foreign' not in item for item in choices[0][0])
    assert all('已完成' in item and '正常运行' in item for item in choices[0][0])
    assert client.calls == ['capabilities', 'address', ('list', document.project.projectId)]
    assert preview.backend is None
    # Choosing a different observer must never grant the run controller Stop ownership.
    monkeypatch.setattr(QInputDialog, 'getItem', lambda _p, _t, _l, items, _i, _e: (items[0], True))
    previous = preview.session
    preview.watchCurrent()
    waitFor(lambda: preview.session is not None and preview.session is not previous)
    assert previous.closed
    assert preview.session.jobId == 'older'
    assert window.currentJobId == 'current'
    assert preview.backend is None


def testUncapturedJobUsesStatusOnlySharedSession(designer):
    window, client, document = designer
    client.jobs = [metadata(document, capture=False)]
    preview = window.pageCoordinator.preview
    preview.watchCurrent()
    waitFor(lambda: preview.selectedJob is not None or preview.error)
    assert preview.error is None
    assert len(Session.instances) == 1
    assert not preview.session.readResults
    assert preview.session.expectedRuntimeInstanceId == 'runtime'
    assert preview.session.projectId == document.project.projectId
    assert preview.backend is None
    editor = window.pageCoordinator.editor
    assert 'SOURCE_NOT_CAPTURED' in preview.observer.jobStatus.text()
    assert '本次运行未采集所需数据' in preview.observer.widgets['overview']['overview-count'][1].text()
    assert preview.observer.lastView.connection == 'NOT_CAPTURED'
    editor.choosePage(1)
    preview.openObserver()
    assert preview.observer.hub is preview.hub
    assert '无页面采集数据' in preview.observer.jobStatus.text()
    assert len(Session.instances) == 1


def testUnsupportedRuntimeFailsBeforeEndpointOrLocalBackend(designer):
    window, client, document = designer
    client.capabilities.capabilities = ['snapshot', 'subscribe']
    preview = window.pageCoordinator.preview
    preview.watchCurrent()
    waitFor(lambda: preview.error is not None)
    assert '协商' in preview.error
    assert client.calls == ['capabilities']
    assert preview.backend is preview.session is None
    assert not Session.instances


def testNoGlobalLastJobFallback(designer):
    window, client, document = designer
    client.jobs = [metadata(document, 'global-last', project='other-project')]
    preview = window.pageCoordinator.preview
    preview.watchCurrent()
    waitFor(lambda: preview.error is not None)
    assert '尚未运行' in preview.error
    assert preview.session is None


def testCancelledSelectionAndOpeningChangingClosingDoNotExecute(designer, monkeypatch):
    window, client, document = designer
    client.jobs = [metadata(document, 'one'), metadata(document, 'two')]
    monkeypatch.setattr(QInputDialog, 'getItem', lambda *a, **k: ('', False))
    preview = window.pageCoordinator.preview
    preview.watchCurrent()
    waitFor(lambda: not preview.busy)
    window.pageCoordinator.showFlow()
    window.pageCoordinator.showPages()
    window.pageCoordinator.editor.choosePage(1)
    assert preview.backend is preview.session is None
    assert len(client.calls) == 3
    assert not Session.instances


def testWindowCloseFailureRetainsWindowAndCanRetry(designer, monkeypatch):
    window, client, document = designer
    original = window.runtimeController.close
    attempts = []
    def fail():
        attempts.append(True)
        raise RuntimeError('resource owner has not exited')
    monkeypatch.setattr(window.runtimeController, 'close', fail)
    assert not window.close()
    assert window.isVisible()
    assert window.pageCoordinator.editor is not None
    assert window.pageCoordinator.preview.timer.isActive()
    assert '关闭未完成' in window.statusBar().currentMessage()
    monkeypatch.setattr(window.runtimeController, 'close', original)
    assert window.close()
    assert len(attempts) == 1


def selectJob(designer, *, job='current', capture=True, released=False):
    window, client, document = designer
    item = metadata(document, job, capture=capture)
    item.resources_released = released
    client.jobs = [item]
    preview = window.pageCoordinator.preview
    preview.watchCurrent()
    waitFor(lambda: preview.hub is not None or preview.error)
    assert preview.error is None
    return window.pageCoordinator, preview


def testPreviewReusesOneSessionHidesWithWorkspaceAndClosesViewing(designer):
    coordinator, preview = selectJob(designer)
    editor = coordinator.editor
    observer, hub, session = preview.observer, preview.hub, preview.session
    assert observer.isWindow() and observer.isVisible()
    assert not observer.editing and editor.renderer.editing
    assert hub.windows == {observer} and editor.renderer.hub is None
    assert observer.config == editor.renderer.config
    coordinator.showFlow()
    assert not observer.isVisible() and not editor.isVisible()
    coordinator.showPages()
    assert not observer.isVisible()
    assert preview.openObserver() is observer
    assert preview.session is session and len(Session.instances) == 1
    observer.close()
    waitFor(lambda: not preview.active())
    assert session.closed and not hub.windows
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    assert not isValid(observer) and preview.observer is None
    reopened = preview.openObserver()
    assert reopened.designExamples and reopened.hub is None
    assert len(Session.instances) == 1


def testObserverOpeningRejectsInvalidPendingFormWithoutRefresh(designer, monkeypatch):
    coordinator, preview = selectJob(designer)
    editor, observer = coordinator.editor, preview.observer
    before = (editor.pageId, editor.renderer.config, editor.renderer.lastView, len(coordinator.session._undo))
    def invalid():
        raise ValueError('invalid pending input')
    monkeypatch.setattr(editor.tools, 'commitPending', invalid)
    with pytest.raises(ValueError, match='invalid pending input'):
        preview.openObserver()
    assert preview.observer is observer and preview.hub.windows == {observer}
    assert (editor.pageId, editor.renderer.config, editor.renderer.lastView, len(coordinator.session._undo)) == before


def testObserverRetiresOnDisconnectAndNeverBecomesEditor(designer):
    coordinator, preview = selectJob(designer)
    editor = coordinator.editor
    preview.openObserver()
    observer, hub, session = preview.observer, preview.hub, preview.session
    preview.closeAsync()
    assert observer.detached and observer.hub is None
    assert not observer.isVisible() and not hub.windows
    assert not observer.displayed and observer.lastView is None
    assert editor.renderer.hub is None and editor.renderer.lastView is None
    assert 'RUNNING' not in editor.renderer.jobStatus.text()
    waitFor(lambda: not preview.active())
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    assert not isValid(observer) and preview.observer is None
    assert session.closed and editor.renderer.isVisible()


def testObserverDirectDestroyAndJobSwitchReuseWindowWithoutLeakingSession(designer):
    coordinator, preview = selectJob(designer, job='first')
    observer, session = preview.observer, preview.session
    observer.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    waitFor(lambda: not preview.active())
    assert not isValid(observer) and preview.observer is None and session.closed
    observer = preview.openObserver()
    _, client, document = designer
    client.jobs = [metadata(document, 'second')]
    preview.watchCurrent()
    waitFor(lambda: preview.session is not None and not preview.busy)
    previous = preview.session
    client.jobs = [metadata(document, 'third')]
    preview.watchCurrent()
    waitFor(lambda: preview.session is not None and preview.session is not previous and not preview.busy)
    assert preview.observer is observer and previous.closed
    assert observer.lastView.jobId == 'third'
    assert preview.hub.windows == {observer}


def testReleasedJobStatusAndUnavailableAreNeverSelectionStatus(designer):
    coordinator, preview = selectJob(designer, released=True)
    session = preview.session
    assert not session.readResults
    session.job = replace(session.job, status='FAILED', resourcesReleased=True)
    preview.poll()
    assert 'FAILED' in preview.observer.jobStatus.text()
    assert '任务资源已释放' in preview.observer.jobStatus.text()
    assert 'COMPLETED' not in preview.observer.jobStatus.text()
    preview.openObserver()
    assert preview.observer.hub is preview.hub
    assert 'FAILED' in preview.observer.jobStatus.text()
    session.job = replace(session.job, status='', availability='UNAVAILABLE', detail='GetJob timeout')
    preview.poll()
    preview.hub.tick()
    assert '执行状态不可用' in preview.observer.jobStatus.text()
    assert 'FAILED' not in preview.observer.jobStatus.text()
    assert '执行状态不可用' in preview.observer.jobStatus.text()
    assert 'FAILED' not in preview.observer.jobStatus.text()


def testObserverConstructionAndAttachFailuresRetirePartialNativeWindows(designer, monkeypatch):
    from emo_master.apps.designer.page_designer.workspace import ObserverPages
    from emo_master.ui.presentation.hub import DisplayHub
    window, client, document = designer
    coordinator = window.pageCoordinator
    preview = coordinator.preview
    original = ObserverPages._build
    def failBuild(self, _page):
        raise ValueError('construction failed after native allocation')
    monkeypatch.setattr(ObserverPages, '_build', failBuild)
    with pytest.raises(ValueError, match='construction failed'):
        preview.openObserver()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    assert preview.observer is None and not window.findChildren(ObserverPages)
    monkeypatch.setattr(ObserverPages, '_build', original)
    attach = DisplayHub.attach
    def failAttach(hub, view):
        attach(hub, view)
        raise ValueError('attach acknowledgement failed')
    monkeypatch.setattr(DisplayHub, 'attach', failAttach)
    client.jobs = [metadata(document)]
    preview.watchCurrent()
    waitFor(lambda: preview.error is not None)
    assert 'attach acknowledgement failed' in preview.error
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    assert preview.observer is None and not preview.hub.windows
    assert not window.findChildren(ObserverPages)
    assert len(Session.instances) == 1 and not preview.session.closed
    preview.stopViewing()
    waitFor(lambda: not preview.active())
    assert Session.instances[0].closed


def testPreviewReuseDoesNotAllocateAnotherHubWindow(designer):
    from emo_master.ui.presentation.renderer import RuntimePages
    coordinator, preview = selectJob(designer)
    extra = RuntimePages(coordinator.editor.renderer.config, hub=preview.hub)
    try:
        assert not extra.isVisible() and len(preview.hub.windows) == 2
        observer = preview.observer
        assert preview.openObserver() is observer
        assert len(preview.hub.windows) == 2 and len(Session.instances) == 1
    finally:
        extra.close()
        extra.deleteLater()


def testDisconnectFailureKeepsOwnerRetryableButRetiresPopup(designer, monkeypatch):
    coordinator, preview = selectJob(designer)
    preview.openObserver()
    observer, session = preview.observer, preview.session
    close = session.close
    def failClose():
        raise ValueError('session cleanup pending')
    monkeypatch.setattr(session, 'close', failClose)
    preview.closeAsync()
    waitFor(lambda: not preview.busy)
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    assert not isValid(observer) and preview.observer is None
    assert preview.session is session and preview.active() and not preview.closing
    assert preview.error == 'session cleanup pending'
    assert coordinator.editor.renderer.lastView is None
    preview.poll()
    assert preview.observer is None
    assert 'RUNNING' not in coordinator.editor.renderer.jobStatus.text()
    monkeypatch.setattr(session, 'close', close)
    preview.closeAsync()
    waitFor(lambda: not preview.active())
    assert session.closed


def testPreviewStartsWithSamplesAndCommitsFormBeforeOpening(designer):
    window, _client, _document = designer
    coordinator = window.pageCoordinator
    editor, preview = coordinator.editor, coordinator.preview
    observer = preview.openObserver()
    assert not Session.instances and observer.designExamples
    editor.tools.select('overview-count')
    editor.tools.fields['title'].setText('已经提交的新标题')
    rendered = editor.renderer.config
    assert preview.openObserver() is observer
    assert observer.config == editor.store.snapshot()
    assert observer.config != rendered
    assert editor.tools.fields['title'].text() == '已经提交的新标题'
    editor.refresh()
    assert observer.config == editor.renderer.config == editor.store.snapshot()
    assert observer.hub is None and not Session.instances


def testAdvancedModernConnectionFiltersProjectAndSupportsUncapturedJob(designer, monkeypatch):
    window, client, document = designer
    item = metadata(document, capture=False)
    requests = []
    stub = SimpleNamespace(Capabilities=lambda _request, **_kwargs: client.capabilities,
        ListJobs=lambda request, **_kwargs: requests.append(request.project_id) or SimpleNamespace(jobs=[item]))
    monkeypatch.setattr(preview_module.grpc, 'insecure_channel', lambda _address: nullcontext(object()))
    monkeypatch.setattr(preview_module.rpc, 'DisplayServiceStub', lambda _channel: stub)
    preview = window.pageCoordinator.preview
    preview.connect('127.0.0.1:51001', item.job_id)
    waitFor(lambda: preview.hub is not None or preview.error)
    assert preview.error is None and requests == [document.project.projectId]
    assert len(Session.instances) == 1 and not preview.session.readResults
    preview.openObserver()
    assert preview.observer.hub is preview.hub
