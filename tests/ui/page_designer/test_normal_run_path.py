"""Offline synthetic normal-run integration; does not claim field acceptance."""
from dataclasses import asdict
from pathlib import Path
from tests.runtime.runtime_test_utils import jobFailureDetails

from PySide2.QtCore import Qt
from PySide2.QtWidgets import QMessageBox

from emo_master.apps.designer.services.runtime_client import RuntimeClient
from emo_master.apps.designer.state.project_store import saveProject
from emo_master.apps.designer.ui.main_window import MainWindow
from emo_master.apps.runtime.grpc_server.service import RuntimeService
from emo_master.core.project.models import ProjectDocument
from emo_master.clients.runtime.display_session import DisplaySession
from examples.runtime_pages_p2 import sampleProject
from test_preview import waitFor
from tests.ui.page_designer.test_normal_run_viewing import displayedImagesReady


def testNormalRunTwoPagesShareImageNumberDecisionAndRestart(qtApp, tmp_path, monkeypatch):
    monkeypatch.delenv('EMO_PAGE_DESIGNER', raising=False)
    monkeypatch.setattr(QMessageBox, 'question', lambda *a, **k: QMessageBox.Discard)
    root = tmp_path / 'synthetic-normal-project'
    root.mkdir()
    raw = sampleProject(root).model_dump()
    raw['project']['projectId'] = 'r3-synthetic-image-judge'
    raw['workflows']['main']['nodes'][1]['params']['imagePath'] = str(root / 'input.png')
    raw['workflows']['main']['nodes'].append({'nodeId': 'judge', 'operatorId': 'vision.compare.number',
        'params': {'operator': 'gte', 'rightValue': 2.0}})
    raw['workflows']['main']['edges'].append({'fromNode': 'count', 'fromPort': 'count', 'toNode': 'judge', 'toPort': 'left'})
    raw['presentation']['dataSources']['judge'] = dict(raw['presentation']['dataSources']['count'],
        nodeId='judge', port='result', expectedType='boolean')
    raw['presentation']['pages']['main']['components'].append({'componentId': 'judge', 'type': 'indicator',
        'bindings': {'value': 'judge'}, 'layout': {'row': 2},
        'props': {'indicatorStates': {'true': {'text': 'OK', 'color': 'green'}, 'false': {'text': 'NG', 'color': 'red'}}}})
    document = ProjectDocument.model_validate(raw)
    saveProject(root, document.model_dump())
    runtime = RuntimeService(dbPath=tmp_path / 'production.sqlite3', workspaceRoot=tmp_path / 'jobs')
    client = RuntimeClient(runtime)
    window = MainWindow(client)
    observer = None
    try:
        assert window.loadProjectDirectory(str(root))
        window.show()
        waitFor(lambda: window.operatorCatalogController.state == 'ready')
        window.operatorCatalog = [asdict(item.manifest) for item in runtime.pluginScanResult.activeOperators.values()]
        coordinator = window.pageCoordinator
        coordinator.showPages()
        editor = coordinator.editor
        secondPage = editor.store.copyPage(editor.pageId)
        editor.refresh()
        assert window.saveProjectToDirectory(str(root))
        # This is the original toolbar controller, not isolated Prepare/Start.
        window.startJob()
        waitFor(lambda: window.currentJobId is not None)
        firstJob = window.currentJobId
        waitFor(lambda: not window.runtimeController._jobActive)
        assert len(runtime.jobRepository.all()) == 1
        coordinator.preview.watchCurrent()
        waitFor(lambda: coordinator.preview.hub is not None or coordinator.preview.error is not None)
        assert coordinator.preview.error is None
        waitFor(lambda: displayedImagesReady(editor.renderer))
        first = next(iter(editor.renderer.displayed.values()))
        assert first.result.identity.jobId == firstJob
        assert first.result.identity.mode == 'runtime'
        assert first.result.status == 'COMPLETE', jobFailureDetails(runtime, firstJob, result=first.result)
        assert len(first.images) == 1
        values = {source.sourceId: source.valueJson for source in first.result.sources if source.valueJson is not None}
        assert values == {'count': '2', 'judge': 'true'}
        editor.renderer.navigate(secondPage)
        assert editor.renderer.currentPageId == secondPage
        assert next(iter(editor.renderer.displayed.values())).result.identity.resultKey == first.result.identity.resultKey
        assert editor.tools.preview.isChecked()
        editor.tools.preview.setChecked(False)
        assert editor.renderer.editing
        assert editor.pageId == editor.renderer.currentPageId == secondPage
        assert editor.pageList.currentItem().data(Qt.UserRole) == secondPage
        assert editor.tools._loadedPage == secondPage and editor.tools.selected is None
        assert next(iter(editor.renderer.displayed.values())).result.identity.resultKey == first.result.identity.resultKey
        assert not coordinator.session.dirty
        # A second observer and disconnect cannot create or stop a job.
        observer = DisplaySession(client.displayAddress(), firstJob)
        waitFor(lambda: bool(observer.latest))
        assert len(runtime.jobRepository.all()) == 1
        coordinator.preview.closeAsync()
        waitFor(lambda: not coordinator.preview.active())
        assert runtime.jobRepository.get(firstJob).status == 'COMPLETED', jobFailureDetails(runtime, firstJob)
        observer.close()
        observer = None
        # Original controller starts another job only on this explicit operation.
        waitFor(lambda: window.runtimeController._worker is None)
        window.startJob()
        waitFor(lambda: window.currentJobId is not None and window.currentJobId != firstJob)
        waitFor(lambda: not window.runtimeController._jobActive)
        assert len(runtime.jobRepository.all()) == 2
        assert not runtime._presentationOwner.prepared
        assert Path(runtime.sqliteStore.dbPath) == tmp_path / 'production.sqlite3'
    finally:
        if observer:
            observer.close()
        if window.pageCoordinator.preview.active():
            window.pageCoordinator.preview.closeAsync()
            waitFor(lambda: not window.pageCoordinator.preview.active())
        window.close()
        runtime.close()


