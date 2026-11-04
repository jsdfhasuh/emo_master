"""Page panel menu checks follow the actual overlays, not action positions."""
import pytest

from test_workspace import designer  # noqa: F401


def panelActions(chrome):
    return [(next(action for action in chrome.pageActions if action.text() == title), index)
            for title, index in [('页面工具区', 0), ('属性面板', 2)]]


def assertPanelChecks(coordinator):
    for action, index in panelActions(coordinator.chrome):
        assert action.isCheckable()
        assert action.isChecked() == coordinator.editor.panel(index).isExpanded()
    for action in (coordinator.chrome.previewAction, coordinator.chrome.exportAction):
        assert not action.isCheckable()
        assert not action.isChecked()


def testInitialPanelMenuChecks(designer):  # noqa: F811
    coordinator = designer.pageCoordinator
    coordinator.showPages()
    assertPanelChecks(coordinator)


@pytest.mark.parametrize('index', [0, 2])
@pytest.mark.parametrize('route', ['menu', 'panel'])
def testPanelMenuChecksTrackRepeatedToggles(designer, index, route):  # noqa: F811
    coordinator = designer.pageCoordinator
    coordinator.showPages()
    panel = coordinator.editor.panel(index)
    action = next(action for action, panelIndex in panelActions(coordinator.chrome) if panelIndex == index)
    for expanded in (False, True):
        panel.setExpanded(expanded)
        for _ in range(3):
            before = panel.isExpanded()
            if route == 'menu':
                action.trigger()
            else:
                panel.toggleButton.click()
            assert panel.isExpanded() != before
            assertPanelChecks(coordinator)
        panel.closeButton.click()
        assert not panel.isExpanded()
        assertPanelChecks(coordinator)


@pytest.mark.parametrize('expanded', [(False, True), (True, False), (False, False), (True, True)])
def testPanelMenuChecksSurviveWorkspaceSwitch(designer, expanded):  # noqa: F811
    coordinator = designer.pageCoordinator
    coordinator.showPages()
    for (_, index), state in zip(panelActions(coordinator.chrome), expanded):
        coordinator.editor.panel(index).setExpanded(state)
    for _ in range(2):
        coordinator.showFlow()
        for action, _ in panelActions(coordinator.chrome):
            assert not action.isVisible() and not action.isEnabled()
        coordinator.showPages()
        assertPanelChecks(coordinator)
        for (action, index), state in zip(panelActions(coordinator.chrome), expanded):
            assert action.isVisible() and action.isEnabled()
            assert coordinator.editor.panel(index).isExpanded() == state


def testPanelSyncDoesNotWriteQtPreviewOrExportChecks(designer, monkeypatch):  # noqa: F811
    coordinator = designer.pageCoordinator
    coordinator.showPages()
    writes = []
    for action in (coordinator.chrome.previewAction, coordinator.chrome.exportAction):
        monkeypatch.setattr(action, 'setChecked', lambda value: writes.append(value))
    coordinator.chrome.update()
    coordinator.editor.togglePanel(0)
    coordinator.editor.togglePanel(2)
    coordinator.showFlow()
    coordinator.showPages()
    assert writes == []


def testDesignerKeepsQtActionsWithoutWebExportPreviewOrLauncher(designer):  # noqa: F811
    from test_pending_inputs import setupPage
    coordinator, _editor, _key = setupPage(designer)
    chrome = coordinator.chrome
    actions = [action for menu in chrome.menus.values() for action in menu.actions()]
    assert all('Web' not in action.text() and '网页' not in action.text() for action in actions)
    assert chrome.previewAction.text() == '预览页面'
    assert chrome.previewAction.isEnabled() and chrome.previewAction.isVisible()
    assert chrome.previewAction in chrome.menus['视图'].actions()
    assert chrome.exportAction in chrome.menus['文件'].actions()
    assert chrome.publishPages in chrome.menus['Runtime'].actions()
    for name in ('previewWebAction', 'previewWebRuntimeAction', 'exportWebAction'):
        assert not hasattr(chrome, name)
    assert not hasattr(coordinator, 'webPreview')
    assert not hasattr(coordinator, '_closingWebPreview')
