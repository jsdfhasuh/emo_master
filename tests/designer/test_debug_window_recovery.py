"""Negative/recovery paths keep debug actions visible, pinned, and at most once."""
from contextlib import contextmanager
from copy import deepcopy
import threading

import emo_master  # noqa: F401 - bootstrap before native Qt imports
import numpy as np
import pytest
from PySide2.QtCore import Qt, QTimer
from PySide2.QtTest import QTest
from PySide2.QtWidgets import QApplication, QDialog, QDialogButtonBox, QFileDialog, QLabel
from shiboken2 import isValid

from emo_master.apps.designer.services.debug_artifacts import readFixture, saveFixture, writeArchive
from emo_master.apps.designer.services.operator_debug import DebugConnection
from emo_master.apps.designer.services.runtime_client import RuntimeClient, RuntimeClientError
from emo_master.apps.designer.ui.operator_debug_window import OperatorDebugWindow
from emo_master.apps.designer.ui.debug_values import InputRow
from emo_master.apps.designer.ui.param_form import SchemaParamForm
from emo_master.apps.designer.ui.workflow_debug_window import WorkflowDebugWindow
from emo_master.apps.runtime.operator_debug.assets import decode, imageBytes
from tests.designer.test_debug_artifacts import Connection
from tests.designer.test_dot_debug_contracts import integerProject
from tests.designer.test_operator_debug_window import editorFor, wait
from tests.designer.test_workflow_debug_window import processEventsFor
from tests.runtime.test_operator_debug_rpc import runtime as runtime
from tests.runtime.test_workflow_debug_control import nestedProject


@contextmanager
def inputWindow(runtime, kind):
    editor = None
    if kind == 'operator':
        editor, *_ = editorFor(runtime, 'vision.compare.number')
        editor._schemaForm.setSchema({'type': 'object', 'properties': {
            'operator': {'type': 'string'}, 'rightValue': {'type': 'number'}}},
            {'operator': 'gte', 'rightValue': 5})
        dialog = OperatorDebugWindow(editor)
        editor._debugWindow = dialog
        port, start, choose = 'left', dialog.run, dialog.selectFile
    else:
        dialog = WorkflowDebugWindow(RuntimeClient(runtime), integerProject(), 'main')
        port, start, choose = 'item', dialog.startButton, dialog.file
    dialog.show()
    try:
        try:
            wait(start.isEnabled)
        except AssertionError as error:
            raise AssertionError(f"Debug open did not become ready: state={dialog.state}, busy={dialog.busy}, "
                f"identity={dialog.connection.identity}, uncertain={dialog.connection.uncertain}, "
                f"status={dialog.status.text()!r}, logs={dialog.logs.toPlainText()!r}") from error
        yield dialog, port, start, choose
    finally:
        if editor is not None:
            editor.forceClose()
        elif isValid(dialog):
            dialog.close()
        wait(lambda: not isValid(dialog))
        wait(lambda: not runtime.operatorDebugManager.ownsResources())


@pytest.mark.parametrize('operator', ['vision.value.number', 'vision.compare.number'])
@pytest.mark.parametrize('fail', [False, True])
def testInputToolbarHasLayoutDuringSlowOpenAndAfterFailure(runtime, monkeypatch, operator, fail):
    entered, release = threading.Event(), threading.Event()
    original = DebugConnection.open

    def slowOpen(connection, *args):
        entered.set()
        assert release.wait(5)
        if fail:
            raise RuntimeClientError('E_DEBUG_CONTEXT_INVALID', '测试打开失败')
        return original(connection, *args)

    monkeypatch.setattr(DebugConnection, 'open', slowOpen)
    editor, *_ = editorFor(runtime, operator)
    dialog = OperatorDebugWindow(editor)
    editor._debugWindow = dialog
    dialog.show()
    try:
        wait(entered.is_set)
        assert dialog.state == 'STARTING'
        panel = dialog.artifacts.panel
        assert panel.parentWidget() is dialog.inputBody
        assert dialog.inputBody.layout().indexOf(panel) == 0
        assert not dialog.artifacts.load.isEnabled()
        dialog.tabs.setCurrentIndex(1)
        assert not panel.isVisible()
        dialog.tabs.setCurrentIndex(0)
        assert panel.isVisible()
        release.set()
        wait(lambda: dialog.state == 'FAULTED' if fail else dialog.run.isEnabled())
        if not fail:
            for _ in range(3):
                dialog.buildInputs()
            originalPorts = dialog.capability['inputPorts']
            dialog.capability['inputPorts'] = {'temporary': 'number'}
            dialog.buildInputs()
            dialog.capability['inputPorts'] = originalPorts
            dialog.buildInputs()
        assert panel.parentWidget() is dialog.inputBody
        assert dialog.inputBody.layout().indexOf(panel) == 0
        assert dialog.inputBody.layout().count() == 3
    finally:
        release.set()
        editor.forceClose()
        wait(lambda: not runtime.operatorDebugManager.ownsResources())


