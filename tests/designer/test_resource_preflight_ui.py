"""Missing candidate resources have a normal upload-and-revalidate exit."""
from copy import deepcopy
import os
from types import SimpleNamespace

import pytest
from PySide2.QtCore import Qt
from PySide2.QtWidgets import QApplication, QLabel, QMessageBox, QTextEdit

from emo_master.apps.designer.state.workflow_store import WorkflowStore
from emo_master.apps.designer.ui.project_configuration import ProjectConfigurationDialog, configurationDiagnostics
from emo_master.core.project.models import ProjectDocument
from emo_master.core.project.snapshots import verifyResources
from emo_master.core.workflow.errors import WorkflowCompileError
from tests.designer.runtime_feedback_fixture import messages  # noqa: F401
from tests.designer.test_device_workspace import waitUntil
from tests.runtime.test_initial_project_configuration import initialRuntime, waitForWrite  # noqa: F401
from tests.runtime.test_project_resource_preflight import resourceProject


@pytest.mark.parametrize('code,field,phrase', [
    ('E_RESOURCE_MISSING', 'path', 'Runtime \u7f3a\u5c11\u6b64\u6587\u4ef6'),
    ('E_RESOURCE_SIZE', 'size', '\u5927\u5c0f\u4e0e\u5de5\u7a0b\u8bb0\u5f55\u4e0d\u4e00\u81f4'),
    ('E_RESOURCE_DIGEST', 'sha256', '\u5185\u5bb9\u4e0e\u5de5\u7a0b\u8bb0\u5f55\u4e0d\u4e00\u81f4'),
])
def testResourceDiagnosticNamesFileAndUploadAction(tmp_path, code, field, phrase):
    payload = resourceProject(tmp_path / 'candidate')
    error = {'issues': [dict(code=code, fieldPath='resources.items.fixture.image.' + field, message='technical detail')]}
    entry, = configurationDiagnostics(error, payload, {})
    assert entry['target'] == 'resources'
    assert 'input.png' in entry['text'] and phrase in entry['text']
    assert '\u4e0a\u4f20\u5019\u9009\u8d44\u6e90' in entry['text']
    assert entry['resourcePath'] == 'input.png' and entry['code'] == code
    assert 'technical detail' not in entry['text']


def testInvalidResourcePathDoesNotClaimFileIsNotUploaded(tmp_path):
    payload = resourceProject(tmp_path / 'candidate')
    error = {'issues': [dict(code='E_RESOURCE_INPUT', fieldPath='resources.items.fixture.image.path',
                            message='resource or site path crosses a link')]}
    entry, = configurationDiagnostics(error, payload, {})
    assert entry['target'] == 'configuration'
    assert '\u6587\u4ef6\u8def\u5f84\u65e0\u6548' in entry['text']
    assert '\u4e0a\u4f20\u5019\u9009\u8d44\u6e90' not in entry['text']


def testUnrelatedDiagnosticKeepsLegacyNullResourcePlan():
    # Compatibility input only; newly created projects always use schema 2.7.
    payload = {'schemaVersion': '2.1', 'resources': None}
    entry, = configurationDiagnostics({'issues': [dict(code='E_CONFIG_INVALID', message='upgrade required')]}, payload, {})
    assert entry['target'] == 'configuration' and 'upgrade required' in entry['text']


