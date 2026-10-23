from copy import deepcopy
import json
from types import SimpleNamespace

import pytest
from PySide2.QtCore import Qt, QSettings
from PySide2.QtWidgets import QMessageBox

from emo_master.apps.designer.services.runtime_client import RuntimeClientError
from emo_master.apps.designer.state.project_store import saveProject
from emo_master.apps.designer.ui.project_configuration import ProjectConfigurationDialog
from tests.runtime.test_device_runtime import simulatedProject
from tests.designer.test_device_workspace import waitUntil
from tests.designer.runtime_feedback_fixture import messages  # noqa: F401


def setupDialog(ownedDesignerWindow, tmp_path, *, allowRun=False):
    local = simulatedProject()
    online = deepcopy(local)
    online["devices"]["definitions"]["camera"]["defaults"]["exposureUs"] = 12000
    snapshot = dict(runtimeInstanceId="instance", documentRevision=8, executionRevision=5, payload=online)
    calls = []
    def write(snapshot, payload, paths, requestId, *, validate=False):
        calls.append((deepcopy(payload), paths, requestId, validate))
        return {"candidate": payload} if validate else {"state": "COMMITTED", "documentRevision": 9}
    client = SimpleNamespace(listOperators=lambda: [], getOnlineProjectSnapshot=lambda projectId: snapshot,
                             writeProjectConfiguration=write)
    window = ownedDesignerWindow(client, settingsStore=QSettings(str(tmp_path / "settings.ini"), QSettings.IniFormat))
    path = tmp_path / "project.emoproj"
    saveProject(path, local)
    assert window.loadProjectDirectory(str(path), confirmed=True)
    window.pageCoordinator.sync()
    dialog = ProjectConfigurationDialog(window.pageCoordinator, window, allowRun=allowRun)
    waitUntil(lambda: dialog.pending is None)
    return window, dialog, calls, path


def testRecoveryErrorAllowsExplicitRepairButNeverRun(ownedDesignerWindow, tmp_path, monkeypatch):
    window, dialog, calls, path = setupDialog(ownedDesignerWindow, tmp_path, allowRun=True)
    try:
        before, disk = window.pageCoordinator.session.payload(), path.read_bytes()
        dialog.snapshot["recoveryError"] = "resource missing or size mismatch"
        dialog._controls()
        assert dialog.writeButton.isEnabled() and dialog.validateButton.isEnabled()
        assert not dialog.runButton.isEnabled()
        for row in range(dialog.table.rowCount()):
            item = dialog.table.item(row, 0)
            if item.data(Qt.UserRole) == "/devices/definitions/camera/defaults/exposureUs":
                item.setCheckState(Qt.Checked)
        dialog.validateButton.click()
        waitUntil(lambda: dialog.pending is None)
        assert calls[-1][3]
        monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.Yes)
        dialog.writeButton.click()
        waitUntil(lambda: dialog.pending is None)
        assert len(calls) == 2 and not calls[-1][3]
        assert window.pageCoordinator.session.payload() == before and path.read_bytes() == disk
    finally:
        dialog.reject()


def testCompareDoesNotSaveOrMutateAndSelectionIsExplicit(ownedDesignerWindow, tmp_path, monkeypatch, messages):  # noqa: F811
    window, dialog, calls, path = setupDialog(ownedDesignerWindow, tmp_path)
    try:
        before = window.pageCoordinator.session.payload()
        disk = path.read_bytes()
        assert not calls
        assert dialog.table.rowCount() > 0
        dialog.write()
        assert not calls and len(messages) == 1 and '请选择' in messages[0]['text']
        pathToSelect = "/devices/definitions/camera/defaults/exposureUs"
        for row in range(dialog.table.rowCount()):
            item = dialog.table.item(row, 0)
            if item.data(Qt.UserRole) == pathToSelect:
                item.setCheckState(Qt.Checked)
        dialog.write(validate=True)
        waitUntil(lambda: dialog.pending is None)
        assert calls[-1][1] == [pathToSelect] and calls[-1][3]
        monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.Yes)
        dialog.write()
        waitUntil(lambda: dialog.pending is None)
        assert calls[-1][1] == [pathToSelect] and not calls[-1][3]
        assert window.pageCoordinator.session.payload() == before
        assert path.read_bytes() == disk
    finally:
        dialog.reject()