def testActiveNormalObserverDisconnectStopRetireAndExplicitRestart(qtApp, tmp_path, monkeypatch):
    """Exercise active GUI lifecycle using only a bounded synthetic test pacer."""
    from examples.runtime_pages_p2 import pacedProject, pluginRoots

    monkeypatch.delenv('EMO_PAGE_DESIGNER', raising=False)
    monkeypatch.setattr(QMessageBox, 'question', lambda *a, **k: QMessageBox.Discard)
    root = tmp_path / 'synthetic-active-project'
    root.mkdir()
    document = pacedProject(root, count=100)
    document.project.projectId = 'r3-synthetic-active-loop'
    next(node for node in document.workflows['detect'].nodes if node.nodeId == 'load').params['imagePath'] = str(root / 'input.png')
    saveProject(root, document.model_dump())
    runtime = RuntimeService(dbPath=tmp_path / 'production.sqlite3', workspaceRoot=tmp_path / 'jobs',
                             pluginRootPaths=pluginRoots(root))
    client = RuntimeClient(runtime)
    window = MainWindow(client)
    try:
        assert window.loadProjectDirectory(str(root))
        window.show()
        waitFor(lambda: window.operatorCatalogController.state == 'ready')
        window.pageCoordinator.showPages()
        preview = window.pageCoordinator.preview
        editor = window.pageCoordinator.editor
        window.startJob()
        waitFor(lambda: window.currentJobId is not None)
        firstJob = window.currentJobId
        waitFor(lambda: runtime.jobRepository.get(firstJob).status == 'RUNNING')
        preview.watchCurrent()
        waitFor(lambda: preview.hub is not None or preview.error is not None)
        assert preview.error is None
        waitFor(lambda: bool(editor.renderer.displayed))
        assert next(iter(editor.renderer.displayed.values())).result.identity.jobId == firstJob
        assert runtime.jobRepository.get(firstJob).status == 'RUNNING'
        preview.closeAsync()
        waitFor(lambda: not preview.active())
        # Closing the read-only observer cannot stop or replace the active job.
        assert runtime.jobRepository.get(firstJob).status == 'RUNNING'
        assert window.currentJobId == firstJob and window.isJobRunning
        assert len(runtime.jobRepository.all()) == 1
        # Explicit original GUI Stop is the only operation that stops this job.
        window.stopJob()
        waitFor(lambda: runtime.jobRepository.get(firstJob).isTerminal)
        assert runtime.jobRepository.get(firstJob).status == 'ABORTED'
        waitFor(lambda: not runtime.jobSupervisor.ownsJobResources(firstJob))
        waitFor(lambda: not window.runtimeController._jobActive
                and window.runtimeController._worker is None
                and window.runtimeController._stopWorker is None)
        assert not (runtime.workspaceRoot / firstJob).exists()
        window.startJob()
        waitFor(lambda: window.currentJobId is not None and window.currentJobId != firstJob)
        secondJob = window.currentJobId
        waitFor(lambda: runtime.jobRepository.get(secondJob).status == 'RUNNING')
        assert firstJob not in runtime._presentationOwner.jobs
        assert len(runtime.jobRepository.all()) == 2
        window.runtimeController.stopJob(mode='force')
        waitFor(lambda: runtime.jobRepository.get(secondJob).isTerminal)
        waitFor(lambda: not runtime.jobSupervisor.ownsJobResources(secondJob))
        assert runtime.jobRepository.get(secondJob).status == 'ABORTED'
    finally:
        if window.pageCoordinator.preview.active():
            window.pageCoordinator.preview.closeAsync()
            waitFor(lambda: not window.pageCoordinator.preview.active())
        window.close()
        runtime.close()
