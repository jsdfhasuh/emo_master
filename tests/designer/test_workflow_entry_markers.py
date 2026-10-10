"""Entry roles must remain readable independently of active tab or name length."""
from copy import deepcopy
from types import SimpleNamespace

import pytest
import emo_master  # noqa: F401 - preload ONNX Runtime before Qt on Windows
from PySide2.QtCore import QPoint, Qt
from PySide2.QtGui import QFontDatabase
from PySide2.QtTest import QTest
from PySide2.QtWidgets import QTabBar, QToolButton, QWidget

from emo_master.apps.designer.ui.main_window import MainWindow
from emo_master.apps.designer.ui.widgets import WorkflowTabs
from emo_master.ui.run_targets_dialog import RunTargetsDialog
from emo_master.ui.workflow_labels import workflowDisplayNames, workflowEntryMarker
from tests.designer.test_workflow_execution_state import event


@pytest.fixture(scope='module', autouse=True)
def readableNativeFont(designerApplication):
    from emo_master.apps.designer.ui.theme import uiFont
    fontId = QFontDatabase.addApplicationFont('C:/Windows/Fonts/msyh.ttc')
    families = QFontDatabase.applicationFontFamilies(fontId)
    oldFamily = getattr(designerApplication, '_designerFontFamily', None)
    oldFont = designerApplication.font()
    if families:
        designerApplication._designerFontFamily = families[0]
        designerApplication.setFont(uiFont())
    yield
    designerApplication._designerFontFamily = oldFamily
    designerApplication.setFont(oldFont)


def settle(app):
    for _ in range(5):
        app.processEvents()


def makeWindow():
    values = {}
    settings = SimpleNamespace(value=lambda key, default=None: values.get(key, default),
                               setValue=lambda key, value: values.__setitem__(key, value))
    return MainWindow(SimpleNamespace(listOperators=lambda: []), settingsStore=settings)


def badgeFor(window, workflowId):
    tabs = window.workflowTabs
    index = next(i for i in range(tabs.count()) if tabs.tabBar().tabData(i) == workflowId)
    return tabs.tabBar().tabButton(index, QTabBar.LeftSide)


def testRolesUseIdsNotNamesAndDuplicateNamesStayUnmodified():
    workflows = {'a': {'name': 'Main'}, 'b': {'name': 'Main'},
                 'body': {'name': '自称持续运行入口'}}
    original = deepcopy(workflows)
    assert workflowDisplayNames(workflows) == {'a': 'Main · a', 'b': 'Main · b',
                                             'body': '自称持续运行入口'}
    assert workflowEntryMarker('a', 'a', ['b']) == '默认入口'
    assert workflowEntryMarker('b', 'a', ['b']) == '运行入口'
    assert workflowEntryMarker('body', 'a', ['b']) == ''
    assert workflows == original


def testBadgesDistinguishDefaultSelectedAndDuplicateNames(designerApplication):
    window = makeWindow()
    duplicate = window.createWorkflow('Main')
    first = window.createWorkflow('A 持续运行')
    second = window.createWorkflow('B 持续运行')
    window.setEntryWorkflow(first)
    window.runTargetIds = [first, second]
    window._refreshWorkflowEntryMarkers()
    window.activateWorkflow(duplicate)
    assert window.workflowTabs.tabText(0) == 'Main · main'
    assert window.workflowTabs.tabText(1) == f'Main · {duplicate}'
    assert badgeFor(window, 'main') is None
    assert badgeFor(window, duplicate) is None
    assert badgeFor(window, first).text() == '默认入口'
    assert badgeFor(window, second).text() == '运行入口'
    assert '不表示已经运行' in window.workflowTabs.tabToolTip(2)
    assert window.workflowTabs.tabBar().accessibleTabName(3).startswith('运行入口 ')

    window.runTargetIds = [second]
    window._refreshWorkflowEntryMarkers()
    assert '未选为本次运行入口' in window.workflowTabs.tabToolTip(2)
    window.runTargetIds = []
    window._refreshWorkflowEntryMarkers()
    assert badgeFor(window, second) is None
    assert '本次运行入口：' in window.workflowTabs.tabToolTip(2)
    assert window.workflowStore.get('main').name == window.workflowStore.get(duplicate).name == 'Main'