def testSelectAllChangesWritesAllPathsAfterValidation(ownedDesignerWindow, tmp_path, monkeypatch):
    window, dialog, calls, path = setupDialog(ownedDesignerWindow, tmp_path)
    try:
        before, disk = window.pageCoordinator.session.payload(), path.read_bytes()
        dialog.snapshot['payload']['project']['name'] = 'Online name'
        dialog._showSnapshot(dialog.snapshot)
        dialog._controls()
        paths = [dialog.table.item(row, 0).data(Qt.UserRole) for row in range(dialog.table.rowCount())]
        assert len(paths) > 1
        assert dialog.selectAllCheckBox.checkState() == Qt.Unchecked
        dialog.selectAllCheckBox.click()
        assert dialog.selectAllCheckBox.checkState() == Qt.Checked
        assert all(dialog.table.item(row, 0).checkState() == Qt.Checked for row in range(len(paths)))
        assert not calls

        dialog.validateButton.click()
        waitUntil(lambda: dialog.pending is None)
        assert calls[-1][1] == paths and calls[-1][3]
        assert dialog.validatedSelection is not None
        dialog.table.item(0, 0).setCheckState(Qt.Unchecked)
        assert dialog.selectAllCheckBox.checkState() == Qt.PartiallyChecked
        assert dialog.validatedSelection is None
        dialog.selectAllCheckBox.click()
        assert dialog.selectAllCheckBox.checkState() == Qt.Checked
        dialog.selectAllCheckBox.click()
        assert dialog.selectAllCheckBox.checkState() == Qt.Unchecked
        assert all(dialog.table.item(row, 0).checkState() == Qt.Unchecked for row in range(len(paths)))

        dialog.selectAllCheckBox.click()
        monkeypatch.setattr(QMessageBox, 'question', lambda *_: QMessageBox.Yes)
        dialog.writeButton.click()
        waitUntil(lambda: dialog.pending is None)
        assert [call[3] for call in calls] == [True, True, False]
        assert all(call[1] == paths for call in calls)
        assert window.pageCoordinator.session.payload() == before and path.read_bytes() == disk
    finally:
        dialog.reject()


def testSelectAllDisabledWithoutDifferencesOrWhileBusy(ownedDesignerWindow, tmp_path):
    window, dialog, calls, path = setupDialog(ownedDesignerWindow, tmp_path)
    try:
        assert dialog.selectAllCheckBox.isEnabled()
        dialog.confirming = True
        dialog._controls()
        assert not dialog.selectAllCheckBox.isEnabled()
        dialog.confirming = False
        dialog._controls()
        assert dialog.selectAllCheckBox.isEnabled()
        dialog.selectAllCheckBox.click()
        dialog._showSnapshot(dialog.snapshot)
        assert dialog.selectAllCheckBox.checkState() == Qt.Unchecked
        dialog.snapshot['payload'] = deepcopy(dialog.local)
        dialog._showSnapshot(dialog.snapshot)
        dialog._controls()
        assert not dialog.selectAllCheckBox.isEnabled()
        assert dialog.selectAllCheckBox.checkState() == Qt.Unchecked
        dialog._showInitial()
        dialog._controls()
        assert dialog.selectAllCheckBox.isHidden()
        assert dialog.closeButton.text() == '关闭'
        assert not calls
    finally:
        dialog.reject()