def testNativeDialogCanUploadAndRevalidateWithoutChangingOnlineProject(
        initialRuntime, ownedDesignerWindow, tmp_path, monkeypatch, messages):  # noqa: F811
    service, client = initialRuntime
    original = resourceProject(tmp_path / 'original')
    client.initializeProjectConfiguration(original, tmp_path / 'original', 'original',
                                           runtimeInstanceId=service.runtimeInstanceId)
    assert waitForWrite(service, client, 'original')['state'] == 'COMMITTED'
    snapshot = deepcopy(client.getOnlineProjectSnapshot())
    payload = resourceProject(tmp_path / 'candidate', extraImages=2)
    path = tmp_path / 'candidate/resource.emoproj'
    disk = path.read_bytes()
    window = ownedDesignerWindow(client)
    assert window.loadProjectSelection(str(path))
    window.pageCoordinator.sync()
    before = deepcopy(window.pageCoordinator.session.payload())
    dialog = ProjectConfigurationDialog(window.pageCoordinator, window)
    dialog.show()
    try:
        waitUntil(lambda: dialog.pending is None)
        assert dialog.snapshot and not dialog.initial
        dialog.replacementPolicy.setCurrentIndex(dialog.replacementPolicy.findData('stopAffected'))
        dialog.validateButton.click()
        waitUntil(lambda: dialog.pending is None)
        assert len(messages) == 1
        assert 'input.png' in messages[0]['text'] and 'WinError' not in messages[0]['text']
        assert 'Runtime \u7f3a\u5c11 3 \u4e2a\u5de5\u7a0b\u6587\u4ef6' in messages[0]['text']
        for item in payload['resources']['items'].values():
            assert item['path'] in messages[0]['text']
        assert '\u6821\u9a8c\u9009\u4e2d\u53d8\u66f4' in messages[0]['text']
        assert '\u672a\u5199\u5165 Runtime' in messages[0]['text']
        assert '\u672a\u8fde\u63a5\u8bbe\u5907\u6216\u542f\u52a8\u4efb\u52a1' in messages[0]['text']
        assert 'E_RESOURCE_MISSING' not in messages[0]['text']
        assert 'E_RESOURCE_MISSING' in messages[0]['detail']
        assert 'Traceback' in messages[0]['detail']
        assert 'online-projects' not in messages[0]['text']
        assert dialog.diagnosticChoice.count() == 3
        assert '\u4e0a\u4f20\u5019\u9009\u8d44\u6e90' in messages[0]['buttons']
        assert '\u8be6\u7ec6\u4fe1\u606f' in messages[0]['buttons']
        assert dialog.locateButton.text() == '\u4e0a\u4f20\u5019\u9009\u8d44\u6e90'
        assert not service.projectResourceTransfers.resolve(ProjectDocument.model_validate(payload))
        assert client.getOnlineProjectSnapshot() == snapshot
        monkeypatch.setattr(QMessageBox, 'question', lambda *_args, **_kwargs: QMessageBox.Yes)
        dialog.locateButton.click()
        waitUntil(lambda: dialog.pending is None)
        assert not dialog.closed and dialog.isVisible()
        assert client.getOnlineProjectSnapshot() == snapshot
        dialog.validateButton.click()
        waitUntil(lambda: dialog.pending is None)
        assert len(messages) == 1 and dialog.validatedSelection is not None
        assert client.getOnlineProjectSnapshot() == snapshot
        assert window.pageCoordinator.session.payload() == before and path.read_bytes() == disk
        assert not service.jobRepository.all() and not service.deviceManager.drivers
    finally:
        dialog.reject()


@pytest.mark.parametrize('problem,phrase,code', [
    ('missing', '\u672c\u5730\u7f3a\u5c11 1 \u4e2a\u5de5\u7a0b\u6587\u4ef6', 'E_RESOURCE_MISSING'),
    ('size', '\u672c\u5730\u6587\u4ef6\u5927\u5c0f\u4e0e\u5de5\u7a0b\u8bb0\u5f55\u4e0d\u4e00\u81f4', 'E_RESOURCE_SIZE'),
    ('digest', '\u672c\u5730\u6587\u4ef6\u5185\u5bb9\u4e0e\u5de5\u7a0b\u8bb0\u5f55\u4e0d\u4e00\u81f4', 'E_RESOURCE_DIGEST'),
])
def testUploadExplainsLocalFileProblemWithoutOfferingUploadAgain(
        initialRuntime, ownedDesignerWindow, tmp_path, monkeypatch, messages, problem, phrase, code):  # noqa: F811
    service, client = initialRuntime
    original = resourceProject(tmp_path / 'original')
    client.initializeProjectConfiguration(original, tmp_path / 'original', 'original',
                                           runtimeInstanceId=service.runtimeInstanceId)
    assert waitForWrite(service, client, 'original')['state'] == 'COMMITTED'
    snapshot = deepcopy(client.getOnlineProjectSnapshot())
    payload = resourceProject(tmp_path / 'candidate')
    path = tmp_path / 'candidate/resource.emoproj'
    disk = path.read_bytes()
    window = ownedDesignerWindow(client)
    assert window.loadProjectSelection(str(path))
    window.pageCoordinator.sync()
    before = deepcopy(window.pageCoordinator.session.payload())
    image = path.parent / 'input.png'
    if problem == 'missing':
        image.unlink()
    else:
        content = image.read_bytes()
        image.write_bytes(content + b'x' if problem == 'size' else bytes([content[0] ^ 1]) + content[1:])
    dialog = ProjectConfigurationDialog(window.pageCoordinator, window)
    dialog.show()
    try:
        waitUntil(lambda: dialog.pending is None)
        monkeypatch.setattr(QMessageBox, 'question', lambda *_args, **_kwargs: QMessageBox.Yes)
        dialog.uploadButton.click()
        waitUntil(lambda: dialog.pending is None)
        assert len(messages) == 1 and phrase in messages[0]['text']
        assert 'input.png' in messages[0]['text']
        assert code in messages[0]['detail'] and code not in messages[0]['text']
        assert 'Runtime \u7f3a\u5c11' not in messages[0]['text']
        assert '\u4e0a\u4f20\u5019\u9009\u8d44\u6e90' not in messages[0]['buttons']
        assert dialog.locateButton.text() == '\u8fd4\u56de\u914d\u7f6e'
        assert not service.projectResourceTransfers.resolve(ProjectDocument.model_validate(payload))
        assert client.getOnlineProjectSnapshot() == snapshot
        dialog.locateButton.click()
        assert dialog.closed
        assert window.pageCoordinator.session.payload() == before and path.read_bytes() == disk
        assert not service.jobRepository.all() and not service.deviceManager.drivers
    finally:
        dialog.reject()


