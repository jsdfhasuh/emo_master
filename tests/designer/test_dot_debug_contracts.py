from copy import deepcopy
import json
import sys
from zipfile import ZipFile

import pytest
import emo_master  # noqa: F401 - application bootstrap precedes Qt native imports
from PySide2.QtCore import Qt
from PySide2.QtTest import QTest
from PySide2.QtWidgets import QFileDialog, QLabel, QPushButton
from shiboken2 import isValid

from emo_master.apps.designer.services.runtime_client import RuntimeClient
from emo_master.apps.designer.ui.param_form import SchemaParamForm
from emo_master.apps.designer.ui.workflow_debug_window import WorkflowDebugWindow
from emo_master.apps.designer.ui.operator_debug_window import OperatorDebugWindow
from emo_master.apps.designer.ui.debug_errors import explainDebugError
from emo_master.core.workflow.output_conditions import disabledOutputConflicts
from emo_master.plugins.builtins.flow_switch.operator import FlowSwitchOperator
from tests.designer.test_operator_debug_window import editorFor, wait
from tests.designer.test_workflow_debug_window import processEventsFor
from tests.runtime.test_operator_debug_rpc import runtime as runtime
from tests.runtime.test_workflow_debug_control import nestedProject


@pytest.mark.parametrize('enabled,required,dynamic,count', [(False, True, False, 1),
    (True, True, False, 0), (False, False, False, 0), (False, True, True, 0)])
def testConditionalOverlayOnlyRejectsDefiniteRequiredConflict(enabled, required, dynamic, count):
    payload = nestedProject()
    workflow = payload['workflows']['child']
    workflow['outputs'] = {'overlay': dict(type='image', required=required, nullable=True)}
    node = dict(nodeId='blob', operatorId='vision.analysis.blob', params={'drawOverlay': enabled})
    if dynamic:
        node['globalVariableBindings'] = [dict(parameterPath=['drawOverlay'], variableId='draw')]
    workflow['nodes'] = [dict(nodeId='input', kind='workflow_input'), node, dict(nodeId='out', kind='workflow_output')]
    workflow['edges'] = [dict(fromNode='blob', fromPort='overlay', toNode='out', toPort='overlay')]
    before = deepcopy(payload)
    problems = disabledOutputConflicts(payload)
    assert len(problems) == count and payload == before
    if problems:
        assert problems[0]['nodeId'] == 'blob' and problems[0]['parameter'] == 'drawOverlay'
        assert '可空不等于可缺失' in problems[0]['message']


def testSwitchVisibleRulesShortcutsAndPreviewKeepLegacyStrings(designerApplication):
    form = SchemaParamForm()
    form.setSchema(FlowSwitchOperator.meta.paramSchema, {'case0Value': 'true'})
    preview = form.findChild(QLabel, 'switchMatchPreview')
    assert preview and 'default' in preview.text()
    first = form._controls['case0Value']
    button = next(button for button in first.findChildren(QPushButton) if button.text() == 'True')
    button.click()
    assert form.getValues() == {'case0Value': 'True'}
    assert 'case0' in preview.text()
    assert FlowSwitchOperator().executeNode({'value': True}, form.getValues(), {})['outputs'] == {'case0': True}
    assert FlowSwitchOperator().executeNode({'value': 'true'}, form.getValues(), {})['outputs'] == {'default': 'true'}
    assert FlowSwitchOperator().executeNode({'value': 1}, {'case0Value': '1'}, {})['outputs'] == {'case0': 1}


@pytest.mark.parametrize('operator', ['vision.io.huaray_camera', 'communication.plc.slmp_write',
    'communication.tcp.client', 'vision.io.result_writer', 'vision.inference.yolo'])
def testSafetyExplanationIsChineseWithoutSuggestingBypass(operator):
    message = explainDebugError('E_DEBUG_UNSUPPORTED', 'No reviewed debug resource or side-effect adapter', operator)
    assert '已阻止执行' in message and '不会解除' in message and '技术原因' in message


@pytest.mark.parametrize('operator,guidance', [
    ('vision.io.huaray_camera', '相机专用采集预览'),
    ('communication.plc.slmp_write', '写入需明确解锁'),
    ('communication.tcp.client', '不会为解除调试拒绝而自动启动正式任务'),
    ('vision.io.result_writer', '不会为解除调试拒绝而自动启动正式任务'),
    ('vision.inference.yolo', '模型配置入口'),
])
def testActualOperatorRefusalUiExplainsSafetyWithoutOpeningResources(runtime, operator, guidance):
    editor, payload, before, calls = editorFor(runtime, operator)
    dialog = OperatorDebugWindow(editor)
    editor._debugWindow = dialog
    dialog.show()
    try:
        wait(lambda: dialog.state == 'FAULTED')
        message = dialog.status.text()
        assert 'E_DEBUG_UNSUPPORTED' in message and '已阻止执行' in message
        assert '技术原因：No reviewed debug resource or side-effect adapter' in message
        assert guidance in message and not dialog.run.isEnabled()
        if operator == 'vision.io.huaray_camera' and sys.platform != 'win32':
            assert 'Windows 平台限制' in message
        assert not runtime.operatorDebugManager.opens
        assert not runtime.operatorDebugManager.ownsResources()
        assert not runtime.jobRepository.all() and runtime.loadedDocument is None
        assert payload == before and not calls
    finally:
        editor.forceClose()
        wait(lambda: not isValid(dialog))


