"""Draft incompleteness is allowed; explicit bad references remain errors."""
from copy import deepcopy
from concurrent.futures import Future
from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide2.QtWidgets import QApplication

from emo_master.apps.designer.state.device_catalog import DeviceCatalogEditor
from emo_master.apps.designer.state.device_package import exportDevices, importDevices
from emo_master.apps.designer.state.project_edit_session import ProjectEditSession
from emo_master.apps.designer.ui.device_wizard import configurationForm, DeviceNodeDialog
from emo_master.core.plugin.devices import DeviceType, validateDeviceBinding
from emo_master.core.project.devices import DeviceDefinition
from tests.core.project.test_devices import declaration, definition, project  # noqa: F401
from tests.designer.test_device_workspace import windowFor, waitUntil
from tests.runtime.test_tcp_device_integration import definition as tcpDefinition


def testMissingSlotDraftPersistsAndStrictFinishStillRejects(definition, declaration, tmp_path):  # noqa: F811
    session = ProjectEditSession(project(definition), enablePresentation=False)
    requirements = {'vision.io.huaray_camera': {'camera': {'deviceTypeId': declaration.deviceTypeId}}}
    editor = DeviceCatalogEditor(session, {declaration.deviceTypeId: declaration}, requirements)
    node = dict(nodeId='pending', operatorId='vision.io.huaray_camera', params={})
    with pytest.raises(ValueError):
        editor.commitNode('main', node)
    editor.commitNode('main', node, allowIncomplete=True, position=dict(x=20.0, y=30.0))
    source = tmp_path / 'draft.emoproj'
    session.save(source)
    restored = ProjectEditSession.load(source)
    pending = next(n for n in restored.document().workflows['main'].nodes if n.nodeId == 'pending')
    assert not pending.deviceBindings
    assert restored.workflows.get('main').layout['nodePositions']['pending'] == dict(x=20.0, y=30.0)


def testMissingSelectorAllowedButWrongSelectorRejected():
    typeDecl = DeviceType.model_validate_json((Path(__file__).resolve().parents[2] /
        'src/emo_master/plugins/builtins/tcp_session/device_types/server.json').read_text(encoding='utf-8'))
    raw = tcpDefinition(typeDecl, channels=[])
    raw['enabled'] = False
    deviceDef = DeviceDefinition.model_validate(raw)
    requirement = dict(deviceTypeId=typeDecl.deviceTypeId, selectorSchema=dict(type='object',
        properties={'channelId': {'type': 'string'}}, required=['channelId'], additionalProperties=False))
    binding = dict(deviceId='tcp', selectors={})
    with pytest.raises(ValueError):
        validateDeviceBinding(requirement, binding, deviceDef, typeDecl)
    assert validateDeviceBinding(requirement, binding, deviceDef, typeDecl, allowMissingSelectors=True) == {}
    for selectors in ({'channelId': 'gone'}, {'channelId': 42}, {'unknown': 'x'}):
        with pytest.raises(ValueError):
            validateDeviceBinding(requirement, dict(binding, selectors=selectors), deviceDef, typeDecl,
                                  allowMissingSelectors=True)


def testPendingSelectorsPreserveOptionalDefaultsWithoutFillingRequiredSelector():
    typeDecl = DeviceType.model_validate_json((Path(__file__).resolve().parents[2] /
        'src/emo_master/plugins/builtins/tcp_session/device_types/server.json').read_text(encoding='utf-8'))
    declarationValue = typeDecl.model_dump()
    declarationValue['bindingSelectors']['backupChannelId'] = deepcopy(declarationValue['bindingSelectors']['channelId'])
    typeDecl = DeviceType.model_validate(declarationValue)
    deviceDef = DeviceDefinition.model_validate(tcpDefinition(typeDecl, channels=[
        dict(channelId='a', name='本机通道', allowedIps=['127.0.0.1'])]))
    requirement = dict(deviceTypeId=typeDecl.deviceTypeId, selectorSchema=dict(type='object', properties={
        'channelId': dict(type='string', default='a'),
        'backupChannelId': dict(type='string', default='a')}, required=['channelId'], additionalProperties=False))
    binding = dict(deviceId='tcp', selectors={})
    original = deepcopy(requirement)
    assert validateDeviceBinding(requirement, binding, deviceDef, typeDecl,
                                 allowMissingSelectors=True) == {'backupChannelId': 'a'}
    assert validateDeviceBinding(requirement, binding, deviceDef, typeDecl) == {
        'channelId': 'a', 'backupChannelId': 'a'}
    assert requirement == original and binding['selectors'] == {}
    requirement['selectorSchema']['properties']['backupChannelId']['default'] = 'gone'
    with pytest.raises(ValueError, match='backupChannelId'):
        validateDeviceBinding(requirement, binding, deviceDef, typeDecl, allowMissingSelectors=True)


