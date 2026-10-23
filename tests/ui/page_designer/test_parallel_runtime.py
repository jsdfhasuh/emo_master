"""Native UI + actual spawned While Jobs; synthetic offline inputs only."""
import json

import pytest
from PySide2.QtGui import QFontDatabase
from PySide2.QtWidgets import QMessageBox

from emo_master.apps.designer.services.runtime_client import RuntimeClient
from emo_master.apps.designer.ui.main_window import MainWindow
from emo_master.ui.run_targets_dialog import RunTargetsDialog
from emo_master.apps.runtime.grpc_server.service import RuntimeService
from examples.runtime_pages_p2 import pluginRoots
from tests.runtime.parallel_workflow_fixture import whileProject
from tests.ui.operator_view.test_view import waitFor


@pytest.mark.parametrize('cancelled', [False, True])
def testPaceObservationHoldReleasesAndCancels(tmp_path, cancelled):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from emo_master.apps.runtime.workflow.cancellation import CancellationRequested, CancellationToken
    from tests.runtime.parallel_workflow_fixture import HeldPace
    path = tmp_path / 'hold'
    path.touch()
    entered, token = Event(), CancellationToken()
    pace = HeldPace()
    pace.initOperator(None)
    def check():
        entered.set()
        token.raise_if_cancelled()
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(pace.executeNode, {}, {'holdPath': str(path), 'initialDelay': 0},
                             {'raiseIfCancellationRequested': check})
        try:
            assert entered.wait(2) and not future.done() and pace.index == 0
            if cancelled:
                token.cancel()
                with pytest.raises(CancellationRequested):
                    future.result(timeout=2)
                assert pace.index == 0
            else:
                path.unlink()
                assert future.result(timeout=2)['outputs'] == {'tick': True}
                assert pace.index == 1
        finally:
            token.cancel()
            path.unlink(missing_ok=True)


