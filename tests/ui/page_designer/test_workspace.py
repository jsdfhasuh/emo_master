import json
from types import SimpleNamespace

import pytest
from PySide2.QtWidgets import QMessageBox, QInputDialog

from emo_master.apps.designer.ui.main_window import MainWindow


class Client:
    def listOperators(self):
        return []

    def loadProject(self, path):
        return SimpleNamespace(ok=True, message='ok')


@pytest.fixture
def designer(qtApp, monkeypatch):
    monkeypatch.setenv('EMO_PAGE_DESIGNER', '1')
    monkeypatch.setattr(QMessageBox, 'question', lambda *a, **k: QMessageBox.Yes)
    window = MainWindow(Client())
    window.show()
    yield window
    monkeypatch.setattr(QMessageBox, 'question', lambda *a, **k: QMessageBox.Discard)
    window.close()


def testUnifiedPagesFlowSaveReopenAndUndo(designer, monkeypatch, tmp_path):
    w = designer
    c = w.pageCoordinator
    assert c.session.workflows is w.workflowStore
    assert c.session.document().schemaVersion == '2.1'
    assert not c.session.dirty
    assert w.saveProjectToDirectory(str(tmp_path/'original'))
    c.showPages()
    e = c.editor
    assert c.session.document().schemaVersion == '2.2'
    monkeypatch.setattr(QInputDialog, 'getText', lambda *a, **k: ('运行总览', True))
    e.run(e.newPage)
    first = e.pageId
    e.run(e.copyPage)
    second = e.pageId
    assert first != second
    c.history()
    assert second not in c.session.presentation.snapshot().pages
    c.history(True)
    assert second in c.session.presentation.snapshot().pages
    assert w.saveProjectToDirectory(str(tmp_path/'original'))
    saved = c.session.document().presentation
    w.createWorkflow('new flow')
    assert c.session.dirty
    c.history()
    assert len(w.workflowStore.workflows) == 1
    c.history(True)
    assert w.saveProjectToDirectory(str(tmp_path/'copy'))
    assert not c.session.dirty
    assert (tmp_path/'original'/'project.json.bak').exists()
    assert w.loadProjectDirectory(str(tmp_path/'copy'))
    assert c.session.document().presentation == saved
    assert len(w.workflowStore.workflows) == 2
    assert json.loads((tmp_path/'copy'/'project.json').read_text())['schemaVersion'] == '2.2'


def testSaveFailureAndCancelDoNotLoseDraft(designer, monkeypatch, tmp_path):
    c = designer.pageCoordinator
    c.showPages()
    c.session.presentation.createPage('keep')
    from emo_master.apps.designer.controllers import project_controller
    monkeypatch.setattr(project_controller, 'saveProject', lambda *a: (_ for _ in ()).throw(OSError('disk full')))
    assert not designer.saveProjectToDirectory(str(tmp_path/'fail'))
    assert c.session.dirty
    monkeypatch.setattr(QMessageBox, 'question', lambda *a, **k: QMessageBox.Cancel)
    assert not c.confirmLeave()
    assert len(c.session.presentation.snapshot().pages) == 1


def testViewAndRuntimeUpdatesDoNotDirty(designer):
    c = designer.pageCoordinator
    c.showPages()
    c.session.presentation.createPage('one')
    c.editor.refresh()
    c.session.markSaved()
    history = len(c.session._undo)
    designer.setCurrentNodeRuntimeState('nonexistent', 'COMPLETED', '')
    c.showFlow()
    c.showPages()
    c.sync()
    assert not c.session.dirty
    assert len(c.session._undo) == history