def selectorProject(*, required=True, default='a', explicit=None):
    import json
    from emo_master.apps.designer.state.workflow_store import WorkflowStore
    from tests.runtime.test_tcp_device_integration import graph, node
    root = Path(__file__).resolve().parents[2]/'src/emo_master/plugins/builtins/tcp_session'
    typeDecl = DeviceType.model_validate_json((root/'device_types/server.json').read_text(encoding='utf-8'))
    manifest = json.loads((root/'manifest.json').read_text(encoding='utf-8'))
    manifest['operatorId'] = 'review.default_session'
    schema = manifest['deviceRequirements']['tcp']['selectorSchema']
    schema['required'] = ['channelId'] if required else []
    if default is not None:
        schema['properties']['channelId']['default'] = default
    payload = WorkflowStore().toPayload()
    payload['devices']['definitions']['server'] = tcpDefinition(typeDecl, channels=[
        dict(channelId='a', name='A', allowedIps=['127.0.0.1']),
        dict(channelId='b', name='B', allowedIps=['127.0.0.2'])])
    payload['workflows']['main'] = graph([node('acquire', manifest['operatorId'], 'server', selectors=explicit)])
    return payload, typeDecl, manifest


@pytest.mark.parametrize('required,explicit,expectedDraft,expectedPlan', [
    (True, {}, {}, {'channelId': 'a'}),
    (False, {}, {'channelId': 'a'}, {'channelId': 'a'}),
    (True, {'channelId': 'b'}, {'channelId': 'b'}, {'channelId': 'b'}),
])
def testSelectorDefaultsAgreeBetweenDraftAndCompiledPlan(required, explicit, expectedDraft, expectedPlan):
    from emo_master.core.workflow.compiler import WorkflowCompiler
    payload, typeDecl, manifest = selectorProject(required=required, explicit=explicit)
    original = deepcopy(payload)
    types = {typeDecl.deviceTypeId: typeDecl}
    compiled = WorkflowCompiler({manifest['operatorId']: manifest}, deviceTypes=types).compile(payload)
    plan = next(n for n in compiled.workflows['main'].nodes if n.nodeId == 'acquire').devicePlans['tcp']
    assert dict(plan['selectors']) == expectedPlan
    assert payload == original
    session = ProjectEditSession(payload)
    editor = DeviceCatalogEditor(session, types, {manifest['operatorId']: manifest['deviceRequirements']})
    rawNode = payload['workflows']['main']['nodes'][1]
    editor.commitNode('main', rawNode, allowIncomplete=True)
    saved = next(n for n in session.document().workflows['main'].nodes if n.nodeId == 'acquire')
    assert saved.deviceBindings['tcp'].selectors == expectedDraft
    assert rawNode['deviceBindings']['tcp']['selectors'] == explicit


@pytest.mark.parametrize('default,explicit', [('gone', {}), (42, {}),
    ('a', {'channelId': 'gone'}), ('a', {'channelId': 42}), ('a', {'undeclared': 'a'}), (None, {})])
def testInvalidSelectorsRetainStrictLocation(default, explicit):
    from emo_master.core.workflow.compiler import WorkflowCompiler
    from emo_master.core.workflow.errors import WorkflowCompileError
    payload, typeDecl, manifest = selectorProject(default=default, explicit=explicit)
    original = deepcopy(payload)
    with pytest.raises(WorkflowCompileError) as caught:
        WorkflowCompiler({manifest['operatorId']: manifest}, deviceTypes={typeDecl.deviceTypeId: typeDecl}).compile(payload)
    issue = caught.value.issues[0]
    assert issue.code == ('E_DEVICE_BINDING' if default == 'gone' or explicit == {'channelId': 'gone'}
                          else 'E_PARAM_INVALID')
    assert issue.workflowId == 'main' and issue.nodeId == 'acquire'
    assert issue.fieldPath.startswith('deviceBindings.tcp.selectors')
    assert payload == original