def testZeroPortFixtureCanSaveLoadAndReset(runtime, monkeypatch, tmp_path):
    editor, *_ = editorFor(runtime)
    dialog = OperatorDebugWindow(editor)
    editor._debugWindow = dialog
    dialog.show()
    path = tmp_path / 'empty.emofixture'
    try:
        wait(dialog.run.isEnabled)
        monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *a: (str(path), ''))
        QTest.mouseClick(dialog.artifacts.save, Qt.LeftButton)
        wait(lambda: path.exists() and not dialog.busy)
        assert readFixture(path, {})[0]['ports'] == {}
        monkeypatch.setattr(QFileDialog, 'getOpenFileName', lambda *a: (str(path), ''))
        QTest.mouseClick(dialog.artifacts.load, Qt.LeftButton)
        wait(lambda: '输入集已整体载入' in dialog.actionStatus.text() and not dialog.busy)
        QTest.mouseClick(dialog.reset, Qt.LeftButton)
        wait(lambda: dialog.run.isEnabled() and dialog.connection.identity['generation'] == 2)
        assert dialog.rows == {} and dialog.inputBody.layout().indexOf(dialog.artifacts.panel) == 0
    finally:
        editor.forceClose()
        wait(lambda: not runtime.operatorDebugManager.ownsResources())


@pytest.mark.parametrize('withHistory', [False, True])
def testClahePrepareRejectionNeverInventsExecutionAndCanRecover(runtime, monkeypatch, withHistory):
    editor, *_ = editorFor(runtime, 'vision.preprocess.clahe')
    params = {'clipLimit': 2.0, 'tileGridWidth': 8, 'tileGridHeight': 8}
    monkeypatch.setattr(editor, 'collectParams', lambda: deepcopy(params))
    dialog = OperatorDebugWindow(editor)
    editor._debugWindow = dialog
    dialog.show()
    try:
        wait(dialog.run.isEnabled)
        wire = dialog.connection.upload(imageBytes(np.zeros((12, 12), np.uint8)), 'image/png', {'kind': 'upload'})
        dialog.rows['image'].restoreWire('source', wire)
        if withHistory:
            QTest.mouseClick(dialog.run, Qt.LeftButton)
            wait(lambda: len(dialog.records) == 1 and dialog.run.isEnabled())
        before = deepcopy(dialog.records)
        calls, call = [], dialog.connection.call

        def observed(method, **fields):
            calls.append(method)
            return call(method, **fields)

        monkeypatch.setattr(dialog.connection, 'call', observed)
        params['clipLimit'] = 0
        QTest.mouseClick(dialog.run, Qt.LeftButton)
        wait(lambda: 'execute' in dialog.actionFeedback.errors and not dialog.busy)
        processEventsFor(.8)
        assert dialog.executionId == '' and dialog.records == before
        assert dialog.connection.executionPhase == 'preparing'
        assert '本次未启动执行' in dialog.resultLabel.text() and '执行中' not in dialog.resultLabel.text()
        assert 'clipLimit' in dialog.actionStatus.text()
        assert calls.count('PrepareOperatorDebugInputs') == 1 and 'ExecuteOperatorDebugNode' not in calls
        assert dialog.run.isEnabled() and not dialog.connection.uncertain
        if withHistory:
            assert dialog.exportSelection()['identity']['rawParams']['clipLimit'] == 2.0
            assert '历史结果' in dialog.resultLabel.text()
        params['clipLimit'] = 2
        QTest.mouseClick(dialog.run, Qt.LeftButton)
        wait(lambda: len(dialog.records) == len(before) + 1 and dialog.run.isEnabled())
        assert calls.count('ExecuteOperatorDebugNode') == 1
        assert dialog.records[0]['status'] == 'SUCCEEDED'
        assert 'execute' not in dialog.actionFeedback.errors
    finally:
        editor.forceClose()
        wait(lambda: not runtime.operatorDebugManager.ownsResources())


