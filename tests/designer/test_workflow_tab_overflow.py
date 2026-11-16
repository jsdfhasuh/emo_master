"""Overflow counts and bidirectional browsing of fitted workflow tabs."""
import pytest
from PySide2.QtCore import QPoint, QPointF, Qt
from PySide2.QtGui import QWheelEvent
from PySide2.QtWidgets import QApplication

from tests.designer.test_workflow_navigation import makeTabs, settle
from tests.designer.test_workflow_entry_markers import readableNativeFont  # noqa: F401


def wheel(bar, *, angle=QPoint(), pixel=QPoint(), phase=Qt.NoScrollPhase):
    local = QPointF(bar.rect().center())
    event = QWheelEvent(local, QPointF(bar.mapToGlobal(local.toPoint())), pixel,
                        angle, Qt.NoButton, Qt.NoModifier, phase, False)
    QApplication.sendEvent(bar, event)
    settle()


def assertOverflow(tabs):
    bar = tabs.tabBar()
    visible = [i for i in range(bar.count()) if bar.isTabVisible(i)]
    hidden = bar.count() - len(visible)
    assert hidden > 0
    assert tabs._allButton.text() == f'+{hidden}'
    assert f'前面 {visible[0]} 个' in tabs._allButton.toolTip()
    assert f'后面 {bar.count() - visible[-1] - 1} 个' in tabs._allButton.toolTip()
    assert tabs._allButton.width() >= tabs._allButton.sizeHint().width()
    assert all(bar.rect().contains(bar.tabRect(i)) for i in visible)
    assert bar.isTabVisible(bar.currentIndex())
    left = min(bar.tabRect(i).left() for i in visible)
    right = max(bar.tabRect(i).right() for i in visible)
    assert left == 0
    assert right == bar.width() - 1
    assert sum(bar.tabRect(i).width() for i in visible) == bar.width()
    assert not bar.geometry().intersects(tabs._allButton.geometry())


@pytest.mark.parametrize('width', [380, 810, 1129, 1864])
@pytest.mark.parametrize('direction', [Qt.LeftToRight, Qt.RightToLeft])
def testOverflowCountAndTabsFillWidthWhileBrowsingBothWays(width, direction):
    tabs = makeTabs(width, direction)
    bar = tabs.tabBar()
    tabs.setCurrentIndex(15)
    settle()
    assertOverflow(tabs)
    buttonWidth = tabs._allButton.width()
    for delta in [120] * 8 + [-120] * 16 + [120] * 8:
        before = tabs.currentIndex()
        wheel(bar, angle=QPoint(delta, 0))
        step = -1 if delta > 0 else 1
        if direction == Qt.RightToLeft:
            step = -step
        assert tabs.currentIndex() == max(0, min(tabs.count() - 1, before + step))
        assertOverflow(tabs)
        assert tabs._allButton.width() == buttonWidth


@pytest.mark.parametrize('direction', [Qt.LeftToRight, Qt.RightToLeft])
def testPixelOnlySwipesHonorDirectionAndEmptyEndDoesNotSelect(direction):
    tabs = makeTabs(810, direction)
    bar = tabs.tabBar()
    tabs.setCurrentIndex(15)
    step = -1 if direction == Qt.LeftToRight else 1
    wheel(bar, pixel=QPoint(40, 0), phase=Qt.ScrollUpdate)
    assert tabs.currentIndex() == 15 + step
    wheel(bar, pixel=QPoint(-40, 0), phase=Qt.ScrollUpdate)
    assert tabs.currentIndex() == 15
    wheel(bar, phase=Qt.ScrollEnd)
    assert tabs.currentIndex() == 15
    assertOverflow(tabs)


@pytest.mark.parametrize('index, delta', [(0, 120), (28, -120)])
def testWheelStopsAtBoundsWithoutBlankTabsOrSelectionChange(index, delta):
    tabs = makeTabs(810)
    bar = tabs.tabBar()
    tabs.setCurrentIndex(index)
    changes = []
    tabs.currentChanged.connect(changes.append)
    for _ in range(3):
        wheel(bar, angle=QPoint(0, delta))
        assert tabs.currentIndex() == index
        assertOverflow(tabs)
    assert not changes


def testNativeOverflowStripScreenshot(tmp_path):
    import os
    from pathlib import Path

    tabs = makeTabs(1129)
    tabs.setCurrentIndex(15)
    settle()
    assertOverflow(tabs)
    out = Path(os.environ.get('EMO_NAV_QA_DIR', str(tmp_path)))
    out.mkdir(parents=True, exist_ok=True)
    assert tabs.grab().save(str(out / 'tabs-overflow-count.png'))


def testOverflowCountRefreshesForResizeRenameMarkersAndRemoval():
    tabs = makeTabs(810)
    bar = tabs.tabBar()
    for width in (380, 1129, 810):
        tabs.resize(width, tabs.height())
        tabs.setTabText(15, 'Long workflow title ' * 12)
        tabs.setRuntimeStatus(15, 'WAITING_CHILD')
        tabs.setCurrentIndex(15)
        settle()
        assertOverflow(tabs)
    tabs.syncWorkflows([('first', 'First'), ('last', 'Last')], 'last')
    settle()
    assert tabs._allButton.text() == '2'
    assert all(bar.isTabVisible(i) for i in range(2))
    assert not bar.expanding()
    assert sum(bar.tabRect(i).width() for i in range(2)) < bar.width()
    tabs.clear()
    settle()
    assert tabs._allButton.text() == ''
    tabs.addWorkflow('only', 'Only')
    settle()
    assert tabs._allButton.text() == ''
    assert tabs._allButton.width() == tabs._addButton.width()