def testPendingWizardPreservesUnavailableDeviceInsteadOfErasingIt(ownedDesignerWindow, tmp_path,
        definition, declaration):  # noqa: F811
    window, requirements = windowFor(ownedDesignerWindow, tmp_path, definition, declaration)
    node = dict(nodeId='new', operatorId='vision.io.huaray_camera', params={}, paramSchema={},
                deviceBindings={'camera': {'deviceId': 'missing-device'}})
    wizard = DeviceNodeDialog(window.pageCoordinator, node, requirements[node['operatorId']], window)
    wizard.show()
    try:
        waitUntil(lambda: bool(wizard.types))
        combo = wizard.slots['camera'][0]
        assert combo.currentData() == 'missing-device' and '不可用' in combo.currentText()
        before = window.pageCoordinator.session.payload()
        wizard.savePending()
        assert wizard.isVisible() and window.pageCoordinator.session.payload() == before
        assert 'missing-device' in wizard.error.text()
        combo.setCurrentIndex(0)  # Explicitly remove the invalid reference.
        wizard.savePending()
        assert wizard.result() == wizard.Accepted
        assert not window.pageCoordinator.session.document().workflows['main'].nodes[-1].deviceBindings
    finally:
        wizard.reject()


def testDeviceCopiesAreNamedAndEachImportIsOneUndo(tmp_path, declaration, definition):  # noqa: F811
    session = ProjectEditSession(project(definition))
    package = exportDevices(session, ['camera'], tmp_path)
    original = package.model_dump()
    first = importDevices(session, package, {declaration.deviceTypeId: declaration}, tmp_path)['camera']
    second = importDevices(session, package, {declaration.deviceTypeId: declaration}, tmp_path)['camera']
    definitions = session.document().devices.definitions
    assert definitions[first].name == definition.name + '（导入副本）'
    assert definitions[second].name == definition.name + '（导入副本 2）'
    assert first != second and not definitions[first].enabled and not definitions[second].enabled
    assert package.model_dump() == original
    assert session.undo() and second not in session.document().devices.definitions
    assert first in session.document().devices.definitions


def testAdvancedDeviceFieldsKeepValuesAndRevealInvalidField(retainedQtApplication):
    schema = dict(type='object', properties={'name': {'type': 'string'},
        'budget': dict(type='number', minimum=1, default=20, xAdvanced=True)})
    form = configurationForm(schema, dict(name='kept', budget=37.5), deviceTypeId='third.party', section='connection')
    form.show()
    try:
        assert not form.advancedButton.isChecked()
        control = form._controls['budget']
        assert control.isEnabled() and not control.isVisible()
        assert form.getValues()['budget'] == 37.5
        control.setText('-1')
        assert form.validationMessage()
        QApplication.processEvents()
        assert form.advancedButton.isChecked() and control.isVisible() and control.hasFocus()
        control.setText('37.5')
        assert not form.validationMessage()
        form.advancedButton.setChecked(False)
        assert form.getValues()['budget'] == 37.5
    finally:
        form.close()


def testImvRequiredKeyAndFieldFeedbackClearWithoutLosingOtherErrors(retainedQtApplication):
    from emo_master.apps.designer.ui.device_wizard import DeviceDefinitionDialog
    typeDecl = DeviceType.model_validate_json((Path(__file__).resolve().parents[2] /
        'src/emo_master/plugins/builtins/huaray_camera/device_types/imv.json').read_text(encoding='utf-8'))
    dialog = DeviceDefinitionDialog(typeDecl)
    dialog.show()
    try:
        key = dialog.forms['connection']._controls['cameraKey']
        dialog.accept()
        QApplication.processEvents()
        assert dialog.value is None and key.hasFocus()
        assert dialog.error.text() == '请填写 SDK 设备唯一标识，或通过设备发现选择'
        dialog.name.setText('只修改名称')
        assert dialog.error.text() and key.accessibleDescription()
        key.setText('SIMULATED-KEY-FOR-EDITING-ONLY')
        assert not key.accessibleDescription() and not dialog.error.text()
        assert dialog.value is None
    finally:
        dialog.reject()


