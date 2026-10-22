"""New project UI entry stays local after a real published Job has retired."""
from copy import deepcopy

from emo_master.apps.designer.state.project_store import loadProject
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
