"""Real overflow navigation, not just programmatic selection of offscreen tabs."""
from copy import deepcopy
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide2.QtCore import QEventLoop, QSettings, Qt, QTimer
from PySide2.QtTest import QTest
from PySide2.QtWidgets import QStackedWidget, QTabBar, QToolButton

from emo_master.apps.designer.state.project_store import saveProject
from emo_master.apps.designer.ui.widgets import WorkflowTabs
from tests.designer.test_workflow_entry_markers import readableNativeFont  # noqa: F401
from tests.runtime.test_workflow_debug_control import nestedProject
from tests.designer.navigation_fixture import navigationPayload, saveNavigationProject


PAYLOAD = navigationPayload()
WORKFLOWS = [(key, PAYLOAD['workflows'][key]['name']) for key in PAYLOAD['workflowOrder']]


def settle():
    loop = QEventLoop()
    QTimer.singleShot(50, loop.quit)
    loop.exec_()


def makeTabs(width, direction=Qt.LeftToRight):
    tabs = WorkflowTabs()
    tabs.setObjectName('workflowTabs')
    tabs.setLayoutDirection(direction)
    tabs.syncWorkflows(WORKFLOWS, 'station1-run')
    tabs.setEntryMarker(0, '默认入口', '默认入口：工位1')
    tabs.resize(width, tabs.sizeHint().height())
    tabs.show()
    settle()
    return tabs


def arrow(bar, direction):
    return next(button for button in bar.findChildren(QToolButton) if button.arrowType() == direction)


def browseToEnd(tabs, direction):
    bar = tabs.tabBar()
    button = arrow(bar, direction)
    assert button.isVisible() and button.isEnabled()
    for _ in range(150):
        if not button.isEnabled():
            break
        before = bar.tabRect(0).x()
        QTest.mouseClick(button, Qt.LeftButton)
        settle()
        assert bar.tabRect(0).x() != before, 'enabled arrow must move the strip'
    assert not button.isEnabled(), 'must reach an actual end, not loop forever'
    return bar


@pytest.mark.parametrize('width', [380, 810, 1129])
@pytest.mark.parametrize('direction', [Qt.LeftToRight, Qt.RightToLeft])
def testAll29TabsReachableByRealArrowClicksWithoutChangingSelection(width, direction):
    tabs = makeTabs(width, direction)
    assert tabs.count() == 29 and tabs.findChild(QStackedWidget) is None
    changes = []
    tabs.currentChanged.connect(changes.append)
    forward = Qt.RightArrow if direction == Qt.LeftToRight else Qt.LeftArrow
    backward = Qt.LeftArrow if direction == Qt.LeftToRight else Qt.RightArrow
    bar = browseToEnd(tabs, forward)
    assert bar.rect().contains(bar.tabRect(28).center())
    assert tabs.currentIndex() == 0 and not changes
    browseToEnd(tabs, backward)
    assert bar.rect().contains(bar.tabRect(0).center())
    assert tabs.currentIndex() == 0 and not changes
    # Browse again, then select by actual mouse hit on the last workflow.
    browseToEnd(tabs, forward)
    QTest.mouseClick(bar, Qt.LeftButton, pos=bar.tabRect(28).center())
    assert bar.tabData(tabs.currentIndex()) == WORKFLOWS[-1][0]
    assert changes == [28]


@pytest.mark.parametrize('direction', [Qt.LeftToRight, Qt.RightToLeft])
def testResizingAndMarkerChangesKeepOverflowUsableAndSelectionStable(direction):
    tabs = makeTabs(1129, direction)
    bar = tabs.tabBar()
    for width in [380, 1129, 640, 1500, 380]:
        tabs.resize(width, tabs.height())
        settle()
        for index in [3, 4, 12, 28, 0]:
            tabs.setCurrentIndex(index)
            tabs.setRuntimeStatus(index, 'RUNNING', 'Job test: running')
            tabs.setRuntimeStatus(index, 'FAILED', 'Job test: failed')
            settle()
            assert bar.rect().contains(bar.tabRect(index).center())
            first = bar.tabRect(0)
            assert first.right() >= bar.width() - 1 if direction == Qt.RightToLeft else first.left() <= 0
            assert any(b.isEnabled() for b in bar.findChildren(QToolButton))
            assert not bar.geometry().intersects(tabs._allButton.geometry())
            assert not bar.geometry().intersects(tabs._addButton.geometry())
            assert tabs.currentIndex() == index


