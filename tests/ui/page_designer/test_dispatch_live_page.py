"""Native Designer observes independent acquisition and detection results."""
from dataclasses import replace

import pytest

from PySide2.QtCore import QSettings, Qt
from PySide2.QtWidgets import QMessageBox

from emo_master.apps.designer.services.runtime_client import RuntimeClient
from emo_master.apps.designer.state.project_store import saveProject
from emo_master.apps.designer.ui.main_window import MainWindow
from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb
from emo_master.apps.runtime.grpc_server.service import RuntimeService
from emo_master.clients.runtime.dispatch_display import DispatchDisplaySession
from tests.runtime.dispatch_page_fixture import sharedPageProject
from tests.runtime.test_dispatch_queue_integration import gateConsumers
from tests.ui.page_designer.test_normal_run_viewing import waitFor


@pytest.mark.parametrize('snapshotPolicy', ['ALL', 'NONE'])
def testNormalRunEnablesSharedPageCapture(qtApp, tmp_path, monkeypatch, snapshotPolicy):
    first, rest = gateConsumers(monkeypatch)
    failures = []
    phase = 'running'
    def recordFailure(self, failure):
        failures.append({**failure, 'testPhase': phase})
    monkeypatch.setattr(MainWindow, '_showRunFailure', recordFailure)
    monkeypatch.setattr(QMessageBox, 'question', lambda *args, **kwargs: QMessageBox.Discard)
    document = sharedPageProject(tmp_path)
    path = tmp_path / 'normal-live.emoproj'
    saveProject(path, document.model_dump(mode='json'))
    runtime = RuntimeService(dbPath=tmp_path / 'runtime.db', workspaceRoot=tmp_path / 'jobs')
    client = RuntimeClient(runtime)
    window = MainWindow(client, settingsStore=QSettings(str(tmp_path / 'settings.ini'), QSettings.IniFormat))
    window.setAttribute(Qt.WA_DontShowOnScreen)
    preview = window.pageCoordinator.preview
    try:
        assert window.loadProjectDirectory(str(path), confirmed=True)
        waitFor(lambda: window.operatorCatalogController.state == 'ready')
        window.pageCoordinator.sync()
        draft = window.pageCoordinator.session.payload()
        checked = client.initializeProjectConfiguration(draft, tmp_path, 'check', validate=True, projectPath=path)
        client.initializeProjectConfiguration(draft, tmp_path, 'publish', runtimeInstanceId=checked['runtimeInstanceId'],
            projectPath=path)
        waitFor(lambda: client.getProjectWriteStatus('publish')['state'] in {'COMMITTED', 'FAILED'})
        assert client.getProjectWriteStatus('publish')['state'] == 'COMMITTED'
        monkeypatch.setattr(window.pageCoordinator, 'configureRuntime',
                            lambda **kwargs: pytest.fail('unchanged published project must run directly'))
        window.nextRunLegacySnapshotPolicy = snapshotPolicy
        # Exercise the user's Run entry, without manually enabling page capture.
        window.startJob()
        waitFor(lambda: window.currentJobId or window.runtimePanelState.jobStatus == 'FAILED')
        job = window.currentJobId
        assert job, {'lastMessage': window.runtimePanelState.lastMessage, 'failures': failures}
        assert client.displayAddress(), 'normal Run did not enable shared-page capture'
        group = runtime.deviceManager.dispatchGroups[job]
        consumer = group.slots['detection'].jobId
        metadata = {item.job_id: item for item in client.listDisplayJobs(document.project.projectId)}
        assert metadata[job].capture_enabled and set(metadata[job].source_ids) == {'live'}
        assert metadata[consumer].capture_enabled and set(metadata[consumer].source_ids) == {'image', 'count'}
        preview.watchCurrent()
        waitFor(lambda: preview.hub is not None or preview.error)
        assert preview.error is None
        assert isinstance(preview.session, DispatchDisplaySession)
        view = preview.observer
        waitFor(lambda: not view.widgets['consumer']['live'][1].image.isNull())
        assert view.widgets['consumer']['image'][1].image.isNull()
        assert group.slots['detection'].completedTotal == 0
        first.set()
        waitFor(lambda: not view.widgets['consumer']['image'][1].image.isNull())
        assert set(preview.selectedWorkflows) == {'main', 'consumer'}
        preview.stopViewing()
        waitFor(lambda: not preview.active())
        assert not runtime.jobRepository.get(job).isTerminal
        assert not runtime.jobRepository.get(consumer).isTerminal
        assert len(runtime.jobRepository.all()) == 2
        assert not failures, failures
    finally:
        first.set()
        rest.set()
        if preview.active():
            preview.closeAsync()
            waitFor(lambda: not preview.active())
        if window.isJobRunning:
            phase = 'stopping'
            window.stopJob()
            waitFor(lambda: not window.isJobRunning)
        window.close()
        client.close()
        runtime.close()
        if failures:
            import json
            (tmp_path / 'run-failures.json').write_text(json.dumps(failures, ensure_ascii=False, indent=2), encoding='utf-8')
    # Only failures delivered after our explicit Stop qualify as cleanup.
    # The device proxy either rejects a not-yet-sent capture, or retains UNKNOWN
    # for an interrupted receipt. Running/viewing failures still fail above.
    for failure in failures:
        assert failure.get('testPhase') == 'stopping' and failure.get('eventType') == 'node.failed', failure
        outcome = failure.get('payload', {}).get('diagnostics', {}).get('deviceOutcome', {}).get('outcome')
        assert ((failure.get('code') == 'E_DEVICE_OUTCOME_UNKNOWN' and outcome == 'UNKNOWN')
                or (failure.get('code') == 'E_DEVICE_CANCELLED' and outcome == 'NOT_EXECUTED'
                    and failure.get('message') == 'device proxy cancelled')), failure


