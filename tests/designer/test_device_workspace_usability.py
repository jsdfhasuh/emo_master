"""Native Qt regressions for visible actions and recoverable device editing."""
from copy import deepcopy
from concurrent.futures import Future

import pytest
from PySide2.QtCore import QRect, Qt
from PySide2.QtWidgets import QApplication, QMessageBox, QToolButton, QDialogButtonBox

from tests.core.project.test_devices import declaration, definition  # noqa: F401
from tests.designer.test_device_workspace import windowFor, waitUntil


def workspace(ownedDesignerWindow, tmp_path, definition, declaration):  # noqa: F811
    window, _ = windowFor(ownedDesignerWindow, tmp_path, definition, declaration)
    window.pageCoordinator.showDevices()
    d = window.pageCoordinator.devices
    waitUntil(lambda: d.pending is None)
    d.table.setCurrentCell(0, 0)
    return d


def testVisibleActionLabelsExplainDraftBoundary(ownedDesignerWindow, tmp_path, definition, declaration):  # noqa: F811
    d = workspace(ownedDesignerWindow, tmp_path, definition, declaration)
    for key, button in d.buttons.items():
        assert button.text(), key
        assert button.toolTip()
        if isinstance(button, QToolButton):
            assert button.toolButtonStyle() == Qt.ToolButtonTextBesideIcon
            assert button.minimumHeight() >= 32
            assert button.accessibleName()
    assert {'save', 'compare', 'bindings', 'state'}.isdisjoint(d.buttons)
    assert d.buttons['delete'] in d.listMore.actions()
    assert d.buttons['migration'] in d.listMore.actions()
    assert d.buttons['diagnostics'] in d.diagnosticMenu.actions()
    assert d.readParametersButton in d.diagnosticMenu.actions()
    assert d.buttons['network'] in d.deviceMore.actions()
    assert {b.text() for b in d.findChild(QDialogButtonBox).buttons()} == {'保留草稿并关闭', '查看并写入 Runtime…'}
    assert '保留草稿' in d.closeButton.text()
    assert '切换设备或关闭' in d.draftHelp.toolTip()
    assert 'Runtime' in d.draftHelp.toolTip()


def testAutoapplyRenameAndHistoryNeverReapplyOldControls(ownedDesignerWindow, tmp_path,
        definition, declaration):  # noqa: F811
    d = workspace(ownedDesignerWindow, tmp_path, definition, declaration)
    c = d.c
    count = len(c.session._undo)
    d.name.setText('自动保留的名称')
    d.commitPending()
    assert len(c.session._undo) == count + 1
    assert d.table.item(0, 0).text() == d.detailTitle.text() == '自动保留的名称'
    assert d.instanceItems['camera'].text(0).startswith('自动保留的名称')
    d.commitPending()
    assert len(c.session._undo) == count + 1
    c.history()
    assert d.name.text() == definition.name
    d.closeButton.click()
    assert c.session.document().devices.definitions['camera'].name == definition.name
    c.showDevices()
    c.history(True)
    d.closeButton.click()
    assert c.session.document().devices.definitions['camera'].name == '自动保留的名称'


@pytest.mark.parametrize('confirm', [False, True])
def testWorkspaceDuplicateIsExplicitAndNeverOverwrites(ownedDesignerWindow, tmp_path,
        definition, declaration, monkeypatch, confirm):  # noqa: F811
    declaration.connectionSchema['properties']['source']['xDeviceIdentity'] = True
    d = workspace(ownedDesignerWindow, tmp_path, definition, declaration)
    candidate = definition.model_dump()
    candidate.update(name='Keep this input')
    candidate['defaults']['gain'] = 4
    before = d.c.session.payload()
    undo = len(d.c.session._undo)
    monkeypatch.setattr(QMessageBox, 'question', lambda *_: QMessageBox.Yes if confirm else QMessageBox.No)
    assert d._acceptNewDevice(candidate, d) is confirm
    assert d.c.session.payload() == before
    assert len(d.c.session._undo) == undo
    assert d.selectedId == 'camera'
    assert candidate['name'] == 'Keep this input'


def testWorkspaceValidationFocusAndRevertStayAtomic(ownedDesignerWindow, tmp_path,
        definition, declaration):  # noqa: F811
    d = workspace(ownedDesignerWindow, tmp_path, definition, declaration)
    before = d.c.session.payload()
    d.tabs.setCurrentIndex(3)
    d.name.clear()
    d.apply()
    assert d.status.text() == '设备名称不能为空'
    assert d.tabs.currentIndex() == 0
    assert d.c.session.payload() == before
    d.revert()
    assert d.name.text() == definition.name
    d.name.setText('Applied draft')
    d.apply()
    d.name.setText('Unapplied edit')
    d.revert()
    assert d.name.text() == 'Applied draft'
    assert '尚未应用' in d.status.text()


