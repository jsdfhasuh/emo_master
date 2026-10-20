"""Native Qt command activation with temporary projects and isolated Runtime."""
from copy import deepcopy
from types import SimpleNamespace
from threading import Event

import pytest
from PySide2.QtCore import Qt, QSettings
from PySide2.QtWidgets import QDialog, QMessageBox

from emo_master.apps.designer.services.runtime_client import RuntimeClientError
from emo_master.apps.designer.state.project_store import saveProject
from emo_master.apps.designer.ui.project_configuration import ProjectConfigurationDialog
from emo_master.apps.designer.ui.resource_bindings import ResourceBindingsDialog
from tests.designer.test_device_workspace import waitUntil
from tests.designer.runtime_feedback_fixture import messages  # noqa: F401
from tests.designer.runtime_state_fixture import setRunning
from tests.designer.test_designer_shortcuts import press
from tests.runtime.test_initial_project_configuration import initialRuntime, imageProject, waitForWrite  # noqa: F401
from tests.runtime.test_device_runtime import simulatedProject
from tests.ui.operator_view.test_view import waitFor


def menuDialog(window, monkeypatch):
    dialogs = []
    def show(dialog):
        dialogs.append(dialog)
        dialog.show()
        return QDialog.Rejected
    monkeypatch.setattr(ProjectConfigurationDialog, 'exec_', show)
    window.pageCoordinator.chrome.writeRuntimeAction.trigger()
    assert len(dialogs) == 1
    dialog = dialogs[0]
    assert dialog.parentWidget() is window
    waitUntil(lambda: dialog.pending is None)
    return dialog


@pytest.mark.parametrize('confirm', [False, True])
def testMenuFirstWriteUsesUnsavedDraftAndDoesNotSaveOrStart(
        ownedDesignerWindow, initialRuntime, tmp_path, monkeypatch, confirm):  # noqa: F811
    service, client = initialRuntime
    root = tmp_path / 'engineering'
    payload = imageProject(root)
    path = root / 'original.emoproj'
    saveProject(path, payload)
    disk = path.read_bytes()
    window = ownedDesignerWindow(client, settingsStore=QSettings(str(tmp_path / 'settings.ini'), QSettings.IniFormat))
    assert window.loadProjectDirectory(str(path), confirmed=True)
    window.flowModel.nodes['blur'].params['kernelSize'] = 5
    window.pageCoordinator.sync()
    before = window.pageCoordinator.session.payload()
    undo = deepcopy(window.pageCoordinator.session._undo)
    dialog = menuDialog(window, monkeypatch)
    try:
        assert dialog.initial and dialog.writeButton.isEnabled() and dialog.validateButton.isEnabled()
        assert not dialog.readButton.isEnabled()
        assert '尚未加载工程' in dialog.status.text()
        dialog.validateButton.click()
        waitUntil(lambda: dialog.pending is None)
        assert dialog.initialValidated and service.loadedDocument is None
        monkeypatch.setattr(QMessageBox, 'question', lambda *_: QMessageBox.Yes if confirm else QMessageBox.No)
        dialog.writeButton.click()
        waitUntil(lambda: dialog.pending is None)
        if confirm:
            waitUntil(lambda: dialog.pending is None and not dialog.writePending and not dialog.initial)
            online = client.getOnlineProjectSnapshot()['payload']
            assert next(n for n in online['workflows']['main']['nodes'] if n['nodeId'] == 'blur')['params']['kernelSize'] == 5
            assert not dialog.initial and '在线文档' in window.pageCoordinator.chrome.runtimeStatus.text()
            assert service.loadedProjectFile == str(path.resolve())
        else:
            assert service.loadedDocument is None and not dialog.requestId
        assert not service.jobRepository.all()
        assert path.read_bytes() == disk
        assert window.pageCoordinator.session.payload() == before
        assert window.pageCoordinator.session._undo == undo and window.pageCoordinator.session.dirty
    finally:
        dialog.reject()