def integerProject():
    payload = nestedProject()
    payload['workflowOrder'] = ['main']
    payload['workflows'] = {'main': dict(name='Integer fixture', inputs={'item': 'integer'}, outputs={'item': 'integer'},
        nodes=[dict(nodeId='input', kind='workflow_input'), dict(nodeId='output', kind='workflow_output')],
        edges=[dict(fromNode='input', fromPort='item', toNode='output', toPort='item')])}
    return payload


@pytest.mark.parametrize('interaction', ['keyboard', 'mouse'])
@pytest.mark.parametrize('value', [0, -1, 1, 9])
def testReadyIntegerModeSurvivesNaturalPollAndSubmitsInteger(runtime, interaction, value):
    dialog = WorkflowDebugWindow(RuntimeClient(runtime), integerProject(), 'main')
    dialog.show()
    try:
        wait(lambda: dialog.startButton.isEnabled())
        row = dialog.rows['item']
        transitions = []
        row.mode.currentIndexChanged.connect(lambda index: transitions.append(index))
        if interaction == 'keyboard':
            row.mode.setFocus()
            QTest.keyClick(row.mode, Qt.Key_Down)
        else:
            row.mode.showPopup()
            view = row.mode.view()
            index = row.mode.model().index(1, 0)
            QTest.mouseClick(view.viewport(), Qt.LeftButton, pos=view.visualRect(index).center())
        assert row.mode.currentIndex() == 1
        QTest.keyClicks(row.text, str(value))
        processEventsFor(.8)  # crosses at least two natural 250 ms polls
        assert row.mode.currentIndex() == 1 and transitions == [1]
        assert row.wire() == {'inline': value} and type(row.wire()['inline']) is int
        QTest.mouseClick(dialog.startButton, Qt.LeftButton)
        wait(lambda: dialog.state == 'SUCCEEDED' and dialog.displayed is not None)
        assert dialog.displayed['outputs']['item'] == value
        assert not runtime.jobRepository.all()
    finally:
        dialog.close()
        wait(lambda: not isValid(dialog))


def testOperatorFixtureSurvivesSessionAndHistoricalRawExportKeepsSelectedValue(runtime, monkeypatch, tmp_path):
    fixture = tmp_path / 'compare.emofixture'
    raw = tmp_path / 'historical.json'
    editor, payload, before, calls = editorFor(runtime, 'vision.compare.number')
    schema = {'type': 'object', 'properties': {'rightValue': {'type': 'number'}, 'operator': {'type': 'string'}}}
    editor._schemaForm.setSchema(schema, {'rightValue': 5, 'operator': 'gte'})
    dialog = OperatorDebugWindow(editor)
    editor._debugWindow = dialog
    dialog.show()
    try:
        wait(lambda: dialog.run.isEnabled())
        dialog.rows['left'].restoreWire('value', {'inline': 9})
        monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *a: (str(fixture), ''))
        QTest.mouseClick(dialog.artifacts.save, Qt.LeftButton)
        wait(lambda: fixture.exists() and not dialog.busy)
        dialog.close()
        wait(lambda: not isValid(dialog))
        dialog = OperatorDebugWindow(editor)
        editor._debugWindow = dialog
        dialog.show()
        wait(lambda: dialog.run.isEnabled())
        monkeypatch.setattr(QFileDialog, 'getOpenFileName', lambda *a: (str(fixture), ''))
        QTest.mouseClick(dialog.artifacts.load, Qt.LeftButton)
        wait(lambda: not dialog.busy and dialog.rows['left'].wire() == {'inline': 9})
        QTest.mouseClick(dialog.run, Qt.LeftButton)
        wait(lambda: len(dialog.records) == 1 and dialog.run.isEnabled())
        assert dialog.records[0]['outputs'] == {'result': True}
        editor._schemaForm.setSchema(schema, {'rightValue': 10, 'operator': 'gte'})
        QTest.mouseClick(dialog.run, Qt.LeftButton)
        wait(lambda: len(dialog.records) == 2 and dialog.run.isEnabled())
        assert dialog.records[0]['outputs'] == {'result': False}
        dialog.versions.setCurrentIndex(1)
        dialog.outputPorts.setCurrentIndex(dialog.outputPorts.findText('result'))
        selection = dialog.exportSelection()
        assert selection['identity']['selection'] == 'history'
        monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *a: (str(raw), ''))
        QTest.mouseClick(dialog.artifacts.raw, Qt.LeftButton)
        wait(lambda: raw.exists() and not dialog.busy)
        assert json.loads(raw.read_bytes()) is True
        assert payload == before and not calls and runtime.loadedDocument is None
        assert not runtime.jobRepository.all()
    finally:
        editor.forceClose()
        wait(lambda: not runtime.operatorDebugManager.ownsResources())