def testUnchangedDisabledPortableDefinitionCanClose(ownedDesignerWindow, tmp_path,
        definition, declaration):  # noqa: F811
    d = workspace(ownedDesignerWindow, tmp_path, definition, declaration)
    raw = d.c.session.workflows.devices['definitions']['camera']
    raw['enabled'] = False
    raw['connection']['source'] = ''
    declaration.connectionSchema['properties']['source']['minLength'] = 1
    d._showDetails()
    before = deepcopy(d.c.session.payload())
    assert d.close()
    assert d.c.session.payload() == before


@pytest.mark.parametrize('laterMessage', ['', '设备名称不能为空'])
def testBackgroundRecoveryClearsOnlyItsOwnErrorAndPreservesEdit(ownedDesignerWindow, tmp_path,
        definition, declaration, laterMessage):  # noqa: F811
    d = workspace(ownedDesignerWindow, tmp_path, definition, declaration)
    before = d.c.session.payload()
    failed = Future()
    failed.set_exception(ValueError('Runtime 未加载此工程'))
    d.pending, d.pendingKind, d.pendingScope = failed, 'state', d._scope()
    d.backgroundState = True
    d._metadataReady()
    assert d.status.text() == '设备信息读取失败：Runtime 未加载此工程'
    d.name.setText('Pending device edit')
    if laterMessage:
        d.status.setText(laterMessage)
    recovered = Future()
    recovered.set_result(('runtime', {'camera': dict(state='IDLE', generation='g1',
                                                   definitionRevision='r1', queued=0, activeOwner=None)}))
    d.pending, d.pendingKind, d.pendingScope = recovered, 'state', d._scope()
    d._metadataReady()
    assert d.stateError == ''
    assert d.status.text() == (laterMessage or '在线状态已刷新')
    assert d.name.text() == 'Pending device edit'
    assert d.c.session.payload() == before


@pytest.mark.parametrize('action', ['apply', 'close'])
def testIncompleteNumberRevealsItsTabWithoutCommitting(ownedDesignerWindow, tmp_path,
        definition, declaration, action):  # noqa: F811
    d = workspace(ownedDesignerWindow, tmp_path, definition, declaration)
    before = d.c.session.payload()
    control = d.forms['defaults']._controls['gain']
    control.setText('-')
    d.tabs.setCurrentIndex(0)
    try:
        getattr(d, action)()
        assert d.isVisible()
        assert d.tabs.currentIndex() == 2
        assert '请输入有效数值' in d.status.text()
        assert control.text() == '-'
        assert d.c.session.payload() == before
    finally:
        d.revert()
        assert d.close()


def tcpWorkspace(ownedDesignerWindow, tmp_path):
    from pathlib import Path
    from emo_master.core.plugin.devices import DeviceType
    from emo_master.core.project.devices import DeviceDefinition
    from tests.runtime.test_tcp_device_integration import definition as tcpDefinition
    typeDecl = DeviceType.model_validate_json((Path(__file__).resolve().parents[2] /
        'src/emo_master/plugins/builtins/tcp_session/device_types/server.json').read_text(encoding='utf-8'))
    value = tcpDefinition(typeDecl, port=10000,
        channels=[dict(channelId='a', name='A', allowedIps=['127.0.0.1'])])
    value.update(description='未发布的候选')
    return workspace(ownedDesignerWindow, tmp_path, DeviceDefinition.model_validate(value), typeDecl)


