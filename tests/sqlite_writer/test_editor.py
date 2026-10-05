from copy import deepcopy
import hashlib
import json
from pathlib import Path
import threading
from types import SimpleNamespace

import pytest
import emo_master  # noqa: F401 - keep the repository's Windows preload order
from PySide2.QtCore import QCoreApplication, QEvent, QTimer, Qt
from PySide2.QtWidgets import QMessageBox

from emo_master.apps.designer.operator_editors import OperatorEditorManager
from emo_master.apps.designer.operator_editors.sqlite_requests import EditorRequests
from emo_master.apps.designer.operator_editors.sqlite_sources import sourceCatalog, validateDraftMappings
from emo_master.apps.runtime.grpc_server.service import RuntimeService
from emo_master.apps.designer.services.runtime_client import RuntimeClient
from emo_master.core.contracts.sqlite_writer import SqliteWriterError
from tests.sqlite_writer.test_dependencies import config, mapping, project


def draftPayload():
    return project(nodes=[{'nodeId': 'writer', 'operatorId': 'vision.io.sqlite_writer', 'params': config()},
        {'nodeId': 'source', 'operatorId': 'vision.value.number', 'displayName': '来源 Long ' * 20,
         'outputPorts': {'wrongSavedPort': 'string'}, 'params': {'value': 7.0}}])


def catalog():
    return [{'operatorId': 'vision.value.number', 'inputPorts': {},
             'outputPortSpecs': {'value': {'type': 'number', 'required': False, 'nullable': False}}}]


def testDraftSourcesUseFormalPortsAndStableIdsWithoutRunning():
    payload = draftPayload()
    original = deepcopy(payload)
    sources = sourceCatalog(payload, 'main', catalog())
    assert payload == original
    source = next(n for n in sources if n['nodeId'] == 'source')
    assert set(source['ports']) == {'value'} and not source['ports']['value']['required']
    configValue = config(rows=[mapping(node='source', storage='INTEGER')])
    validateDraftMappings(configValue, sources, payload['workflows']['main'], 'writer')
    source['name'] = '改名不会改变绑定'
    validateDraftMappings(configValue, sources, payload['workflows']['main'], 'writer')
    with pytest.raises(SqliteWriterError, match='来源已删除'):
        validateDraftMappings(configValue, [n for n in sources if n['nodeId'] != 'source'], payload['workflows']['main'], 'writer')
    payload['workflows']['main']['edges'] = [{'fromNode': 'writer', 'toNode': 'source'}]
    with pytest.raises(SqliteWriterError, match='循环依赖'):
        validateDraftMappings(configValue, sources, payload['workflows']['main'], 'writer')


def testUnavailableFormalDefinitionCannotOfferSavedStalePorts():
    payload = draftPayload()
    sources = {n['nodeId']: n for n in sourceCatalog(payload, 'main', [])}
    assert not sources['source']['ports'] and '旧保存端口' in sources['source']['error']
    payload['workflows']['main']['nodes'].append({'nodeId': 'deleted-call', 'kind': 'subflow',
        'targetWorkflowId': 'removed', 'outputPorts': {'stale': 'integer'}})
    # The formal project model rejects a dangling call before a catalog exists.
    # Both paths reject stale saved ports; neither invents a usable source.
    with pytest.raises(ValueError, match='targetWorkflowId must reference an existing workflow'):
        sourceCatalog(payload, 'main', [])


def testManagementOwnerDoesNotReleaseCancelledWorkEarly():
    owner = EditorRequests()
    entered = threading.Barrier(3)
    release = threading.Event()
    def blocked(_token):
        entered.wait(3)
        release.wait(3)
        return 'exited'
    handles = [owner.submit(blocked) for _ in range(2)]
    try:
        entered.wait(3)
        handles[0][1].cancel()
        assert owner.active == 2
        with pytest.raises(RuntimeError, match='额度'):
            owner.submit(lambda _token: None)
    finally:
        release.set()
        for future, _token in handles:
            assert future.result(timeout=4) == 'exited'
        owner.pool.shutdown(wait=True)
    assert owner.active == 0 and owner.peak == 2


