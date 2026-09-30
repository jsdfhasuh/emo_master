"""Current-project read-only selection and no implicit execution regressions."""
import json
import time
from types import MappingProxyType, SimpleNamespace

import pytest
from PySide2.QtWidgets import QApplication, QInputDialog, QMessageBox

from emo_master.apps.designer.ui.main_window import MainWindow
from emo_master.apps.designer.page_designer import preview as preview_module
from emo_master.clients.runtime.view_state import SessionView
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

    def __init__(self, address, job):
        self.address, self.jobId, self.closed = address, job, False
        self.instances.append(self)

    def readSnapshot(self):
        empty = MappingProxyType({})
        return SessionView(0, 0, 'runtime', self.jobId, 'CONNECTED', '', empty, empty, empty)

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
    assert any(action.text() == '页面设计' for action in window.mainToolbar.actions())
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
    assert all('COMPLETED' in item and '正常运行' in item for item in choices[0][0])
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


def testUncapturedJobIsVisibleAndNeverCreatesSession(designer):
    window, client, document = designer
    client.jobs = [metadata(document, capture=False)]
    preview = window.pageCoordinator.preview
    preview.watchCurrent()
    waitFor(lambda: preview.selectedJob is not None or preview.error)
    assert preview.error is None
    assert not Session.instances
    assert preview.backend is None
    editor = window.pageCoordinator.editor
    assert 'SOURCE_NOT_CAPTURED' in editor.observation.text()
    assert 'NOT_CAPTURED' in editor.renderer.widgets['overview']['overview-count'][1].text()
    assert '下一次明确启动' in editor.renderer.status.text()
    editor.choosePage(1)
    assert not Session.instances


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
    assert '当前工程没有可观看任务' in preview.error
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
