"""Synthetic local fixture: normal Designer Run and two read-only pages share a Job.

This regression is not a user-project, device, performance or field acceptance.
"""
import time
from tests.runtime.runtime_test_utils import jobFailureDetails
import pytest

from PySide2.QtWidgets import QApplication, QMessageBox

from emo_master.apps.designer.services.runtime_client import RuntimeClient
from emo_master.apps.designer.state.project_store import saveProject
from emo_master.apps.designer.ui.main_window import MainWindow
from emo_master.apps.runtime.grpc_server.service import RuntimeService
from examples.runtime_pages_p2 import sampleProject


def waitFor(predicate, seconds=20):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        QApplication.processEvents()
        if predicate():
            return
        time.sleep(.01)
    raise AssertionError('normal run observation deadline')


def testNormalEntrySameJobTwoPagesReconnectAndRunAgain(qtApp, tmp_path, monkeypatch):
    monkeypatch.delenv('EMO_PAGE_DESIGNER', raising=False)
    monkeypatch.setattr(QMessageBox, 'question', lambda *a, **k: QMessageBox.Discard)
    root = tmp_path / 'project'
    root.mkdir()
    document = sampleProject(root)
    # Keep the normal run's configured path, rather than using debug materialization.
    document.workflows['main'].nodes[1].params['imagePath'] = str(root / 'input.png')
    saveProject(root, document.model_dump())
    runtime = RuntimeService(dbPath=tmp_path / 'runtime.sqlite3', workspaceRoot=tmp_path / 'jobs')
    window = MainWindow(RuntimeClient(runtime))
    try:
        assert window.loadProjectDirectory(str(root))
        coordinator = window.pageCoordinator
        coordinator.showPages()
        second = coordinator.session.presentation.copyPage('main')
        coordinator.editor.refresh()
        window.show()
        assert not runtime.jobRepository.all()
        window.startJob()
        waitFor(lambda: window.currentJobId is not None or window.runtimePanelState.jobStatus == 'FAILED')
        first = window.currentJobId
        assert first, window.runtimePanelState.jobMessage
        waitFor(lambda: not window.isJobRunning)
        assert len(runtime.jobRepository.all()) == 1
        coordinator.preview.watchCurrent()
        waitFor(lambda: coordinator.preview.hub is not None or coordinator.preview.error)
        assert coordinator.preview.error is None
        waitFor(lambda: bool(coordinator.editor.renderer.displayed))
        renderer = coordinator.editor.renderer
        scope = next(iter(renderer.displayed.values()))
        assert scope.result.identity.jobId == first
        assert scope.result.identity.mode == 'runtime'
        assert scope.result.status == 'COMPLETE', jobFailureDetails(runtime, first, result=scope.result)
        assert next(source.valueJson for source in scope.result.sources if source.sourceId == 'count') == '2'
        assert len(scope.images) == 1
        key = scope.result.identity.resultKey
        renderer.navigate(second)
        assert next(iter(renderer.displayed.values())).result.identity.resultKey == key
        session = coordinator.preview.session
        coordinator.preview.watchCurrent()
        waitFor(lambda: coordinator.preview.session is not None and coordinator.preview.session is not session)
        waitFor(lambda: bool(renderer.displayed))
        assert next(iter(renderer.displayed.values())).result.identity.resultKey == key
        assert len(runtime.jobRepository.all()) == 1
        coordinator.preview.closeAsync()
        waitFor(lambda: not coordinator.preview.active())
        assert not runtime._closed
        assert runtime.jobRepository.get(first).status == 'COMPLETED', jobFailureDetails(runtime, first)
        window.startJob()
        waitFor(lambda: window.currentJobId not in (None, first) or window.runtimePanelState.jobStatus == 'FAILED')
        assert window.currentJobId not in (None, first), window.runtimePanelState.jobMessage
        waitFor(lambda: not window.isJobRunning)
        assert len(runtime.jobRepository.all()) == 2
        assert runtime.jobRepository.get(window.currentJobId).status == 'COMPLETED', jobFailureDetails(runtime, window.currentJobId)
    finally:
        if window.pageCoordinator.preview.active():
            window.pageCoordinator.preview.closeAsync()
            waitFor(lambda: not window.pageCoordinator.preview.active())
        window.close()
        runtime.close()