def testChoosingTargetsRefreshesMarkersWithoutRebuildingOrDirtying(designerApplication, monkeypatch):
    window = makeWindow()
    other = window.createWorkflow('Other')
    before = window.pageCoordinator.session.document().model_dump()
    dirty = window.pageCoordinator.session.dirty
    pages = [window.workflowTabs.widget(i) for i in range(window.workflowTabs.count())]
    active = window.getActiveWorkflowId()

    def choose(dialog):
        assert dialog.entryWorkflowId == 'main'
        for index in range(dialog.targets.count()):
            item = dialog.targets.item(index)
            item.setCheckState(Qt.Checked if item.data(Qt.UserRole) == other else Qt.Unchecked)
        return True

    monkeypatch.setattr(RunTargetsDialog, 'exec_', choose)
    window.openRunTargetsDialog()
    assert window.runTargetIds == [other]
    assert window.getActiveWorkflowId() == active
    assert [window.workflowTabs.widget(i) for i in range(window.workflowTabs.count())] == pages
    assert window.pageCoordinator.session.document().model_dump() == before
    assert window.pageCoordinator.session.dirty == dirty
    assert badgeFor(window, 'main').text() == '默认入口'
    assert badgeFor(window, other).text() == '运行入口'
    assert '未选为本次运行入口' in window.workflowTabs.tabToolTip(0)

    def cancel(dialog):
        dialog.targets.item(0).setCheckState(Qt.Checked)
        return False

    monkeypatch.setattr(RunTargetsDialog, 'exec_', cancel)
    window.openRunTargetsDialog()
    assert window.runTargetIds == [other]
    assert '未选为本次运行入口' in window.workflowTabs.tabToolTip(0)


def testDialogShowsDefaultAndUpdatesRoleOnCheck(designerApplication, tmp_path):
    dialog = RunTargetsDialog({'a': {'name': 'Main'}, 'b': {'name': 'Main'},
                               'c': {'name': 'Processing'}}, ['a', 'b'], 4, entryWorkflowId='a')
    a, b, c = [dialog.targets.item(i) for i in range(3)]
    assert a.text() == '【默认入口 · 已选】 Main  ·  a'
    assert b.text() == '【运行入口】 Main  ·  b'
    assert c.text() == 'Processing  ·  c'
    a.setCheckState(Qt.Unchecked)
    b.setCheckState(Qt.Unchecked)
    c.setCheckState(Qt.Checked)
    assert '默认入口 · 未选' in a.text()
    assert b.text() == 'Main  ·  b' and not b.font().bold()
    assert c.text().startswith('【运行入口】')
    assert dialog.selectedWorkflowIds() == ['c']
    dialog.show()
    settle(designerApplication)
    assert dialog.grab().save(str(tmp_path / 'entry-dialog.png'))


@pytest.mark.parametrize('width', [320, 640, 1060])
def testLongTabNamesNeverElideEntryBadgeAndBadgeClickSelectsTab(designerApplication, tmp_path, width):
    QFontDatabase.addApplicationFont('C:/Windows/Fonts/msyh.ttc')
    tabs = WorkflowTabs()
    tabs.setObjectName('workflowTabs')
    for i, role in enumerate(('', '默认入口', '运行入口')):
        title = '很长的工作流名称 Camera Capture ' * 5
        tabs.addTab(QWidget(), title)
        tabs.setEntryMarker(i, role, f'{role}\n{title}')
    tabs.addTab(QWidget(), '+')
    tabs.resize(width, tabs.sizeHint().height())
    tabs.show()
    bar = tabs.tabBar()
    for index in (1, 2, 1):
        tabs.setCurrentIndex(index)
        settle(designerApplication)
        badge = bar.tabButton(index, QTabBar.LeftSide)
        assert badge.isVisible()
        assert bar.tabRect(index).contains(badge.geometry())
        assert bar.rect().contains(badge.geometry())
        assert badge.visibleRegion().boundingRect() == badge.rect()
        assert badge.contentsRect().width() >= badge.fontMetrics().horizontalAdvance(badge.text())
        assert badge.contentsRect().height() >= badge.fontMetrics().height()
        assert badge.testAttribute(Qt.WA_TransparentForMouseEvents)
        # Actual native text pixels, not just a layout/size assertion.
        pixels = badge.grab().toImage()
        ink = sum(1 for y in range(pixels.height()) for x in range(pixels.width())
                  if (lambda c: c.red() < 100 and c.green() < 160)(pixels.pixelColor(x, y)))
        assert ink > 80
        assert not badge.geometry().intersects(tabs._addButton.geometry())
    assert tabs.grab().save(str(tmp_path / f'entry-badges-{width}.png'))
    if width == 1060:
        tabs.setCurrentIndex(0)
        settle(designerApplication)
        badge = bar.tabButton(2, QTabBar.LeftSide)
        QTest.mouseClick(bar, Qt.LeftButton, pos=badge.mapTo(bar, badge.rect().center()))
        assert tabs.currentIndex() == 2
    assert tabs.rect().contains(tabs._addButton.geometry())