def testCloseButtonAfterCommittedWriteKeepsDraftAndDoesNotWriteAgain(
        ownedDesignerWindow, tmp_path, monkeypatch):
    window, dialog, calls, path = setupDialog(ownedDesignerWindow, tmp_path)
    try:
        before, disk = window.pageCoordinator.session.payload(), path.read_bytes()
        assert dialog.table.rowCount() > 0
        assert dialog.closeButton.text() == '保持差异并关闭'
        originalWrite = dialog.client.writeProjectConfiguration

        def write(snapshot, payload, paths, requestId, *, validate=False):
            result = originalWrite(snapshot, payload, paths, requestId, validate=validate)
            if not validate:
                snapshot['payload'] = deepcopy(payload)
                snapshot['documentRevision'] = result['documentRevision']
            return result

        monkeypatch.setattr(dialog.client, 'writeProjectConfiguration', write)
        monkeypatch.setattr(QMessageBox, 'question', lambda *_: QMessageBox.Yes)
        dialog.selectAllCheckBox.click()
        dialog.writeButton.click()
        waitUntil(lambda: dialog.pending is None)
        assert [call[3] for call in calls] == [True, False]
        assert dialog.table.rowCount() == 0 and '已提交' in dialog.status.text()
        assert dialog.closeButton.text() == '关闭'
        assert not dialog.writeButton.isEnabled() and not dialog.validateButton.isEnabled()
        dialog.show()
        from PySide2.QtWidgets import QApplication
        QApplication.processEvents()
        assert dialog.grab().save(str(tmp_path / 'committed-no-differences.png'))
        assert dialog.rect().contains(dialog.closeButton.geometry())
        dialog.closeButton.click()
        assert dialog.closed and not dialog.isVisible()
        assert [call[3] for call in calls] == [True, False]
        assert window.pageCoordinator.session.payload() == before and path.read_bytes() == disk
    finally:
        dialog.reject()


def testReadBackIsUndoableDraftNotDiskWrite(ownedDesignerWindow, tmp_path, monkeypatch):
    window, dialog, calls, path = setupDialog(ownedDesignerWindow, tmp_path)
    try:
        before = window.pageCoordinator.session.payload()
        disk = path.read_bytes()
        monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.Yes)
        dialog.readBack()
        assert window.pageCoordinator.session.document().devices.definitions["camera"].defaults["exposureUs"] == 12000
        assert window.pageCoordinator.session.dirty
        assert not calls and path.read_bytes() == disk
        window.pageCoordinator.history()
        assert window.pageCoordinator.session.payload() == before
    finally:
        dialog.reject()


@pytest.mark.parametrize('changed', [False, True])
def testReadBackFeedbackSurvivesPollAndLaterEditsInvalidateValidation(
        ownedDesignerWindow, tmp_path, monkeypatch, changed):
    window, dialog, calls, path = setupDialog(ownedDesignerWindow, tmp_path)
    try:
        session = window.pageCoordinator.session
        before, disk = session.payload(), path.read_bytes()
        # Use Designer-normalized nodes so this checks read-back, not migration.
        dialog.snapshot['payload'] = deepcopy(before)
        if changed:
            dialog.snapshot['payload']['devices']['definitions']['camera']['defaults']['exposureUs'] = 12000
        dialog._showSnapshot(dialog.snapshot)
        monkeypatch.setattr(QMessageBox, 'question', lambda *_: QMessageBox.Yes)
        dialog.readBack()
        feedback = dialog.status.text()
        dialog.poll()
        assert dialog.status.text() == feedback
        assert '已读入工程草稿' in feedback and '无需写入' in feedback
        assert dialog.draftCheckpoint is session._checkpoint
        assert dialog.table.rowCount() == 0
        assert dialog.closeButton.text() == '关闭'
        assert not dialog.validateButton.isEnabled() and not dialog.writeButton.isEnabled()
        assert '无需校验' in dialog.impact.text()
        assert not dialog.details.toPlainText()
        assert session.dirty == changed
        assert path.read_bytes() == disk and not calls

        dialog.validationSelection = dialog.validatedSelection = ('old-selection',)
        with session.transaction():
            session.workflows.devices['definitions']['camera']['defaults']['exposureUs'] = 16000
        dialog.poll()
        assert '草稿已变化' in dialog.status.text()
        assert dialog.validationSelection is None and dialog.validatedSelection is None
        assert dialog.table.rowCount() == 1 and dialog.validateButton.isEnabled()
        assert dialog.closeButton.text() == '保持差异并关闭'
        dialog._showChangeDetails(0)
        assert dialog.details.toPlainText()
        window.pageCoordinator.history()
        dialog.poll()
        assert dialog.table.rowCount() == 0 and '无需写入' in dialog.status.text()
        assert dialog.closeButton.text() == '关闭'
        assert not dialog.details.toPlainText() and '无需校验' in dialog.impact.text()
        if changed:
            window.pageCoordinator.history()
        assert session.payload() == before and path.read_bytes() == disk and not calls
    finally:
        dialog.reject()


