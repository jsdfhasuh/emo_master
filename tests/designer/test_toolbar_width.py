"""Menu commands and workspace switching remain reachable at supported sizes."""
import pytest
from PySide2.QtWidgets import QToolButton

from tests.designer.test_visual_layout import makeWindow
from tests.designer.runtime_state_fixture import setRunning


def settle(app):
    for _ in range(6):
        app.processEvents()


@pytest.mark.parametrize('width', [480, 640, 800, 900, 960, 1280])
@pytest.mark.parametrize('running', [False, True])
def testMenuRunAndStopRemainReachableAtSupportedWidths(designerApplication, width, running):
    window = makeWindow()
    try:
        window.loadedProjectPath = 'toolbar-test-only'
        setRunning(window, running)
        window.updateToolbarState()
        window.show()
        settle(designerApplication)
        window.resize(width, 500)
        settle(designerApplication)
        assert window.width() == width
        chrome = window.pageCoordinator.chrome
        for key in ['文件', '运行', '编辑', 'Runtime']:
            menu = chrome.menus[key]
            assert menu.menuAction().isVisible()
            assert window.menuBar().rect().contains(window.menuBar().actionGeometry(menu.menuAction()))
        for action, key in [(window.startButton, '开始运行'), (window.stopButton, '停止运行')]:
            assert action is window._toolbarActions[key]
            assert action in chrome.menus['运行'].actions()
            assert action not in window.mainToolbar.actions()
            assert window.mainToolbar.widgetForAction(action) is None
            assert not action.icon().isNull() and action.toolTip()
        assert window.startButton.isEnabled() is (not running)
        assert window.stopButton.isEnabled() is running
        assert window.mainToolbar.contentsRect().contains(chrome.selector.geometry())
        assert len(window.mainToolbar.actions()) == 1
    finally:
        setRunning(window, False)
        window.close()


@pytest.mark.parametrize('toolbarFontPixels', [None, 28])
def testResizeKeepsSwitcherAndMenuActionsWithoutEditingProject(designerApplication, toolbarFontPixels):
    window = makeWindow()
    try:
        if toolbarFontPixels is not None:
            window.mainToolbar.setStyleSheet('QToolBar, QToolBar QToolButton, QWidget#workspaceSelector '
                                            '{ font-size: %dpx; }' % toolbarFontPixels)
        window.show()
        before = window.pageCoordinator.session.payload()
        actions = tuple(window.mainToolbar.actions())
        commands = tuple(window._toolbarActions.values())
        for _ in range(3):
            for width in (480, 640, 960, 1280):
                window.resize(width, 500)
                settle(designerApplication)
                assert window.width() == width
                assert tuple(window.mainToolbar.actions()) == actions
                assert tuple(window._toolbarActions.values()) == commands
                selector = window.pageCoordinator.chrome.selector
                assert selector.isVisible()
                assert window.mainToolbar.contentsRect().contains(selector.geometry())
                extension = window.mainToolbar.findChild(QToolButton, 'qt_toolbar_ext_button')
                assert extension is None or not extension.isVisible()
        assert window.pageCoordinator.session.payload() == before
    finally:
        window.close()


def testToolbarFontChangeRecalculatesSwitcherAtSameWindowWidth(designerApplication):
    window = makeWindow()
    try:
        window.show()
        window.resize(640, 500)
        settle(designerApplication)
        selector = window.pageCoordinator.chrome.selector
        previous = selector.sizeHint().width()
        selector.setStyleSheet(selector.styleSheet() + '\nQWidget#workspaceSelector { font-size: 24px; }')
        settle(designerApplication)
        assert window.width() == 640
        assert selector.sizeHint().width() > previous
        assert window.mainToolbar.contentsRect().contains(selector.geometry())
    finally:
        window.close()


def testNarrowWorkspaceLabelsSurviveActionChanges(designerApplication):
    window = makeWindow()
    try:
        window.show()
        window.resize(480, 500)
        settle(designerApplication)
        chrome = window.pageCoordinator.chrome
        for action in chrome.workspaceActions:
            action.setChecked(True)
            chrome.resizeTools()
            settle(designerApplication)
            assert [button.text() for button in chrome.selector.buttons] == ['流程', '页面']
            assert chrome.deviceAction.text() == '设备管理'
            assert all(button.accessibleName() == action.text()
                       for button, action in zip(chrome.selector.buttons, chrome.workspaceActions))
            assert window.stopButton in chrome.menus['运行'].actions()
    finally:
        window.close()