def testUnknownExecutionAckSurvivesPollAndCannotResend(runtime, monkeypatch):
    editor, *_ = editorFor(runtime)
    dialog = OperatorDebugWindow(editor)
    editor._debugWindow = dialog
    dialog.show()
    try:
        wait(dialog.run.isEnabled)
        calls, call = [], dialog.connection.call

        def lostAck(method, **fields):
            if method == 'ExecuteOperatorDebugNode':
                calls.append(deepcopy(fields))
                call(method, **fields)
                raise RuntimeError('ACK lost')
            if method == 'GetOperatorDebugExecution' and fields.get('request_id'):
                raise RuntimeError('ACK lookup unavailable')
            return call(method, **fields)

        monkeypatch.setattr(dialog.connection, 'call', lostAck)
        QTest.mouseClick(dialog.run, Qt.LeftButton)
        wait(lambda: dialog.connection.uncertain and not dialog.busy)
        processEventsFor(.8)
        assert '执行状态未知' in dialog.resultLabel.text()
        assert 'E_DEBUG_UNCERTAIN' in dialog.actionStatus.text()
        assert not dialog.run.isEnabled() and not dialog.reset.isEnabled()
        assert dialog.connection.executionPhase == 'unknown'
        dialog.execute()
        processEventsFor(.35)
        assert len(calls) == 1 and not dialog.records and not dialog.executionId
    finally:
        editor.forceClose()
        wait(lambda: not runtime.operatorDebugManager.ownsResources())


@pytest.mark.parametrize('failure', ['transport', 'E_DEBUG_CONTEXT_INVALID'])
def testAcceptedExecutionErrorRecoversByOriginalRequestIdWithoutResending(runtime, monkeypatch, failure):
    editor, *_ = editorFor(runtime)
    dialog = OperatorDebugWindow(editor)
    editor._debugWindow = dialog
    dialog.show()
    try:
        wait(dialog.run.isEnabled)
        executions, lookups, call = [], [], dialog.connection.call

        def failedReply(method, **fields):
            if method == 'ExecuteOperatorDebugNode':
                executions.append(fields['request_id'])
                call(method, **fields)
                if failure == 'transport':
                    raise RuntimeError('ACK lost after acceptance')
                raise RuntimeClientError(failure, 'Error after dispatch and acceptance')
            if method == 'GetOperatorDebugExecution' and fields.get('request_id'):
                lookups.append(fields['request_id'])
            return call(method, **fields)

        monkeypatch.setattr(dialog.connection, 'call', failedReply)
        QTest.mouseClick(dialog.run, Qt.LeftButton)
        wait(lambda: len(dialog.records) == 1 and dialog.run.isEnabled())
        assert len(executions) == 1 and lookups == executions
        assert dialog.records[0]['status'] == 'SUCCEEDED'
        assert dialog.records[0]['requestId'] == executions[0]
        assert dialog.connection.executionPhase == 'accepted' and not dialog.connection.uncertain
    finally:
        editor.forceClose()
        wait(lambda: not runtime.operatorDebugManager.ownsResources())


