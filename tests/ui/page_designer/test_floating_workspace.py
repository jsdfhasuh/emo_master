"""Floating page editing surfaces preserve geometry, validation and page IDs."""
import pytest
from PySide2.QtCore import QCoreApplication, QEvent, QPoint, Qt
from PySide2.QtTest import QTest
from PySide2.QtWidgets import QApplication, QInputDialog, QMessageBox
from shiboken2 import isValid

from test_workspace import designer  # noqa: F401
from test_pending_inputs import setupPage


def settle():
    for _ in range(5):
        QApplication.processEvents()


@pytest.mark.parametrize('size', [(640, 400), (1000, 600), (1600, 900)])
def testBothOverlaysLeavePageAndComponentsStationary(designer, size):  # noqa: F811
    coordinator, editor, key = setupPage(designer, 'number')
    designer.resize(*size)
    settle()
    renderer = editor.renderer
    canvas = renderer.geometry()
    component = renderer.widgets[editor.pageId][key][1]
    bounds = component.geometry()
    before, undo = coordinator.session.payload(), len(coordinator.session._undo)
    for tools, properties in ((False, False), (True, False), (True, True), (False, True), (False, False)):
        editor.floatingToolbox.setExpanded(tools)
        editor.floatingProperties.setExpanded(properties)
        settle()
        assert renderer.geometry() == canvas and component.geometry() == bounds
        assert editor.tools.selected == key and renderer.currentPageId == editor.pageId
        for panel in (editor.floatingToolbox, editor.floatingProperties):
            assert panel.parentWidget() is editor.canvasHost
            assert panel.visibleCanvasRect().contains(panel.geometry())
            assert not renderer.isAncestorOf(panel)
            assert not panel.acceptDrops()
        assert editor.floatingToolbox.isExpanded() is tools
        assert editor.floatingProperties.isExpanded() is properties
    assert coordinator.session.payload() == before and len(coordinator.session._undo) == undo
    assert designer.currentJobId is None and coordinator.preview.session is None


@pytest.mark.parametrize('size', [(640, 400), (1000, 600), (1600, 900)])
def testCollapsingDoesNotCommitOrDiscardFormAndErrorRevealsIt(designer, size):  # noqa: F811
    coordinator, editor, key = setupPage(designer)
    designer.resize(*size)
    QApplication.setActiveWindow(designer)
    editor.tools.fields['title'].setText('待应用标题')
    editor.tools.fields['fontSize'].setValue(1)
    before, undo = coordinator.session.payload(), len(coordinator.session._undo)
    editor.floatingProperties.setExpanded(False)
    editor.floatingToolbox.setExpanded(True)
    settle()
    assert editor.tools.selected == key and editor.tools.fields['title'].text() == '待应用标题'
    assert coordinator.session.payload() == before and len(coordinator.session._undo) == undo
    coordinator.showFlow()
    settle()
    assert coordinator.pageActive() and editor.floatingProperties.isExpanded()
    assert editor.tools.fields['fontSize'].hasFocus()
    assert editor.tools.fields['fontSize'].value() == 1
    assert coordinator.session.payload() == before
    field = editor.tools.fields['fontSize']
    assert field.visibleRegion().contains(field.rect())
    hit = designer.childAt(field.mapTo(designer, field.rect().center()))
    assert hit is field or field.isAncestorOf(hit)
    QTest.mouseClick(field, Qt.LeftButton)
    QTest.keyClick(field, Qt.Key_A, Qt.ControlModifier)
    QTest.keyClicks(field, '12')
    assert field.value() == 12
    coordinator.showFlow()
    assert coordinator.flowActive()
    coordinator.showPages()
    assert editor.tools.selected == key


def testOverlayEscapeAndWorkspaceSwitchKeepIndependentState(designer):  # noqa: F811
    coordinator, editor, _ = setupPage(designer)
    editor.floatingToolbox.setExpanded(True)
    editor.floatingProperties.setExpanded(True)
    designer.activateWindow()
    editor.tools.search.setFocus()
    settle()
    QTest.keyClick(editor.tools.search, Qt.Key_Escape)
    assert not editor.floatingToolbox.isExpanded() and editor.floatingProperties.isExpanded()
    editor.floatingProperties.moveWithinCanvas(QPoint(700, 55))
    editor.floatingProperties._savePosition()
    position = editor.floatingProperties.pos()
    before = coordinator.session.payload()
    for _ in range(3):
        coordinator.showFlow()
        designer.floatingRunPanel.setExpanded(True)
        coordinator.showPages()
        settle()
        assert not editor.floatingToolbox.isExpanded()
        assert editor.floatingProperties.isExpanded() and editor.floatingProperties.pos() == position
        assert not designer.floatingRunPanel.isVisible()
    assert coordinator.session.payload() == before