def testNoDifferencesStillReportsRuntimeRecoveryError(ownedDesignerWindow, tmp_path):
    window, dialog, calls, path = setupDialog(ownedDesignerWindow, tmp_path, allowRun=True)
    try:
        dialog.snapshot['payload'] = deepcopy(dialog.local)
        dialog.snapshot['recoveryError'] = 'resource missing'
        dialog._showSnapshot(dialog.snapshot)
        dialog._controls()
        assert '无需写入' in dialog.status.text()
        assert 'resource missing' in dialog.status.text()
        assert dialog.status.property('feedbackState') == 'warning'
        assert not dialog.runButton.isEnabled() and not calls
    finally:
        dialog.reject()


def testRuntimeSwitchFencesOldDialog(ownedDesignerWindow, tmp_path):
    window, dialog, calls, path = setupDialog(ownedDesignerWindow, tmp_path)
    try:
        before = window.pageCoordinator.session.payload()
        dialog.table.item(0, 0).setCheckState(Qt.Checked)
        original = window.runtimeClient
        window.runtimeClient = SimpleNamespace()
        dialog.write()
        dialog.readBack()
        assert not calls and window.pageCoordinator.session.payload() == before
        window.runtimeClient = original
    finally:
        dialog.reject()


def testDifferentOnlineProjectSelectsCompleteReplacementAndReadBackRequiresConsent(ownedDesignerWindow, tmp_path, monkeypatch):
    window, dialog, calls, path = setupDialog(ownedDesignerWindow, tmp_path)
    try:
        before, disk = window.pageCoordinator.session.payload(), path.read_bytes()
        dialog.snapshot["payload"]["project"]["projectId"] = "different-online-project"
        dialog._showSnapshot(dialog.snapshot)
        dialog._controls()
        assert dialog.replacementPolicy.currentData() == "stopAffected"
        assert all(dialog.table.item(row, 0).checkState() == Qt.Checked for row in range(dialog.table.rowCount()))
        monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.No)
        dialog.readButton.click()
        assert window.pageCoordinator.session.payload() == before
        monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.Yes)
        dialog.readButton.click()
        assert window.pageCoordinator.session.document().project.projectId == "different-online-project"
        assert dialog._valid() and path.read_bytes() == disk and not calls
        window.pageCoordinator.history()
        assert window.pageCoordinator.session.payload() == before
    finally:
        dialog.reject()