def testRuntimeFailureAfterWorkerDispatchWithoutLedgerIsUnknownAndCannotReplay(runtime, monkeypatch):
    editor, *_ = editorFor(runtime)
    dialog = OperatorDebugWindow(editor)
    editor._debugWindow = dialog
    dialog.show()
    try:
        wait(dialog.run.isEnabled)
        session = runtime.operatorDebugManager.sessions[dialog.connection.identity['session_id']]
        collect, injected = session.assets.collectOutputs, []

        def failAfterDispatch(retained):
            if session.active and not injected:
                injected.append(session.active)
                raise OSError('Acceptance bookkeeping failed after Worker dispatch')
            return collect(retained)

        monkeypatch.setattr(session.assets, 'collectOutputs', failAfterDispatch)
        executions, lookups, call = [], [], dialog.connection.call

        def observed(method, **fields):
            if method == 'ExecuteOperatorDebugNode':
                executions.append(fields['request_id'])
            if method == 'GetOperatorDebugExecution' and fields.get('request_id'):
                lookups.append(fields['request_id'])
            return call(method, **fields)

        monkeypatch.setattr(dialog.connection, 'call', observed)
        QTest.mouseClick(dialog.run, Qt.LeftButton)
        wait(lambda: dialog.connection.uncertain and not dialog.busy)
        processEventsFor(.8)
        assert len(injected) == 1 and len(session.executions) == 1
        assert len(executions) == 1 and lookups == executions
        assert executions[0] not in session.requests
        assert '执行状态未知' in dialog.resultLabel.text() and '未启动' not in dialog.resultLabel.text()
        assert 'E_DEBUG_UNCERTAIN' in dialog.actionStatus.text()
        assert not dialog.run.isEnabled() and not dialog.reset.isEnabled()
        dialog.execute()
        processEventsFor(.35)
        assert len(executions) == 1 and not dialog.records
    finally:
        editor.forceClose()
        wait(lambda: not runtime.operatorDebugManager.ownsResources())


def fixtureFile(path, port, portType, *, corruption=None, types=None):
    connection = Connection()
    rows = {key: {'type': kind, 'mode': 'missing', 'wire': None} for key, kind in (types or {}).items()}
    rows[port] = {'type': portType, 'mode': 'value', 'wire': {'inline': 9}}
    if corruption == 'hash':
        rows[port] = {'type': portType, 'mode': 'source',
                      'wire': connection.upload(b'9', 'application/json', {})}
    saveFixture(path, '恢复输入集', rows, {}, {}, connection)
    if corruption == 'hash':
        manifest, files = readFixture(path, {key: row['type'] for key, row in rows.items()})
        files[next(iter(files))] = b'8'
        writeArchive(path, manifest, files)


@pytest.mark.parametrize('kind', ['operator', 'workflow'])
@pytest.mark.parametrize('corruption', ['ports', 'hash'])
def testFixtureFailureSurvivesNaturalPollAndOnlySuccessfulLoadClearsIt(runtime, monkeypatch, tmp_path, kind, corruption):
    with inputWindow(runtime, kind) as (dialog, port, start, _choose):
        row = dialog.rows[port]
        row.restoreWire('value', {'inline': 3})
        bad, good = tmp_path / 'bad.emofixture', tmp_path / 'good.emofixture'
        fixtureFile(bad, 'wrong' if corruption == 'ports' else port, row.portType, corruption=corruption,
                    types={key: row.portType for key, row in dialog.rows.items()} if corruption == 'hash' else None)
        fixtureFile(good, port, row.portType, types={key: row.portType for key, row in dialog.rows.items()})
        monkeypatch.setattr(QFileDialog, 'getOpenFileName', lambda *a: (str(bad), ''))
        QTest.mouseClick(dialog.artifacts.load, Qt.LeftButton)
        wait(lambda: 'artifact:load' in dialog.actionFeedback.errors and not dialog.busy)
        message = dialog.actionStatus.text()
        assert ('摘要' if corruption == 'hash' else '端口') in message
        method = 'snapshot' if kind == 'operator' else 'flowSnapshot'
        polls, snapshot = [], getattr(dialog.connection, method)

        def observed(*args):
            polls.append(True)
            return snapshot(*args)

        monkeypatch.setattr(dialog.connection, method, observed)
        processEventsFor(.85)
        assert len(polls) >= 2 and 'READY' in dialog.status.text()
        assert message == dialog.actionStatus.text() and message in dialog.logs.toPlainText()
        assert dialog.actionStatus.isVisible()
        assert row.wire() == {'inline': 3} and start.isEnabled()
        dialog.actionFeedback.success('artifact:save', '其他保存成功')
        assert message in dialog.actionStatus.text()
        monkeypatch.setattr(QFileDialog, 'getOpenFileName', lambda *a: (str(good), ''))
        QTest.mouseClick(dialog.artifacts.load, Qt.LeftButton)
        wait(lambda: row.wire() == {'inline': 9} and not dialog.busy)
        assert 'artifact:load' not in dialog.actionFeedback.errors
        assert '输入集已整体载入' in dialog.actionStatus.text()
        assert message in dialog.logs.toPlainText()


