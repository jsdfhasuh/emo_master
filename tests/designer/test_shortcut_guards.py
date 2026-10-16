"""Real key events must obey the same mutation guard as node menus."""
import pytest
import emo_master  # noqa: F401 - preload Windows native dependencies before Qt
from PySide2.QtCore import Qt
from PySide2.QtTest import QTest
from PySide2.QtWidgets import QApplication

from tests.designer.test_node_context_menu import canvas  # noqa: F401


@pytest.mark.parametrize('key', [Qt.Key_Delete, Qt.Key_Backspace])
def testRunningDeleteKeysCannotRemoveDraftNodes(canvas, key):  # noqa: F811
    window, (first, _second), client = canvas
    window.show()
    window.activateWindow()
    window.flowScene.setNodeSelected(first)
    window.flowView.setFocus()
    QApplication.processEvents()
    before = window.pageCoordinator.session.payload()
    depth = len(window.pageCoordinator.session._undo)
    window._setIsJobRunning(True)
    window.updateToolbarState()
    try:
        QTest.keyClick(window.flowView, key)
        QApplication.processEvents()
        assert window.pageCoordinator.session.payload() == before
        assert len(window.pageCoordinator.session._undo) == depth
        assert client.starts == client.stops == 0
    finally:
        window._setIsJobRunning(False)


@pytest.mark.parametrize('method', ['handleDeleteShortcut', 'deleteSelectedElements',
                                  'duplicateSelectedNode', 'duplicateCurrentWorkflow', 'autoLayoutNodes'])
def testRunningLegacyMutationEntryCannotBypassGuard(canvas, method):  # noqa: F811
    window, (first, _second), _client = canvas
    window.flowScene.setNodeSelected(first)
    before = window.pageCoordinator.session.payload()
    positions = window.flowScene.getNodePositions()
    window._setIsJobRunning(True)
    try:
        getattr(window, method)()
        assert window.pageCoordinator.session.payload() == before
        assert window.flowScene.getNodePositions() == positions
    finally:
        window._setIsJobRunning(False)


def testRunningConfigurationDoesNotOpenEditor(canvas):  # noqa: F811
    window, (first, _second), _client = canvas
    window._setIsJobRunning(True)
    try:
        window.openNodeParamDialog(first)
        assert window.operatorEditorManager.count() == 0
    finally:
        window._setIsJobRunning(False)


def testRunningHistoryCannotMutateDraft(canvas):  # noqa: F811
    window, (first, _second), _client = canvas
    window.flowScene.setNodeSelected(first)
    window.duplicateSelectedNode()
    before = window.pageCoordinator.session.payload()
    depth = len(window.pageCoordinator.session._undo)
    window._setIsJobRunning(True)
    try:
        window.pageCoordinator.history()
        assert window.pageCoordinator.session.payload() == before
        assert len(window.pageCoordinator.session._undo) == depth
    finally:
        window._setIsJobRunning(False)
