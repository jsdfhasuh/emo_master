"""New project UI entry stays local after a real published Job has retired."""
from copy import deepcopy
import time

import pytest
from PySide2.QtWidgets import QApplication

from emo_master.apps.designer.services.runtime_client import RuntimeClientError
from emo_master.apps.designer.state.project_store import loadProject
from tests.designer.test_startup_project_entry import EntryDialogStub
from tests.runtime.test_initial_project_configuration import initialRuntime, waitForWrite  # noqa: F401
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
    assert payload['schemaVersion'] == '2.7'
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