def testOldOperatorReadCannotUndoNewCommandOrReleaseCommandBusy(runtime):
    editor, *_ = editorFor(runtime)
    dialog = OperatorDebugWindow(editor)
    editor._debugWindow = dialog
    try:
        wait(lambda: dialog.run.isEnabled())
        dialog.timer.stop()
        wait(lambda: not dialog.polling)
        dialog.polling = True
        dialog.pollCommandVersion = dialog.commandVersion
        dialog.commandVersion += 1
        dialog.busy = True
        dialog.state = 'CANCELLING'
        dialog.completed('poll', {'session': {'state': 'READY'}}, None)
        assert dialog.state == 'CANCELLING' and dialog.busy and not dialog.polling
        assert not dialog.run.isEnabled() and not dialog.cancel.isEnabled()
        dialog.busy = False  # synthetic command has no background owner
    finally:
        editor.forceClose()
        wait(lambda: not runtime.operatorDebugManager.ownsResources())


def testWorkflowFixtureAndFullResultUiRoundtrip(runtime, monkeypatch, tmp_path):
    path, result = tmp_path / 'integers.emofixture', tmp_path / 'result.emodebug.zip'
    before = integerProject()
    dialog = WorkflowDebugWindow(RuntimeClient(runtime), before, 'main')
    dialog.show()
    try:
        wait(lambda: dialog.startButton.isEnabled())
        dialog.rows['item'].restoreWire('value', {'inline': 9})
        monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *a: (str(path), ''))
        QTest.mouseClick(dialog.artifacts.save, Qt.LeftButton)
        wait(lambda: path.exists() and not dialog.busy)
        with ZipFile(path) as archive:
            parameters = json.loads(archive.read('metadata.json'))['parameters']
            assert parameters == dict(draftDigest=dialog.startupDigest, workflows={'main': {}})
        dialog.close()
        wait(lambda: not isValid(dialog))
        dialog = WorkflowDebugWindow(RuntimeClient(runtime), integerProject(), 'main')
        dialog.show()
        wait(lambda: dialog.startButton.isEnabled())
        monkeypatch.setattr(QFileDialog, 'getOpenFileName', lambda *a: (str(path), ''))
        QTest.mouseClick(dialog.artifacts.load, Qt.LeftButton)
        wait(lambda: dialog.rows['item'].wire() == {'inline': 9} and not dialog.busy)
        QTest.mouseClick(dialog.startButton, Qt.LeftButton)
        wait(lambda: dialog.state == 'SUCCEEDED' and dialog.displayed is not None and not dialog.busy)
        dialog.ports.setCurrentIndex(dialog.ports.findText('outputs / item'))
        monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *a: (str(result), ''))
        wait(lambda: dialog.artifacts.export.isEnabled())
        QTest.mouseClick(dialog.artifacts.export, Qt.LeftButton)
        wait(lambda: result.exists() and not dialog.busy)
        with ZipFile(result) as archive:
            metadata = json.loads(archive.read('metadata.json'))
            assert json.loads(archive.read(metadata['file'])) == 9
            assert metadata['identity']['workflowId'] == 'main'
            assert metadata['identity']['snapshotKind'] == 'observed'
            assert metadata['identity']['phase'] == 'workflow.result'
        assert dialog.payload == before and runtime.loadedDocument is None
        assert not runtime.jobRepository.all()
    finally:
        dialog.close()
        wait(lambda: not isValid(dialog))


def testWorkflowFixtureParameterSnapshotIncludesChildBindingsWithoutApplying(runtime):
    payload = nestedProject()
    node = next(node for node in payload['workflows']['child']['nodes'] if 'operatorId' in node)
    node['globalVariableBindings'] = []
    before = deepcopy(payload)
    dialog = WorkflowDebugWindow(RuntimeClient(runtime), payload, 'main')
    try:
        snapshot = dialog.fixtureParameters()
        assert snapshot['workflows']['child'][node['nodeId']] == {
            key: node[key] for key in ('params', 'globalVariableBindings') if key in node}
        snapshot['workflows']['child'][node['nodeId']]['params']['audit-only'] = True
        assert payload == before and dialog.payload == before
    finally:
        dialog.close()
        wait(lambda: not isValid(dialog))