@pytest.mark.parametrize('width', [1060, 1280])
def testNativeEntryMarkersInDesigner(designerApplication, tmp_path, width):
    QFontDatabase.addApplicationFont('C:/Windows/Fonts/msyh.ttc')
    window = makeWindow()
    duplicate = window.createWorkflow('Main')
    first = window.createWorkflow('main · 持续运行入口')
    second = window.createWorkflow('second · 持续运行入口')
    window.setEntryWorkflow(first)
    window.runTargetIds = [first, second]
    window.activateWorkflow(duplicate)
    window.resize(width, 800)
    window.show()
    settle(designerApplication)
    # The offscreen screen defaults to 800x600; resize after the queued
    # fit-to-screen callback so this really exercises the requested viewport.
    window.resize(width, 800)
    settle(designerApplication)
    assert window.width() == width
    assert badgeFor(window, first).text() == '默认入口'
    assert badgeFor(window, second).text() == '运行入口'
    assert window.grab().save(str(tmp_path / f'designer-entry-markers-{width}.png'))
    # Show each marked tab on narrow windows too; overflow must remain navigable.
    for key in (first, second):
        window.activateWorkflow(key)
        settle(designerApplication)
        badge = badgeFor(window, key)
        assert badge.visibleRegion().boundingRect() == badge.rect()
        rect = badge.rect()
        rect.moveTopLeft(badge.mapTo(window.workflowTabs, QPoint()))
        assert window.workflowTabs.rect().contains(rect)


def testLiveWorkflowColorsAcrossJobsSurviveSelectionAndRestart(designerApplication, tmp_path):
    window = makeWindow()
    second = window.createWorkflow('Main')
    a = window.createWorkflow('A 循环')
    b = window.createWorkflow('B 循环')
    window.setEntryWorkflow(a)
    window.runTargetIds = [a, b]
    window.activateWorkflow('main')
    manager = window.runtimeController
    first, other = manager._create(a), manager._create(b)
    manager._setJob(first, 'job-1')
    manager._setJob(other, 'job-2')
    first.state.updateJob('RUNNING')
    other.state.updateJob('RUNNING')
    try:
        first.state.applyEvent(event('workflow.started', a, 'root-a', 1))
        first.state.applyEvent(event('workflow.started', 'main', 'body-a', 2, 'root-a'))
        other.state.applyEvent(dict(event('workflow.started', b, 'root-b', 1), jobId='job-2'))
        other.state.applyEvent(dict(event('workflow.started', second, 'body-b', 2, 'root-b'), jobId='job-2'))
        manager._refresh(first)
        window.resize(1280, 800)
        window.show()
        settle(designerApplication)
        window.resize(1280, 800)
        settle(designerApplication)
        assert window.width() == 1280

        def status(key):
            bar = window.workflowTabs.tabBar()
            index = next(i for i in range(bar.count()) if bar.tabData(i) == key)
            badge = bar.tabButton(index, QTabBar.RightSide)
            return badge.property('runtimeStatus') if badge else ''

        assert [status(key) for key in ('main', second, a, b)] == ['RUNNING', 'RUNNING', 'WAITING_CHILD', 'WAITING_CHILD']
        assert window.grab().save(str(tmp_path / 'designer-live-workflows.png'))
        manager.select(b)
        assert window.getActiveWorkflowId() == 'main'
        assert status('main') == status(second) == 'RUNNING'
        window.activateWorkflow(second)
        assert status(a) == status(b) == 'WAITING_CHILD'

        first.state.applyEvent(event('workflow.completed', 'main', 'body-a', 3, 'root-a'))
        manager._refresh(first)
        assert status('main') == 'COMPLETED' and status(a) == 'RUNNING'
        assert status(second) == 'RUNNING'
        first.state.updateJob('ABORTED')
        manager._refresh(first)
        assert status(a) == 'ABORTED' and status(b) == 'WAITING_CHILD'
        manager._setJob(first, 'job-new')
        first.state.updateJob('STARTING')
        manager._refresh(first)
        assert status('main') == '' and status(a) == 'STARTING'
        assert status(second) == 'RUNNING'
        other.state.applyEvent(dict(event('workflow.failed', second, 'body-b', 3, 'root-b', code='E_CAMERA'), jobId='job-2'))
        manager._refresh(other)
        assert status(second) == 'FAILED'
        assert 'job-2' in window.workflowTabs.tabToolTip(1)
        other.state.updateJob('FAILED')
        manager._refresh(other)
        assert status(b) == 'FAILED'
        assert window.grab().save(str(tmp_path / 'designer-failed-workflow.png'))
    finally:
        first.state.updateJob('ABORTED')
        other.state.updateJob('ABORTED')
        manager.reset()
    assert status(second) == ''