@pytest.mark.parametrize('kind', ['operator', 'workflow'])
@pytest.mark.parametrize('operation', ['file', 'fixture'])
def testChosenInputQueuesBehindBusyOperationExactlyOnce(runtime, monkeypatch, tmp_path, kind, operation):
    with inputWindow(runtime, kind) as (dialog, port, start, choose):
        gate, entered = threading.Event(), threading.Event()
        path = tmp_path / ('input.json' if operation == 'file' else 'input.emofixture')
        if operation == 'file':
            path.write_text('9')
        else:
            fixtureFile(path, port, dialog.rows[port].portType, types={key: row.portType for key, row in dialog.rows.items()})
        calls, upload = [], dialog.connection.upload

        def observed(*args):
            calls.append(deepcopy(dialog.connection.identity))
            return upload(*args)

        monkeypatch.setattr(dialog.connection, 'upload', observed)
        identity = deepcopy(dialog.connection.identity)

        def modal(*args):
            def work():
                entered.set()
                assert gate.wait(5)
            assert dialog.submit('busy-test', work)
            wait(entered.is_set)
            return str(path), ''

        monkeypatch.setattr(QFileDialog, 'getOpenFileName', modal)
        try:
            choose(port) if operation == 'file' else dialog.artifacts.loadInputs()
            assert dialog.artifacts.pendingInput is not None
            assert str(path) in dialog.actionStatus.text()
            assert not start.isEnabled() and not calls
            gate.set()
            wait(lambda: dialog.rows[port].wire() is not None and not dialog.busy)
            processEventsFor(.65)
            assert dialog.artifacts.pendingInput is None
            assert dialog.connection.identity == identity
            if operation == 'file':
                assert calls == [identity]
                assert dialog.connection.download(dialog.rows[port].wire()['assetRef'])['content'] == b'9'
            else:
                assert dialog.rows[port].wire() == {'inline': 9}
            assert start.isEnabled()
        finally:
            gate.set()


@pytest.mark.parametrize('kind', ['operator', 'workflow'])
@pytest.mark.parametrize('change', ['generation', 'ports'])
def testQueuedUploadDoesNotCrossChangedInputIdentity(runtime, monkeypatch, tmp_path, kind, change):
    with inputWindow(runtime, kind) as (dialog, port, start, choose):
        gate = threading.Event()
        path = tmp_path / 'input.json'
        path.write_text('9')
        calls = []
        monkeypatch.setattr(dialog.connection, 'upload', lambda *a: calls.append(a))
        originalIdentity = deepcopy(dialog.connection.identity)
        originalRows = dict(dialog.rows)

        def modal(*args):
            assert dialog.submit('busy-test', lambda: gate.wait(5))
            return str(path), ''

        monkeypatch.setattr(QFileDialog, 'getOpenFileName', modal)
        try:
            choose(port)
            if change == 'generation':
                dialog.connection.identity = dict(originalIdentity, generation=originalIdentity['generation'] + 1)
            else:
                dialog.rows = {}
            gate.set()
            wait(lambda: dialog.artifacts.pendingInput is None and not dialog.busy)
            assert calls == []
            assert '未上传或替换输入' in dialog.actionStatus.text()
            assert originalRows[port].wire() is None
        finally:
            dialog.connection.identity = originalIdentity
            dialog.rows = originalRows
            gate.set()