def testSyncUsesIdsAndPreservesUnchangedBadgesThroughRenameReorderAndDelete():
    tabs = makeTabs(810)
    bar = tabs.tabBar()
    tabs.setRuntimeStatus(12, 'RUNNING', 'station2 job')
    badge = bar.tabButton(12, QTabBar.RightSide)
    default = bar.tabButton(0, QTabBar.LeftSide)
    active = WORKFLOWS[12][0]
    tabs.selectWorkflow(active)
    changes = []
    tabs.currentChanged.connect(changes.append)
    rows = [WORKFLOWS[12], (WORKFLOWS[0][0], 'Renamed'), ('new-id', 'New')]
    tabs.syncWorkflows(rows, active)
    assert [bar.tabData(i) for i in range(tabs.count())] == [row[0] for row in rows]
    assert bar.tabButton(0, QTabBar.RightSide) is badge
    assert bar.tabButton(1, QTabBar.LeftSide) is default
    assert tabs.tabText(1) == 'Renamed'
    assert tabs.currentIndex() == 0 and not changes
    tabs.syncWorkflows(rows[1:], 'new-id')
    assert tabs.currentIndex() == 1 and tabs.indexForWorkflow(active) == -1 and not changes
    assert tabs.tabBar().tabButton(0, QTabBar.RightSide) is None


def testPickerSearchByIdKeyboardEmptyResultsEscapeAndFullName():
    tabs = makeTabs(810)
    tabs.setRuntimeStatus(12, 'RUNNING', 'station2 job')
    before = tabs.currentIndex()
    QTest.mouseClick(tabs._allButton, Qt.LeftButton)
    settle()
    picker = tabs._picker
    assert picker.isVisible() and picker.list.count() == 29
    picker.search.setText('station2-frame')
    visible = [picker.list.item(i) for i in range(29) if not picker.list.item(i).isHidden()]
    assert len(visible) == 1
    assert '执行中' in visible[0].text() and WORKFLOWS[12][1] in visible[0].text()
    QTest.keyClick(picker.search, Qt.Key_Return)
    settle()
    assert tabs.tabBar().tabData(tabs.currentIndex()) == 'station2-frame'
    assert not picker.isVisible() and tabs.currentIndex() != before
    tabs.selectWorkflow('station1-run')
    QTest.mouseClick(tabs._allButton, Qt.LeftButton)
    settle()
    assert picker.list.currentItem().data(Qt.UserRole) == 'station1-run'
    picker.search.setText('not-a-workflow')
    assert picker.empty.isVisible() and picker.list.currentItem() is None
    QTest.keyClick(picker.search, Qt.Key_Return)
    assert picker.isVisible() and tabs.currentIndex() == 0
    QTest.keyClick(picker.search, Qt.Key_Escape)
    settle()
    assert not picker.isVisible() and tabs.currentIndex() == 0


def testPickerRefreshesOpenRowsAfterRenameDeletionAndStatusWithoutStaleActivation():
    tabs = makeTabs(810)
    QTest.mouseClick(tabs._allButton, Qt.LeftButton)
    settle()
    picker = tabs._picker
    picker.search.setText('station2-frame')
    tabs.setRuntimeStatus(12, 'FAILED', 'latest job')
    assert '失败' in picker.list.currentItem().text()
    rows = [(key, 'Renamed' if key == 'station2-frame' else title) for key, title in WORKFLOWS]
    tabs.syncWorkflows(rows, 'station1-run')
    assert 'Renamed' in picker.list.currentItem().text()
    tabs.syncWorkflows([row for row in rows if row[0] != 'station2-frame'], 'station1-run')
    assert picker.list.currentItem() is None and picker.empty.isVisible()
    QTest.keyClick(picker.search, Qt.Key_Return)
    assert tabs.currentIndex() == 0
    picker.hide()


