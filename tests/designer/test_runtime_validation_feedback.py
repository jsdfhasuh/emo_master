"""Feedback contracts exercised through Qt buttons, not production devices."""
# ruff: noqa: F811
from copy import deepcopy
import json
from threading import Event
from types import SimpleNamespace

import pytest
from PySide2.QtCore import Qt
from PySide2.QtWidgets import QMessageBox, QDialogButtonBox, QApplication

from emo_master.apps.designer.services.runtime_client import RuntimeClientError, runtimeConnectionDescription
from emo_master.apps.designer.state.project_store import saveProject
from emo_master.apps.designer.ui.project_configuration import ProjectConfigurationDialog
from emo_master.apps.designer.ui.runtime_connection import RuntimeConnectionDialog
from tests.designer.runtime_feedback_fixture import messages  # noqa: F401
from tests.designer.test_device_workspace import waitUntil
from tests.designer.test_project_configuration_ui import setupDialog
from tests.runtime.test_initial_project_configuration import initialRuntime, imageProject  # noqa: F401


def legacyDialog(ownedDesignerWindow, client, tmp_path, *, badOperator=False, version='2.1'):
    payload = imageProject(tmp_path / 'engineering', bound=version != '2.1')
    payload['schemaVersion'] = version
    if version == '2.1':
        for key in ('presentation', 'resources', 'production', 'globalVariables', 'communication'):
            payload.pop(key, None)
    else:
        from emo_master.core.project.communication import CommunicationSettings
        payload['communication'] = CommunicationSettings().model_dump()
    payload['devices'] = dict(bindings={})
    if badOperator:
        payload['workflows']['main']['nodes'][2]['operatorId'] = 'missing.operator'
    path = tmp_path / 'engineering/original.emoproj'
    saveProject(path, payload)
    window = ownedDesignerWindow(client)
    # Legacy Open predates the draft boundary and calls LoadProject. Isolate that
    # unrelated API here so the screenshot's *empty Runtime* is the precondition.
    from unittest.mock import patch
    with patch.object(client, 'loadProject', return_value=SimpleNamespace(ok=True)):
        assert window.loadProjectDirectory(str(path), confirmed=True)
    window.flowModel.nodes['blur'].params['kernelSize'] = 5
    window.pageCoordinator.sync()
    dialog = ProjectConfigurationDialog(window.pageCoordinator, window)
    dialog.show()
    waitUntil(lambda: dialog.pending is None)
    return window, dialog, path


def selectChange(dialog):
    dialog.table.item(0, 0).setCheckState(Qt.Checked)