def testDeviceDependenciesRequireConfirmationAndLeaveUnselectedDraftOut(ownedDesignerWindow, tmp_path, monkeypatch):
    window, dialog, calls, path = setupDialog(ownedDesignerWindow, tmp_path)
    try:
        disk = path.read_bytes()
        before = window.pageCoordinator.session.payload()
        local = dialog.local
        device = deepcopy(local["devices"]["definitions"]["camera"])
        device["connection"]["serial"] = "NEW"
        local["devices"]["definitions"]["new-camera"] = device
        capture = next(node for node in local["workflows"]["main"]["nodes"] if node["nodeId"] == "capture")
        capture["deviceBindings"]["camera"]["deviceId"] = "new-camera"
        dialog._showSnapshot(dialog.snapshot)
        bindingPath = "/workflows/main/nodes/capture/deviceBindings/camera/deviceId"
        for row in range(dialog.table.rowCount()):
            item = dialog.table.item(row, 0)
            if item.data(Qt.UserRole) == bindingPath:
                item.setCheckState(Qt.Checked)
                assert dialog.table.item(row, 3).text() == "设备引用"
        monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.No)
        dialog.write(validate=True)
        assert not calls
        monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.Yes)
        dialog.write(validate=True)
        waitUntil(lambda: dialog.pending is None)
        assert set(calls[-1][1]) == {bindingPath, "/devices/definitions/new-camera"}
        assert path.read_bytes() == disk and window.pageCoordinator.session.payload() == before
    finally:
        dialog.reject()


def testResourceUploadRequiresConfirmationAndDoesNotPublishDraft(ownedDesignerWindow, tmp_path, monkeypatch):
    import hashlib
    window, dialog, calls, path = setupDialog(ownedDesignerWindow, tmp_path)
    uploads = []
    def upload(snapshot, payload, root, cancelled, *, repairConfirmed=False):
        assert repairConfirmed
        uploads.append((snapshot, payload, root, cancelled))
        return dict(uploaded=[dict(resourceId="model", state="READY")])
    dialog.client.uploadProjectResources = upload
    try:
        before, disk = window.pageCoordinator.session.payload(), path.read_bytes()
        dialog.local["resources"]["items"]["model"] = dict(path="models/model.bin", size=3,
            sha256=hashlib.sha256(b"abc").hexdigest(), purpose="model")
        dialog._showSnapshot(dialog.snapshot)
        for row in range(dialog.table.rowCount()):
            item = dialog.table.item(row, 0)
            if item.data(Qt.UserRole) == "/resources/items/model":
                item.setCheckState(Qt.Checked)
        monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.No)
        dialog.uploadResources()
        assert not uploads
        monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.Yes)
        dialog.uploadResources()
        waitUntil(lambda: dialog.pending is None)
        assert len(uploads) == 1 and not calls
        assert uploads[0][1]["resources"]["items"]["model"]["size"] == 3
        assert uploads[0][1]["devices"] == dialog.snapshot["payload"]["devices"]
        assert "在线配置未改变" in dialog.status.text()
        dialog.show()
        from PySide2.QtWidgets import QApplication
        QApplication.processEvents()
        assert dialog.grab().save(str(tmp_path / "resource-upload-comparison.png"))
        for button in (dialog.uploadButton, dialog.queryButton, dialog.cancelButton, dialog.graphButton, dialog.closeButton):
            assert dialog.rect().contains(button.geometry())
        assert window.pageCoordinator.session.payload() == before and path.read_bytes() == disk
    finally:
        dialog.reject()
    assert uploads[0][3].is_set()