def testTopTabsPickerAndExistingPageActionsRemainIdBased(designer, monkeypatch):  # noqa: F811
    coordinator = designer.pageCoordinator
    coordinator.showPages()
    editor = coordinator.editor
    ids = [editor.store.createPage(f'检测页面 {i}') for i in range(14)]
    editor.refresh()
    designer.resize(800, 600)
    settle()
    tabs = editor.pageTabs
    assert tabs.count() == 14
    assert any(not tabs.tabBar().isTabVisible(i) for i in range(tabs.count()))
    tabs._showPicker()
    tabs._picker.search.setText(ids[-1])
    QTest.keyClick(tabs._picker.search, Qt.Key_Return)
    assert editor.pageId == ids[-1] and tabs.pageId(tabs.currentIndex()) == ids[-1]
    assert tabs.tabBar().isTabVisible(tabs.currentIndex())
    actions = {action.text(): action for action in editor.pageMenu.actions()}
    monkeypatch.setattr(QInputDialog, 'getText', lambda *a, **k: ('已重命名', True))
    actions['重命名'].trigger()
    assert tabs.tabText(tabs.currentIndex()) == '已重命名'
    actions['设为首页'].trigger()
    assert editor.store.snapshot().defaultPageId == ids[-1]
    assert '首页' in tabs.tabBar().accessibleTabName(tabs.currentIndex())
    actions['上移'].trigger()
    assert tabs.pageId(12) == ids[-1]
    actions['下移'].trigger()
    assert tabs.pageId(13) == ids[-1]
    actions['复制页面'].trigger()
    copied = editor.pageId
    assert copied not in ids and tabs.count() == 15
    monkeypatch.setattr(QMessageBox, 'question', lambda *a, **k: QMessageBox.Yes)
    actions['删除页面'].trigger()
    assert copied not in editor.store.snapshot().pages and tabs.count() == 14
    monkeypatch.setattr(QInputDialog, 'getText', lambda *a, **k: ('新增页', True))
    QTest.mouseClick(tabs._addButton, Qt.LeftButton)
    assert tabs.count() == 15 and tabs.tabText(tabs.currentIndex()) == '新增页'
    assert designer.currentJobId is None
    monkeypatch.setattr(QMessageBox, 'question', lambda *a, **k: QMessageBox.Discard)


@pytest.mark.parametrize('width', [480, 640, 1000, 1600])
def testWorkspaceSwitchCenteredOnWholeWindowInBothModes(designer, width):  # noqa: F811
    coordinator, editor, _ = setupPage(designer)
    designer.resize(width, 600)
    selector = coordinator.chrome.selector
    extra = designer.mainToolbar.addAction('辅助')
    for pageMode in (False, True, False, True):
        (coordinator.showPages if pageMode else coordinator.showFlow)()
        editor.floatingProperties.setExpanded(not pageMode)
        settle()
        center = selector.mapTo(designer, selector.rect().center()).x()
        assert abs(center - designer.rect().center().x()) <= 1
        assert coordinator.chrome.previewButton.isVisible() is pageMode
        if pageMode:
            assert not selector.geometry().intersects(coordinator.chrome.previewButton.geometry())
    designer.mainToolbar.removeAction(extra)


def testFloatingPageOwnersRetireWithWindow(qtApp):
    from test_workspace import Client
    from emo_master.apps.designer.ui.main_window import MainWindow
    window = MainWindow(Client())
    window.show()
    coordinator, editor, _ = setupPage(window)
    panels = [editor.floatingToolbox, editor.floatingProperties]
    timer = editor.tools.refreshTimer
    revealTimer = editor.tools.errorRevealTimer
    editor.tools.errorField = editor.tools.fields['fontSize']
    revealTimer.start(0)
    coordinator.session.markSaved()
    window.close()
    assert not timer.isActive() and not revealTimer.isActive() and editor.closed
    window.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    assert all(not isValid(panel) for panel in panels)