def testUnknownDefinitionValidationKeepsTechnicalDetails(retainedQtApplication, monkeypatch):
    from emo_master.apps.designer.ui.device_wizard import DeviceDefinitionDialog
    from tests.designer.test_device_identity_reuse import builtin
    dialog = DeviceDefinitionDialog(builtin('simulated'))
    try:
        def invalid(*_args):
            raise ValueError('vendor validation diagnostic retained verbatim')
        monkeypatch.setattr(DeviceType, 'validateDefinition', invalid)
        dialog.accept()
        assert dialog.value is None
        assert dialog.error.text() == '设备配置校验未通过，请查看详情'
        assert 'vendor validation diagnostic retained verbatim' in dialog.error.toolTip()
        assert 'E_DEVICE_DEFINITION' in dialog.error.toolTip()
    finally:
        dialog.reject()


def testCreationIgnoresOldScopeTestResult(retainedQtApplication):
    from emo_master.apps.designer.ui.device_wizard import DeviceDefinitionDialog
    from tests.designer.test_device_identity_reuse import builtin
    client = SimpleNamespace(runtimeScope='before')
    dialog = DeviceDefinitionDialog(builtin('simulated'), client=client)
    try:
        dialog.setupKind, dialog.setupScope = 'test', dialog._setupScope()
        dialog.setupPending = Future()
        dialog.setupPending.set_result(dict(message='旧设备测试成功'))
        client.runtimeScope = 'after'
        dialog.setupReady()
        assert '旧设备测试成功' not in dialog.error.text()
        assert '工程或 Runtime 连接已变化' in dialog.error.toolTip()
        assert dialog.value is None
    finally:
        dialog.reject()


@pytest.mark.parametrize('fault', ['type', 'source', 'unknown-slot'])
def testDraftModeRetainsStrictFilledReferenceChecks(definition, declaration, fault):  # noqa: F811
    session = ProjectEditSession(project(definition))
    requirements = {'vision.io.huaray_camera': {'camera': {'deviceTypeId': declaration.deviceTypeId},
                                              'later': {'deviceTypeId': declaration.deviceTypeId}}}
    editor = DeviceCatalogEditor(session, {declaration.deviceTypeId: declaration}, requirements)
    candidate = dict(nodeId='partial', operatorId='vision.io.huaray_camera',
                     deviceBindings={'camera': {'deviceId': 'camera'}})
    editor.commitNode('main', candidate, allowIncomplete=True)
    edited = definition.model_dump()
    edited['name'] = '合法编辑不受其他缺槽阻止'
    editor.put(edited, deviceId='camera')
    assert session.document().devices.definitions['camera'].name == edited['name']
    before = session.payload()
    if fault == 'type':
        editor.operatorRequirements['vision.io.huaray_camera']['camera']['deviceTypeId'] = 'wrong.type'
    elif fault == 'source':
        candidate['deviceValueBindings'] = {'camera': {'gain': {'source': 'variable', 'variableId': 'missing'}}}
    else:
        candidate['deviceBindings']['unknown'] = {'deviceId': 'camera'}
    with pytest.raises(ValueError):
        editor.commitNode('main', candidate, allowIncomplete=True)
    assert session.payload() == before


def testImportNamesReserveSuffixAtLimitAndWithinBatch(tmp_path, definition, declaration):  # noqa: F811
    from emo_master.apps.designer.state.device_catalog import importedDeviceName
    raw = definition.model_dump()
    raw['name'] = '长' * 160
    session = ProjectEditSession(project(DeviceDefinition.model_validate(raw)))
    session.workflows.devices['definitions']['second'] = deepcopy(raw)
    package = exportDevices(session, ['camera', 'second'], tmp_path)
    ids = importDevices(session, package, {declaration.deviceTypeId: declaration}, tmp_path)
    copies = [session.document().devices.definitions[key] for key in ids.values()]
    assert len({device.name for device in copies}) == 2
    assert all(len(device.name) == 160 and not device.enabled for device in copies)
    assert copies[0].name.endswith('（导入副本）') and copies[1].name.endswith('（导入副本 2）')
    assert importedDeviceName('无冲突', set()) == '无冲突'