def testLostFirstWriteReplyQueriesTheSameReceiptWithoutReplaying(
        ownedDesignerWindow, initialRuntime, tmp_path, monkeypatch, messages):  # noqa: F811
    service, client = initialRuntime
    root = tmp_path / 'engineering'
    path = root / 'original.emoproj'
    saveProject(path, imageProject(root))
    window = ownedDesignerWindow(client)
    assert window.loadProjectDirectory(str(path), confirmed=True)
    original = client.initializeProjectConfiguration
    writes = []
    def lost(*args, **kwargs):
        result = original(*args, **kwargs)
        if not kwargs.get('validate'):
            assert waitForWrite(service, client, args[2])['state'] == 'COMMITTED'
            writes.append(args[2])
            raise RuntimeClientError('UNAVAILABLE', 'simulated lost reply after commit')
        return result
    monkeypatch.setattr(client, 'initializeProjectConfiguration', lost)
    monkeypatch.setattr(QMessageBox, 'question', lambda *_: QMessageBox.Yes)
    dialog = menuDialog(window, monkeypatch)
    try:
        dialog.writeButton.click()
        waitUntil(lambda: dialog.pending is None)
        assert dialog.writePending and dialog.queryButton.isEnabled() and len(writes) == 1
        assert len(messages) == 1 and '结果尚未确认' in messages[0]['text']
        assert not dialog.writeButton.isEnabled()
        dialog.queryButton.click()
        waitUntil(lambda: dialog.pending is None)
        assert not dialog.writePending and not dialog.initial
        assert writes == [dialog.requestId] and not service.jobRepository.all()
    finally:
        dialog.reject()


