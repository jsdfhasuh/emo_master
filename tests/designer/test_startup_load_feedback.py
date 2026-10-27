"""Startup load failures stay visible and allow an explicit new selection."""
import json
from types import SimpleNamespace

import pytest
from PySide2.QtCore import QTimer
from PySide2.QtWidgets import QFileDialog, QMessageBox

from emo_master.apps.designer.state.workflow_store import WorkflowStore
from emo_master.apps.designer.ui.project_entry_dialog import ProjectEntryDialog
from tests.core.project.test_devices import legacyPayload
from tests.runtime.test_operator_debug_rpc import runtime  # noqa: F401


@pytest.mark.parametrize('failure', ['runtime', 'transport', 'json', 'missing'])
@pytest.mark.parametrize('nextAction', ['cancel', 'open'])
def testLegacyStartupFailureExplainsAndWaitsForExplicitSelection(
        ownedDesignerWindow, monkeypatch, tmp_path, failure, nextAction):
    invalid = tmp_path / '旧工程.emoproj'
    valid = tmp_path / '有效工程.emoproj'
    valid.write_text(json.dumps(legacyPayload()), encoding='utf-8')
    if failure != 'missing':
        invalid.write_text('{broken' if failure == 'json' else valid.read_text(encoding='utf-8'), encoding='utf-8')
    original = invalid.read_bytes() if invalid.exists() else None
    calls, observed = [], []

    def load(path):
        calls.append(path)
        rejected = path == str(invalid)
        if rejected and failure == 'transport':
            from emo_master.apps.designer.services.runtime_client import RuntimeClientError
            raise RuntimeClientError('E_RUNTIME_UNAVAILABLE', 'Runtime disconnected')
        return SimpleNamespace(ok=not rejected, code='E_PROJECT_INVALID' if rejected else '',
                               message='main/node-01211f2b: ipAddress must be a valid IPv4 address' if rejected else 'ok')

    client = SimpleNamespace(listOperators=lambda: [], loadProject=load)
    window = ownedDesignerWindow(client)

    class Entry(ProjectEntryDialog):
        attempts = 0

        def execSelection(self):
            self.attempts += 1
            if self.attempts == 1:
                QTimer.singleShot(0, self._onOpenProject)
            else:
                observed.append(self._loadErrorEdit.toPlainText())
                # Esc/window-close after an error must not replay the old Open.
                QTimer.singleShot(0, self.reject if nextAction == 'cancel' else self._onOpenProject)
            return super().execSelection()

    entry = Entry(window)
    paths = iter([str(invalid), str(valid)])
    monkeypatch.setattr(QFileDialog, 'getOpenFileName', lambda *_: (next(paths), ''))
    monkeypatch.setattr(QMessageBox, 'warning', lambda *_: pytest.fail('startup error should stay in the chooser'))
    window._projectEntryDialogFactory = lambda: entry
    assert window.showStartupProjectEntry() is (nextAction == 'open')
    assert entry.attempts == 2
    assert observed and str(invalid) in observed[0]
    assert '未被修改' in observed[0] and '选择' in observed[0]
    if failure == 'runtime':
        assert 'E_PROJECT_INVALID' in observed[0]
        assert 'ipAddress' in observed[0]
        assert calls.count(str(invalid)) == 1
    elif failure == 'transport':
        assert 'E_RUNTIME_UNAVAILABLE' in observed[0]
        assert calls.count(str(invalid)) == 1
    else:
        assert str(invalid) not in calls
    assert (invalid.read_bytes() if invalid.exists() else None) == original
    assert window.loadedProjectPath == (str(valid) if nextAction == 'open' else None)


def testCurrentDraftStartupDoesNotLoadRuntime(ownedDesignerWindow, tmp_path):
    project = tmp_path / 'draft.emoproj'
    payload = WorkflowStore().toPayload()
    assert payload['schemaVersion'] == '2.7'
    original = json.dumps(payload).encode('utf-8')
    project.write_bytes(original)
    client = SimpleNamespace(listOperators=lambda: [],
        loadProject=lambda *_: pytest.fail('opening a current draft must not load Runtime'))
    window = ownedDesignerWindow(client)
    window._projectEntryDialogFactory = lambda: SimpleNamespace(
        execSelection=lambda: ('open_project', str(project)))
    assert window.showStartupProjectEntry()
    assert window.loadedProjectPath == str(project)
    assert window.pageCoordinator.session.document().schemaVersion == '2.7'
    assert project.read_bytes() == original


def testRealLegacyHuarayBindingFailureRemainsInChooser(
        runtime, ownedDesignerWindow, monkeypatch, tmp_path):  # noqa: F811
    from emo_master.apps.designer.services.runtime_client import RuntimeClient
    from emo_master.plugins.builtins.huaray_camera.operator import HuarayCameraOperator
    payload = legacyPayload()
    assert payload['schemaVersion'] == '2.1'
    payload['workflows']['main']['nodes'].append(dict(
        nodeId='node-01211f2b', kind='operator', operatorId='vision.io.huaray_camera', params={}))
    project = tmp_path / 'followup.emoproj'
    original = json.dumps(payload).encode('utf-8')
    project.write_bytes(original)
    window = ownedDesignerWindow(RuntimeClient(runtime))
    observed = []

    class Entry(ProjectEntryDialog):
        attempts = 0

        def execSelection(self):
            self.attempts += 1
            if self.attempts == 1:
                QTimer.singleShot(0, self._onOpenProject)
            else:
                def cancelAfterReading():
                    observed.append((self.isVisible(), self._loadErrorEdit.isVisible(),
                                     self._loadErrorEdit.toPlainText()))
                    self.reject()
                QTimer.singleShot(0, cancelAfterReading)
            return super().execSelection()

    entry = Entry(window)
    window._projectEntryDialogFactory = lambda: entry
    monkeypatch.setattr(QFileDialog, 'getOpenFileName', lambda *_: (str(project), ''))
    monkeypatch.setattr(HuarayCameraOperator, 'executeNode',
                        lambda *_: pytest.fail('startup validation must not execute a camera'))
    assert not window.showStartupProjectEntry()
    assert entry.attempts == 2 and observed[0][:2] == (True, True)
    assert 'device slots do not match the operator declaration' in observed[0][2]
    assert 'E_DEVICE_BINDING' in observed[0][2]
    assert 'main/node-01211f2b/deviceBindings.camera' in observed[0][2] and str(project) in observed[0][2]
    assert project.read_bytes() == original
    assert not runtime.jobRepository.all() and window.loadedProjectPath is None


@pytest.mark.parametrize('kind', ['operator', 'workflow'])
def testProjectRefusalNoticeBelongsToForegroundDebug(ownedDesignerWindow, kind):
    from shiboken2 import isValid
    from tests.designer.test_debug_main_window_boundaries import PendingDebug, addOperator
    window = ownedDesignerWindow(SimpleNamespace(listOperators=lambda: []))
    window.show()
    debug = PendingDebug(window)
    if kind == 'operator':
        addOperator(window, debug)
    else:
        window._workflowDebugWindow = debug
    try:
        assert not window._allowProjectReplacement()
        notice = window._projectReplacementNotice
        assert notice.isVisible() and notice.parentWidget() is debug
        assert '当前项目保持不变' in notice.messageLabel.text() and '结束调试' in notice.messageLabel.text()
        assert not window._allowProjectReplacement()
        assert window._projectReplacementNotice is notice  # No dialog flood.
        assert debug.attempts == 0 and isValid(debug) and not debug.disposed
        notice.accept()
    finally:
        debug.retire()
        window.operatorEditorManager._windows.clear()
