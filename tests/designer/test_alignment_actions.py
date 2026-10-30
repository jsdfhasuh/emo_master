"""Edit-menu arrangement routes to the visible workspace and existing history."""
from copy import deepcopy

from emo_master.apps.designer.ui.designer_actions import ALIGNMENT_MODES
from tests.designer.test_node_context_menu import canvas, Client  # noqa: F401
from tests.designer.test_main_window_menubar import SettingsStoreStub
from tests.designer.test_designer_shortcuts import makePage


def testEditMenuPreferencesPersistLocallyWithoutProjectHistory(ownedDesignerWindow):
    settings = SettingsStoreStub()
    window = ownedDesignerWindow(Client(), settingsStore=settings)
    registry = window.designerActions
    before, history = window.pageCoordinator.session.payload(), len(window.pageCoordinator.session._undo)
    menu = registry.menus['编辑']
    titles = [action.text() for action in menu.actions()]
    assert titles.index('动态参考线') < titles.index('对齐') < titles.index('等距分布')
    assert [action.text() for action in registry.alignmentMenu.actions() if not action.isSeparator()] == [
        title for _mode, title in ALIGNMENT_MODES]
    for key in ('alignment_guides', 'alignment_snap'):
        assert registry.actions[key].isCheckable() and registry.actions[key].isChecked()
        registry.actions[key].trigger()
    assert not window.flowScene.alignmentGuidesEnabled and not window.flowScene.alignmentSnapEnabled
    assert window.pageCoordinator.session.payload() == before
    assert len(window.pageCoordinator.session._undo) == history
    reopened = ownedDesignerWindow(Client(), settingsStore=settings)
    assert not reopened.designerActions.actions['alignment_guides'].isChecked()
    assert not reopened.flowScene.alignmentSnapEnabled
    settings.setValue('ui/canvas/alignment_guides', 'true')
    settings.setValue('ui/canvas/alignment_snap', 'false')
    third = ownedDesignerWindow(Client(), settingsStore=settings)
    assert third.flowScene.alignmentGuidesEnabled and not third.flowScene.alignmentSnapEnabled


def testSelectionLimitsAndRunningGuardAreRecheckedAtExecution(canvas):  # noqa: F811
    window, keys, _client = canvas
    scene, registry = window.flowScene, window.designerActions
    scene.clearSelection()
    registry.menus['编辑'].aboutToShow.emit()
    assert not registry.actions['align_left'].isEnabled()
    assert registry.blockedReason('align_left') == '请至少选择 2 个对象'
    for key in keys:
        scene._nodeItems[key].setSelected(True)
    registry.refresh()
    assert registry.actions['align_left'].isEnabled()
    assert not registry.actions['distribute_horizontal'].isEnabled()
    before = scene.getNodePositions()
    window._setIsJobRunning(True)
    try:
        registry.invoke('align_left')
        assert scene.getNodePositions() == before
    finally:
        window._setIsJobRunning(False)


def testAlignmentHasOneUndoAndSaveReopenPreservesCoordinates(canvas, tmp_path):  # noqa: F811
    window, (first, second), client = canvas
    scene, coordinator = window.flowScene, window.pageCoordinator
    scene._nodeItems[first].setPos(100, 100)
    scene._nodeItems[second].setPos(500, 250)
    for key in (first, second):
        scene._nodeItems[key].setSelected(True)
    coordinator.sync()
    before, graph = scene.getNodePositions(), deepcopy(window.flowModel.toProjectGraph())
    history = len(coordinator.session._undo)
    window.designerActions.invoke('align_left')
    after = scene.getNodePositions()
    assert after[first][0] == after[second][0] == 100
    assert len(coordinator.session._undo) == history + 1
    window.designerActions.invoke('align_left')
    assert len(coordinator.session._undo) == history + 1
    coordinator.history()
    assert scene.getNodePositions() == before
    coordinator.history(True)
    assert scene.getNodePositions() == after
    assert window.flowModel.toProjectGraph() == graph
    assert window.saveProjectToDirectory(str(tmp_path / 'aligned'))
    assert window.loadProjectDirectory(str(tmp_path / 'aligned'))
    assert scene.getNodePositions() == after
    assert client.starts == client.stops == 0


def testPageCommandsUsePageSelectionAndNeverHiddenFlow(canvas, monkeypatch):  # noqa: F811
    window, keys, _client = canvas
    for key in keys:
        window.flowScene._nodeItems[key].setSelected(True)
    before = window.flowScene.getNodePositions()
    editor, _key = makePage(window, monkeypatch)
    called = []
    monkeypatch.setattr(editor.tools, 'selectionCount', lambda: 3, raising=False)
    monkeypatch.setattr(editor.tools, 'arrangementBlockedReason', lambda _key: '', raising=False)
    monkeypatch.setattr(editor.tools, 'alignSelection', lambda mode: called.append(('align', mode)), raising=False)
    monkeypatch.setattr(editor.tools, 'distributeSelection', lambda axis: called.append(('distribute', axis)), raising=False)
    registry = window.designerActions
    registry.invoke('align_top')
    registry.invoke('distribute_horizontal')
    assert called == [('align', 'top'), ('distribute', 'horizontal')]
    assert window.flowScene.getNodePositions() == before
    editor.renderer.editing = False
    registry.invoke('align_left')
    assert len(called) == 2 and '编辑模式' in registry.blockedReason('align_left')


def testDistributionFailureUsesStatusMessageWithoutMutation(canvas, monkeypatch):  # noqa: F811
    window, _keys, _client = canvas
    for item in window.flowScene._nodeItems.values():
        item.setSelected(True)
    before, history = window.pageCoordinator.session.payload(), len(window.pageCoordinator.session._undo)
    def insufficient(_axis):
        raise ValueError('等距分布空间不足，请先拉开两端对象后重试')
    monkeypatch.setattr(window.flowScene, 'distributeSelectedNodes', insufficient)
    window.designerActions.invoke('distribute_horizontal')
    assert '空间不足' in window.statusBar().currentMessage()
    assert window.pageCoordinator.session.payload() == before
    assert len(window.pageCoordinator.session._undo) == history