def testChangeLabelsUseWorkflowNodeParameterAndVariableNames(ownedDesignerWindow, tmp_path):
    window, dialog, calls, path = setupDialog(ownedDesignerWindow, tmp_path)
    try:
        flow = dialog.local['workflows']['main']
        flow['name'] = '取帧并检测'
        node = next(n for n in flow['nodes'] if n['nodeId'] == 'capture')
        node['displayName'] = '读取视频'
        node['paramSchema'] = {'properties': {'intervalSec': {'title': '取帧间隔（秒）'}}}
        dialog.local['globalVariables'] = {'vehicleIndex': {'name': '候选车辆索引', 'initialValue': 0}}
        cases = [
            (['dependencies', 'operators'], [], [{'operatorId': 'example'}], '所需算子与插件', '原来 0 个，现在 1 个'),
            (['globalVariables', 'vehicleIndex'], None, dialog.local['globalVariables']['vehicleIndex'],
             '工程变量：候选车辆索引', '初始值：0'),
            (['workflows', 'main', 'edges'], [], [{}, {}], '流程：取帧并检测', '节点连线：0 项 → 2 项'),
            (['workflows', 'main', 'nodes', 'capture', 'params', 'intervalSec'], 1, 2,
             '流程：取帧并检测 / 读取视频', '取帧间隔（秒）：1 → 2'),
            (['workflowOrder'], [], ['main'], '流程排列顺序', '取帧并检测'),
        ]
        for parts, before, after, title, summary in cases:
            change = dict(parts=parts, path='/' + '/'.join(parts), before=before, after=after, operation='replace')
            labels = dialog._changeLabels(change)
            assert labels[0] == title and summary in labels[2]
            assert '· 0 项' not in labels[0]
        dialog._showSnapshot(dialog.snapshot)
        for row in range(dialog.table.rowCount()):
            for col in (0, 1, 2):
                assert not dialog.table.item(row, col).toolTip().startswith('{')
        assert not calls
    finally:
        dialog.reject()


def testHumanDetailsAndEffectsKeepTechnicalDataOptional(ownedDesignerWindow, tmp_path):
    window, dialog, calls, path = setupDialog(ownedDesignerWindow, tmp_path)
    try:
        dialog.local['workflows']['main']['name'] = '检测流程'
        dialog.local['devices']['definitions']['camera']['name'] = '入口相机'
        dialog.table.setCurrentCell(0, 0)
        dialog.table.item(0, 0).setCheckState(Qt.Checked)
        key = dialog.table.item(0, 0).data(Qt.UserRole)
        assert '检查后确定' in dialog.details.toPlainText()
        dialog._showEffects({'changeEffects': {key: dict(effect='next_cycle', workflows=['main'], devices=['camera'])}})
        assert '下一检测周期' in dialog.details.toPlainText()
        assert '检测流程' in dialog.table.item(0, 5).text()
        assert '入口相机' in dialog.table.item(0, 5).text()
        dialog.technicalDetails.click()
        assert json.loads(dialog.details.toPlainText())['path'] == key
        dialog.technicalDetails.click()
        assert '修改位置：' in dialog.details.toPlainText()
        assert '"parts"' not in dialog.details.toPlainText()
        assert not calls
    finally:
        dialog.reject()


@pytest.mark.parametrize('initial', [False, True])
@pytest.mark.parametrize('bounds', [(-(1 << 63), (1 << 63) - 1),
                                   (-9223372036854776000, 9223372036854776000)])
def testConfigurationDetailsPreserveLargeIntegersWithoutQtConversion(
        ownedDesignerWindow, tmp_path, initial, bounds):
    window, dialog, calls, path = setupDialog(ownedDesignerWindow, tmp_path)
    try:
        disk, draft = path.read_bytes(), window.pageCoordinator.session.payload()
        schema = dict(type='object', properties=dict(delta=dict(
            type='integer', minimum=bounds[0], maximum=bounds[1], default=1)))
        localNode = next(node for node in dialog.local['workflows']['main']['nodes']
                         if node['nodeId'] == 'capture')
        localNode['paramSchema'] = schema
        if initial:
            dialog._showInitial()
            expectedPath = 'workflows'
        else:
            dialog._showSnapshot(dialog.snapshot)
            expectedPath = '/workflows/main/nodes/capture/paramSchema'
        found = False
        for row in range(dialog.table.rowCount()):
            stored = dialog.table.item(row, 0).data(Qt.UserRole + 1)
            assert isinstance(stored, str)
            change = json.loads(stored)
            if change['path'] != expectedPath:
                continue
            actual = (next(node for node in change['after']['main']['nodes'] if node['nodeId'] == 'capture')['paramSchema']
                      if initial else change['after'])
            assert actual == schema
            dialog._showChangeDetails(row)
            assert '修改位置：' in dialog.details.toPlainText()
            dialog.table.setCurrentCell(row, 0)
            dialog.technicalDetails.setChecked(True)
            assert json.loads(dialog.details.toPlainText()) == change
            dialog.technicalDetails.setChecked(False)
            found = True
        assert found and not dialog.table.signalsBlocked()
        assert not calls and path.read_bytes() == disk
        assert window.pageCoordinator.session.payload() == draft
    finally:
        dialog.reject()