class Settings:
    def value(self, _key, default=None):
        return default


@pytest.fixture
def editor(retainedQtApplication, tmp_path):
    runtime = RuntimeService(dbPath=tmp_path / 'runtime.sqlite3', workspaceRoot=tmp_path / 'jobs')
    payload = draftPayload()
    applied = []
    draft = {'directory': str(tmp_path), 'sources': sourceCatalog(payload, 'main', catalog()),
             'workflow': payload['workflows']['main']}
    manager = OperatorEditorManager(runtimeClient=RuntimeClient(runtime), settingsStore=Settings(),
        applyParams=lambda key, params: not applied.append(deepcopy(params)),
        appendLog=lambda *_args: None, cacheRoot=tmp_path / 'cache', getSqliteDraft=lambda key: draft)
    manifest = json.loads(Path('src/emo_master/plugins/builtins/sqlite_writer/manifest.json').read_text(encoding='utf-8'))
    window = manager.open(projectId='sqlite-test', workflowId='main', nodeId='writer',
        operatorId=manifest['operatorId'], displayName=manifest['displayName'], schema=manifest['paramSchema'],
        values=config(), operatorDefinition={'version': manifest['version'], 'editorSpec': manifest['editor']})
    try:
        assert window._controller is not None
        yield window, window._controller, applied, draft, manager
    finally:
        manager.closeAll()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        retainedQtApplication.processEvents()
        runtime.close()


def testRowAddDeleteOrderFalsyConstantsTypesAndApplication(editor):
    window, controller, applied, _draft, _manager = editor
    controller._loadRows([{'column': 'zero', 'storageType': 'INTEGER', 'missing': 'error', 'source': {'kind': 'constant', 'value': 0}},
        {'column': 'false', 'storageType': 'BOOLEAN', 'source': {'kind': 'constant', 'value': False}},
        {'column': 'empty', 'storageType': 'TEXT', 'source': {'kind': 'constant', 'value': ''}}])
    controller.fields.selectRow(0)
    controller.moveMapping(1)
    assert [r['column'] for r in controller.collectParams()['mappings']] == ['false', 'zero', 'empty']
    controller.fields.selectRow(2)
    controller.deleteMapping()
    assert window.applyChanges() and len(applied) == 1
    assert [r['source']['value'] for r in applied[0]['mappings']] == [False, 0]
    controller._loadRows([mapping(node='source', storage='TEXT')])
    assert not window.applyChanges() and '不兼容' in controller.error.text()
    assert len(applied) == 1


def testDeletedSourceLocalErrorAndPendingProjectValidation(editor, monkeypatch):
    window, controller, applied, draft, manager = editor
    controller._loadRows([mapping(node='source', storage='INTEGER')])
    controller._changed()
    draft['sources'] = [n for n in draft['sources'] if n['nodeId'] != 'source']
    monkeypatch.setattr(QMessageBox, 'question', lambda *_a, **_k: QMessageBox.Save)
    assert not manager.resolveSqlitePending()
    assert controller.fields.currentRow() == 0 and not applied
    monkeypatch.setattr(QMessageBox, 'question', lambda *_a, **_k: QMessageBox.Cancel)
    assert not manager.resolveSqlitePending()
    monkeypatch.setattr(QMessageBox, 'question', lambda *_a, **_k: QMessageBox.Discard)
    assert manager.resolveSqlitePending() and not window.isDirty() and not applied


def testLateManagementReplyCannotReachClosedWindow(editor):
    _window, controller, _applied, _draft, manager = editor
    entered, release = threading.Event(), threading.Event()
    def operation(_token):
        entered.set()
        release.wait(3)
        return SimpleNamespace(exists=False)
    from emo_master.apps.designer.operator_editors.sqlite_requests import requests
    future, token = requests.submit(operation)
    controller._pending = (future, token, controller._generation, 'inspect')
    assert entered.wait(2)
    manager.closeAll()
    assert controller._closed and not token.is_active() and requests.active >= 1
    release.set()
    future.result(timeout=4)
    controller._poll()  # No widget access after disposal.


