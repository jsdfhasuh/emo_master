"""Two synthetic normal runs exercise the real multi-job observation chooser.

Only the modal user decision is supplied by the test. Runtime jobs, display
sessions, shared windows, pins and retirement use their production paths.
"""
import faulthandler

from PySide2.QtCore import QCoreApplication, QEvent, Qt
from PySide2.QtTest import QTest
from PySide2.QtWidgets import QInputDialog, QMessageBox
from shiboken2 import isValid

from emo_master.apps.designer.services.runtime_client import RuntimeClient
from emo_master.apps.designer.state.project_store import saveProject
from emo_master.apps.designer.ui.main_window import MainWindow
from emo_master.apps.runtime.grpc_server.service import RuntimeService
from emo_master.core.presentation.models import Action
from examples.runtime_pages_p2 import sampleProject
from test_preview import waitFor
from tests.runtime.runtime_test_utils import jobFailureDetails
from tests.ui.page_designer.test_normal_run_viewing import displayedImagesReady


def testTwoCompletedNormalJobsChooseCurrentObserveFreezeResumeAndRetire(qtApp, tmp_path, monkeypatch):
    monkeypatch.delenv('EMO_PAGE_DESIGNER', raising=False)
    monkeypatch.setattr(QMessageBox, 'question', lambda *a, **k: QMessageBox.Discard)
    root = tmp_path / 'two-job-observation'
    root.mkdir()
    document = sampleProject(root)
    document.workflows['main'].nodes[1].params['imagePath'] = str(root / 'input.png')
    saveProject(root, document.model_dump())
    runtime = RuntimeService(dbPath=tmp_path / 'runtime.sqlite3', workspaceRoot=tmp_path / 'jobs')
    client = RuntimeClient(runtime)
    window = MainWindow(client)
    preview = window.pageCoordinator.preview
    jobs, choices, sessions = [], [], []
    lifecycleCalls = {'StartJob': 0, 'StopJob': 0}
    stage = 'setup'

    def mark(value):
        nonlocal stage
        stage = value
        print('TWO_JOB_OBSERVATION ' + value, flush=True)

    def wait(value, predicate):
        mark(value)
        # Retain the existing normal-run fixture's 25-second condition deadline.
        waitFor(predicate)

    originalStart, originalStop = runtime.StartJob, runtime.StopJob

    def start(request, context):
        lifecycleCalls['StartJob'] += 1
        return originalStart(request, context)

    def stop(request, context):
        lifecycleCalls['StopJob'] += 1
        return originalStop(request, context)

    monkeypatch.setattr(runtime, 'StartJob', start)
    monkeypatch.setattr(runtime, 'StopJob', stop)

    def chooseCurrent(parent, title, label, items, index, editable):
        # The first run has one job and must not invoke a chooser. The second
        # run reaches the actual _chooseJob branch, which would otherwise enter
        # a modal event loop even though both jobs already say COMPLETED.
        mark('second_chooser_entered')
        assert parent is window and not editable
        assert len(jobs) == len(items) == 2
        assert {item.rsplit(' · ', 1)[-1] for item in items} == {job[:8] for job in jobs}
        assert all('已完成' in item for item in items)
        assert items[index].rsplit(' · ', 1)[-1] == window.currentJobId[:8] == jobs[-1][:8]
        choices.append((tuple(items), index, window.currentJobId))
        assert lifecycleCalls == {'StartJob': 2, 'StopJob': 0}
        return items[index], True

    monkeypatch.setattr(QInputDialog, 'getItem', chooseCurrent)
    try:
        assert window.loadProjectDirectory(str(root))
        window.show()
        wait('catalog_ready', lambda: window.operatorCatalogController.state == 'ready')
        window.pageCoordinator.showPages()
        editor = window.pageCoordinator.editor
        assert not runtime.jobRepository.all()
        for number in (1, 2):
            prefix = f'job_{number}'
            previous = window.currentJobId
            mark(prefix + '_explicit_start')
            window.startJob()
            wait(prefix + '_accepted', lambda: window.currentJobId not in (None, previous))
            job = window.currentJobId
            jobs.append(job)
            wait(prefix + '_completed', lambda: not window.runtimeController._jobActive)
            assert runtime.jobRepository.get(job).status == 'COMPLETED', jobFailureDetails(runtime, job)
            assert len(runtime.jobRepository.all()) == number
            assert lifecycleCalls == {'StartJob': number, 'StopJob': 0}

            mark(prefix + '_watch_current')
            preview.watchCurrent()
            wait(prefix + '_attached', lambda: preview.hub is not None or preview.error is not None)
            assert preview.error is None
            assert preview.selectedJob.job_id == preview.session.jobId == job
            assert len(choices) == number - 1
            session, hub = preview.session, preview.hub
            sessions.append(session)
            wait(prefix + '_image', lambda: displayedImagesReady(preview.observer))
            result = preview.observer.displayed['root'].result
            assert result.identity.jobId == job and result.status == 'COMPLETE'
            assert next(source.valueJson for source in result.sources if source.sourceId == 'count') == '2'
            assert preview.backend is None and not runtime._presentationOwner.prepared

            preview.openObserver()
            observer = preview.observer
            assert observer is not None and observer.hub is hub
            assert hub.windows == {observer}
            wait(prefix + '_observer_image', lambda: displayedImagesReady(observer))
            assert observer.displayed['root'].result.identity.resultKey == result.identity.resultKey
            mark(prefix + '_freeze')
            observer.act(Action(type='freeze', resultScopeId='root'))
            assert observer.frozen is not None and editor.renderer.frozen is None
            pinStore = session.pins()
            wait(prefix + '_pinned', lambda: pinStore.read(observer.frozen).state == 'PINNED')
            assert observer.displayed['root'].result.identity.resultKey == result.identity.resultKey
            QTest.mouseClick(observer.resumeButton, Qt.LeftButton)
            assert observer.frozen is None
            wait(prefix + '_resumed', lambda: pinStore.bytesHeld() == 0
                 and runtime._presentationOwner.assets.stats()['lease_handles'] == 0)

            mark(prefix + '_observer_close')
            observer.close()
            wait(prefix + '_view_closed', lambda: not preview.active())
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
            assert not isValid(observer) and preview.observer is None
            assert not hub.windows and session.stop.is_set()
            assert window.currentJobId == job and not runtime._closed
            # Disconnect must retire another open popup as well as the session.
            preview.openObserver()
            observer = preview.observer
            assert observer is not None and observer.designExamples and observer.hub is None
            mark(prefix + '_disconnect')
            preview.closeAsync()
            wait(prefix + '_disconnected', lambda: not preview.active() or preview.error is not None)
            assert preview.error is None and not preview.active()
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
            assert not isValid(observer) and preview.observer is None
            assert not isValid(hub) or not hub.timer.isActive()
            assert not hub.windows and editor.renderer.hub is None
            assert all(not thread.is_alive() for thread in session.threads)
            assert not pinStore.thread.is_alive() and not preview.worker.is_alive()
            assert session.pending.empty() and not session._imageConsumers
            assert lifecycleCalls == {'StartJob': number, 'StopJob': 0}
            assert len(runtime.jobRepository.all()) == number
            assert runtime.jobRepository.get(job).status == 'COMPLETED'
            wait(prefix + '_worker_retired', lambda: window.runtimeController._worker is None
                 and not runtime.jobSupervisor.ownsJobResources(job))
            mark(prefix + '_retired')

        assert len(choices) == 1 and choices[0][2] == jobs[1]
        mark('designer_close')
        assert window.close()
        assert not window.isVisible() and window.runtimeController._closed and client._closed
        assert all(not thread.is_alive() for session in sessions for thread in session.threads)
        assert lifecycleCalls == {'StartJob': 2, 'StopJob': 0}
        mark('designer_closed')
    except BaseException:
        print('TWO_JOB_OBSERVATION FAILED_AT ' + stage, flush=True)
        faulthandler.dump_traceback(all_threads=True)
        raise
    finally:
        mark('cleanup')
        try:
            if preview.active():
                preview.closeAsync()
                waitFor(lambda: not preview.active() or preview.error is not None)
                assert preview.error is None
            window.close()
        finally:
            runtime.close()
        mark('all_owners_closed')
