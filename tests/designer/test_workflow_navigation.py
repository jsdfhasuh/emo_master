"""Real overflow navigation, not just programmatic selection of offscreen tabs."""
from copy import deepcopy
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide2.QtCore import QEventLoop, QSettings, QSize, Qt, QTimer
from PySide2.QtTest import QTest
from PySide2.QtWidgets import QStackedWidget, QStyle, QStyleOptionTab, QTabBar, QToolButton

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


@pytest.mark.parametrize('width', [380, 810, 1129])
@pytest.mark.parametrize('direction', [Qt.LeftToRight, Qt.RightToLeft])
def testAll29TabsReachableByPickerClicksAndBrowsingDoesNotChangeSelection(width, direction):
    tabs = makeTabs(width, direction)
    assert tabs.count() == 29 and tabs.findChild(QStackedWidget) is None
    bar = tabs.tabBar()
    changes = []
    tabs.currentChanged.connect(changes.append)
    for index, (workflowId, title) in enumerate(WORKFLOWS):
        before = tabs.currentIndex(), list(changes)
        QTest.mouseClick(tabs._allButton, Qt.LeftButton)
        settle()
        picker = tabs._picker
        assert picker.isVisible() and picker.list.count() == 29
        picker.search.setText(workflowId)
        settle()
        item = picker.list.currentItem()
        assert item.data(Qt.UserRole) == workflowId and title in item.text()
        assert (tabs.currentIndex(), changes) == before
        QTest.mouseClick(picker.list.viewport(), Qt.LeftButton,
                         pos=picker.list.visualItemRect(item).center())
        settle()
        assert not picker.isVisible()
        assert tabs.currentIndex() == index and bar.tabData(index) == workflowId
        assert bar.rect().contains(bar.tabRect(index))
        assert all(bar.rect().contains(bar.tabRect(i))
                   for i in range(bar.count()) if bar.isTabVisible(i))
        assert all(button.visibleRegion().isEmpty() for button in bar.findChildren(QToolButton))
    assert changes == list(range(1, 29))


@pytest.mark.parametrize('direction', [Qt.LeftToRight, Qt.RightToLeft])
def testKeyboardSelectionScrollsCurrentTabIntoViewWithoutArrowButtons(direction):
    tabs = makeTabs(380, direction)
    bar = tabs.tabBar()
    bar.setFocus()
    forward = Qt.Key_Right if direction == Qt.LeftToRight else Qt.Key_Left
    backward = Qt.Key_Left if direction == Qt.LeftToRight else Qt.Key_Right
    for index in range(1, 29):
        QTest.keyClick(bar, forward)
        settle()
        assert tabs.currentIndex() == index
        assert bar.rect().contains(bar.tabRect(index))
    QTest.keyClick(bar, backward)
    settle()
    assert tabs.currentIndex() == 27 and bar.rect().contains(bar.tabRect(27))


@pytest.mark.parametrize('width', [380, 810, 1200])
def testCompactCompletionMarkersAndStatusTransitionsRenderWithoutMovingSelection(tmp_path, width):
    tabs = WorkflowTabs()
    tabs.setObjectName('workflowTabs')
    titles = ['主流程', '取帧并检测人和两轮车', '逐人判断骑乘', '人车位置关系筛选',
              '检查人车上下位置', '载人物并传递下游']
    for index, title in enumerate(titles):
        tabs.addWorkflow(str(index), title)
        tabs.setRuntimeStatus(index, 'COMPLETED', 'Job sample')
    tabs.setEntryMarker(0, '默认入口', '主流程：工程默认入口')
    tabs.resize(width, tabs.sizeHint().height())
    tabs.show()
    settle()
    bar = tabs.tabBar()
    out = Path(os.environ.get('EMO_NAV_QA_DIR', str(tmp_path)))
    out.mkdir(parents=True, exist_ok=True)
    assert tabs.grab().save(str(out / f'tabs-completed-{width}.png'))
    visible = [i for i in range(bar.count()) if bar.isTabVisible(i)]
    if len(visible) > 1:
        index = visible[1]
        badge = bar.tabButton(index, QTabBar.RightSide)
        QTest.mouseClick(bar, Qt.LeftButton, pos=badge.geometry().center())
        assert tabs.currentIndex() == index
    tabs.selectWorkflow('5')
    settle()
    changes = []
    tabs.currentChanged.connect(changes.append)
    for status in ['RUNNING', 'WAITING_CHILD', 'FAILED', 'COMPLETED', '']:
        tabs.setRuntimeStatus(5, status, 'Job sample' if status else '')
        settle()
        assert tabs.currentIndex() == 5 and not changes
        assert bar.isTabVisible(5) and bar.rect().contains(bar.tabRect(5))
        assert all(bar.rect().contains(bar.tabRect(i))
                   for i in range(bar.count()) if bar.isTabVisible(i))
    assert '已完成' not in bar.accessibleTabName(5)
    assert 'Job sample' not in tabs.tabToolTip(5)
    for index, status in enumerate(['WAITING_CHILD', 'RUNNING', 'COMPLETED', 'FAILED', 'ABORTED', '']):
        tabs.setRuntimeStatus(index, status, 'Job sample' if status else '')
    tabs.setCurrentIndex(0)
    settle()
    assert tabs.grab().save(str(out / f'tabs-mixed-{width}.png'))
    QTest.mouseClick(tabs._allButton, Qt.LeftButton)
    settle()
    assert tabs._picker.grab().save(str(out / f'picker-statuses-{width}.png'))
    tabs._picker.hide()