def testOneClickParallelStartSelectionStopRestartAndClose(qtApp, tmp_path, monkeypatch):
    monkeypatch.setattr(QMessageBox, 'question', lambda *a, **k: QMessageBox.Discard)
    QFontDatabase.addApplicationFont('C:/Windows/Fonts/msyh.ttc')
    root = tmp_path / 'engineering'
    document, roots = whileProject(root)
    from tests.runtime.production_fixture import saveDocument
    holdPath = tmp_path / 'hold-selected-pace'
    next(node for node in document.workflows['second'].nodes if node.nodeId == 'pace').params['holdPath'] = str(holdPath)
    document.runtime.startupWorkflowIds = roots
    saveDocument(root, document)
    plugins = pluginRoots(tmp_path)
    manifestPath = tmp_path / 'plugins/pace/manifest.json'
    manifest = json.loads(manifestPath.read_text(encoding='utf-8'))
    manifest['entry'] = 'tests.runtime.parallel_workflow_fixture:HeldPace'
    manifest['paramSchema']['properties']['holdPath'] = {'type': 'string'}
    manifestPath.write_text(json.dumps(manifest), encoding='utf-8')
    runtime = RuntimeService(dbPath=tmp_path / 'data/runtime.sqlite3', workspaceRoot=tmp_path / 'data/jobs',
        pluginRootPaths=plugins)
    window = MainWindow(RuntimeClient(runtime))
    try:
        assert window.loadProjectDirectory(str(root))
        window.show()
        waitFor(lambda: window.operatorCatalogController.state == 'ready')
        window.activateWorkflow('second')
        window.autoLayoutNodes()
        assert not window.runTargetIds  # No manual selection after reopening.
        assert window.runtimeController.targets() == roots
        sync = window.runtimeController.syncRuntimeProjectBeforeRun
        calls = []
        def synchronize():
            calls.append(True)
            return sync()
        window.runtimeController.syncRuntimeProjectBeforeRun = synchronize
        window._menuActions['开始运行'].trigger()
        runs = window.runtimeController.runs
        waitFor(lambda: all(key in runs and runs[key].jobId for key in roots))
        first, second = (runs[key].jobId for key in roots)
        waitFor(lambda: all(runtime.jobRepository.get(job).status == 'RUNNING' for job in (first, second)))
        waitFor(lambda: all(runs[key].state.nodeInspection.get(body, 'count', runs[key].jobId)
                           for key, body in zip(roots, ('main', 'second'))))
        assert all(runs[key].state.jobStatus == 'RUNNING' for key in roots)
        # Real worker events drive both roots and their called workflows,
        # independent of which Job is selected in the inspector.
        waitFor(lambda: all(
            runs[key].state.workflowExecution.statuses(runs[key].state.jobStatus).get(key) == 'WAITING_CHILD'
            and runs[key].state.workflowExecution.statuses(runs[key].state.jobStatus).get(body) == 'RUNNING'
            for key, body in zip(roots, ('main', 'second'))))
        assert len(calls) == 1
        assert len(runtime.jobRepository.all()) == 2
        assert window.currentJobId == first
        window.runningWorkflowCombo.setCurrentIndex(window.runningWorkflowCombo.findData(roots[1]))
        assert window.currentJobId == second
        assert window.runtimePanelState is runs[roots[1]].state
        assert window.nodeResultCoordinator.history.current.jobId == second
        assert runs[roots[0]].state.workflowExecution.statuses('RUNNING').get(roots[0]) in {'RUNNING', 'WAITING_CHILD'}
        window.activateWorkflow('second')
        window.navigateToNodeFromSidebar('count')
        for width in (1060, 1280):
            window.resize(width, 800)
            qtApp.processEvents()
            assert window.runTargetsButton.isVisible()
            assert window.runningWorkflowCombo.isVisible()
            assert window.stopAllJobsButton.isVisible() and window.stopAllJobsButton.isEnabled()
            assert window.pageCoordinator.chrome.menus['运行'].menuAction().isVisible()
            assert window._menuActions['停止运行'].isEnabled()
            assert window.flowModel.selectedNodeId == 'count'
            window.grab().save(str(tmp_path / f'designer-parallel-{width}.png'))
        window.resize(1600, 800)
        previousRun = window._nodeRuntimeState.get('pace', {}).get('nodeRunId')
        holdPath.touch()
        try:
            window.navigateToNodeFromSidebar('pace')
            waitFor(lambda: window._nodeRuntimeState.get('pace', {}).get('status') == 'RUNNING'
                and window._nodeRuntimeState['pace'].get('nodeRunId') != previousRun)
            assert window._nodeRuntimeState['pace']['jobId'] == second
            assert window._nodeRuntimeState['pace']['workflowId'] == 'second'
            qtApp.processEvents()
            assert window.grab().save(str(tmp_path / 'designer-live-position.png'))
            assert window.workflowTabs.grab().save(str(tmp_path / 'designer-running-tabs.png'))
        finally:
            holdPath.unlink(missing_ok=True)
        window.pageCoordinator.showPages()
        assert window.pageCoordinator.pageActive()
        window._menuActions['停止运行'].trigger()
        waitFor(lambda: not window.runtimeController.busy(runs[roots[1]]))
        assert runtime.jobRepository.get(first).status == 'RUNNING'
        assert all(status not in {'RUNNING', 'WAITING_CHILD'} for status in
                   runs[roots[1]].state.workflowExecution.statuses(runs[roots[1]].state.jobStatus).values())
        assert window.isJobRunning
        assert window.startButton.isEnabled()
        window._menuActions['开始运行'].trigger()
        waitFor(lambda: runs[roots[1]].jobId not in (None, second))
        assert runs[roots[0]].jobId == first and len(calls) == 1
        assert len(runtime.jobRepository.all()) == 3
        assert window.nodeResultCoordinator.workflowContexts[roots[0]]['history'].current.jobId == first
        assert window._menuActions['停止全部'].isEnabled()
        window._menuActions['停止全部'].trigger()
        try:
            waitFor(lambda: not window.isJobRunning)
        except AssertionError:
            pytest.fail(str({key: dict(job=run.jobId, status=run.state.jobStatus,
                busy=window.runtimeController.busy(run),
                runtime=runtime.jobRepository.get(run.jobId).status,
                worker=run.controller._worker is not None, stopWorker=run.controller._stopWorker is not None)
                for key, run in runs.items()}))
    finally:
        window.close()
        waitFor(lambda: window.runtimeController._closed)
        qtApp.processEvents()
        assert runtime.jobSupervisor.activeCount() == 0
        runtime.close()  # RuntimeClient borrows an injected service; the test owns it.
    assert runtime._closed


