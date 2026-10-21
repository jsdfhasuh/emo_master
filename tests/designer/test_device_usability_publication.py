"""Device observation yields to the existing publication dialog, without writing."""
from concurrent.futures import Future

import pytest
from PySide2.QtCore import Qt
from PySide2.QtWidgets import QApplication

from tests.core.project.test_devices import declaration, definition  # noqa: F401
from tests.designer.test_device_workspace import windowFor, waitUntil
from tests.designer.test_project_configuration_ui import setupDialog


def testShortcutValidatesThenHidesAndRestoresSameObserver(ownedDesignerWindow, tmp_path,
        definition, declaration, monkeypatch):  # noqa: F811
    window, _ = windowFor(ownedDesignerWindow, tmp_path, definition, declaration)
    c = window.pageCoordinator
    c.showDevices()
    d = c.devices
    waitUntil(lambda: d.pending is None)
    d.table.setCurrentCell(0, 0)
    calls = []
    def configure():
        assert not d.isVisible()
        calls.append(c.session.payload())
    monkeypatch.setattr(c, 'configureRuntime', configure)
    source = tmp_path / 'device.emoproj'
    disk = source.read_bytes()
    d.name.clear()
    d.buttons['publish'].click()
    QApplication.processEvents()
    assert d.isVisible() and not calls and d.name.hasFocus()
    d.name.setText('未保存但可发布')
    d.buttons['publish'].click()
    waitUntil(lambda: bool(calls) and d.isVisible())
    assert c.devices is d and d.selectedId == 'camera'
    assert calls[0]['devices']['definitions']['camera']['name'] == '未保存但可发布'
    assert c.session.dirty
    assert source.read_bytes() == disk
    d.pending = Future()
    d.pendingKind = 'control'
    c.configureFromDevices()
    assert len(calls) == 1 and d.isVisible()
    d.pending = None


@pytest.mark.parametrize('offset', [0, 1])
def testPublicationReturnPreservesDeviceListViewport(ownedDesignerWindow, tmp_path,
        definition, declaration, monkeypatch, offset):  # noqa: F811
    window, _ = windowFor(ownedDesignerWindow, tmp_path, definition, declaration)
    c = window.pageCoordinator
    with c.session.transaction():
        for index in range(24):
            c.session.workflows.devices['definitions'][f'copy-{index}'] = definition.model_copy(
                update={'name': f'导入副本 {index + 1}', 'enabled': False}).model_dump()
    c.showDevices()
    d = c.devices
    waitUntil(lambda: d.pending is None)
    d.table.setCurrentCell(0, 0)
    for column in range(d.table.columnCount()):
        d.table.setColumnWidth(column, 180)
    QApplication.processEvents()
    bar = d.table.horizontalScrollBar()
    assert bar.maximum() > offset
    bar.setValue(offset)
    vertical = d.table.verticalScrollBar()
    assert vertical.maximum() > 1
    vertical.setValue(vertical.maximum() // 2)
    tablePosition = vertical.value()
    treeBar = d.tree.verticalScrollBar()
    treeBar.setValue(treeBar.maximum())
    treePosition = treeBar.value()
    calls = []
    monkeypatch.setattr(c, 'configureRuntime', lambda: calls.append(True))
    d.buttons['publish'].click()
    waitUntil(lambda: bool(calls) and d.isVisible())
    QApplication.processEvents()
    assert d.selectedId == 'camera'
    assert bar.value() == offset
    assert vertical.value() == tablePosition
    assert treeBar.value() == treePosition


def testReadableDeviceDiffRetainsExactPathAndBoundedRows(ownedDesignerWindow, tmp_path):
    window, d, calls, _ = setupDialog(ownedDesignerWindow, tmp_path)
    try:
        row = next(row for row in range(d.table.rowCount())
            if d.table.item(row, 0).data(Qt.UserRole) == '/devices/definitions/camera/defaults/exposureUs')
        assert 'camera' not in d.table.item(row, 0).text() or not d.table.item(row, 0).text().startswith('/')
        assert d.local['devices']['definitions']['camera']['name'] in d.table.item(row, 0).text()
        assert '12000' in d.table.item(row, 2).text()
        d.table.item(row, 0).setCheckState(Qt.Checked)
        d.validationSelection = (['/devices/definitions/camera/defaults/exposureUs'], 'reject')
        d._showEffects(dict(changeEffects={'/devices/definitions/camera/defaults/exposureUs':
            dict(effect='next_cycle', devices=['camera']*100)}))
        assert d.table.rowHeight(row) <= 48
        d.table.setCurrentCell(row, 0)
        assert '/devices/definitions/camera/defaults/exposureUs' in d.details.toPlainText()
        assert not calls
    finally:
        d.reject()
