"""Primary run controls must survive overflow, font changes and repeated resizing."""
import pytest
from PySide2.QtCore import Qt

from tests.designer.test_visual_layout import makeWindow


def settle(app):
    for _ in range(6):
        app.processEvents()


@pytest.mark.parametrize('width', [480, 640, 800, 900, 960, 1280])
@pytest.mark.parametrize('running', [False, True])
def testRunAndStopRemainVisibleAtSupportedWidths(designerApplication, width, running):
    window = makeWindow()
    try:
        window.loadedProjectPath = 'toolbar-test-only'
        window.isJobRunning = running
        window.currentJobId = 'toolbar-test-only' if running else None
        window.updateToolbarState()
        window.show()
        settle(designerApplication)
        window.resize(width, 500)
        settle(designerApplication)
        assert window.width() == width
        for button, key in [(window.startButton, '开始运行'), (window.stopButton, '停止运行')]:
            assert button.isVisible()
            assert button.defaultAction() is window._toolbarActions[key]
            assert not button.icon().isNull()
            assert button.accessibleName()
            if key == '停止运行' and not running:
                assert button.toolTip() == '没有可停止的当前任务'
            else:
                assert '运行' in button.toolTip()
            assert window.mainToolbar.contentsRect().contains(button.geometry())
        assert window.startButton.isEnabled() is (not running)
        assert window.stopButton.isEnabled() is running
    finally:
        window.isJobRunning = False
        window.currentJobId = None
        window.close()


def testResizeRestoresLabelsWithoutRecreatingActionsOrEditingProject(designerApplication):
    window = makeWindow()
    try:
        window.show()
        before = window.pageCoordinator.session.payload()
        actions = tuple(window.mainToolbar.actions())
        buttons = window.startButton, window.stopButton
        for _ in range(3):
            for width in (480, 640, 960, 1280):
                window.resize(width, 500)
                settle(designerApplication)
                assert tuple(window.mainToolbar.actions()) == actions
                assert (window.startButton, window.stopButton) == buttons
                assert all(button.isVisible() for button in buttons)
                if width == 480:
                    assert all(button.toolButtonStyle() == Qt.ToolButtonIconOnly for button in buttons)
                if width == 1280:
                    assert all(button.toolButtonStyle() == Qt.ToolButtonTextBesideIcon for button in buttons)
                    assert window.loadButton.toolButtonStyle() == Qt.ToolButtonTextBesideIcon
        assert window.pageCoordinator.session.payload() == before
    finally:
        window.close()


def testToolbarFontChangeRecalculatesSpaceAtSameWindowWidth(designerApplication):
    window = makeWindow()
    try:
        window.show()
        settle(designerApplication)
        window.resize(640, 500)
        settle(designerApplication)
        selector = window.pageCoordinator.chrome.selector
        previous = selector.sizeHint().width()
        selector.setStyleSheet(selector.styleSheet() + '\nQWidget#workspaceSelector { font-size: 24px; }')
        settle(designerApplication)
        assert window.width() == 640
        assert window.pageCoordinator.chrome.selector.sizeHint().width() > previous
        assert window.startButton.isVisible() and window.stopButton.isVisible()
        assert window.mainToolbar.contentsRect().contains(window.stopButton.geometry())
    finally:
        window.close()
