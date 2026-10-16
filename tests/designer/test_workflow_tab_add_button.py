"""Navigation commands have fixed layout slots independent of real tabs."""
from types import SimpleNamespace

import pytest
import emo_master  # noqa: F401 - preload the Windows dependency DLLs before Qt
from PySide2.QtCore import QPoint, Qt
from PySide2.QtTest import QTest
from PySide2.QtWidgets import QInputDialog, QToolButton

from emo_master.apps.designer.ui.main_window import MainWindow
from emo_master.apps.designer.ui.widgets import WorkflowTabs


def settle(app):
    for _ in range(5):
        app.processEvents()


def makeTabs(app, titles, width, direction=Qt.LeftToRight):
    tabs = WorkflowTabs()
    tabs.setObjectName('workflowTabs')
    tabs.setLayoutDirection(direction)
    for index, title in enumerate(titles):
        tabs.addWorkflow(str(index), title)
    tabs.resize(width, tabs.sizeHint().height())
    tabs.show()
    settle(app)
    return tabs


@pytest.mark.parametrize('titles', [['Main'], ['Main', 'Body'], ['很长的工作流名称 ' * 12]])
@pytest.mark.parametrize('direction', [Qt.LeftToRight, Qt.RightToLeft])
def testCommandsHaveFixedSlotsSeparateFromTabs(designerApplication, titles, direction):
    tabs = makeTabs(designerApplication, titles, 1000, direction)
    bar = tabs.tabBar()
    rect = bar.tabRect(len(titles) - 1)
    rect.translate(bar.mapTo(tabs, QPoint()))
    button = tabs._addButton.geometry()
    assert not button.intersects(bar.geometry())
    assert not tabs._allButton.geometry().intersects(bar.geometry())
    assert button.right() == tabs.width() - 1 if direction == Qt.LeftToRight else button.left() == 0
    assert abs(button.center().y() - rect.center().y()) <= 2
    assert tabs.rect().contains(button)
    assert tabs._addButton.visibleRegion().boundingRect() == tabs._addButton.rect()
    assert bar.count() == len(titles) and all(bar.isTabVisible(i) for i in range(bar.count()))


@pytest.mark.parametrize('width', [180, 320, 520])
@pytest.mark.parametrize('direction', [Qt.LeftToRight, Qt.RightToLeft])
def testOverflowReservesButtonAndKeepsScrollControlsSeparate(designerApplication, width, direction):
    titles = ['Camera Capture 中文流程 ' + str(i) for i in range(12)]
    tabs = makeTabs(designerApplication, titles, width, direction)
    bar, button = tabs.tabBar(), tabs._addButton
    for index in (0, 11, 4, 0):
        tabs.setCurrentIndex(index)
        settle(designerApplication)
        assert tabs.rect().contains(button.geometry())
        assert not button.geometry().intersects(bar.geometry())
        arrows = [b for b in bar.findChildren(QToolButton) if b.isVisible()]
        assert arrows
        for arrow in arrows:
            rect = arrow.geometry()
            rect.moveTopLeft(arrow.mapTo(tabs, QPoint()))
            assert not rect.intersects(button.geometry())
        assert button.visibleRegion().boundingRect() == button.rect()
    clicks = []
    tabs.addRequested.connect(lambda: clicks.append('add'))
    QTest.mouseClick(button, Qt.LeftButton)
    assert clicks == ['add'] and tabs.currentIndex() == 0


def testResizeRenameRemovalAndClearRecomputeButtonWithoutCallbacks(designerApplication):
    tabs = makeTabs(designerApplication, ['Main', 'Body'], 1000)
    bar = tabs.tabBar()
    original = tabs._addButton.x()
    tabs.setTabText(1, '子流程长名称 ' * 20)
    settle(designerApplication)
    assert tabs._addButton.x() == original
    tabs.resize(200, tabs.height())
    settle(designerApplication)
    assert tabs.rect().contains(tabs._addButton.geometry())
    tabs.resize(1000, tabs.height())
    tabs.removeTab(1)
    settle(designerApplication)
    assert tabs._addButton.x() == original
    assert not tabs._addButton.geometry().intersects(bar.geometry())
    clicks = []
    tabs.addRequested.connect(lambda: clicks.append('add'))
    tabs.clear()
    QTest.mouseClick(tabs._addButton, Qt.LeftButton)
    assert clicks == ['add']
    tabs.addWorkflow('reloaded', 'Reloaded')
    settle(designerApplication)
    QTest.mouseClick(tabs._addButton, Qt.LeftButton)
    assert clicks == ['add', 'add']
    tabs.close()


@pytest.mark.parametrize('accepted', [False, True])
def testRealWindowAddButtonKeepsWorkflowIdentityAndCancelBehavior(designerApplication, monkeypatch, accepted):
    client = SimpleNamespace(listOperators=lambda: [])
    prefs = {}
    settings = SimpleNamespace(value=lambda k, d=None: prefs.get(k, d), setValue=lambda k, v: prefs.update({k: v}))
    window = MainWindow(client, settingsStore=settings)
    window.resize(1280, 720)
    window.show()
    settle(designerApplication)
    monkeypatch.setattr(QInputDialog, 'getText', lambda *a, **k: ('Body', accepted))
    original = window.getActiveWorkflowId()
    QTest.mouseClick(window.workflowTabs._addButton, Qt.LeftButton)
    settle(designerApplication)
    assert [w['name'] for w in window.getWorkflowTabs()] == (['Main', 'Body'] if accepted else ['Main'])
    assert window.getActiveWorkflowId() == ('body' if accepted else original)
    assert window.workflowTabs.tabBar().tabData(window.workflowTabs.currentIndex()) == window.getActiveWorkflowId()
    assert window.currentJobId is None
