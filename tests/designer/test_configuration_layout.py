from copy import deepcopy

import pytest
from PySide2.QtCore import Qt
from PySide2.QtWidgets import QDialog, QMessageBox

from emo_master.apps.designer.state.project_store import loadProjectDocument, saveProject
from emo_master.core.project.configuration import projectChanges, selectedCandidate
from tests.designer.test_device_workspace import waitUntil
from tests.designer.test_project_configuration_ui import setupDialog
from tests.designer.runtime_feedback_fixture import messages  # noqa: F401


def testPositionOnlyChangesHaveNoConfigurationPrompt(ownedDesignerWindow, tmp_path, monkeypatch):
    window, dialog, calls, path = setupDialog(ownedDesignerWindow, tmp_path)
    try:
        session = window.pageCoordinator.session
        before, disk = session.payload(), path.read_bytes()
        dialog.snapshot['payload'] = deepcopy(before)
        position = dict(x=460.0, y=280.0)
        with session.transaction():
            session.workflows.get('main').layout['nodePositions']['capture'] = position
        window.workflowController._renderActive()
        dialog.poll()
        draft = session.payload()
        assert projectChanges(before, draft)
        assert dialog.table.rowCount() == 0
        assert '0 项差异' in dialog.status.text() and '运行配置一致，无需写入' in dialog.status.text()
        assert dialog.closeButton.text() == '关闭'
        assert not dialog.validateButton.isEnabled() and not dialog.writeButton.isEnabled()
        assert not dialog.selectAllCheckBox.isEnabled() and not dialog.details.toPlainText()
        assert '无需校验' in dialog.impact.text()
        monkeypatch.setattr(window.pageCoordinator, 'configureRuntime',
                            lambda **_: pytest.fail('moving a node must not prompt for publication'))
        assert window._syncRuntimeProjectBeforeRun()
        assert session.payload() == draft and session.dirty
        assert not calls and path.read_bytes() == disk

        saved = tmp_path / 'moved.emoproj'
        saveProject(saved, draft)
        assert loadProjectDocument(saved).workflows['main'].layout.nodePositions['capture'] == position
        window.pageCoordinator.history()
        dialog.poll()
        assert session.payload() == before and dialog.table.rowCount() == 0
        window.pageCoordinator.history(redo=True)
        dialog.poll()
        assert session.payload() == draft and dialog.table.rowCount() == 0
        dialog.show()
        from PySide2.QtWidgets import QApplication
        QApplication.processEvents()
        assert dialog.grab().save(str(tmp_path / 'layout-only-no-differences.png'))
        assert dialog.rect().contains(dialog.closeButton.geometry())
        dialog.closeButton.click()
        assert dialog.closed and not calls and path.read_bytes() == disk
        assert window.loadProjectDirectory(str(saved), confirmed=True)
        assert session.payload()['workflows']['main']['layout']['nodePositions']['capture'] == position
    finally:
        dialog.reject()


@pytest.mark.parametrize('replacement', [False, True])
def testSelectedWriteDoesNotLeavePositionOnlyDifferences(
        ownedDesignerWindow, tmp_path, monkeypatch, replacement, messages):  # noqa: F811
    window, dialog, calls, path = setupDialog(ownedDesignerWindow, tmp_path)
    try:
        before, disk = window.pageCoordinator.session.payload(), path.read_bytes()
        online = deepcopy(before)
        online['workflows']['main']['layout']['nodePositions']['capture'] = dict(x=1, y=2)
        online['devices']['definitions']['camera']['defaults']['exposureUs'] = 12000
        if replacement:
            online['project']['projectId'] = 'other-online-project'
        dialog.snapshot['payload'] = online
        dialog._showSnapshot(dialog.snapshot)
        dialog._controls()
        paths = [dialog.table.item(row, 0).data(Qt.UserRole) for row in range(dialog.table.rowCount())]
        if replacement:
            assert paths == [change['path'] for change in projectChanges(online, dialog.local)]
            assert all(dialog.table.item(row, 0).checkState() == Qt.Checked for row in range(len(paths)))
        else:
            assert paths == ['/devices/definitions/camera/defaults/exposureUs']
            dialog.selectAllCheckBox.click()

        originalWrite = dialog.client.writeProjectConfiguration

        def write(snapshot, payload, selected, requestId, *, validate=False, replacementPolicy='reject'):
            assert replacementPolicy == ('stopAffected' if replacement else 'reject')
            result = originalWrite(snapshot, payload, selected, requestId, validate=validate)
            candidate = selectedCandidate(snapshot['payload'], payload, selected)
            if validate:
                result['candidate'] = candidate
            else:
                snapshot['payload'] = candidate
                snapshot['documentRevision'] = result['documentRevision']
            return result

        monkeypatch.setattr(dialog.client, 'writeProjectConfiguration', write)
        monkeypatch.setattr(QMessageBox, 'question', lambda *_: QMessageBox.Yes)
        dialog.writeButton.click()
        waitUntil(lambda: dialog.pending is None)
        assert not messages
        assert [call[3] for call in calls] == [True, False]
        assert all(call[1] == paths for call in calls)
        assert dialog.table.rowCount() == 0 and dialog.closeButton.text() == '关闭'
        assert '无需写入' in dialog.status.text()
        assert not dialog.validateButton.isEnabled() and not dialog.writeButton.isEnabled()
        if replacement:
            assert not projectChanges(dialog.snapshot['payload'], before)
        else:
            assert dialog.snapshot['payload']['workflows']['main']['layout'] == online['workflows']['main']['layout']
        assert window.pageCoordinator.session.payload() == before and path.read_bytes() == disk
    finally:
        dialog.reject()


@pytest.mark.parametrize('change', ['parameter', 'connection', 'node', 'label', 'recovery', 'project'])
def testPositionFilteringDoesNotBypassOtherRunChecks(ownedDesignerWindow, tmp_path, monkeypatch, change):
    window, dialog, calls, path = setupDialog(ownedDesignerWindow, tmp_path)
    try:
        before, disk = window.pageCoordinator.session.payload(), path.read_bytes()
        online = deepcopy(before)
        flow = online['workflows']['main']
        flow['layout']['nodePositions']['capture'] = dict(x=10, y=20)
        if change == 'parameter':
            online['devices']['definitions']['camera']['defaults']['exposureUs'] = 12000
        elif change == 'connection':
            output = next(node['nodeId'] for node in flow['nodes'] if node['kind'] == 'workflow_output')
            flow['edges'] = [dict(fromNode='capture', fromPort='image', toNode=output, toPort='image')]
        elif change == 'node':
            flow['nodes'] = [node for node in flow['nodes'] if node['nodeId'] != 'capture']
        elif change == 'label':
            flow['name'] = 'Renamed workflow'
        elif change == 'recovery':
            dialog.snapshot['recoveryError'] = 'resource missing'
        elif change == 'project':
            online['project']['projectId'] = 'other-online-project'
        dialog.snapshot['payload'] = online
        prompts = []
        monkeypatch.setattr(window.pageCoordinator, 'configureRuntime',
                            lambda **kwargs: prompts.append(kwargs) or QDialog.Rejected)
        assert not window._syncRuntimeProjectBeforeRun()
        assert prompts == [dict(allowRun=True)]
        assert not calls and path.read_bytes() == disk
        assert window.pageCoordinator.session.payload() == before
    finally:
        dialog.reject()