@pytest.mark.parametrize('changed', ['device', 'port'])
@pytest.mark.parametrize('failed', [False, True])
def testConnectionReceiptBelongsToDeviceAndCandidate(ownedDesignerWindow, tmp_path, monkeypatch, changed, failed):
    d = tcpWorkspace(ownedDesignerWindow, tmp_path)
    if changed == 'device':
        other = deepcopy(d.original)
        other['connection']['port'] = 10002
        other['name'] = 'B'
        d.catalog().put(other, deviceId='other')
        d.refresh()
        d.table.setCurrentCell(next(i for i in range(d.table.rowCount())
            if d.table.item(i, 0).data(Qt.UserRole) == 'camera'), 0)
    future = Future()
    monkeypatch.setattr(d.reader, 'submit', lambda *_args, **_kw: future)
    monkeypatch.setattr(QMessageBox, 'question', lambda *_: QMessageBox.Yes)
    d.testConnection()
    assert d.pending is future and d.timer.isActive()
    if changed == 'device':
        d.table.setCurrentCell(next(i for i in range(d.table.rowCount())
            if d.table.item(i, 0).data(Qt.UserRole) == 'other'), 0)
        assert d.selectedId == 'other'
    else:
        d.forms['connection'].fields._controls['port'].setValue(10001)
    d.status.setText('当前输入待测试')
    d.status.setToolTip('当前输入')
    if failed:
        future.set_exception(ValueError('OLD-ENDPOINT-FAILURE'))
    else:
        future.set_result(dict(message='OLD-ENDPOINT-SUCCESS'))
    d._metadataReady()
    assert d.pending is None and not d.timer.isActive()
    assert d.buttons['refresh'].isEnabled()
    assert d.status.text() == '当前输入待测试'
    assert 'OLD-ENDPOINT' not in d.status.toolTip()
    d.revert()


@pytest.mark.parametrize('size', [(1200, 760), (826, 590)])
def testDetailConnectionValidationIsChineseVisibleAndReadOnly(ownedDesignerWindow, tmp_path, monkeypatch, size):
    d = tcpWorkspace(ownedDesignerWindow, tmp_path)
    before = d.c.session.payload()
    source = tmp_path / 'device.emoproj'
    sourceBytes = source.read_bytes()
    calls = []
    monkeypatch.setattr(d.reader, 'submit', lambda *_args, **_kw: calls.append('RPC'))
    monkeypatch.setattr(QMessageBox, 'question', lambda *_: calls.append('confirm') or QMessageBox.Yes)
    form = d.forms['connection']
    d.resize(*size)
    d.tabs.setCurrentIndex(1)
    QApplication.processEvents()
    assert d.formScrolls['connection'].viewport().height() >= 2 * d.name.height(), (
        d.detailSplitter.sizes(), d.details.size(), d.detailSplitter.size(),
        [(w.text(), w.height()) for w in (d.detailTitle, d.typeInfo, d.onlineIndicator, d.status, d.difference)])
    form.channels.setValue([dict(channelId=f'ch-{index}', name=f'通道 {index + 1}',
        allowedIps=[f'127.0.0.{index + 1}'] if index < 7 else []) for index in range(8)])
    policies = d.forms['policies']
    policies._controls['sendTimeoutMs'].setValue(2500)
    candidate = deepcopy(d._formValue())
    policies.advancedButton.setChecked(True)
    policies.advancedButton.setChecked(False)
    assert d._formValue() == candidate
    # An error must reopen a user-collapsed editor, and traverse both scroll
    # areas to reveal a later row, not merely give an offscreen table focus.
    d.detailSplitter.setSizes([0, 1])
    d.tabs.setCurrentIndex(2)
    d.activateWindow()
    waitUntil(d.isActiveWindow)
    d.testConnection()
    QApplication.processEvents()
    try:
        assert d.status.text() == '通道 8：请填写至少一个允许接入的客户端 IPv4'
        table = form.channels.table
        assert d.tabs.currentIndex() == 1 and table.hasFocus()
        assert (table.currentRow(), table.currentColumn()) == (7, 2)
        cell = table.visualItemRect(table.currentItem())
        assert table.viewport().rect().contains(cell)
        outer = d.formScrolls['connection'].viewport()
        assert outer.rect().contains(QRect(table.viewport().mapTo(outer, cell.topLeft()), cell.size()))
        for key in ('apply', 'revert'):
            button = d.buttons[key]
            assert button.isVisibleTo(d)
            assert d.rect().contains(QRect(button.mapTo(d, button.rect().topLeft()), button.size()))
        assert d._formValue() == candidate
        assert d.c.session.payload() == before and source.read_bytes() == sourceBytes
        assert calls == [] and d.pending is None
    finally:
        d.revert()