def testSourceDialogPreservesPortIdentityAndMarksOptional(editor):
    from emo_master.plugins.builtins.sqlite_writer.editor import SourceDialog
    window, controller, _applied, draft, _manager = editor
    dialog = SourceDialog(window, draft['sources'], {'kind': 'node_output', 'nodeId': 'source', 'port': 'value'}, 'INTEGER')
    try:
        assert dialog.tree.currentItem().text(1).endswith('可选，可能缺失')
        dialog.accept()
        assert dialog.source == {'kind': 'node_output', 'nodeId': 'source', 'port': 'value'}
        assert dialog.tree.currentItem().flags() & Qt.ItemIsEnabled
    finally:
        dialog.deleteLater()


def testManifestEditorUiIsRealAndTrusted():
    from emo_master.apps.designer.operator_editors.trust import isBuiltinController
    root = Path('src/emo_master/plugins/builtins/sqlite_writer')
    manifest = json.loads((root / 'manifest.json').read_text(encoding='utf-8'))
    data = (root / manifest['editor']['uiResource']).read_bytes()
    assert b'SqliteWriterEditor' in data and hashlib.sha256(data).hexdigest()
    assert isBuiltinController(manifest['editor']['controllerEntry'])
    assert manifest['editor']['previewMode'] == 'none'


def testDraftCallPortsUseSameSubflowAndLoopContract():
    payload = draftPayload()
    child = deepcopy(payload['workflows']['main'])
    child['outputs'] = {'total': 'integer'}
    payload['workflows']['body'] = child
    payload['workflowOrder'].append('body')
    payload['workflows']['main']['nodes'].extend([
        {'nodeId': 'call', 'kind': 'subflow', 'targetWorkflowId': 'body', 'outputPorts': {'stale': 'string'}},
        {'nodeId': 'repeat', 'kind': 'loop', 'loop': {'mode': 'repeat', 'contractVersion': 2,
            'bodyWorkflowId': 'body', 'repeatCount': 2, 'maxIterations': 2, 'timeoutMs': 0}}])
    sources = {n['nodeId']: n for n in sourceCatalog(payload, 'main', catalog())}
    assert set(sources['call']['ports']) == {'total'}
    assert 'total' in sources['repeat']['ports'] and 'stale' not in sources['repeat']['ports']
    assert sources['repeat']['error'] == ''
    assert not any(n.endswith('.source') for n in sources)


@pytest.mark.parametrize('action', ['source', 'preview', 'initialize'])
def testProjectCloseDuringModalRejectsCallbacksAndInitialization(editor, action):
    _window, controller, applied, _draft, manager = editor
    controller.addMapping()
    rejected = []
    def close():
        rejected.append(bool(controller._dialogs))
        manager.closeAll()
    QTimer.singleShot(20, close)
    if action == 'source':
        controller._chooseSource(controller.fields.cellWidget(0, 1))
    elif action == 'initialize':
        controller._preview = (controller._generation, 'temporary-test.sqlite3', 'host', 'CREATE TABLE', [])
        controller._initialize()
    else:
        from concurrent.futures import Future
        reply = SimpleNamespace(runtime_host='host', resolved_path='temporary-test.sqlite3',
                                tables=[], structure_json='null', preview_sql='CREATE TABLE')
        future = Future()
        future.set_result(reply)
        controller._pending = (future, None, controller._generation, 'preview')
        controller._poll()
    from tests.sqlite_writer.test_user_path import waitQt
    waitQt(lambda: controller._closed, seconds=2)
    assert rejected == [True] and controller._closed and not controller._dialogs
    assert controller._pending is None and not applied
    controller._poll()
    controller.dispose()


def testInvalidNewColumnDefaultIsLocatedBeforeReorder(editor):
    _window, controller, _applied, _draft, _manager = editor
    controller.addMapping()
    controller.addMapping()
    original = controller._rows()
    controller.fields.item(0, 5).setText('bad json')
    controller.fields.selectRow(0)
    controller.moveMapping(1)
    assert controller._rows() == original and controller.fields.currentRow() == 0
    assert '第 1 行' in controller.error.text()
