"""Native Qt regressions for visible actions and recoverable device editing."""
from copy import deepcopy
from concurrent.futures import Future

import pytest
from PySide2.QtCore import Qt
from PySide2.QtWidgets import QMessageBox, QToolButton, QDialogButtonBox

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
    assert [b.text() for b in d.findChild(QDialogButtonBox).buttons()] == ['保留草稿并关闭']
    assert '保留草稿' in d.closeButton.text()
    assert '切换设备或关闭' in d.draftHelp.toolTip()
    assert 'Runtime' in d.draftHelp.toolTip()


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
    assert d.status.text() == (laterMessage or '在线状态已读取；工程草稿未改变')
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