@pytest.mark.parametrize('width', [380, 810])
@pytest.mark.parametrize('status', ['', 'COMPLETED', 'WAITING_CHILD'])
def testLongSelectedNamesElideInsideStyledTextArea(width, status):
    tabs = makeTabs(width)
    bar = tabs.tabBar()
    for index in [0, 12, 28]:
        name = '很长的工作流名称 Sample - navigation and coordinate processing' * 3
        tabs.setTabText(index, name)
        tabs.setRuntimeStatus(index, status)
        tabs.setCurrentIndex(index)
        settle()
        option = QStyleOptionTab()
        bar.initStyleOption(option, index)
        textRect = bar.style().subElementRect(QStyle.SE_TabBarTabText, option, bar)
        padding = bar.style().sizeFromContents(QStyle.CT_TabBarTab, option, QSize(0, 0), bar).width()
        assert option.text.endswith('…')
        assert option.fontMetrics.horizontalAdvance(option.text) <= textRect.width() - padding
        assert tabs.tabText(index) == name
        assert bar.rect().contains(bar.tabRect(index))
        # Inspect rendered text too: stylesheet font substitution can clip a
        # label even when the option's own metrics claim it fits.
        textRect.adjust(padding // 2, 0, -(padding - padding // 2), 0)
        pixels = bar.grab().toImage()
        dpr = pixels.devicePixelRatio()
        inkColumns = [x for x in range(round(textRect.left() * dpr), round((textRect.right() + 1) * dpr))
                      if any((lambda c: c.red() < 100 and c.green() < 100 and c.blue() < 130)(pixels.pixelColor(x, y))
                             for y in range(round(textRect.top() * dpr), round((textRect.bottom() + 1) * dpr)))]
        assert inkColumns
        assert max(inkColumns) - min(inkColumns) + 1 <= option.fontMetrics.horizontalAdvance(option.text) * dpr + 2


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
            assert bar.rect().contains(bar.tabRect(index))
            first = next(bar.tabRect(i) for i in range(bar.count()) if bar.isTabVisible(i))
            assert first.right() == bar.width() - 1 if direction == Qt.RightToLeft else first.left() == 0
            assert all(b.visibleRegion().isEmpty() for b in bar.findChildren(QToolButton))
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


@pytest.mark.parametrize(('status', 'label'), [
    ('RUNNING', '执行中'),
    ('WAITING_CHILD', '等待子流程'),
    ('COMPLETED', '已完成'),
])
def testPickerSearchByIdKeyboardEmptyResultsEscapeAndFullName(status, label):
    tabs = makeTabs(810)
    tabs.setRuntimeStatus(12, status, 'station2 job')
    badge = tabs.tabBar().tabButton(12, QTabBar.RightSide)
    assert badge.accessibleName() == label
    if status == 'COMPLETED':
        assert badge.text() == '' and not badge.pixmap().isNull()
    else:
        assert badge.text() == label
    before = tabs.currentIndex()
    QTest.mouseClick(tabs._allButton, Qt.LeftButton)
    settle()
    picker = tabs._picker
    assert picker.isVisible() and picker.list.count() == 29
    picker.search.setText('station2-frame')
    visible = [picker.list.item(i) for i in range(29) if not picker.list.item(i).isHidden()]
    assert len(visible) == 1
    assert label in visible[0].text() and WORKFLOWS[12][1] in visible[0].text()
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
        first = next(tabs.tabBar().tabRect(i) for i in range(tabs.count()) if tabs.tabBar().isTabVisible(i))
        assert first.left() == 0
        assert tabs.tabBar().rect().contains(tabs.tabBar().tabRect(index))
        assert all(b.visibleRegion().isEmpty() for b in tabs.tabBar().findChildren(QToolButton))
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