@pytest.mark.parametrize('selection', ['producer', 'consumer'])
def testLeftUpdatesWhileDetectionIsBlocked(qtApp, tmp_path, monkeypatch, selection):
    first, rest = gateConsumers(monkeypatch)
    document = sharedPageProject(tmp_path)
    path = tmp_path / 'live.emoproj'
    saveProject(path, document.model_dump(mode='json'))
    runtime = RuntimeService(dbPath=tmp_path / 'runtime.db', workspaceRoot=tmp_path / 'jobs')
    client = RuntimeClient(runtime)
    window = MainWindow(client, settingsStore=QSettings(str(tmp_path / 'settings.ini'), QSettings.IniFormat))
    window.setAttribute(Qt.WA_DontShowOnScreen)
    preview = window.pageCoordinator.preview
    try:
        assert window.loadProjectDirectory(str(path), confirmed=True)
        assert window.pageCoordinator._canSync()
        assert runtime.LoadProject(pb.LoadProjectRequest(project_path=str(path)), None).ok
        client.prepareStart(capturePresentation=True)
        started = runtime.StartJob(pb.StartJobRequest(workflow_id='main', capture_presentation=True,
                                                      legacy_snapshot_policy='NONE'), None)
        assert started.ok, started.message
        group = runtime.deviceManager.dispatchGroups[started.job_id]
        consumer = group.slots['detection'].jobId
        preview.connect(client.displayAddress(), consumer if selection == 'consumer' else started.job_id)
        waitFor(lambda: preview.hub is not None or preview.error)
        assert preview.error is None
        assert isinstance(preview.session, DispatchDisplaySession), (
            window.pageCoordinator.session.document().presentation.model_dump(),
            [(j.job_id, j.workflow_id, j.run_group_id, j.source_job_id, j.target_slot_id)
             for j in client.listDisplayJobs(document.project.projectId)])
        view = preview.observer
        liveImage = view.widgets['consumer']['live'][1]
        def ordinal(scope):
            value = view.displayed.get(scope)
            return value.result.identity.resultOrdinal if value else 0
        def readyProducerScope(scope):
            return (scope is not None and scope.result.identity.jobId == started.job_id
                    and scope.result.status == 'COMPLETE' and not scope.failures
                    and 'live' in scope.images
                    and any(source.sourceId == 'live' and source.state == 'AVAILABLE'
                            for source in scope.result.sources))
        def producerImageReady():
            scope = view.displayed.get('acquisition')
            return (readyProducerScope(scope) and not liveImage.image.isNull()
                    and liveImage.key == scope.result.identity.resultKey)
        waitFor(lambda: ordinal('acquisition') >= 6 and producerImageReady())
        assert not liveImage.image.isNull()
        assert view.widgets['consumer']['image'][1].image.isNull()
        assert not ordinal('consumption') and group.slots['detection'].completedTotal == 0
        assert group.slots['detection'].skippedTotal > 0 and not group.waiting
        first.set()
        waitFor(lambda: ordinal('consumption') == 1 and not view.widgets['consumer']['image'][1].image.isNull())
        previous = ordinal('acquisition')
        right = view.displayed['consumption']
        waitFor(lambda: ordinal('acquisition') >= previous + 5)
        assert view.displayed['consumption'].result.identity == right.result.identity
        snapshot = preview.session.readSnapshot()
        assert snapshot.scopes['consumption'].images['image'] is right.images['image']
        pins = preview.session.pins()
        ticket = pins.acquire(right, snapshot.generation, sourceIds={'image'})
        waitFor(lambda: pins.read(ticket).state == 'PINNED')
        assert pins.read(ticket).scope.result.identity.jobId == consumer
        pins.release(ticket)
        waitFor(lambda: pins.bytesHeld() == 0)
        # Isolate the disconnect against one actual, decoded producer snapshot.
        # Live capture never waits for a busy image lane: a later ordinal alone
        # does not promise an AVAILABLE image and must not stand in for readiness.
        producer = preview.session.sessions[started.job_id]
        originalProducerRead = producer.readSnapshot
        healthy = []
        def rememberHealthyProducer():
            candidate = originalProducerRead()
            if candidate.connection == 'CONNECTED' and readyProducerScope(candidate.scopes.get('acquisition')):
                healthy.append(candidate)
                return True
            return False
        waitFor(rememberHealthyProducer)
        monkeypatch.setattr(producer, 'readSnapshot', lambda: healthy[0])
        preview.hub.tick()
        assert producerImageReady()
        producerKey, producerPixels = liveImage.key, liveImage.image.copy()
        producerIdentity = view.displayed['acquisition'].result.identity
        child = preview.session.sessions[consumer]
        originalRead = child.readSnapshot
        monkeypatch.setattr(child, 'readSnapshot', lambda: replace(originalRead(), connection='UNAVAILABLE', detail='test disconnect'))
        preview.hub.tick()
        assert view.lastView.scopeErrors == {'consumption': 'UNAVAILABLE: test disconnect'}
        assert view.widgets['consumer']['image'][1].image.isNull()
        assert producerImageReady()
        assert liveImage.key == producerKey and liveImage.image == producerPixels
        assert view.displayed['acquisition'].result.identity == producerIdentity
        monkeypatch.setattr(producer, 'readSnapshot', originalProducerRead)
        previous = ordinal('acquisition')
        waitFor(lambda: view.widgets['consumer']['image'][1].image.isNull())
        waitFor(lambda: ordinal('acquisition') >= previous + 2 and producerImageReady())
        assert not liveImage.image.isNull()
        monkeypatch.setattr(child, 'readSnapshot', originalRead)
        preview.closeAsync()
        waitFor(lambda: not preview.active())
        assert not runtime.jobRepository.get(started.job_id).isTerminal
        assert not runtime.jobRepository.get(consumer).isTerminal
        assert runtime.StopJob(pb.StopJobRequest(job_id=started.job_id), None).ok
        waitFor(lambda: not runtime.deviceManager.dispatchGroups)
        assert not runtime.deviceManager.frames.frames
    finally:
        first.set()
        rest.set()
        if preview.active():
            preview.closeAsync()
            waitFor(lambda: not preview.active())
        window.close()
        client.close()
        runtime.close()