@pytest.fixture
def window(ownedDesignerWindow, tmp_path):
    def forbidden(*a, **k):
        raise AssertionError('navigation must not start Runtime or devices')
    w = ownedDesignerWindow(SimpleNamespace(listOperators=lambda: [], startJob=forbidden,
            loadProject=lambda _: SimpleNamespace(ok=True, message='local stub')),
            settingsStore=QSettings(str(tmp_path / 'settings.ini'), QSettings.IniFormat))
    w.show()
    settle()
    w.resize(1640, 900)
    settle()
    return w


def testPortableProjectRepeatedSwitchNeverClearsOrRebuildsTabs(window, tmp_path, monkeypatch):
    path = saveNavigationProject(tmp_path)
    original = path.read_bytes()
    assert window.loadProjectDirectory(str(path), confirmed=True)
    settle()
    tabs = window.workflowTabs
    assert tabs.count() == len(WORKFLOWS) == 29
    runs = {key: (id(run), run.jobId) for key, run in window.runtimeController.runs.items()}
    labels = dict(tabs._labels)
    entry = tabs.tabBar().tabButton(0, QTabBar.LeftSide)
    monkeypatch.setattr(window, '_refreshWorkflowTabs', lambda: pytest.fail('selection must not refresh the collection'))
    for index in [3, 0, 3, 4, 12, 28, 3]:
        window.activateWorkflow(WORKFLOWS[index][0])
        settle()
        assert tabs.currentIndex() == index
        assert tabs.tabBar().tabRect(0).left() <= 0
        assert any(b.isEnabled() for b in tabs.tabBar().findChildren(QToolButton))
        assert all(tabs._labels[key] is value for key, value in labels.items())
        assert tabs.tabBar().tabButton(0, QTabBar.LeftSide) is entry
    assert {key: (id(run), run.jobId) for key, run in window.runtimeController.runs.items()} == runs
    assert all(not run.jobId for run in window.runtimeController.runs.values())
    assert path.read_bytes() == original
    out = Path(os.environ.get('EMO_NAV_QA_DIR', str(tmp_path)))
    out.mkdir(parents=True, exist_ok=True)
    assert window.grab().save(str(out / 'designer-navigation.png'))
    QTest.mouseClick(tabs._allButton, Qt.LeftButton)
    settle()
    tabs._picker.search.setText('Station 2')
    assert tabs._picker.grab().save(str(out / 'workflow-picker.png'))
    tabs._picker.hide()


def testPureNavigationAndCancelKeepCleanProjectHistoryAndViewPreferences(window, tmp_path, monkeypatch):
    from PySide2.QtWidgets import QInputDialog
    path = tmp_path / 'clean.emoproj'
    saveProject(path, nestedProject())
    assert window.loadProjectDirectory(str(path), confirmed=True)
    session = window.pageCoordinator.session
    before = session.payload(), deepcopy(session._undo), deepcopy(session._redo), path.read_bytes()
    window.flowView.setZoomFactor(.24)
    for workflowId in ['child', 'main', 'child', 'main']:
        window.workflowTabs.selectWorkflow(workflowId)
        settle()
    monkeypatch.setattr(QInputDialog, 'getText', lambda *a, **k: ('No', False))
    QTest.mouseClick(window.workflowTabs._addButton, Qt.LeftButton)
    assert window.flowView.getZoomFactor() == pytest.approx(.24)
    assert (session.payload(), session._undo, session._redo, path.read_bytes()) == before
    assert not session.dirty and window.getActiveWorkflowId() == 'main'
    assert window.saveProjectToDirectory(str(path))
    assert window.loadProjectDirectory(str(path), confirmed=True)
    assert window.workflowTabs.count() == 2 and window.getActiveWorkflowId() == 'main'