@pytest.mark.parametrize('stage', ['read', 'display'])
def testSnapshotFailureIdentifiesStageAndCanRetryWithoutWriting(
        ownedDesignerWindow, tmp_path, monkeypatch, messages, stage):  # noqa: F811
    window, dialog, calls, path = setupDialog(ownedDesignerWindow, tmp_path)
    try:
        disk, draft = path.read_bytes(), window.pageCoordinator.session.payload()
        def fail(*_args):
            if stage == 'read':
                raise RuntimeClientError('UNAVAILABLE', '')
            raise OverflowError()
        with monkeypatch.context() as patch:
            if stage == 'read':
                patch.setattr(dialog.client, 'getOnlineProjectSnapshot', fail)
            else:
                patch.setattr(dialog, '_changeLabels', fail)
            dialog.refresh()
            waitUntil(lambda: dialog.pending is None)
        assert len(messages) == 1
        message = messages[0]
        assert message['title'] == ('读取 Runtime 在线配置失败' if stage == 'read' else '工程配置差异显示失败')
        assert message['text'].strip() and '未写入 Runtime' in message['text']
        assert ('RuntimeClientError' if stage == 'read' else 'OverflowError') in message['detail']
        assert 'Traceback' in message['detail']
        if stage == 'display':
            assert '已读取 Runtime 在线配置' in message['text']
            assert '/devices/definitions/camera/defaults/exposureUs' in message['text']
            assert '8000' in message['detail'] and '12000' in message['detail']
            assert '差异显示失败' in window.pageCoordinator.onlineSummary
        else:
            assert 'UNAVAILABLE' in message['detail']
        assert dialog.snapshot is None and not dialog.table.signalsBlocked()
        assert dialog.closeButton.text() == '关闭'
        assert dialog.refreshButton.isEnabled() and not dialog.writeButton.isEnabled()
        dialog.refresh()
        waitUntil(lambda: dialog.pending is None)
        assert dialog.snapshot is not None and dialog.writeButton.isEnabled()
        assert dialog.closeButton.text() == '保持差异并关闭'
        assert len(messages) == 1 and not calls and path.read_bytes() == disk
        assert window.pageCoordinator.session.payload() == draft
    finally:
        dialog.reject()


def testErrorDetailsKeepEmptyExceptionTypeAndChainedTraceback():
    assert 'OverflowError' in ProjectConfigurationDialog._errorDetails(OverflowError())
    try:
        try:
            raise OverflowError()
        except OverflowError as cause:
            raise ValueError('field /workflows/main/nodes/index') from cause
    except ValueError as error:
        details = ProjectConfigurationDialog._errorDetails(error)
    assert 'OverflowError' in details and 'ValueError' in details
    assert 'Traceback' in details and '/workflows/main/nodes/index' in details


def testNotificationNeverShowsBlankBodyOrWhitespaceOnlyDetails(
        ownedDesignerWindow, tmp_path, messages):  # noqa: F811
    window, dialog, calls, path = setupDialog(ownedDesignerWindow, tmp_path)
    try:
        dialog._notify('工程与 Runtime 操作失败', '  ', '\n  ')
        assert messages[-1]['text'] == '工程与 Runtime 操作失败'
        assert messages[-1]['detail'] == ''
        assert not calls
    finally:
        dialog.reject()