@pytest.mark.parametrize('fixtureName', ['twoImages', 'twoCalls'])
def testNormalGuiRunAdvertisedMultiSourceProfile(qtApp, tmp_path, monkeypatch, fixtureName):
    """Two real images or two call scopes through the original GUI Run path."""
    import json
    import numpy as np
    from tests.runtime.presentation import test_multi_capture as fixtures
    monkeypatch.setattr(QMessageBox, 'question', lambda *a, **k: QMessageBox.Discard)
    root = tmp_path / 'project'
    root.mkdir()
    document = getattr(fixtures, fixtureName)(root)
    saveProject(root, document.model_dump())
    runtime = RuntimeService(dbPath=tmp_path / 'runtime.sqlite3', workspaceRoot=tmp_path / 'jobs')
    window = MainWindow(RuntimeClient(runtime))
    try:
        assert window.loadProjectDirectory(str(root))
        coordinator = window.pageCoordinator
        coordinator.showPages()
        window.show()
        window.startJob()
        waitFor(lambda: window.currentJobId is not None or window.runtimePanelState.jobStatus == 'FAILED')
        job = window.currentJobId
        assert job, window.runtimePanelState.jobMessage
        waitFor(lambda: not window.isJobRunning)
        assert runtime.jobRepository.get(job).status == 'COMPLETED', jobFailureDetails(runtime, job)
        coordinator.preview.watchCurrent()
        waitFor(lambda: coordinator.preview.hub is not None or coordinator.preview.error)
        assert coordinator.preview.error is None
        renderer = coordinator.editor.renderer
        waitFor(lambda: bool(renderer.displayed))
        first = next(iter(renderer.displayed.values()))
        assert first.result.identity.jobId == job
        assert first.result.status == 'COMPLETE', jobFailureDetails(runtime, job, result=first.result)
        metadata = window.runtimeClient.listDisplayJobs(document.project.projectId)[0]
        limits = json.loads(metadata.capture_limits_json)
        assert set(limits['rawBytesBySource'].values()) == {4 * 1024 * 1024}
        renderer.navigate(document.presentation.pageOrder[1])
        waitFor(lambda: bool(renderer.displayed))
        second = next(iter(renderer.displayed.values()))
        assert second.result.identity.jobId == job and second.result.status == 'COMPLETE'
        if fixtureName == 'twoImages':
            assert second.result.identity.resultKey == first.result.identity.resultKey
            assert not np.array_equal(second.images['original'], second.images['image'])
            assert second.images['image'] is second.images['overlay-alias']
            assert coordinator.preview.hub.conversions == 2
        else:
            assert first.result.identity.resultScopeId == 'a'
            assert second.result.identity.resultScopeId == 'b'
            assert first.result.identity.resultKey != second.result.identity.resultKey
            assert first.result.identity.invocationId != second.result.identity.invocationId
        assert len(runtime.jobRepository.all()) == 1
    finally:
        if window.pageCoordinator.preview.active():
            window.pageCoordinator.preview.closeAsync()
            waitFor(lambda: not window.pageCoordinator.preview.active())
        window.close()
        runtime.close()


@pytest.mark.parametrize('debugFirst', [False, True])
def testNormalAndIsolatedDebugShareOwnerInEitherOrder(qtApp, tmp_path, debugFirst):
    from emo_master.apps.designer.page_designer.preview import LocalBackend
    root = tmp_path / 'project'
    root.mkdir()
    document = sampleProject(root)
    document.workflows['main'].nodes[1].params['imagePath'] = str(root / 'input.png')
    saveProject(root, document.model_dump())
    runtime = RuntimeService(dbPath=tmp_path / 'runtime.sqlite3', workspaceRoot=tmp_path / 'jobs')
    client = RuntimeClient(runtime)
    backend = None
    try:
        assert client.loadProject(str(root)).ok
        if debugFirst:
            backend = LocalBackend(runtime)
            debug = backend.start(document, root)
            waitFor(lambda: runtime.jobRepository.get(debug).isTerminal)
        generation = client.prepareStart(True)
        reply = client.startJob(str(root), capturePresentation=True, startRequestId='normal-once',
                                expectedRuntimeInstanceId=generation)
        assert reply.ok, reply.message
        normal = reply.job_id
        if not debugFirst:
            backend = LocalBackend(runtime)
            debug = backend.start(document, root)
        owner = runtime._presentationOwner
        assert backend.service is owner
        assert client._presentationServer.presentation is owner
        waitFor(lambda: runtime.jobRepository.get(normal).isTerminal and runtime.jobRepository.get(debug).isTerminal)
        backend.close()
        assert runtime._presentationOwner is owner
        assert not owner.closed
        assert normal in owner.jobs
        assert debug not in owner.jobs
        assert client.listDisplayJobs(document.project.projectId)
        assert runtime.jobRepository.get(normal).status == 'COMPLETED', jobFailureDetails(runtime, normal)
        assert len(runtime.jobRepository.all()) == 2
    finally:
        if backend:
            backend.close()
        client.close()
        runtime.close()
