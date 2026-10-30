"""New project UI entry stays local after a real published Job has retired."""
from copy import deepcopy
import time

import pytest
from PySide2.QtWidgets import QApplication

from emo_master.apps.designer.services.runtime_client import RuntimeClientError
from emo_master.apps.designer.state.project_store import loadProject, saveProject
from emo_master.core.project.configuration import projectChanges
from tests.designer.test_startup_project_entry import EntryDialogStub
from tests.runtime.test_initial_project_configuration import imageProject, initialRuntime, waitForWrite  # noqa: F401
from tests.runtime.test_tcp_device_closure import runtime, thirdParty  # noqa: F401
from tests.runtime.test_tcp_device_integration import definition, freePort, run, waitWrite


def testMenuNewAndReopenAfterOnlineJob(runtime, ownedDesignerWindow, tmp_path, monkeypatch):  # noqa: F811
    service, client = runtime
    window = ownedDesignerWindow(client)
    source = tmp_path / 'A.emoproj'
    monkeypatch.setattr(window, '_chooseProjectFile', lambda _: str(source))
    window.newProjectAction()
    payload = window.pageCoordinator.session.payload()
    server = definition(service.pluginScanResult.deviceTypes['device.tcp.server'], port=freePort(),
                        channels=[dict(channelId='local', name='Local', allowedIps=['127.0.0.1'])])
    server['policies']['connect'] = 'on_project_open'
    payload['devices']['definitions']['server'] = server
    client.initializeProjectConfiguration(payload, tmp_path, 'new-project-online', runtimeInstanceId=service.runtimeInstanceId)
    assert waitWrite(service, client, 'new-project-online')['state'] == 'COMMITTED'
    run(service, client, 'main')
    snapshot = deepcopy(client.getOnlineProjectSnapshot(''))
    manager = service.deviceManager
    driver = manager.drivers['server']
    lifecycle = manager.lifecycles['server']
    assert lifecycle.connected
    target = tmp_path / 'B.emoproj'
    monkeypatch.setattr(window, '_chooseProjectFile', lambda _: str(target))
    window.newProjectAction()
    assert window.projectController.currentProjectFile == target
    assert window.workflowStore.project['projectId'] != payload['project']['projectId']
    assert window.saveProjectToDirectory(str(target))
    assert window.loadProjectDirectory(str(target), confirmed=True)
    assert loadProject(target)['project']['projectId'] == window.workflowStore.project['projectId']
    assert client.getOnlineProjectSnapshot('') == snapshot
    assert manager.drivers['server'] is driver and manager.lifecycles['server'] is lifecycle
    assert lifecycle.connected and manager.definitions['server'].policies['connect'] == 'on_project_open'


def testStartupAliasFirstPublicationUsesActualRunAction(initialRuntime, ownedDesignerWindow, tmp_path, monkeypatch):  # noqa: F811
    service, client = initialRuntime
    realRoot = tmp_path / 'engineering'
    realRoot.mkdir()
    alias = tmp_path / 'short-path'
    try:
        alias.symlink_to(realRoot, target_is_directory=True)
    except (OSError, NotImplementedError) as error:
        pytest.skip(f'directory symlinks are unavailable: {error}')
    source = alias / 'inspection.emoproj'
    window = ownedDesignerWindow(client)
    monkeypatch.setattr(window, '_chooseProjectFile', lambda _: str(source))
    monkeypatch.setattr(window, '_projectEntryDialogFactory', lambda: EntryDialogStub('new_blank', ''))
    assert window.showStartupProjectEntry()
    assert window.loadedProjectPath == str(source)
    payload = window.pageCoordinator.session.payload()
    assert payload['schemaVersion'] == '2.8'
    original = source.read_bytes()
    client.initializeProjectConfiguration(payload, alias, 'alias-first-publication',
        runtimeInstanceId=service.runtimeInstanceId, projectPath=source)
    assert waitForWrite(service, client, 'alias-first-publication')['state'] == 'COMMITTED'
    assert service.loadedProjectFile == str(source.resolve())
    assert not service.jobRepository.all()
    window.show()
    QApplication.processEvents()

    action = window.designerActions.actions['run']
    assert 'F5' in [shortcut.toString() for shortcut in action.shortcuts()]
    for expectedCount in (1, 2):
        assert action.isEnabled()
        action.trigger()
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            QApplication.processEvents()
            if not window.isJobRunning and window.runtimePanelState.jobStatus in {'COMPLETED', 'FAILED'}:
                break
            time.sleep(.01)
        records = service.jobRepository.all()
        assert len(records) == expectedCount, window.runtimePanelState.lastMessage
        assert all(record.status == 'COMPLETED' for record in records)
        assert window.currentJobId in {record.jobId for record in records}
        assert window.runtimePanelState.jobStatus == 'COMPLETED'

    # Canonical paths identify the same copy; UUID matching still works, but
    # identical bytes at another path must not become an accepted copy alias.
    assert client.validateProject(payload['project']['projectId']).ok
    assert client.validateProject(str(source.resolve())).ok
    assert client.validateProject(str(source)).ok
    assert client.listGlobalVariables(str(source)) == []
    copied = realRoot / 'other-copy.emoproj'
    copied.write_bytes(original)
    for wrongReference in (str(copied), 'different-project-id'):
        assert not client.validateProject(wrongReference).ok
        with pytest.raises(RuntimeClientError, match='E_PROJECT_NOT_LOADED'):
            client.inspectionSession('open', wrongReference)
    assert source.read_bytes() == original


