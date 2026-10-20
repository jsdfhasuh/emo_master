from copy import deepcopy

import pytest
from PySide2.QtCore import Qt

from emo_master.apps.designer.ui.main_window import MainWindow
from emo_master.ui.run_targets_dialog import RunTargetsDialog
from tests.designer.test_main_window_project_save_load import RuntimeClientStub
from tests.designer.test_workflow_entry_markers import badgeFor
from tests.designer.navigation_fixture import saveNavigationProject


def select(dialog, targets):
    for index in range(dialog.targets.count()):
        item = dialog.targets.item(index)
        item.setCheckState(Qt.Checked if item.data(Qt.UserRole) in targets else Qt.Unchecked)


def makeWindow():
    window = MainWindow(RuntimeClientStub())
    assert window.createWorkflow('Second') == 'second'
    window.pageCoordinator.sync()
    window.pageCoordinator.session.markSaved()
    return window


def testDialogDefaultsToProjectEditAndReopenRestoresBothTargets(designerApplication, tmp_path, monkeypatch):
    window = makeWindow()
    graph = deepcopy(window.workflowStore.workflows)
    session = window.pageCoordinator.session
    labels = {key: id(label) for key, label in window.workflowTabs._labels.items()}
    history = len(session._undo)
    def choose(dialog):
        assert dialog.saveSelection()
        select(dialog, ['main', 'second'])
        dialog.limit.setValue(3)
        return True
    monkeypatch.setattr(RunTargetsDialog, 'exec_', choose)
    window.openRunTargetsDialog()
    assert session.dirty and len(session._undo) == history + 1
    assert not window.runTargetIds and window.runtimeController.targets() == ['main', 'second']
    assert window.workflowStore.runtime['maxConcurrentJobs'] == 3
    assert badgeFor(window, 'second').text() == '运行入口'
    assert {key: id(label) for key, label in window.workflowTabs._labels.items()} == labels
    assert window.workflowStore.workflows == graph
    assert window.runTargetsButton.text() == '运行目标（工程 · 2）…'
    window.pageCoordinator.history()
    assert window.runtimeController.targets() == ['main']
    assert window.runTargetsButton.text() == '运行目标（工程 · 1）…'
    assert window.workflowStore.runtime['maxConcurrentJobs'] == 2 and not session.dirty
    assert badgeFor(window, 'second') is None
    window.pageCoordinator.history(True)
    assert window.runtimeController.targets() == ['main', 'second']
    assert window.runTargetsButton.text() == '运行目标（工程 · 2）…'
    target = tmp_path / 'saved'
    assert window.saveProjectToDirectory(str(target))
    assert window.loadProjectDirectory(str(target), confirmed=True)
    assert window.runtimeController.targets() == ['main', 'second']
    assert not session.dirty
    assert badgeFor(window, 'second').text() == '运行入口'
    assert '2 个' in window.startButton.toolTip()


def testTemporaryChoiceCancelAndRestoreNeverOverwriteStartupDefaults(designerApplication, tmp_path, monkeypatch):
    window = makeWindow()
    window._setRunConfiguration(['main', 'second'], 2)
    root = tmp_path / 'project'
    assert window.saveProjectToDirectory(str(root))
    before = window.pageCoordinator.session.payload()
    def temporary(dialog):
        select(dialog, ['second'])
        dialog.temporary.setChecked(True)
        assert not dialog.saveSelection()
        assert not dialog.limit.isEnabled() and not dialog.unlimited.isEnabled()
        return True
    monkeypatch.setattr(RunTargetsDialog, 'exec_', temporary)
    window.openRunTargetsDialog()
    assert window.runtimeController.targets() == ['second']
    assert window.pageCoordinator.session.payload() == before
    assert not window.pageCoordinator.session.dirty
    assert '临时' in window.runTargetsButton.text()
    assert window.saveProjectToDirectory(str(root))
    assert window.loadProjectDirectory(str(root), confirmed=True)
    assert window.runtimeController.targets() == ['main', 'second']
    window.openRunTargetsDialog()
    def cancel(dialog):
        dialog.restoreProjectTargets()
        assert dialog.saveSelection()
        assert dialog.selectedWorkflowIds() == ['main', 'second']
        return False
    monkeypatch.setattr(RunTargetsDialog, 'exec_', cancel)
    window.openRunTargetsDialog()
    assert window.runtimeController.targets() == ['second']
    monkeypatch.setattr(RunTargetsDialog, 'exec_', lambda dialog: (dialog.restoreProjectTargets(), True)[1])
    window.openRunTargetsDialog()
    assert not window.runTargetIds
    assert window.runtimeController.targets() == ['main', 'second']
    assert not window.pageCoordinator.session.dirty


def testLegacyUnchangedSelectionStaysCleanAndRuntimeSelectionIsTemporary(designerApplication, monkeypatch):
    window = makeWindow()
    monkeypatch.setattr(RunTargetsDialog, 'exec_', lambda dialog: True)
    window.openRunTargetsDialog()
    assert 'startupWorkflowIds' not in window.workflowStore.runtime
    assert not window.pageCoordinator.session.dirty
    window.isJobRunning = True
    def choose(dialog):
        assert not dialog.saveSelection() and not dialog.temporary.isEnabled()
        assert not dialog.limit.isEnabled()
        select(dialog, ['second'])
        return True
    monkeypatch.setattr(RunTargetsDialog, 'exec_', choose)
    window.openRunTargetsDialog()
    assert window.runtimeController.targets() == ['second']
    assert not window.pageCoordinator.session.dirty
    with pytest.raises(ValueError, match='停止'):
        window._setRunConfiguration(['main', 'second'], 2)
    window.isJobRunning = False


def testPersistedDifferentEntryDoesNotChangeGraphOrLaunchDuringOpen(designerApplication, tmp_path):
    window = makeWindow()
    window._setRunConfiguration(['second'], 2)
    directory = tmp_path / 'single-other'
    assert window.saveProjectToDirectory(str(directory))
    assert window.loadProjectDirectory(str(directory), confirmed=True)
    assert window.workflowStore.entryWorkflowId == 'main'
    assert window.runtimeController.targets() == ['second']
    assert not window.isJobRunning and not window.currentJobId


def testPortableProjectDeclaresOnlyItsTwoWhileRoots(designerApplication, tmp_path):
    path = saveNavigationProject(tmp_path)
    window = MainWindow(RuntimeClientStub())
    assert window.loadProjectDirectory(str(path), confirmed=True)
    assert window.runtimeController.targets() == ['station1-run', 'station2-run']
    assert not window.pageCoordinator.session.dirty
    assert badgeFor(window, 'station2-run').text() == '运行入口'