def testValidationHasImmediateBusyAndPassedNotWrittenFeedback(ownedDesignerWindow, tmp_path, messages):
    window, dialog, calls, path = setupDialog(ownedDesignerWindow, tmp_path)
    entered, release = Event(), Event()
    original = dialog.client.writeProjectConfiguration
    def slow(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return original(*args, **kwargs)
    dialog.client.writeProjectConfiguration = slow
    before, disk = window.pageCoordinator.session.payload(), path.read_bytes()
    try:
        selectChange(dialog)
        dialog.validateButton.click()
        assert '正在校验' in dialog.status.text()
        assert not dialog.validateButton.isEnabled() and '正在校验' in dialog.validateButton.text()
        dialog.validateButton.click()
        waitUntil(entered.is_set)
        release.set()
        waitUntil(lambda: dialog.pending is None)
        assert len(calls) == 1 and calls[0][3]
        assert '校验通过' in dialog.status.text() and '尚未写入' in dialog.status.text()
        assert dialog.status.property('feedbackState') == 'success'
        assert dialog.validateButton.isEnabled() and not messages
        assert window.pageCoordinator.session.payload() == before and path.read_bytes() == disk
    finally:
        release.set()
        dialog.reject()


def testLocalSelectionErrorIsVisibleAndRecoverable(ownedDesignerWindow, tmp_path, messages):
    _window, dialog, calls, _path = setupDialog(ownedDesignerWindow, tmp_path)
    try:
        dialog.validateButton.click()
        assert len(messages) == 1 and '请选择' in messages[0]['text']
        assert not calls and dialog.validateButton.isEnabled()
    finally:
        dialog.reject()


def testCurrentConnectionHasItsOwnArea(ownedDesignerWindow, tmp_path, messages):
    client = SimpleNamespace(listOperators=lambda: [],
        deviceNetworkCommand=lambda *_: dict(runtimeInstanceId='current', projectId=''))
    window = ownedDesignerWindow(client)
    dialog = RuntimeConnectionDialog(window.pageCoordinator)
    try:
        assert hasattr(dialog, 'currentStatus')
        waitUntil(lambda: dialog.currentPending is None)
        assert 'current' in dialog.currentStatus.text() and '尚未加载工程' in dialog.currentStatus.text()
        assert not dialog.target.text() and not messages
    finally:
        dialog.reject()


def testLegacyValidationOffersUpgradeWithoutMutatingDraft(ownedDesignerWindow, initialRuntime, tmp_path, messages):
    service, client = initialRuntime
    window, dialog, path = legacyDialog(ownedDesignerWindow, client, tmp_path)
    session = window.pageCoordinator.session
    before, history, disk = session.payload(), deepcopy(session._undo), path.read_bytes()
    try:
        assert '2.1' in dialog.formatStatus.text() and not dialog.upgradeButton.isHidden()
        assert not messages
        dialog.validateButton.click()
        assert len(messages) == 1 and '2.1' in messages[0]['text'] and '2.7' in messages[0]['text']
        assert '升级草稿并重新校验' in messages[0]['buttons']
        assert session.payload() == before and session._undo == history and path.read_bytes() == disk
        assert not service.loadedDocument and not service.jobRepository.all()
        assert dialog.validateButton.isEnabled()
    finally:
        dialog.reject()


@pytest.mark.parametrize('version', ['2.1', '2.5'])
def testExplicitUpgradeIsOneUndoableDraftChangeAndNeverWrites(ownedDesignerWindow, initialRuntime, tmp_path, messages, version):
    service, client = initialRuntime
    window, dialog, path = legacyDialog(ownedDesignerWindow, client, tmp_path, version=version)
    session = window.pageCoordinator.session
    before, history, disk = session.payload(), deepcopy(session._undo), path.read_bytes()
    source = (window.projectController.currentProjectFile, window.pageCoordinator.directory)
    try:
        dialog.upgradeButton.click()
        waitUntil(lambda: dialog.pending is None)
        assert not messages, messages
        upgraded = session.payload()
        assert upgraded['schemaVersion'] == '2.6' and dialog.initialValidated
        for key in ('workflows', 'workflowOrder', 'entryWorkflowId', 'project', 'dependencies', 'runtime'):
            assert upgraded[key] == before[key], key
        if 'resources' in before:
            assert upgraded['resources'] == before['resources']
            assert upgraded['presentation'] == before['presentation']
        assert session._undo == history + [before]
        assert source == (window.projectController.currentProjectFile, window.pageCoordinator.directory)
        assert dialog.upgradeButton.isHidden() and '尚未写入' in dialog.status.text()
        assert not service.loadedDocument and not service.jobRepository.all() and not service.deviceManager.drivers
        window.pageCoordinator.sync()
        assert session.payload() == upgraded and session._undo == history + [before]
        dialog.validateButton.click()
        waitUntil(lambda: dialog.pending is None)
        assert session._undo == history + [before]
        window.pageCoordinator.history()
        dialog.poll()
        assert session.payload() == before and not dialog.initialValidated
        assert not dialog.upgradeButton.isHidden()
        window.pageCoordinator.history(redo=True)
        dialog.poll()
        assert session.payload() == upgraded and not dialog.initialValidated
        window.pageCoordinator.sync()
        assert session.payload() == upgraded and path.read_bytes() == disk
    finally:
        dialog.reject()


def testUpgradeActionFromPopupDoesNotResumePrewrite(ownedDesignerWindow, initialRuntime, tmp_path, monkeypatch):
    service, client = initialRuntime
    _window, dialog, path = legacyDialog(ownedDesignerWindow, client, tmp_path)
    disk = path.read_bytes()
    seen = []
    def execute(box):
        seen.append(box.windowTitle())
        assert dialog.pending is None and not dialog.writeButton.isEnabled()
        # Control only the modal runner; exercise the real QMessageBox button.
        # A standalone QMessageBox.exec_ also crashes this Windows offscreen plugin.
        next(b for b in box.buttons() if b.text() == '升级草稿并重新校验').click()
        return box.result()
    def noSecondConfirmation(*_args):
        pytest.fail('explicit upgrade must not add another confirmation or continue writing')
    monkeypatch.setattr(QMessageBox, 'exec_', execute)
    originalQuestion = QMessageBox.question
    monkeypatch.setattr(QMessageBox, 'question', noSecondConfirmation)
    try:
        dialog.writeButton.click()
        waitUntil(lambda: dialog.pending is None)
        assert len(seen) == 1 and dialog.initialValidated
        assert not dialog.requestId and not service.loadedDocument and not service.jobRepository.all()
        assert path.read_bytes() == disk
    finally:
        monkeypatch.setattr(QMessageBox, 'question', originalQuestion)
        dialog.reject()


def testUpgradeKeepsDraftOnCompilerFailureAndPreservesLocations(ownedDesignerWindow, initialRuntime, tmp_path, messages):
    service, client = initialRuntime
    window, dialog, path = legacyDialog(ownedDesignerWindow, client, tmp_path, badOperator=True)
    before, disk = window.pageCoordinator.session.payload(), path.read_bytes()
    try:
        dialog.upgradeButton.click()
        waitUntil(lambda: dialog.pending is None)
        assert window.pageCoordinator.session.document().schemaVersion == '2.6'
        assert len(messages) == 1 and 'missing.operator' in messages[0]['text']
        assert 'workflowId' in messages[0]['detail'] and 'main' in messages[0]['detail']
        assert 'nodeId' in messages[0]['detail'] and 'blur' in messages[0]['detail']
        assert 'code' in messages[0]['detail'] and not dialog.initialValidated
        assert dialog.validateButton.isEnabled() and not service.loadedDocument and not service.jobRepository.all()
        window.pageCoordinator.history()
        dialog.poll()
        assert window.pageCoordinator.session.payload() == before and path.read_bytes() == disk
    finally:
        dialog.reject()


def testMigrationStructureFailureLeavesNoHalfUpgrade(ownedDesignerWindow, initialRuntime, tmp_path, monkeypatch, messages):
    _service, client = initialRuntime
    window, dialog, path = legacyDialog(ownedDesignerWindow, client, tmp_path)
    before, history, disk = window.pageCoordinator.session.payload(), deepcopy(window.pageCoordinator.session._undo), path.read_bytes()
    def invalid(*args, **kwargs):
        candidate = deepcopy(before)
        candidate['schemaVersion'] = '2.6'  # intentionally missing required structure
        return candidate
    monkeypatch.setattr('emo_master.core.project.migration.migrateProjectPayload', invalid)
    try:
        dialog.upgradeButton.click()
        assert len(messages) == 1 and '升级未完成' in messages[0]['title']
        assert window.pageCoordinator.session.payload() == before
        assert window.pageCoordinator.session._undo == history and path.read_bytes() == disk
    finally:
        dialog.reject()


def testUpgradeRechecksRuntimeThatAcquiredAnotherOnlineProject(ownedDesignerWindow, initialRuntime, tmp_path, messages):
    from emo_master.apps.runtime.grpc_server.generated import runtime_pb2
    service, client = initialRuntime
    _window, dialog, _path = legacyDialog(ownedDesignerWindow, client, tmp_path)
    onlinePath = tmp_path / 'online/online.emoproj'
    online = imageProject(onlinePath.parent)
    saveProject(onlinePath, online)
    assert service.LoadProject(runtime_pb2.LoadProjectRequest(project_path=str(onlinePath)), None).ok
    try:
        dialog.upgradeButton.click()
        waitUntil(lambda: dialog.pending is None)
        assert not dialog.initial and dialog.replacementPolicy.currentData() == 'stopAffected'
        # A different online project's managed resource root cannot resolve an
        # unbound local legacy input. Comparing/revalidating is required, not a
        # forced initial install or an invented successful replacement.
        assert len(messages) == 1 and 'input file not found' in messages[0]['detail']
        assert not dialog.validatedSelection and not dialog.requestId
        assert service.loadedProjectId == online['project']['projectId'] and not service.jobRepository.all()
    finally:
        dialog.reject()


@pytest.mark.parametrize('result', ['exception', 'FAILED'])
def testValidationFailuresPopupEachNewOperationAndRestoreControls(ownedDesignerWindow, tmp_path, messages, result):
    _window, dialog, _calls, _path = setupDialog(ownedDesignerWindow, tmp_path)
    def failed(*_args, **_kwargs):
        if result == 'exception':
            raise RuntimeClientError('E_CONFIG_INVALID', 'bad node parameter')
        return dict(state='FAILED', code='E_TEST_NODE', message='bad node parameter', nodeId='node-1')
    dialog.client.writeProjectConfiguration = failed
    try:
        selectChange(dialog)
        for count in (1, 2):
            dialog.validateButton.click()
            waitUntil(lambda: dialog.pending is None)
            assert len(messages) == count and 'bad node parameter' in messages[-1]['text']
            assert 'E_' in messages[-1]['detail'] and dialog.validateButton.isEnabled()
            assert not dialog.writePending and not dialog.validatedSelection
    finally:
        dialog.reject()


@pytest.mark.parametrize('state', ['FAILED', 'COMMITTED', 'CANCELLED', 'WAITING'])
def testReceiptNotificationsRespectActualStateAndDeduplicate(ownedDesignerWindow, tmp_path, messages, state):
    _window, dialog, _calls, _path = setupDialog(ownedDesignerWindow, tmp_path)
    receipt = dict(state=state, message='receipt message')
    if state == 'COMMITTED':
        receipt.update(publicationError='publication issue', cleanupError='cleanup issue', exportError='export issue')
    dialog.client.getProjectWriteStatus = lambda _request: receipt
    dialog.requestId = 'original-request'
    try:
        for _ in range(2):
            dialog.query()
            waitUntil(lambda: dialog.pending is None)
        assert len(messages) == (1 if state in {'FAILED', 'COMMITTED'} else 0)
        assert dialog.writePending == (state == 'WAITING')
        if state == 'COMMITTED':
            assert '已提交' in messages[0]['text'] and '已提交' in dialog.status.text()
            assert 'publication issue' in messages[0]['detail'] and 'export issue' in messages[0]['detail']
        if state == 'FAILED':
            dialog.requestId = 'new-request'
            dialog.query()
            waitUntil(lambda: dialog.pending is None)
            assert len(messages) == 2
    finally:
        dialog.reject()


@pytest.mark.parametrize('target, failure', [
    ('camera', 'FAILED: connection timeout'),
    ('communication', 'FAILED: address already in use'),
])
def testCommittedConnectionFailureWarnsWithoutRewriting(ownedDesignerWindow, tmp_path, messages, target, failure):
    window, dialog, calls, path = setupDialog(ownedDesignerWindow, tmp_path)
    before, disk = window.pageCoordinator.session.payload(), path.read_bytes()
    receipt = dict(state='COMMITTED', connections={target: failure, 'other-camera': 'PENDING'})
    original = deepcopy(receipt)
    queries = []
    def query(request):
        queries.append(request)
        return receipt
    dialog.client.getProjectWriteStatus = query
    dialog.requestId = 'committed-connection-failure'
    dialog._controls()
    try:
        for _ in range(2):
            dialog.queryButton.click()
            waitUntil(lambda: dialog.pending is None)
        assert len(messages) == 1
        assert messages[0]['title'] == '配置已提交，但后续操作异常'
        assert '已提交' in messages[0]['text'] and '不要重复写入' in messages[0]['text']
        assert target in messages[0]['text'] and failure in messages[0]['text']
        assert json.loads(messages[0]['detail']) == dict(connections={target: failure})
        assert receipt == original and not dialog.writePending
        assert '已提交' in dialog.status.text() and failure in dialog.status.text()
        assert dialog.writeButton.isEnabled() and dialog.validateButton.isEnabled() and dialog.queryButton.isEnabled()
        # Successful progress elsewhere must not repeat the same failure warning.
        receipt['connections']['other-camera'] = 'CONNECTED'
        dialog.queryButton.click()
        waitUntil(lambda: dialog.pending is None)
        assert len(messages) == 1
        assert queries == ['committed-connection-failure'] * 3
        # A different explicit request still reports an identical failure.
        dialog.requestId = 'new-connection-failure'
        dialog.queryButton.click()
        waitUntil(lambda: dialog.pending is None)
        assert len(messages) == 2 and queries[-1] == 'new-connection-failure'
        assert not calls and window.pageCoordinator.session.payload() == before and path.read_bytes() == disk
    finally:
        dialog.reject()


def testCommittedConnectionFailureReportedWhenLaterQueryResolvesPending(ownedDesignerWindow, tmp_path, messages):
    _window, dialog, calls, _path = setupDialog(ownedDesignerWindow, tmp_path)
    receipt = dict(state='COMMITTED', connections=dict(camera='PENDING'))
    dialog.client.getProjectWriteStatus = lambda _request: receipt
    dialog.requestId = 'pending-connection'
    dialog._controls()
    try:
        dialog.queryButton.click()
        waitUntil(lambda: dialog.pending is None)
        assert not messages and not dialog.writePending
        receipt['connections']['camera'] = 'FAILED: connection timeout'
        dialog.queryButton.click()
        waitUntil(lambda: dialog.pending is None)
        assert len(messages) == 1 and 'connection timeout' in messages[0]['detail']
        receipt['connections']['communication'] = 'FAILED: address already in use'
        for _ in range(2):
            dialog.queryButton.click()
            waitUntil(lambda: dialog.pending is None)
        assert len(messages) == 2
        assert json.loads(messages[-1]['detail'])['connections'] == receipt['connections']
        assert not calls and not dialog.writePending and dialog.requestId == 'pending-connection'
    finally:
        dialog.reject()


def testCommittedPendingIsObservedReadOnlyAndLateFailureWarnsOnce(ownedDesignerWindow, tmp_path, messages):
    _window, dialog, writes, _path = setupDialog(ownedDesignerWindow, tmp_path)
    receipt = dict(state='COMMITTED', connections=dict(server='PENDING'))
    queries = []
    def query(request):
        queries.append(request)
        return deepcopy(receipt)
    dialog.client.getProjectWriteStatus = query
    dialog.requestId = 'automatic-connection-observation'
    dialog._controls()
    try:
        dialog.queryButton.click()
        waitUntil(lambda: dialog.pending is None)
        assert dialog.connectionObserve and not dialog.writePending and not messages
        receipt['connections']['server'] = 'FAILED: loopback port occupied'
        waitUntil(lambda: len(messages) == 1 and dialog.connectionObserve is None)
        assert set(queries) == {'automatic-connection-observation'} and len(queries) >= 2
        assert not writes and not dialog.writePending
        assert 'loopback port occupied' in messages[0]['text']
        dialog.queryButton.click()
        waitUntil(lambda: dialog.pending is None)
        assert len(messages) == 1 and not writes
    finally:
        dialog.reject()


@pytest.mark.parametrize('connectionState', ['PENDING', 'CONNECTED', 'LISTENING', 'SUPERSEDED', 'UNKNOWN_AFTER_RESTART'])
def testCommittedNonfailedConnectionDoesNotWarn(ownedDesignerWindow, tmp_path, messages, connectionState):
    _window, dialog, calls, _path = setupDialog(ownedDesignerWindow, tmp_path)
    receipt = dict(state='COMMITTED', connections=dict(camera=connectionState))
    dialog.client.getProjectWriteStatus = lambda _request: receipt
    dialog.requestId = 'nonfailed-connection'
    dialog._controls()
    try:
        dialog.queryButton.click()
        waitUntil(lambda: dialog.pending is None)
        assert not messages and not calls and not dialog.writePending
        assert '已提交' in dialog.status.text() and connectionState in dialog.status.text()
        assert dialog.queryButton.isEnabled() and dialog.writeButton.isEnabled()
    finally:
        dialog.reject()


def testCommittedConnectionAndTopLevelErrorsShareOneWarning(ownedDesignerWindow, tmp_path, messages):
    _window, dialog, calls, _path = setupDialog(ownedDesignerWindow, tmp_path)
    receipt = dict(state='COMMITTED', exportError='export issue', connectionError='observation persistence issue',
                   connections=dict(camera='FAILED: connection timeout'))
    dialog.client.getProjectWriteStatus = lambda _request: receipt
    dialog.requestId = 'combined-errors'
    dialog._controls()
    try:
        for _ in range(2):
            dialog.queryButton.click()
            waitUntil(lambda: dialog.pending is None)
        assert len(messages) == 1
        assert json.loads(messages[0]['detail']) == {key: value for key, value in receipt.items() if key != 'state'}
        assert '已提交' in messages[0]['text'] and not dialog.writePending and not calls
    finally:
        dialog.reject()


@pytest.mark.parametrize('change', ['close', 'runtime', 'project', 'draft'])
def testLateValidationCannotNotifyOrContinueWritingInNewContext(ownedDesignerWindow, tmp_path, messages, change):
    window, dialog, _calls, _path = setupDialog(ownedDesignerWindow, tmp_path)
    entered, release = Event(), Event()
    def slow(*_args, **_kwargs):
        entered.set()
        assert release.wait(5)
        raise RuntimeClientError('E_CONFIG_INVALID', 'late failure')
    dialog.client.writeProjectConfiguration = slow
    try:
        selectChange(dialog)
        dialog.writeButton.click()
        waitUntil(entered.is_set)
        future = dialog.pending
        if change == 'close':
            dialog.reject()
        elif change == 'runtime':
            window.runtimeClient.runtimeScope = 'new-scope'
        elif change == 'project':
            from emo_master.apps.designer.state.project_edit_session import ProjectEditSession
            window.pageCoordinator.session = ProjectEditSession(window.pageCoordinator.session.payload())
        else:
            with window.pageCoordinator.session.transaction():
                window.pageCoordinator.session.workflows.project['name'] = 'changed while checking'
        release.set()
        waitUntil(future.done)
        dialog.poll()
        assert not messages and not dialog.requestId
        assert not dialog.initialValidated and not dialog.validatedSelection
    finally:
        release.set()
        dialog.reject()


def testValidationErrorModalCannotReenterCommandOrPoll(ownedDesignerWindow, tmp_path, monkeypatch):
    _window, dialog, calls, _path = setupDialog(ownedDesignerWindow, tmp_path)
    seen = []
    def execute(box):
        assert dialog.pending is None and dialog.notifying
        assert not dialog.validateButton.isEnabled() and not dialog.writeButton.isEnabled()
        dialog.validateButton.click()
        dialog.write(validate=True)
        dialog.poll()
        QApplication.processEvents()
        seen.append(box.text())
        next(b for b in box.buttons() if b.text() == '关闭').click()
        return box.result()
    monkeypatch.setattr(QMessageBox, 'exec_', execute)
    try:
        dialog.validateButton.click()
        assert len(seen) == 1 and not calls and not dialog.notifying and dialog.validateButton.isEnabled()
    finally:
        dialog.reject()


def testCurrentRuntimeDescriptionReadsActualTransportWithoutProvisioning(ownedDesignerWindow, initialRuntime, messages):
    service, client = initialRuntime
    expected = 'embedded' if client.runtimeService is service else 'grpc'
    if expected == 'embedded':
        client._runtimeTarget = '127.0.0.1:43210'  # an existing page endpoint is not the Runtime transport
    server = client._presentationServer
    window = ownedDesignerWindow(client)
    dialog = RuntimeConnectionDialog(window.pageCoordinator)
    try:
        assert runtimeConnectionDescription(client)['kind'] == expected
        assert ('本机内嵌' if expected == 'embedded' else '独立 Runtime') in dialog.currentType.text()
        assert dialog.target.text() == ('' if expected == 'embedded' else client._runtimeTarget)
        waitUntil(lambda: dialog.currentPending is None)
        assert '已连接' in dialog.currentStatus.text() and '尚未加载工程' in dialog.currentStatus.text()
        assert service.runtimeInstanceId in dialog.currentStatus.text()
        assert ('本机内嵌' if expected == 'embedded' else '独立 Runtime') in window.pageCoordinator.chrome.runtimeStatus.text()
        assert client._presentationServer is server and not service.loadedDocument and not service.jobRepository.all()
        assert not messages and dialog.buttons.button(QDialogButtonBox.Cancel).text() == '取消'
    finally:
        dialog.reject()
    assert not service._closing


@pytest.mark.parametrize('failure', ['identity', 'version'])
def testCurrentInfoFailureDoesNotEraseSuccessfulIdentityOrClaimConnected(ownedDesignerWindow, messages, failure):
    def info(*_args):
        if failure == 'identity':
            raise TimeoutError('identity unavailable')
        return dict(runtimeInstanceId='known-instance', projectId='known-project')
    def snapshot():
        raise RuntimeClientError('UNIMPLEMENTED', 'version unavailable')
    window = ownedDesignerWindow(SimpleNamespace(listOperators=lambda: [], deviceNetworkCommand=info,
                                                 getOnlineProjectSnapshot=snapshot))
    dialog = RuntimeConnectionDialog(window.pageCoordinator)
    try:
        waitUntil(lambda: dialog.currentPending is None)
        text = dialog.currentStatus.text()
        if failure == 'identity':
            assert '读取失败' in text and '已连接' not in text
        else:
            assert all(part in text for part in ('已连接', 'known-instance', 'known-project', '版本信息读取失败'))
        assert not messages
    finally:
        dialog.reject()


@pytest.mark.parametrize('change', ['close', 'runtime', 'project'])
def testLateCandidateIsReleasedWithoutSwitchOrPopup(ownedDesignerWindow, monkeypatch, messages, change):
    from emo_master.apps.designer.ui import runtime_connection
    entered, release, closed, switched = Event(), Event(), Event(), []
    candidate = SimpleNamespace(close=closed.set)
    def probe(_target):
        entered.set()
        assert release.wait(5)
        return candidate, dict(runtimeInstanceId='candidate', projectId=''), None, {}
    monkeypatch.setattr(runtime_connection, 'connectCandidate', probe)
    current = SimpleNamespace(listOperators=lambda: [], deviceNetworkCommand=lambda *_: dict(runtimeInstanceId='current', projectId=''))
    window = ownedDesignerWindow(current)
    window.pageCoordinator.attachRuntime = switched.append
    dialog = RuntimeConnectionDialog(window.pageCoordinator)
    try:
        waitUntil(lambda: dialog.currentPending is None)
        before = dialog.currentStatus.text()
        dialog.target.setText('127.0.0.1:45678')
        dialog.buttons.button(QDialogButtonBox.Ok).click()
        waitUntil(entered.is_set)
        future = dialog.pending
        if change == 'close':
            dialog.reject()
            assert dialog.closed and not dialog.timer.isActive()
        elif change == 'runtime':
            current.runtimeScope = 'new-scope'
        else:
            from emo_master.apps.designer.state.project_edit_session import ProjectEditSession
            window.pageCoordinator.session = ProjectEditSession(window.pageCoordinator.session.payload())
        release.set()
        waitUntil(future.done)
        dialog.ready()
        waitUntil(closed.is_set)
        assert not messages and not switched and dialog.currentStatus.text() == before
    finally:
        release.set()
        dialog.reject()


def testCandidateFailureDoesNotOverwriteCurrentAreaAndCanRetry(ownedDesignerWindow, monkeypatch, messages):
    from emo_master.apps.designer.ui import runtime_connection
    current = SimpleNamespace(listOperators=lambda: [], deviceNetworkCommand=lambda *_: dict(runtimeInstanceId='current', projectId=''))
    window = ownedDesignerWindow(current)
    dialog = RuntimeConnectionDialog(window.pageCoordinator)
    calls, closed = [], []
    candidate = SimpleNamespace(close=lambda: closed.append(True))
    def probe(target):
        calls.append(target)
        if len(calls) == 1:
            raise RuntimeClientError('UNAVAILABLE', 'candidate refused')
        return candidate, dict(runtimeInstanceId='candidate', projectId=''), None, {}
    monkeypatch.setattr(runtime_connection, 'connectCandidate', probe)
    try:
        waitUntil(lambda: dialog.currentPending is None)
        before = dialog.currentStatus.text()
        dialog.target.setText('127.0.0.1:45678')
        for _ in range(2):
            dialog.buttons.button(QDialogButtonBox.Ok).click()
            waitUntil(lambda: dialog.pending is None)
        assert len(messages) == 1 and '原连接未改变' in messages[0]['text']
        assert dialog.currentStatus.text() == before and 'candidate' in dialog.status.text()
        assert dialog.buttons.button(QDialogButtonBox.Ok).text() == '切换到此 Runtime'
        assert window.runtimeClient is current
    finally:
        dialog.reject()
    assert closed == [True]


def testUpgradeRejectsUnknownVersionsAndIllegalFields():
    from emo_master.apps.designer.state.workflow_store import WorkflowStore
    from emo_master.core.project.migration import migrateProjectPayload
    payload = WorkflowStore().toPayload()
    for invalid in (dict(payload, schemaVersion='9.9'), dict(payload, unexpected='not supported')):
        original = deepcopy(invalid)
        with pytest.raises(ValueError):
            migrateProjectPayload(invalid, enableDevices=True)
        assert invalid == original


def testUpgradedDraftResourceFailureCanBeFixedAndRevalidated(ownedDesignerWindow, initialRuntime, tmp_path, messages):
    service, client = initialRuntime
    window, dialog, path = legacyDialog(ownedDesignerWindow, client, tmp_path)
    disk = path.read_bytes()
    try:
        window.flowModel.nodes['load'].params['imagePath'] = 'missing.png'
        window.pageCoordinator.sync()
        dialog.poll()
        dialog.upgradeButton.click()
        waitUntil(lambda: dialog.pending is None)
        assert window.pageCoordinator.session.document().schemaVersion == '2.6'
        assert len(messages) == 1 and 'main/load/imagePath' in messages[0]['detail']
        assert 'input file not found' in messages[0]['text'] and not dialog.initialValidated
        window.flowModel.nodes['load'].params['imagePath'] = 'input.png'
        window.pageCoordinator.sync()
        dialog.validateButton.click()
        waitUntil(lambda: dialog.pending is None)
        assert dialog.initialValidated and len(messages) == 1
        assert not service.loadedDocument and not service.jobRepository.all() and path.read_bytes() == disk
    finally:
        dialog.reject()


def testPrewriteSuccessHasOnlyExistingImpactConfirmation(ownedDesignerWindow, tmp_path, monkeypatch, messages):
    _window, dialog, calls, _path = setupDialog(ownedDesignerWindow, tmp_path)
    questions = []
    originalQuestion = QMessageBox.question
    def confirm(_parent, title, *_args):
        questions.append(title)
        assert dialog.confirming and dialog.pending is None
        assert not dialog.writeButton.isEnabled()
        dialog.writeButton.click()
        dialog.poll()
        return QMessageBox.Yes
    monkeypatch.setattr(QMessageBox, 'question', confirm)
    try:
        selectChange(dialog)
        dialog.writeButton.click()
        waitUntil(lambda: dialog.pending is None)
        assert questions == ['写入 Runtime'] and not messages
        assert [call[3] for call in calls] == [True, False]
        assert not dialog.writePending and dialog.writeButton.isEnabled()
    finally:
        monkeypatch.setattr(QMessageBox, 'question', originalQuestion)
        dialog.reject()


def testOpeningCurrentConnectionWithExistingPageEndpointDoesNotProvisionAgain(ownedDesignerWindow, tmp_path, messages):
    from emo_master.apps.designer.services.runtime_client import RuntimeClient
    from emo_master.apps.runtime.grpc_server.service import RuntimeService
    service = RuntimeService(dbPath=tmp_path / 'runtime.db', workspaceRoot=tmp_path / 'jobs')
    client = RuntimeClient(service)
    dialog = None
    try:
        client.prepareStart(capturePresentation=True)  # explicitly create the pre-existing endpoint, without a Job
        assert client._presentationServer is not None and client._runtimeTarget.startswith('127.0.0.1:')
        before = (client._presentationServer, client._presentationService, client._runtimeTarget)
        window = ownedDesignerWindow(client)
        dialog = RuntimeConnectionDialog(window.pageCoordinator)
        waitUntil(lambda: dialog.currentPending is None)
        assert '本机内嵌' in dialog.currentType.text() and '本机内嵌' in window.pageCoordinator.chrome.runtimeStatus.text()
        assert not dialog.target.text() and '已连接' in dialog.currentStatus.text()
        assert before == (client._presentationServer, client._presentationService, client._runtimeTarget)
        assert not service.jobRepository.all() and not messages
        dialog.buttons.button(QDialogButtonBox.Cancel).click()
        assert not service._closing
    finally:
        if dialog is not None:
            dialog.reject()
        client.close()
        service.close()


def testSelectionChangeImmediatelyInvalidatesVisiblePassedVerdict(ownedDesignerWindow, tmp_path, messages):
    _window, dialog, calls, _path = setupDialog(ownedDesignerWindow, tmp_path)
    try:
        selectChange(dialog)
        dialog.validateButton.click()
        waitUntil(lambda: dialog.pending is None)
        assert dialog.status.property('feedbackState') == 'success'
        dialog.table.item(0, 0).setCheckState(Qt.Unchecked)
        assert dialog.validatedSelection is None and dialog.status.property('feedbackState') != 'success'
        assert '重新校验' in dialog.status.text() and len(calls) == 1 and not messages
    finally:
        dialog.reject()


def testReconnectedRuntimeExplainsExpiredDialogInsteadOfSilentButtons(ownedDesignerWindow, tmp_path, messages):
    window, dialog, calls, _path = setupDialog(ownedDesignerWindow, tmp_path)
    try:
        window.runtimeClient.runtimeScope = 'reconnected-generation'
        dialog.poll()
        assert '连接已变化' in dialog.status.text() and '重新比较' in dialog.status.text()
        assert not dialog.writeButton.isEnabled() and not dialog.validateButton.isEnabled()
        assert dialog.closeButton.isEnabled() and not calls and not messages
    finally:
        dialog.reject()


def testRemoteTargetMetadataUsesIndependentTypeAndShowsCurrentVersions(ownedDesignerWindow, messages):
    target = 'runtime.example.invalid:45001'
    client = SimpleNamespace(listOperators=lambda: [], runtimeService=object(), _ownedChannel=object(),
        _runtimeTarget=target, deviceNetworkCommand=lambda *_: dict(runtimeInstanceId='remote-metadata', projectId='remote-project'),
        getOnlineProjectSnapshot=lambda: dict(documentRevision=9, executionRevision=7))
    window = ownedDesignerWindow(client)
    dialog = RuntimeConnectionDialog(window.pageCoordinator)
    try:
        waitUntil(lambda: dialog.currentPending is None)
        assert '独立 Runtime' in dialog.currentType.text() and target in dialog.currentType.text()
        assert all(value in dialog.currentStatus.text() for value in ('remote-metadata', 'remote-project', 'R9', 'R7'))
        assert target in window.pageCoordinator.chrome.runtimeStatus.text() and dialog.target.text() == target
        assert not messages  # mocked metadata only; no remote network or device access
    finally:
        dialog.reject()


def testRealConnectionDialogSwitchUpdatesChromeAndRetainsEmbeddedOwner(ownedDesignerWindow, tmp_path, messages):
    from emo_master.apps.designer.services.runtime_client import RuntimeClient
    from emo_master.apps.runtime.grpc_server.service import RuntimeService
    from emo_master.apps.runtime.main import createRuntimeServer
    old = RuntimeService(dbPath=tmp_path / 'old/runtime.db', workspaceRoot=tmp_path / 'old/jobs')
    new = RuntimeService(dbPath=tmp_path / 'new/runtime.db', workspaceRoot=tmp_path / 'new/jobs')
    server, port, _ = createRuntimeServer(port=0, runtimeService=new)
    server.start()
    client = RuntimeClient(old, ownedRuntimeService=old)
    dialog = None
    try:
        window = ownedDesignerWindow(client)
        dialog = RuntimeConnectionDialog(window.pageCoordinator)
        waitUntil(lambda: dialog.currentPending is None)
        dialog.target.setText(f'127.0.0.1:{port}')
        dialog.buttons.button(QDialogButtonBox.Ok).click()
        waitUntil(lambda: dialog.pending is None)
        assert client.runtimeService is old and not old._closing
        assert new.runtimeInstanceId in dialog.status.text() and '本机内嵌' in dialog.currentType.text()
        assert window.communicationController is None  # device observation owns the current path
        dialog.buttons.button(QDialogButtonBox.Ok).click()
        assert dialog.closed and window.runtimeClient is client and not old._closing and not messages
        assert client.deviceNetworkCommand('', '', 'info')['runtimeInstanceId'] == new.runtimeInstanceId
        assert '独立 Runtime' in window.pageCoordinator.chrome.runtimeStatus.text()
        assert f'127.0.0.1:{port}' in window.pageCoordinator.chrome.runtimeStatus.text()
        assert not old.jobRepository.all() and not new.jobRepository.all()
    finally:
        if dialog is not None:
            dialog.reject()
        client.close()
        server.stop(0).wait(5)
        new.close()
    assert old._closing


def testClosingDuringCurrentInfoReadIgnoresLateReplyAndKeepsOwner(ownedDesignerWindow, messages):
    entered, release = Event(), Event()
    closed = []
    def info(*_args):
        entered.set()
        assert release.wait(5)
        return dict(runtimeInstanceId='late-current', projectId='')
    client = SimpleNamespace(listOperators=lambda: [], deviceNetworkCommand=info, close=lambda: closed.append(True))
    window = ownedDesignerWindow(client)
    dialog = RuntimeConnectionDialog(window.pageCoordinator)
    try:
        waitUntil(entered.is_set)
        before = dialog.currentStatus.text()
        future = dialog.currentPending
        dialog.buttons.button(QDialogButtonBox.Cancel).click()
        release.set()
        waitUntil(future.done)
        dialog.ready()
        assert dialog.closed and dialog.currentStatus.text() == before and not closed and not messages
    finally:
        release.set()
        dialog.reject()


def testIdleContextChecksUseExistingIdentityWithoutWholeDraftValidation(ownedDesignerWindow, tmp_path, monkeypatch, messages):
    window, configuration, _calls, _path = setupDialog(ownedDesignerWindow, tmp_path)
    connection = RuntimeConnectionDialog(window.pageCoordinator)
    waitUntil(lambda: connection.currentPending is None)
    session = window.pageCoordinator.session
    originalDocument, projectId = session.document, session.workflows.project['projectId']
    def unexpectedValidation():
        pytest.fail('idle timer context checks must not validate the entire draft')
    monkeypatch.setattr(session, 'document', unexpectedValidation)
    try:
        for _ in range(10):
            configuration.poll()
            assert configuration._valid() and connection._valid()
        session.workflows.project['projectId'] = 'different-project'
        assert not configuration._valid() and not connection._valid()
        configuration.poll()
        assert not configuration.writeButton.isEnabled() and not messages
    finally:
        session.workflows.project['projectId'] = projectId
        monkeypatch.setattr(session, 'document', originalDocument)
        configuration.reject()
        connection.reject()