def testRunTargetDialogUnlimitedAndNonPersistentSelection(qtApp, tmp_path):
    QFontDatabase.addApplicationFont('C:/Windows/Fonts/msyh.ttc')
    document, roots = whileProject(tmp_path / 'engineering', count=3)
    before = document.model_dump()
    dialog = RunTargetsDialog(document.workflows, roots, 4)
    dialog.show()
    try:
        assert dialog.selectedWorkflowIds() == roots
        dialog.unlimited.setChecked(True)
        assert dialog.maximum() is None and not dialog.limit.isEnabled()
        qtApp.processEvents()
        dialog.grab().save(str(tmp_path / 'run-targets-dialog.png'))
        assert document.model_dump() == before
    finally:
        dialog.close()


def testConcurrencyUsesExistingDirtyUndoSaveAndResetReleasesOtherContexts(qtApp, tmp_path, monkeypatch):
    from emo_master.core.project.models import ProjectDocument
    monkeypatch.setattr(QMessageBox, 'question', lambda *a, **k: QMessageBox.Discard)
    root = tmp_path / 'engineering'
    whileProject(root)
    runtime = RuntimeService(dbPath=tmp_path / 'data/runtime.sqlite3', pluginRootPaths=pluginRoots(tmp_path))
    window = MainWindow(RuntimeClient(runtime))
    try:
        assert window.loadProjectDirectory(str(root))
        session = window.pageCoordinator.session
        assert not session.dirty
        window._setConcurrencyLimit(None)
        assert session.dirty and session.document().runtime.maxConcurrentJobs is None
        window.pageCoordinator.history()
        assert window.workflowStore.runtime['maxConcurrentJobs'] == 4 and not session.dirty
        window.pageCoordinator.history(True)
        assert window.workflowStore.runtime['maxConcurrentJobs'] is None
        assert window.saveProjectToDirectory(str(root))
        assert ProjectDocument.model_validate_json((root / 'project.json').read_text(encoding='utf-8')).runtime.maxConcurrentJobs is None
        assert not session.dirty
        results = window.nodeResultCoordinator
        results.selectWorkflow('main-run')
        old = results.images
        results.selectWorkflow('second-run')
        retained = results.images
        results.reset()
        assert old._closed and not retained._closed and not results.workflowContexts
        assert results.selectedWorkflow is None
        signals = []
        results.imageReady.connect(signals.append)
        results.selectWorkflow('new-root')
        retained.notify(9)
        assert signals == [9]
    finally:
        window.close()
        runtime.close()


@pytest.mark.parametrize('limit,count', [(4, 4), (None, 3)])
def testDesignerMoreThanTwoJobsAlsoOwnIndependentInspectionSessions(qtApp, tmp_path, monkeypatch, limit, count):
    monkeypatch.setattr(QMessageBox, 'question', lambda *a, **k: QMessageBox.Discard)
    root = tmp_path / 'engineering'
    _, roots = whileProject(root, count=count, limit=limit)
    runtime = RuntimeService(dbPath=tmp_path / 'data/runtime.sqlite3', pluginRootPaths=pluginRoots(tmp_path))
    window = MainWindow(RuntimeClient(runtime))
    try:
        assert window.loadProjectDirectory(str(root))
        window.show()
        waitFor(lambda: window.operatorCatalogController.state == 'ready')
        window.runTargetIds = roots
        window.startJob()
        runs = window.runtimeController.runs
        waitFor(lambda: all(runs[key].jobId for key in roots)
            or any(runs[key].state.jobStatus == 'FAILED' for key in roots), seconds=60)
        assert all(runs[key].jobId for key in roots), {key: runs[key].state.lastMessage for key in roots}
        jobs = [runs[key].jobId for key in roots]
        waitFor(lambda: all(runtime.jobRepository.get(job).status == 'RUNNING' for job in jobs), seconds=60)
        assert len(runtime.runInspectionStore.sessions) == count
        assert window.runningWorkflowCombo.count() == count
        for key in roots:
            window.runtimeController.select(key)
            assert window.nodeResultCoordinator.images.sessionId
            assert window.currentJobId == runs[key].jobId
        window.stopAllJobs()
        waitFor(lambda: not window.isJobRunning, seconds=60)
    finally:
        window.close()
        runtime.close()