def testDetailFieldFeedbackClearsOnlyCorrectedInputAndSurvivesState(ownedDesignerWindow, tmp_path,
        definition, declaration):  # noqa: F811
    d = workspace(ownedDesignerWindow, tmp_path, definition, declaration)
    gain = d.forms['defaults']._controls['gain']
    gain.setText('-')
    d.apply()
    message = d.status.text()
    assert message and gain.accessibleDescription()
    try:
        d.name.setText('仅修改其他字段')
        assert d.status.text() == message
        response = Future()
        response.set_exception(ValueError('background unavailable'))
        d.pending, d.pendingKind, d.pendingScope = response, 'state', d._scope()
        d.backgroundState = True
        d._metadataReady()
        assert d.status.text() == message
        gain.setText('2')
        assert not gain.accessibleDescription() and not d.status.text()
    finally:
        d.revert()


def testDetailChannelFeedbackClearsOnlyEditedErrorCell(ownedDesignerWindow, tmp_path):
    d = tcpWorkspace(ownedDesignerWindow, tmp_path)
    form = d.forms['connection']
    form.channels.setValue([dict(channelId='a', name='A', allowedIps=[])])
    d.apply()
    message = d.status.text()
    table = form.channels.table
    assert '通道 1' in message and table.accessibleDescription()
    try:
        table.item(0, 1).setText('只改显示名')
        assert d.status.text() == message and table.accessibleDescription() == message
        table.item(0, 2).setText('127.0.0.1')
        assert not table.accessibleDescription() and not d.status.text()
    finally:
        d.revert()


@pytest.mark.parametrize('failed', [False, True])
def testDisabledEmptyDetailConnectionTestUsesCandidateWithoutApplying(ownedDesignerWindow, tmp_path, monkeypatch, failed):
    from types import SimpleNamespace
    d = tcpWorkspace(ownedDesignerWindow, tmp_path)
    calls = []

    def network(project, runtime, command, **kwargs):
        calls.append((project, runtime, command, kwargs))
        if failed and command == 'test_connection':
            raise ValueError('CURRENT-ENDPOINT-FAILURE')
        return dict(projectId='online', runtimeInstanceId='r', deviceSetupVersion=1, message='CURRENT-ENDPOINT')

    d.c.window.runtimeClient = SimpleNamespace(deviceNetworkCommand=network)
    d._showDetails()  # Recompute support after installing the test connector.
    d.enabled.setChecked(False)
    d.forms['connection'].channels.setValue([])
    d.forms['connection'].fields._controls['port'].setValue(10001)
    candidate = deepcopy(d._formValue())
    before = d.c.session.payload()
    source = tmp_path/'device.emoproj'
    disk = source.read_bytes()
    monkeypatch.setattr(QMessageBox, 'question', lambda *_: QMessageBox.Yes)
    try:
        d.testConnection()
        waitUntil(lambda: d.pending is None)
        assert [call[2] for call in calls] == ['info', 'test_connection']
        assert calls[-1][3]['payload']['definition'] == candidate
        if failed:
            assert d.status.text() == '设备操作未完成，请查看详情'
            assert 'CURRENT-ENDPOINT-FAILURE' in d.status.toolTip()
        else:
            assert 'CURRENT-ENDPOINT' in d.status.text()
        assert d.c.session.payload() == before and source.read_bytes() == disk
        assert d._formValue() == candidate and not d.timer.isActive()
        assert d.buttons['test'].isEnabled()
    finally:
        d.revert()


def testImportBatchUndoRedoRefreshesOperationNotice(ownedDesignerWindow, tmp_path,
        definition, declaration, monkeypatch):  # noqa: F811
    from PySide2.QtWidgets import QFileDialog
    from emo_master.apps.designer.state.device_package import exportDevices
    d = workspace(ownedDesignerWindow, tmp_path, definition, declaration)
    package = exportDevices(d.c.session, ['camera'], tmp_path)
    path = tmp_path / 'devices.emodev'
    path.write_text(package.model_dump_json(), encoding='utf-8')
    monkeypatch.setattr(QFileDialog, 'getOpenFileName', lambda *_: (str(path), ''))
    monkeypatch.setattr(QMessageBox, 'question', lambda *_: QMessageBox.Yes)
    d.importDevices()
    first = deepcopy(d.c.session.payload()['devices'])
    d.importDevices()
    second = deepcopy(d.c.session.payload()['devices'])
    assert len(second['definitions']) == 3 and '已导入工程草稿' in d.status.text()
    d.buttons['undo'].click()
    assert d.c.session.payload()['devices'] == first
    assert '已撤销' in d.status.text() and '已导入' not in d.status.text()
    d.buttons['redo'].click()
    assert d.c.session.payload()['devices'] == second
    assert '已重做' in d.status.text() and '已导入' not in d.status.text()
    assert all(not item['enabled'] for key, item in second['definitions'].items() if key != 'camera')
