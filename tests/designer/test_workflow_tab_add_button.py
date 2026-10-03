"""The workflow add button follows real tabs and remains usable on overflow."""
from types import SimpleNamespace

import pytest
import emo_master  # noqa: F401 - preload the Windows dependency DLLs before Qt
from PySide2.QtCore import QPoint, Qt
from PySide2.QtTest import QTest
from PySide2.QtWidgets import QInputDialog, QToolButton, QWidget

from emo_master.apps.designer.ui.main_window import MainWindow
from emo_master.apps.designer.ui.widgets import WorkflowTabs


def settle(app):
    for _ in range(5):
        app.processEvents()


def makeTabs(app, titles, width, direction=Qt.LeftToRight):
    tabs = WorkflowTabs()
    tabs.setObjectName('workflowTabs')
    tabs.setLayoutDirection(direction)
    for title in titles:
        tabs.addTab(QWidget(), title)
    tabs.addTab(QWidget(), '+')
    tabs.resize(width, tabs.sizeHint().height())
    tabs.show()
    settle(app)
    return tabs


@pytest.mark.parametrize('titles', [['Main'], ['Main', 'Body'], ['很长的工作流名称 ' * 12]])
@pytest.mark.parametrize('direction', [Qt.LeftToRight, Qt.RightToLeft])
def testAddButtonFollowsLastTabWhenTabsFit(designerApplication, titles, direction):
    tabs = makeTabs(designerApplication, titles, 1000, direction)
    bar = tabs.tabBar()
    rect = bar.tabRect(len(titles) - 1)
    rect.translate(bar.mapTo(tabs, QPoint()))
    button = tabs._addButton.geometry()
    gap = button.left() - rect.right() - 1 if direction == Qt.LeftToRight else rect.left() - button.right() - 1
    assert 0 <= gap <= 8, (rect, button, gap)
    assert abs(button.center().y() - rect.center().y()) <= 2
    assert tabs.rect().contains(button)
    assert tabs._addButton.visibleRegion().boundingRect() == tabs._addButton.rect()
    assert not bar.isTabVisible(len(titles))


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
    tabs.tabBarClicked.connect(clicks.append)
    QTest.mouseClick(button, Qt.LeftButton)
    assert clicks == [12] and tabs.currentIndex() == 0


def testResizeRenameRemovalAndClearRecomputeButtonWithoutCallbacks(designerApplication):
    tabs = makeTabs(designerApplication, ['Main', 'Body'], 1000)
    bar = tabs.tabBar()
    original = tabs._addButton.x()
    tabs.setTabText(1, '子流程长名称 ' * 20)
    settle(designerApplication)
    assert tabs._addButton.x() > original
    tabs.resize(200, tabs.height())
    settle(designerApplication)
    assert tabs.rect().contains(tabs._addButton.geometry())
    tabs.resize(1000, tabs.height())
    tabs.removeTab(1)
    settle(designerApplication)
    assert 0 <= tabs._addButton.x() - bar.geometry().left() - bar.tabRect(0).right() - 1 <= 8
    clicks = []
    tabs.tabBarClicked.connect(clicks.append)
    tabs.clear()
    QTest.mouseClick(tabs._addButton, Qt.LeftButton)
    assert clicks == []
    tabs.addTab(QWidget(), 'Reloaded')
    tabs.addTab(QWidget(), '+')
    settle(designerApplication)
    QTest.mouseClick(tabs._addButton, Qt.LeftButton)
    assert clicks == [1]
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