def testPendingCameraReopensThroughCurrentEditorAndKeepsGraph(ownedDesignerWindow, tmp_path,
        definition, declaration, monkeypatch):  # noqa: F811
    window, requirements = windowFor(ownedDesignerWindow, tmp_path, definition, declaration)
    c = window.pageCoordinator
    editor = DeviceCatalogEditor(c.session, {declaration.deviceTypeId: declaration}, requirements)
    pending = dict(nodeId='pending-camera', operatorId='vision.io.huaray_camera', params={},
                   inputPorts={'gain': 'number'}, outputPorts={'frame': 'image'},
                   paramSchema=dict(type='object', properties={}, xDeviceActionFields=[]))
    editor.commitNode('main', pending, allowIncomplete=True, position=dict(x=40., y=50.))
    payload = c.session.payload()
    main = payload['workflows']['main']
    main.update(inputs={'gain': 'number'}, outputs={'frame': 'image'})
    boundary = {n['kind']: n['nodeId'] for n in main['nodes'] if n['kind'] in {'workflow_input', 'workflow_output'}}
    main['edges'] = [dict(fromNode=boundary['workflow_input'], fromPort='gain', toNode='pending-camera', toPort='gain'),
                     dict(fromNode='pending-camera', fromPort='frame', toNode=boundary['workflow_output'], toPort='frame')]
    with c.session.transaction():
        c.session._restore(payload)
    window.workflowController._renderActive()
    source = tmp_path / 'pending.emoproj'
    assert window.saveProjectToDirectory(str(source))
    assert window.loadProjectDirectory(str(source), confirmed=True)
    window.flowScene._applyPositions({'pending-camera': (80., 90.)})
    c.sync()
    graphBefore = c.session.workflows.graphFor()
    assert len(graphBefore['edges']) == 2
    opened = []
    monkeypatch.setattr(window, '_requireOperatorCatalog', lambda: True)
    monkeypatch.setattr(window, '_operatorDefinition', lambda _:
        dict(deviceRequirements=requirements['vision.io.huaray_camera']))
    def complete(dialog):
        opened.append(dialog)
        dialog.show()
        waitUntil(lambda: dialog.pendingButton.isEnabled())
        assert not dialog.migrateLegacy and dialog.slots['camera'][0].currentData() is None
        dialog.slots['camera'][0].setCurrentIndex(dialog.slots['camera'][0].findData('camera'))
        modes, constant, *_ = dialog.sources['camera'].rows['gain']
        assert constant.advancedButton is None
        modes.setCurrentIndex(modes.findData('constant'))
        constant._controls['value'].setValue(0.)
        dialog.accept()
        assert dialog.result() == dialog.Accepted, dialog.error.text()
        return dialog.result()
    monkeypatch.setattr(DeviceNodeDialog, 'exec_', complete)
    window.openNodeParamDialog('pending-camera')
    assert len(opened) == 1
    after = c.session.workflows.graphFor()
    assert after['edges'] == graphBefore['edges']
    current = next(n for n in after['nodes'] if n['nodeId'] == 'pending-camera')
    assert current['inputPorts'] == pending['inputPorts'] and current['outputPorts'] == pending['outputPorts']
    assert current['deviceOverrides']['camera']['gain'] == 0.
    assert c.session.workflows.get('main').layout['nodePositions']['pending-camera'] == dict(x=80., y=90.)


def testPendingMarkerUpdatesWhenOperatorCatalogArrives(ownedDesignerWindow, tmp_path,
        definition, declaration):  # noqa: F811
    from threading import Event

    window, requirements = windowFor(ownedDesignerWindow, tmp_path, definition, declaration)
    waitUntil(lambda: window.operatorCatalogController.state == 'ready')
    c = window.pageCoordinator
    editor = DeviceCatalogEditor(c.session, {declaration.deviceTypeId: declaration}, requirements)
    editor.commitNode('main', dict(nodeId='pending-camera', operatorId='vision.io.huaray_camera'),
                      allowIncomplete=True, position=dict(x=80., y=90.))
    window.workflowController._renderActive()
    scene = window.flowScene
    item = scene._nodeItems['pending-camera']
    item.setSelected(True)
    assert ('绑定状态待检查', None) in item.model.summaryLines
    before = c.session.payload()
    release, entered = Event(), Event()

    def delayedCatalog(**_kwargs):
        entered.set()
        assert release.wait(5)
        return [SimpleNamespace(operatorId='vision.io.huaray_camera', displayName='相机',
                                deviceRequirements=requirements['vision.io.huaray_camera'])]

    window.runtimeClient.listOperators = delayedCatalog
    try:
        window.refreshOperators()
        assert entered.wait(2)
        release.set()
        waitUntil(lambda: window.operatorCatalogController.state == 'ready')
        current = scene._nodeItems['pending-camera']
        assert ('待选择设备：camera', None) in current.model.summaryLines
        assert ('绑定状态待检查', None) not in current.model.summaryLines
        assert current.isSelected() and (current.pos().x(), current.pos().y()) == (80., 90.)
        assert c.session.payload() == before
    finally:
        release.set()