def testFirstWriteDialogCanQueryAndCancelDuringPreparation(
        ownedDesignerWindow, initialRuntime, tmp_path, monkeypatch):  # noqa: F811
    from emo_master.apps.runtime.project_config import resources
    service, client = initialRuntime
    root = tmp_path / 'engineering'
    path = root / 'original.emoproj'
    saveProject(path, imageProject(root))
    window = ownedDesignerWindow(client)
    assert window.loadProjectDirectory(str(path), confirmed=True)
    draft, disk = window.pageCoordinator.session.payload(), path.read_bytes()
    entered, release = Event(), Event()
    prepare = resources.prepareResources
    def slowPrepare(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return prepare(*args, **kwargs)
    monkeypatch.setattr(resources, 'prepareResources', slowPrepare)
    monkeypatch.setattr(QMessageBox, 'question', lambda *_: QMessageBox.Yes)
    dialog = menuDialog(window, monkeypatch)
    try:
        dialog.writeButton.click()
        waitUntil(lambda: dialog.pending is None and entered.is_set())
        requestId = dialog.requestId
        assert dialog.writePending and dialog.queryButton.isEnabled() and dialog.cancelButton.isEnabled()
        dialog.queryButton.click()
        waitUntil(lambda: dialog.pending is None)
        assert dialog.writePending and dialog.requestId == requestId
        dialog.cancelButton.click()
        waitUntil(lambda: service.projectConfiguration.requests[requestId]['cancelled'].is_set())
        release.set()
        waitUntil(lambda: dialog.pending is None and not dialog.writePending)
        assert dialog.initial and '已取消' in dialog.status.text() and dialog.writeButton.isEnabled()
        assert waitForWrite(service, client, requestId)['state'] == 'CANCELLED'
        assert service.loadedDocument is None and not service.jobRepository.all()
        assert window.pageCoordinator.session.payload() == draft and path.read_bytes() == disk
    finally:
        release.set()
        dialog.reject()


def testMenusOwnAllCommandsAndPageModeKeepsRunAndStop(ownedDesignerWindow, monkeypatch):
    window = ownedDesignerWindow(SimpleNamespace(listOperators=lambda: []))
    try:
        c = window.pageCoordinator
        chrome = c.chrome
        assert [a.text() for a in window.menuBar().actions()] == ['文件', '运行', '编辑', '视图', 'Runtime', '设备管理']
        assert chrome.undo in chrome.menus['编辑'].actions()
        assert chrome.redo in chrome.menus['编辑'].actions()
        assert window._toolbarActions['自动布局'].text() == '整理流程'
        assert chrome.runtimeConnectionAction not in chrome.menus['文件'].actions()
        assert chrome.publishPages in chrome.menus['Runtime'].actions()
        assert all(a.text() != '图片测试' for menu in chrome.menus.values() for a in menu.actions())
        assert len(window.mainToolbar.actions()) == 1
        calls = []
        monkeypatch.setattr(window.runtimeController, 'startJob', lambda: calls.append('start'))
        monkeypatch.setattr(window.runtimeController, 'stopJob', lambda: calls.append('stop'))
        window.loadedProjectPath = 'synthetic-target'
        with monkeypatch.context() as patch:
            patch.setattr(QMessageBox, 'question', lambda *_: QMessageBox.Yes)
            c.showPages()
        window.updateToolbarState()
        assert chrome.menus['运行'].menuAction().isVisible()
        window._menuActions['开始运行'].trigger()
        assert calls == ['start']
        press(window, Qt.Key_F5)
        assert calls == ['start', 'start']
        setRunning(window, True)
        window.updateToolbarState()
        assert window._menuActions['停止运行'].isEnabled()
        window._menuActions['停止运行'].trigger()
        assert calls[-1] == 'stop'
        press(window, Qt.Key_F6)
        assert calls == ['start', 'start', 'stop', 'stop']
    finally:
        setRunning(window, False)


def testMonitorIsReusableReadOnlyAndIndependentOfInvalidDraft(ownedDesignerWindow, tmp_path, monkeypatch):
    reads = []
    def unloaded(*args):
        reads.append(args)
        raise RuntimeClientError('E_CONFIG_NOT_LOADED', 'no configuration')
    window = ownedDesignerWindow(SimpleNamespace(listOperators=lambda: [], getOnlineGraphSnapshot=unloaded))
    c = window.pageCoordinator
    with monkeypatch.context() as patch:
        patch.setattr(QMessageBox, 'question', lambda *_: QMessageBox.Yes)
        c.showPages()
    before, active = c.session.payload(), c.stack.currentWidget()
    undo = deepcopy(c.session._undo)
    with monkeypatch.context() as patch:
        patch.setattr(c, '_canSync', lambda: pytest.fail('monitor must not validate pending editor inputs'))
        c.chrome.monitorAction.trigger()
        first = c.monitor
        waitUntil(lambda: first.pending is None)
        assert first.isWindow() and not first.isModal() and c.stack.currentWidget() is active
        assert '尚未加载工程' in first.status.text()
        assert not first.stopButton.isVisible() and not first.stopAllButton.isVisible()
        c.chrome.monitorAction.trigger()
        assert c.monitor is first and len(reads) == 1
        first.close()
        assert c.monitor is None and not first.timer.isActive()
        c.chrome.monitorAction.trigger()
        second = c.monitor
        waitUntil(lambda: second.pending is None)
        assert second is not first and len(reads) == 2
        assert c.session.payload() == before and c.session._undo == undo
        assert c.stack.currentWidget() is active
        second.close()


def testUnreachableRuntimeOffersRetryNotFirstWrite(ownedDesignerWindow, messages):  # noqa: F811
    def unreachable(_project):
        raise RuntimeClientError('UNAVAILABLE', 'isolated unavailable Runtime')
    window = ownedDesignerWindow(SimpleNamespace(listOperators=lambda: [], getOnlineProjectSnapshot=unreachable))
    dialog = ProjectConfigurationDialog(window.pageCoordinator, window)
    try:
        waitUntil(lambda: dialog.pending is None)
        assert not dialog.initial and not dialog.writeButton.isEnabled()
        assert dialog.refreshButton.isEnabled() and 'unavailable' in dialog.status.text()
        assert len(messages) == 1 and 'UNAVAILABLE' in messages[0]['detail']
    finally:
        dialog.reject()


def testEmptyRuntimeDeviceManagerKeepsNormalDraftEditing(ownedDesignerWindow, initialRuntime, tmp_path):  # noqa: F811
    service, client = initialRuntime
    path = tmp_path / 'devices.emoproj'
    saveProject(path, simulatedProject())
    disk = path.read_bytes()
    window = ownedDesignerWindow(client)
    assert window.loadProjectDirectory(str(path), confirmed=True)
    c = window.pageCoordinator
    c.chrome.deviceAction.trigger()
    dialog = c.devices
    waitFor(lambda: dialog.pending is None and bool(dialog.stateNotice))
    assert '当前工程未下发' in dialog.status.text() and not dialog.stateError
    dialog.table.setCurrentCell(0, 0)
    dialog.name.setText('未下发时仍可编辑')
    dialog.buttons['apply'].click()
    assert c.session.workflows.devices['definitions']['camera']['name'] == '未下发时仍可编辑'
    dialog.buttons['refresh'].click()
    waitFor(lambda: dialog.pending is None)
    assert '未下发' in dialog.status.text()
    assert not dialog.buttons['connect'].isEnabled()
    assert 'save' not in dialog.buttons and 'compare' not in dialog.buttons
    dialog.closeButton.click()
    assert not dialog.isVisible() and c.session.dirty
    assert path.read_bytes() == disk
    assert service.loadedDocument is None and not service.deviceManager.drivers and not service.jobRepository.all()


def testResourcesAndPagePublicationDoNotConstructDeviceManager(
        ownedDesignerWindow, initialRuntime, tmp_path, monkeypatch):  # noqa: F811
    service, client = initialRuntime
    root = tmp_path / 'engineering'
    payload = imageProject(root)
    path = root / 'resources.emoproj'
    saveProject(path, payload)
    window = ownedDesignerWindow(client)
    assert window.loadProjectDirectory(str(path), confirmed=True)
    c = window.pageCoordinator
    waitFor(lambda: window.operatorCatalogController.state == 'ready')
    resources = []
    monkeypatch.setattr(ResourceBindingsDialog, 'exec_', lambda dialog: resources.append(dialog) or QDialog.Rejected)
    c.chrome.bindingsAction.trigger()
    assert len(resources) == 1 and resources[0].parentWidget() is window
    assert resources[0].types and resources[0].operators and c.devices is None
    resources[0].reject()
    configurations = []
    monkeypatch.setattr(ProjectConfigurationDialog, 'exec_', lambda dialog: configurations.append(dialog) or QDialog.Rejected)
    c.chrome.publishPages.trigger()
    assert len(configurations) == 1
    dialog = configurations[0]
    try:
        waitFor(lambda: dialog.pending is None)
        assert dialog.selectCategories == {'presentation'} and dialog.parentWidget() is window
        assert c.devices is None and not service.jobRepository.all() and service.loadedDocument is None
    finally:
        dialog.reject()


@pytest.mark.parametrize('activation', ['menu', 'shortcut'])
def testRealRuntimeRunAndStopRemainAvailableAfterSwitchToPages(
        ownedDesignerWindow, initialRuntime, tmp_path, monkeypatch, activation):  # noqa: F811
    service, client = initialRuntime
    root = tmp_path / 'engineering'
    payload = imageProject(root)
    # While state outputs must have matching inputs. This loop is deliberately
    # stateless: images remain ordinary node results, not loop-carried state.
    payload['workflows']['main']['outputs'] = {}
    payload['workflows']['main']['edges'] = [edge for edge in payload['workflows']['main']['edges']
        if edge['toNode'] != 'output']
    payload['globalVariables']['keep-running'] = dict(name='隔离循环使能', type='boolean',
        initialValue=True, kind='constant', lifetime='job')
    payload['workflows']['run'] = dict(name='隔离运行入口', nodes=[
        dict(nodeId='input', kind='workflow_input'),
        dict(nodeId='loop', kind='loop', loop=dict(mode='while', contractVersion=2, bodyWorkflowId='main',
            conditionMode='globalVariable', conditionVariableId='keep-running', unlimited=True, maxIterations=2, timeoutMs=0)),
        dict(nodeId='output', kind='workflow_output')])
    payload['workflowOrder'].append('run')
    payload['entryWorkflowId'] = 'run'
    payload['runtime']['startupWorkflowIds'] = ['run']
    path = root / 'run.emoproj'
    saveProject(path, payload)
    window = ownedDesignerWindow(client)
    assert window.loadProjectDirectory(str(path), confirmed=True)
    c = window.pageCoordinator
    c.sync()
    configuration = menuDialog(window, monkeypatch)
    try:
        with monkeypatch.context() as patch:
            patch.setattr(QMessageBox, 'question', lambda *_: QMessageBox.Yes)
            configuration.writeButton.click()
            waitFor(lambda: configuration.pending is None and not configuration.initial)
        assert service.loadedProjectFile == str(path.resolve())
    finally:
        configuration.reject()
    assert not service.jobRepository.all()
    calls = []
    original = client.startJob
    def start(*args, **kwargs):
        calls.append((args, kwargs))
        return original(*args, **kwargs)
    monkeypatch.setattr(client, 'startJob', start)
    # Opening a comparison here would mean run preflight no longer recognizes
    # the exact draft just published; do not hide a modal loop in this test.
    monkeypatch.setattr(ProjectConfigurationDialog, 'exec_', lambda _dialog: pytest.fail('unchanged online draft'))
    window.updateToolbarState()
    assert window._menuActions['开始运行'].isEnabled(), window._menuActions['开始运行'].toolTip()
    if activation == 'menu':
        window._menuActions['开始运行'].trigger()
    else:
        press(window, Qt.Key_F5)
    assert window.isJobRunning, window.runtimePanelState.lastMessage
    waitFor(lambda: window.currentJobId is not None or not window.isJobRunning, seconds=30)
    assert window.currentJobId is not None, window.runtimePanelState.lastMessage
    jobId = window.currentJobId
    waitFor(lambda: service.jobRepository.get(jobId).status == 'RUNNING', seconds=45)
    waitFor(lambda: any(event.nodeId == 'blur' and event.eventType == 'node.completed'
        for event in service.eventStore.read(jobId)), seconds=30)
    assert len(calls) == 1 and calls[0][0] == (str(path),) and calls[0][1]['workflowId'] == 'run'
    assert len(service.jobRepository.all()) == 1
    draft, disk = c.session.payload(), path.read_bytes()
    c.showPages()
    assert c.pageActive() and c.chrome.menus['运行'].menuAction().isVisible()
    assert window._menuActions['停止运行'].isEnabled()
    if activation == 'menu':
        window._menuActions['停止运行'].trigger()
    else:
        press(window, Qt.Key_F6)
    waitFor(lambda: not window.isJobRunning, seconds=45)
    assert service.jobRepository.get(jobId).isTerminal and len(calls) == 1
    assert path.read_bytes() == disk and c.session.payload() == draft