@pytest.mark.parametrize('count', [3, 7])
@pytest.mark.skipif(os.environ.get('QT_QPA_PLATFORM') != 'windows',
                    reason='QMessageBox rendering requires the Windows Qt platform; offscreen show crashes')
def testResourceErrorRendersReadableFileListAndKeepsEveryDiagnostic(
        ownedDesignerWindow, tmp_path, monkeypatch, count):
    payload = resourceProject(tmp_path / 'candidate', extraImages=count - 1)
    snapshot = dict(runtimeInstanceId='fixture', documentRevision=1, executionRevision=1,
                    payload=WorkflowStore().toPayload('Online original'))
    client = SimpleNamespace(listOperators=lambda: [], getOnlineProjectSnapshot=lambda _projectId: snapshot)
    window = ownedDesignerWindow(client)
    window.setAttribute(Qt.WA_DontShowOnScreen)
    assert window.loadProjectSelection(str(tmp_path / 'candidate/resource.emoproj'))
    window.pageCoordinator.sync()
    dialog = ProjectConfigurationDialog(window.pageCoordinator, window)
    dialog.setAttribute(Qt.WA_DontShowOnScreen)
    dialog.show()
    waitUntil(lambda: dialog.pending is None)
    rendered = []
    def execute(box):
        box.setAttribute(Qt.WA_DontShowOnScreen)
        box.show()
        QApplication.processEvents()
        label = box.findChild(QLabel, 'qt_msgbox_label')
        assert label is not None and label.isVisible()
        rect = label.contentsRect()
        bounds = label.fontMetrics().boundingRect(rect, Qt.TextWordWrap, label.text())
        assert bounds.height() <= rect.height() + 2
        assert label.visibleRegion().boundingRect() == label.rect()
        for button in box.buttons():
            assert button.fontMetrics().horizontalAdvance(button.text()) <= button.contentsRect().width()
        screenshot = tmp_path / f'resource-preflight-{count}.png'
        assert box.grab().save(str(screenshot))
        detailButton = next(button for button in box.buttons() if button.text() == '\u8be6\u7ec6\u4fe1\u606f')
        detailButton.click()
        QApplication.processEvents()
        detailView = box.findChild(QTextEdit)
        assert detailView.isVisible() and detailButton.text() == '\u6536\u8d77\u8be6\u60c5'
        assert 'E_RESOURCE_MISSING' in detailView.toPlainText()
        for key in payload['resources']['items']:
            assert f'resources.items.{key}.path' in detailView.toPlainText()
        assert box.grab().save(str(tmp_path / f'resource-details-{count}.png'))
        detailButton.click()
        QApplication.processEvents()
        assert not detailView.isVisible() and detailButton.text() == '\u8be6\u7ec6\u4fe1\u606f'
        rendered.append(box.text())
        next(button for button in box.buttons() if button.text() == '\u5173\u95ed').click()
        return box.result()
    monkeypatch.setattr(QMessageBox, 'exec_', execute)
    try:
        with pytest.raises(WorkflowCompileError) as caught:
            verifyResources(ProjectDocument.model_validate(payload), tmp_path / 'not-published')
        dialog._notify('\u5de5\u7a0b\u6821\u9a8c\u672a\u901a\u8fc7', str(caught.value),
                       dialog._errorDetails(caught.value), diagnostic=caught.value)
        assert len(rendered) == 1 and dialog.diagnosticChoice.count() == count
        if count > 5:
            assert '\u53e6\u6709 2 \u9879\u95ee\u9898' in rendered[0]
        assert dialog.grab().save(str(tmp_path / f'resource-comparison-{count}.png'))
    finally:
        dialog.reject()
