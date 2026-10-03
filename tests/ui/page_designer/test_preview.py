import time
from pathlib import Path

import pytest
from PySide2.QtWidgets import QMessageBox, QApplication

from emo_master.apps.runtime.grpc_server.service import RuntimeService
from emo_master.apps.designer.services.runtime_client import RuntimeClient
from emo_master.apps.designer.ui.main_window import MainWindow
from emo_master.apps.designer.page_designer.preview import checkLocalDraft
from emo_master.apps.designer.state.project_store import saveProject
from examples.runtime_pages_p2 import sampleProject


def waitFor(predicate, seconds=25):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        QApplication.processEvents()
        if predicate():
            return
        time.sleep(.01)
    raise AssertionError('bounded Qt condition deadline')


def testDraftOnSameRuntimeReloadObserveAndAsyncExit(qtApp, tmp_path, monkeypatch):
    monkeypatch.setenv('EMO_PAGE_DESIGNER', '1')
    monkeypatch.setattr(QMessageBox, 'question', lambda *a, **k: QMessageBox.Discard)
    root = tmp_path/'project'
    root.mkdir()
    document = sampleProject(root)
    saveProject(root, document.model_dump())
    runtime = RuntimeService(dbPath=tmp_path/'runtime.sqlite3', workspaceRoot=tmp_path/'jobs')
    client = RuntimeClient(runtime)
    window = MainWindow(client)
    preview = window.pageCoordinator.preview
    try:
        assert window.loadProjectDirectory(str(root))
        window.show()
        c = window.pageCoordinator
        c.showPages()
        preview.startDebug()
        waitFor(lambda: preview.hub is not None or preview.error is not None)
        assert preview.error is None
        backend = preview.backend
        assert backend.runtime is runtime
        waitFor(lambda: bool(c.editor.renderer.displayed))
        scope = next(iter(c.editor.renderer.displayed.values()))
        assert scope.result.status == 'COMPLETE'
        assert len(scope.images) == 1
        assert next(s.valueJson for s in scope.result.sources if s.sourceId == 'count') == '2'
        prepared = backend.service.prepared[backend.preparedId]
        assert Path(prepared.snapshot.runtimeDbPath).resolve().is_relative_to(backend.root.resolve())
        assert Path(prepared.snapshot.outputRoot).resolve().is_relative_to(backend.root.resolve())
        session, hub = preview.session, preview.hub
        snapshotJson = prepared.snapshot.projectJson
        savedUndo = len(c.session._undo)
        for _ in range(3):
            c.showFlow()
            c.showPages()
            c.editor.refresh()
        assert preview.session is session and preview.hub is hub
        assert len(backend.service.jobs) == 1
        assert len(c.session._undo) == savedUndo
        # Layout reload is local; the running capture is immutable.
        c.session.presentation.renamePage('main', 'renamed')
        c.editor.refresh()
        assert prepared.snapshot.projectJson == snapshotJson
        assert window.saveProjectToDirectory(str(root))
        c.session.markSaved()
        preview.closeAsync()
        waitFor(lambda: not preview.active())
        assert not runtime._closed
        assert not prepared.projectPath.parent.exists()
        assert not Path(prepared.snapshot.runtimeDbPath).parent.exists()
        assert runtime._presentationOwner is not None
        import shiboken2
        assert not shiboken2.isValid(hub) or not hub.timer.isActive()
        assert c.editor.renderer.hub is None
    finally:
        if preview.active():
            preview.closeAsync()
            waitFor(lambda: not preview.active())
        window.close()
        runtime.close()


def testLocalAllowlistRejectsDeviceBeforeRuntimeAccess(tmp_path):
    document = sampleProject(tmp_path)
    document.workflows['main'].nodes[1].operatorId = 'vision.io.huaray_camera'
    with pytest.raises(ValueError, match='不执行设备'):
        checkLocalDraft(document)


def testBorrowedJobClosingDesignerLeavesRuntimeAndOtherObserver(qtApp, tmp_path, monkeypatch):
    from emo_master.apps.designer.page_designer.preview import LocalBackend
    from emo_master.clients.runtime.display_session import DisplaySession
    from test_workspace import Client
    monkeypatch.setenv('EMO_PAGE_DESIGNER', '1')
    monkeypatch.setattr(QMessageBox, 'question', lambda *a, **k: QMessageBox.Discard)
    root = tmp_path/'project'
    root.mkdir()
    document = sampleProject(root)
    saveProject(root, document.model_dump())
    runtime = RuntimeService(dbPath=tmp_path/'runtime.sqlite3', workspaceRoot=tmp_path/'jobs')
    backend = LocalBackend(runtime)
    other = None
    window = MainWindow(Client())
    preview = window.pageCoordinator.preview
    try:
        job = backend.start(document, root)
        other = DisplaySession(backend.address, job)
        assert window.loadProjectDirectory(str(root))
        window.pageCoordinator.showPages()
        window.show()
        preview.connect(backend.address, job)
        waitFor(lambda: preview.hub is not None)
        waitFor(lambda: bool(window.pageCoordinator.editor.renderer.displayed))
        oldRenderer = window.pageCoordinator.editor.renderer
        key = next(iter(oldRenderer.displayed.values())).result.identity.resultKey
        waitFor(lambda: bool(other.readSnapshot().scopes))
        assert next(iter(other.readSnapshot().scopes.values())).result.identity.resultKey == key
        window.close()
        waitFor(lambda: not window.isVisible())
        assert not preview.active()
        assert not runtime._closed
        assert job in backend.service.jobs
        assert not other.stop.is_set()
        assert all(thread.is_alive() for thread in other.threads)
        assert oldRenderer.detached and oldRenderer.lastView is None
    finally:
        if preview.active():
            preview.closeAsync()
            waitFor(lambda: not preview.active())
        window.close()
        if other:
            other.close()
        backend.close()
        runtime.close()


def testProjectSwitchWhileStartCompletesFencesLateAttach(qtApp, tmp_path, monkeypatch):
    import threading
    from emo_master.apps.designer.page_designer.preview import LocalBackend
    monkeypatch.setenv('EMO_PAGE_DESIGNER', '1')
    monkeypatch.setattr(QMessageBox, 'question', lambda *a, **k: QMessageBox.Discard)
    entered, release = threading.Event(), threading.Event()
    original = LocalBackend.start
    def delayed(self, document, root):
        entered.set()
        if not release.wait(3):
            raise TimeoutError('test start gate')
        return original(self, document, root)
    monkeypatch.setattr(LocalBackend, 'start', delayed)
    first, second = tmp_path/'one', tmp_path/'two'
    for root in [first, second]:
        root.mkdir()
        saveProject(root, sampleProject(root).model_dump())
    runtime = RuntimeService(dbPath=tmp_path/'runtime.sqlite3', workspaceRoot=tmp_path/'jobs')
    window = MainWindow(RuntimeClient(runtime))
    c = window.pageCoordinator
    try:
        assert window.loadProjectDirectory(str(first))
        window.show()
        c.showPages()
        c.preview.startDebug()
        waitFor(entered.is_set)
        assert not window.loadProjectDirectory(str(second))
        release.set()
        waitFor(lambda: not c.preview.active())
        assert c.preview.hub is None
        assert window.loadProjectDirectory(str(second))
        c.showPages()
        assert c.editor.renderer.lastView is None
        assert not c.session.dirty
        assert not runtime._closed
    finally:
        release.set()
        if c.preview.active():
            c.preview.closeAsync()
            waitFor(lambda: not c.preview.active())
        window.close()
        runtime.close()