@pytest.mark.parametrize('width', [380, 640, 1060])
def testRuntimeBadgesAndColorStripeVisibleWithLongNames(designerApplication, tmp_path, width):
    tabs = WorkflowTabs()
    tabs.setObjectName('workflowTabs')
    for index, status in enumerate(('RUNNING', 'WAITING_CHILD', 'COMPLETED', 'FAILED', 'ABORTED')):
        tabs.addTab(QWidget(), '长工作流名称 Workflow ' * 10)
        tabs.setEntryMarker(index, '默认入口' if index == 0 else '运行入口', 'entry')
        tabs.setRuntimeStatus(index, status, f'Job test: {status}')
    tabs.addTab(QWidget(), '+')
    tabs.resize(width, tabs.sizeHint().height())
    tabs.show()
    for index in range(5):
        tabs.setCurrentIndex(index)
        settle(designerApplication)
        bar = tabs.tabBar()
        left, right = [bar.tabButton(index, side) for side in (QTabBar.LeftSide, QTabBar.RightSide)]
        assert not left.geometry().intersects(right.geometry())
        for badge in (left, right):
            assert badge.visibleRegion().boundingRect() == badge.rect()
            assert badge.contentsRect().width() >= badge.fontMetrics().horizontalAdvance(badge.text())
        stripe = bar.grab().toImage()
        rect = bar.tabRect(index)
        # Read native rendered pixels at device scale, not an expected stylesheet.
        dpr = stripe.devicePixelRatio()
        pixel = stripe.pixelColor(round(rect.center().x() * dpr), round((rect.top() + 2) * dpr))
        assert pixel.name() == right.property('runtimeColor')
    assert tabs.grab().save(str(tmp_path / f'runtime-badges-{width}.png'))


@pytest.mark.parametrize('direction', [Qt.LeftToRight, Qt.RightToLeft])
def testGrowingOverflowStripHasNoBlankLeadingGapOrDisabledScroll(designerApplication, direction):
    tabs = WorkflowTabs()
    tabs.setObjectName('workflowTabs')
    tabs.setLayoutDirection(direction)
    for index in range(4):
        tabs.addTab(QWidget(), '持续运行 Workflow ' * 6)
        tabs.setEntryMarker(index, '运行入口', 'test')
        tabs.setRuntimeStatus(index, 'RUNNING', 'Job test')
    tabs.addTab(QWidget(), '+')
    tabs.setCurrentIndex(1)
    tabs.resize(380, 40)
    tabs.show()
    settle(designerApplication)
    changes = []
    tabs.currentChanged.connect(changes.append)
    for width in (750, 640, 1060):
        tabs.resize(width, tabs.sizeHint().height())
        settle(designerApplication)
        bar = tabs.tabBar()
        first = bar.tabRect(0)
        assert first.right() >= bar.width() - 1 if direction == Qt.RightToLeft else first.left() <= 0
        arrows = [button for button in bar.findChildren(QToolButton) if button.isVisible()]
        assert arrows and any(button.isEnabled() for button in arrows)
        assert tabs.currentIndex() == 1 and changes == []
