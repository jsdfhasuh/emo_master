"""Logical-size regressions; real-DPR desktop evidence is recorded separately."""
import pytest

from PySide2.QtCore import QRect
from PySide2.QtWidgets import QApplication, QDialogButtonBox

from emo_master.apps.designer.ui.theme import configureTheme
from tests.core.project.test_devices import declaration, definition  # noqa: F401
from tests.designer.test_device_workspace import windowFor, waitUntil


@pytest.mark.parametrize('width,height', [(642, 452), (826, 590), (1200, 760)])
def testDeviceWorkspaceKeepsActionsReachableAtSmallLogicalSize(
        ownedDesignerWindow, tmp_path, definition, declaration, width, height):  # noqa: F811
    configureTheme(QApplication.instance())
    window, _ = windowFor(ownedDesignerWindow, tmp_path, definition, declaration)
    c = window.pageCoordinator
    c.showDevices()
    d = c.devices
    waitUntil(lambda: d.pending is None)
    d.table.setCurrentCell(0, 0)
    d.resize(width, height)
    QApplication.processEvents()
    assert d.width() <= width and d.height() <= height
    buttons = d.findChild(QDialogButtonBox)
    assert buttons is not None
    for button in buttons.buttons():
        area = QRect(button.mapTo(d, button.rect().topLeft()), button.size())
        assert d.rect().contains(area), (button.text(), area, d.size())
        assert button.isVisibleTo(d)
    # Keeping the footer fixed must not make the lower device details unreachable.
    d.contentScroll.ensureWidgetVisible(d.infoTabs)
    QApplication.processEvents()
    assert d.contentScroll.viewport().rect().intersects(QRect(
        d.infoTabs.mapTo(d.contentScroll.viewport(), d.infoTabs.rect().topLeft()), d.infoTabs.size()))
    before = c.session.payload()
    d.closeButton.click()
    assert not d.isVisible() and c.session.payload() == before