def testSameWindowReplacementRunsCommittedOnlineCopy(initialRuntime, ownedDesignerWindow, tmp_path, monkeypatch):  # noqa: F811
    service, client = initialRuntime
    window = ownedDesignerWindow(client)
    sources = [tmp_path / 'A.emoproj', tmp_path / 'B.emoproj']
    window.show()
    deadline = time.monotonic() + 10
    while window.operatorCatalogController.state != 'ready' and time.monotonic() < deadline:
        QApplication.processEvents()
        time.sleep(.01)
    assert window.operatorCatalogController.state == 'ready'
    for index, source in enumerate(sources):
        monkeypatch.setattr(window, '_chooseProjectFile', lambda _, source=source: str(source))
        window.newProjectAction()
        imagePayload = imageProject(tmp_path)
        imagePayload['project'] = window.pageCoordinator.session.payload()['project']
        imagePayload['workflows']['main']['outputs'] = {}
        imagePayload['workflows']['main']['edges'] = imagePayload['workflows']['main']['edges'][:1]
        saveProject(source, imagePayload)
        assert window.loadProjectDirectory(str(source), confirmed=True)
        payload = window.pageCoordinator.session.payload()
        original = source.read_bytes()
        requestId = f'publish-{index}'
        if index == 0:
            client.initializeProjectConfiguration(payload, tmp_path, requestId,
                runtimeInstanceId=service.runtimeInstanceId, projectPath=source)
        else:
            snapshot = client.getOnlineProjectSnapshot('')
            assert snapshot['payload']['project']['projectId'] != payload['project']['projectId']
            # Opening B must not silently run A or bypass the publication choice.
            with monkeypatch.context() as cancelled:
                choices = []
                cancelled.setattr(window.pageCoordinator, 'configureRuntime',
                    lambda **kwargs: choices.append(kwargs) or 0)
                window.designerActions.actions['run'].trigger()
                assert choices == [dict(allowRun=True)]
                assert len(service.jobRepository.all()) == 1
                assert client.getOnlineProjectSnapshot('') == snapshot
            client.uploadProjectResources(snapshot, payload, tmp_path)
            client.writeProjectConfiguration(snapshot, payload,
                [change['path'] for change in projectChanges(snapshot['payload'], payload)], requestId,
                replacementPolicy='stopAffected')
        receipt = waitForWrite(service, client, requestId)
        assert receipt['state'] == 'COMMITTED', receipt
        assert receipt['documentRevision'] == index + 1
        assert window.loadedProjectPath == str(source)
        if index:
            assert service.loadedProjectFile != str(source)
            assert 'online-projects' in service.loadedProjectFile
            assert not client.validateProject(str(source)).ok

        QApplication.processEvents()
        window.navigateToNodeFromSidebar('load')
        action = window.designerActions.actions['run']
        assert action.isEnabled()
        action.trigger()
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            QApplication.processEvents()
            if not window.isJobRunning and window.runtimePanelState.jobStatus in {'COMPLETED', 'FAILED'}:
                break
            time.sleep(.01)
        records = service.jobRepository.all()
        assert len(records) == index + 1, window.runtimePanelState.lastMessage
        record = service.jobRepository.get(window.currentJobId)
        assert record is not None and record.status == 'COMPLETED'
        assert record.projectId == payload['project']['projectId']
        assert window.runtimePanelState.jobStatus == 'COMPLETED'
        coordinator = window.nodeResultCoordinator
        deadline = time.monotonic() + 10
        while coordinator.imagePacket is None and time.monotonic() < deadline:
            QApplication.processEvents()
            time.sleep(.01)
        assert coordinator.imagePacket is not None, dict(
            selected=window.flowModel.selectedNodeId, ports=coordinator.panel.ports.count(),
            image=coordinator.panel.image.text(), message=coordinator.images.message)
        assert coordinator.imagePacket['source'].originJobId == record.jobId
        assert coordinator.image.pixelColor(0, 0).red() == 120
        assert coordinator.images.projectId == service.loadedProjectFile
        assert coordinator.images.ensureSession(coordinator.images.projectId) == coordinator.images.sessionId
        with monkeypatch.context() as validation:
            messages = []
            validation.setattr(window, 'appendRuntimeLog', lambda *entry: messages.append(entry))
            window.validateProject()
            assert messages == [('INFO', '项目校验通过')]
        assert source.read_bytes() == original
    assert window.projectController.currentProjectFile == sources[1]
    assert len({record.projectId for record in service.jobRepository.all()}) == 2
