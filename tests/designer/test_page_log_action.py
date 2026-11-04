"""Read-only logs remain available without enabling hidden workflow commands."""
import pytest
from PySide2.QtCore import Qt

from emo_master.apps.designer.ui.designer_actions import NAVIGATION_KEYS
from tests.designer.test_designer_shortcuts import makePage, press
from tests.designer.test_node_context_menu import canvas  # noqa: F401


@pytest.mark.parametrize('entry', ['action', 'shortcut'])
def testPageLogsOpenWithoutEnablingFlowActions(canvas, monkeypatch, entry):  # noqa: F811
    window, _nodes, client = canvas
    makePage(window, monkeypatch)
    registry = window.designerActions
    before = window.pageCoordinator.session.payload()
    registry.refresh()
    assert registry.blockedReason('logs') == ''
    assert registry.actions['logs'].isEnabled()
    assert registry.actions['logs'] in registry.menus['运行'].actions()
    for key in {'configure', 'operator_debug', 'workflow_debug', 'results', 'layout'} | NAVIGATION_KEYS:
        assert registry.blockedReason(key) == '仅流程设计可用'
        assert not registry.actions[key].isEnabled()
    if entry == 'action':
        registry.actions['logs'].trigger()
    else:
        press(window, Qt.Key_L, Qt.ControlModifier | Qt.ShiftModifier)
    assert window.logDialog.isVisible()
    assert window.pageCoordinator.session.payload() == before
    assert client.starts == client.stops == 0


@pytest.mark.parametrize('state', ['_closing', '_closed'])
def testPageLogsKeepClosingGuard(canvas, monkeypatch, state):  # noqa: F811
    window, _nodes, _client = canvas
    makePage(window, monkeypatch)
    registry = window.designerActions
    monkeypatch.setattr(window.runtimeController, state, True)
    try:
        registry.refresh()
        assert registry.blockedReason('logs') == 'Designer 已关闭'
        assert not registry.actions['logs'].isEnabled()
        registry.invoke('logs')
        assert not window.logDialog.isVisible()
    finally:
        monkeypatch.setattr(window.runtimeController, state, False)