@pytest.mark.parametrize('kind', ['operator', 'workflow'])
def testFailedUploadIsVisibleAndNeverAutomaticallyRetried(runtime, monkeypatch, tmp_path, kind):
    with inputWindow(runtime, kind) as (dialog, port, start, choose):
        path = tmp_path / 'input.json'
        path.write_text('9')
        monkeypatch.setattr(QFileDialog, 'getOpenFileName', lambda *a: (str(path), ''))
        calls, original = [], dialog.connection.upload

        def failed(*args):
            calls.append(args)
            raise RuntimeError('上传确认丢失')

        monkeypatch.setattr(dialog.connection, 'upload', failed)
        choose(port)
        wait(lambda: 'file:' + port in dialog.actionFeedback.errors and not dialog.busy)
        processEventsFor(.8)
        assert len(calls) == 1 and dialog.rows[port].wire() is None
        assert '上传确认丢失' in dialog.actionStatus.text() and start.isEnabled()
        monkeypatch.setattr(dialog.connection, 'upload', original)
        choose(port)  # an explicit new selection, never an automatic resend
        wait(lambda: dialog.rows[port].wire() is not None and not dialog.busy)
        assert 'file:' + port not in dialog.actionFeedback.errors


@pytest.mark.parametrize('kind', ['operator', 'workflow'])
@pytest.mark.parametrize('change', ['generation', 'ports'])
def testUploadCompletionCannotReplaceInputsAfterIdentityChanged(runtime, monkeypatch, tmp_path, kind, change):
    with inputWindow(runtime, kind) as (dialog, port, _start, choose):
        dialog.timer.stop()
        wait(lambda: not dialog.polling)
        path = tmp_path / 'input.json'
        path.write_text('9')
        monkeypatch.setattr(QFileDialog, 'getOpenFileName', lambda *a: (str(path), ''))
        entered, release = threading.Event(), threading.Event()
        calls = []
        identity, rows = deepcopy(dialog.connection.identity), dict(dialog.rows)
        replacement = None

        def upload(*args):
            calls.append(args)
            entered.set()
            assert release.wait(5)
            return {'assetRef': 'accepted-original-session'}

        monkeypatch.setattr(dialog.connection, 'upload', upload)
        try:
            choose(port)
            wait(entered.is_set)
            if change == 'generation':
                dialog.connection.identity = dict(identity, generation=identity['generation'] + 1)
            else:
                replacement = InputRow(rows[port].portType)
                dialog.rows[port] = replacement
            release.set()
            wait(lambda: not dialog.busy)
            assert len(calls) == 1
            assert '未替换任何输入' in dialog.actionStatus.text()
            assert rows[port].wire() is None and dialog.rows[port].wire() is None
        finally:
            release.set()
            dialog.connection.identity, dialog.rows = identity, rows
            if replacement is not None:
                replacement.deleteLater()


@pytest.mark.parametrize('kind', ['operator', 'workflow'])
def testCloseDiscardsQueuedSelectionWithoutUploading(runtime, monkeypatch, tmp_path, kind):
    with inputWindow(runtime, kind) as (dialog, port, _start, choose):
        path = tmp_path / 'input.json'
        path.write_text('9')
        release = threading.Event()
        uploads = []
        monkeypatch.setattr(dialog.connection, 'upload', lambda *a: uploads.append(a))

        def modal(*args):
            assert dialog.submit('busy-test', lambda: release.wait(5))
            return str(path), ''

        monkeypatch.setattr(QFileDialog, 'getOpenFileName', modal)
        try:
            choose(port)
            assert dialog.artifacts.pendingInput is not None
            dialog.close()
            release.set()
            wait(lambda: not isValid(dialog))
            assert not uploads
        finally:
            release.set()


