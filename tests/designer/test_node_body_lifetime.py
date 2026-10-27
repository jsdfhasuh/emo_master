"""Exercise native double-click delivery into real device editor and scene rebuild."""
import pytest
from PySide2.QtCore import QPointF, Qt
from PySide2.QtWidgets import QApplication
from PySide2.QtTest import QTest
import sys
from shiboken2 import isValid

from emo_master.apps.designer.ui.device_wizard import DeviceNodeDialog
from tests.designer.test_device_workspace import windowFor, waitUntil
from tests.core.project.test_devices import declaration, definition  # noqa: F401


@pytest.mark.parametrize('save', [True, False])
def testBodyDoubleClickDeviceEditLifetime(ownedDesignerWindow, tmp_path, monkeypatch, definition, declaration, save):  # noqa: F811
    window, requirements = windowFor(ownedDesignerWindow, tmp_path, definition, declaration)
    monkeypatch.setattr(window, '_requireOperatorCatalog', lambda: True)
    monkeypatch.setattr(window, '_operatorDefinition', lambda _: dict(deviceRequirements=requirements['vision.io.huaray_camera']))
    window.resize(1100, 800)
    window.show()
    QApplication.processEvents()
    item = window.flowScene._nodeItems['capture']
    item.setPos(150, 250)
    window.pageCoordinator.sync()
    window.focusGraphContent()
    QApplication.processEvents()
    assert item.scene() is window.flowScene
    before = window.flowModel.nodes['capture'].deviceBindings
    opened = []
    def edit(dialog):
        opened.append(dialog)
        waitUntil(lambda: dialog.pendingButton.isEnabled())
        if save:
            modes, form, *_ = dialog.sources['camera'].rows['exposureUs']
            modes.setCurrentIndex(modes.findData('constant'))
            form._controls['value'].setText('12000')
            dialog.accept()
            assert dialog.result() == dialog.Accepted, dialog.error.text()
            assert window.flowModel.nodes['capture'].deviceOverrides['camera']['exposureUs'] == 12000
            assert window.flowScene._nodeItems['capture'] is not item
        else:
            dialog.reject()
        return dialog.result()
    monkeypatch.setattr(DeviceNodeDialog, 'exec_', edit)
    errors = []
    monkeypatch.setattr(sys, 'excepthook', lambda *error: errors.append(error))
    position = window.flowView.mapFromScene(item.mapToScene(QPointF(65, 65)))
    QTest.mouseDClick(window.flowView.viewport(), Qt.LeftButton, Qt.NoModifier, position)
    QApplication.processEvents()
    assert len(opened) == 1
    assert not errors, errors
    assert isValid(item) is (not save)
    replacement = window.flowScene._nodeItems['capture']
    replacement.setSelected(True)
    replacement.setPos(replacement.pos() + QPointF(10, 10))
    assert replacement.isSelected()
    assert window.flowModel.nodes['capture'].deviceBindings == before


def testTitleDoubleClickStillRenames(ownedDesignerWindow, tmp_path, monkeypatch, definition, declaration):  # noqa: F811
    window, _ = windowFor(ownedDesignerWindow, tmp_path, definition, declaration)
    window.resize(1100, 800)
    window.show()
    item = window.flowScene._nodeItems['capture']
    item.setPos(150, 250)
    window.pageCoordinator.sync()
    window.focusGraphContent()
    QApplication.processEvents()
    position = window.flowView.mapFromScene(item.mapToScene(QPointF(65, 15)))
    QTest.mouseDClick(window.flowView.viewport(), Qt.LeftButton, Qt.NoModifier, position)
    assert item._titleProxy is not None
    field = item._titleProxy.widget()
    field.setText('Renamed camera node')
    QTest.keyClick(field, Qt.Key_Return)
    QApplication.processEvents()
    assert window.flowModel.nodes['capture'].displayName == 'Renamed camera node'