@pytest.mark.parametrize('invalid', ['[1,', '[1, 2]', '{"value": 1}'])
@pytest.mark.parametrize('finish', ['correct', 'cancel'])
def testTrialValidationKeepsDialogTextAndPausedFlowUntilCorrected(runtime, monkeypatch, invalid, finish):
    payload = nestedProject()
    payload['workflowOrder'] = ['main']
    params = {'colorSpace': 'BGR', 'lower': [0, 0, 0], 'upper': [255, 255, 255]}
    payload['workflows'] = {'main': dict(name='Trial input validation', inputs={'image': 'image'},
        outputs={'mask': 'image'}, nodes=[dict(nodeId='input', kind='workflow_input'),
            dict(nodeId='range', kind='operator', operatorId='vision.segment.in_range', params=params),
            dict(nodeId='output', kind='workflow_output')], edges=[
                dict(fromNode='input', fromPort='image', toNode='range', toPort='image'),
                dict(fromNode='range', fromPort='mask', toNode='output', toPort='mask')])}
    beforePayload = deepcopy(payload)
    dialog = WorkflowDebugWindow(RuntimeClient(runtime), payload, 'main')
    dialog.show()
    try:
        wait(dialog.startButton.isEnabled)
        wire = dialog.connection.upload(imageBytes(np.zeros((8, 8, 3), np.uint8)), 'image/png', {'kind': 'upload'})
        dialog.rows['image'].restoreWire('source', wire)
        QTest.mouseClick(dialog.startButton, Qt.LeftButton)
        wait(lambda: dialog.current is not None and dialog.current['nodeId'] == 'range'
             and dialog.buttons['trial'].isEnabled())
        beforeCurrent, sequence = deepcopy(dialog.current), dialog.pauseSequence
        calls, control = [], dialog.connection.control

        def observed(action, *args, **kwargs):
            calls.append((action, args, deepcopy(kwargs)))
            return control(action, *args, **kwargs)

        monkeypatch.setattr(dialog.connection, 'control', observed)
        errors = []

        def editTrial():
            modal = QApplication.activeModalWidget()
            try:
                assert isinstance(modal, QDialog)
                form = modal.findChild(SchemaParamForm)
                lower = form._controls['lower']
                lower.setPlainText(invalid)
                buttons = modal.findChild(QDialogButtonBox)
                QTest.mouseClick(buttons.button(QDialogButtonBox.Ok), Qt.LeftButton)
                assert modal.isVisible() and QApplication.activeModalWidget() is modal
                assert lower.toPlainText() == invalid
                message = modal.findChild(QLabel, 'trialValidation')
                assert message.isVisible() and '参数未通过检查' in message.text()
                assert 'lower' in message.text() and not calls
                assert dialog.current == beforeCurrent and dialog.pauseSequence == sequence
                assert dialog.payload == beforePayload and payload == beforePayload
                if finish == 'correct':
                    lower.setPlainText('[1, 0, 0]')
                    QTest.mouseClick(buttons.button(QDialogButtonBox.Ok), Qt.LeftButton)
                else:
                    QTest.mouseClick(buttons.button(QDialogButtonBox.Cancel), Qt.LeftButton)
            except BaseException as error:
                errors.append(error)
                if isinstance(modal, QDialog):
                    modal.reject()

        QTimer.singleShot(0, editTrial)
        QTest.mouseClick(dialog.buttons['trial'], Qt.LeftButton)
        assert not errors, errors
        if finish == 'correct':
            wait(lambda: dialog.displayed is not None and dialog.displayed.get('snapshotKind') == 'trial'
                 and dialog.buttons['continue'].isEnabled())
            assert len(calls) == 1 and calls[0][0] == 'trial'
            assert calls[0][1] == (sequence,) and calls[0][2]['params']['lower'] == [1, 0, 0]
            asset = dialog.displayed['outputsAssets']['mask']['assetId']
            trialMask, _ = decode(dialog.connection.download(asset)['content'], 'image/png')
            assert np.all(trialMask == 0)
        else:
            processEventsFor(.35)
            assert not calls and dialog.buttons['continue'].isEnabled()
        assert dialog.current == beforeCurrent and dialog.pauseSequence == sequence
        assert dialog.payload == beforePayload and payload == beforePayload
        QTest.mouseClick(dialog.buttons['continue'], Qt.LeftButton)
        wait(lambda: dialog.state == 'SUCCEEDED' and dialog.displayed is not None
             and dialog.displayed.get('phase') == 'workflow.result')
        asset = dialog.displayed['outputsAssets']['mask']['assetId']
        originalMask, _ = decode(dialog.connection.download(asset)['content'], 'image/png')
        assert np.all(originalMask == 255)
        assert payload == beforePayload and not runtime.jobRepository.all()
    finally:
        dialog.close()
        wait(lambda: not isValid(dialog))
