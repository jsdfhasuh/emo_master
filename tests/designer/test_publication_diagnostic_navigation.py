"""Compiler/RPC diagnostics locate editable drafts without a second validator."""
from copy import deepcopy
import json

import pytest

from emo_master.apps.designer.state.workflow_store import WorkflowStore
from emo_master.apps.designer.ui.project_configuration import ProjectConfigurationDialog, configurationDiagnostics
from emo_master.core.workflow.compiler import WorkflowCompiler
from emo_master.core.workflow.errors import WorkflowCompileError
from tests.designer.runtime_feedback_fixture import messages  # noqa: F401
from tests.designer.test_device_workspace import waitUntil
from tests.runtime.test_tcp_device_closure import runtime, thirdParty  # noqa: F401
from tests.runtime.test_tcp_device_integration import definition, graph, node


def draft(service):
    payload = WorkflowStore().toPayload()
    payload['workflows']['main'] = graph([node('acquire', 'communication.tcp.session', 'server')])
    payload['workflows']['main']['name'] = '产线检测'
    payload['workflows']['main']['nodes'][1]['displayName'] = '等待机器人信号'
    payload['devices']['definitions'] = {'server': dict(definition(
        service.pluginScanResult.deviceTypes['device.tcp.server'],
        channels=[dict(channelId='a', name='机器人', allowedIps=['127.0.0.1'])]), name='机器人通信服务')}
    return payload


@pytest.mark.parametrize('missing,target,phrase', [
    ('channel', 'node', '通道未选择'),
    ('device', 'node', '设备未选择'),
    ('definition', 'device', '设备待配置'),
])
def testCompilerDiagnosticsExplainNamesAndFields(runtime, missing, target, phrase):  # noqa: F811
    service, _ = runtime
    payload = draft(service)
    if missing == 'device':
        payload['workflows']['main']['nodes'][1].pop('deviceBindings')
    if missing == 'definition':
        payload['devices']['definitions']['server']['connection']['port'] = -1
    compiler = WorkflowCompiler(service.pluginScanResult.activeOperators, deviceTypes=service.pluginScanResult.deviceTypes)
    with pytest.raises(WorkflowCompileError) as caught:
        compiler.compile(payload)
    entries = configurationDiagnostics(caught.value, payload, service.pluginScanResult.deviceTypes)
    assert entries[0]['target'] == target
    assert '产线检测' in entries[0]['text'] and '等待机器人信号' in entries[0]['text']
    assert phrase in entries[0]['text']
    assert entries[0]['workflowId'] == 'main' and entries[0]['nodeId'] == 'acquire'
    if target == 'device':
        assert entries[0]['deviceId'] == 'server' and '端口' in entries[0]['text']
    assert 'E_' not in entries[0]['text'] and 'deviceBindings' not in entries[0]['text']


def testInitialPublishRpcErrorLocatesNodeAndRetainsDraft(runtime, ownedDesignerWindow, tmp_path,
                                                        monkeypatch, messages):  # noqa: F811
    service, client = runtime
    payload = draft(service)
    path = tmp_path / 'pending.emoproj'
    path.write_text(json.dumps(payload), encoding='utf-8')
    window = ownedDesignerWindow(client)
    assert window.loadProjectDirectory(str(path), confirmed=True)
    window.pageCoordinator.sync()
    before = deepcopy(window.pageCoordinator.session.payload())
    disk = path.read_bytes()
    dialog = ProjectConfigurationDialog(window.pageCoordinator, window)
    dialog.show()
    waitUntil(lambda: dialog.pending is None)
    assert dialog.initial
    dialog.validateButton.click()
    waitUntil(lambda: dialog.pending is None)
    assert len(messages) == 1
    assert '产线检测' in messages[0]['text'] and '等待机器人信号' in messages[0]['text']
    assert '通道未选择' in messages[0]['text'] and '机器人通信服务' in messages[0]['text']
    assert 'deviceBindings.tcp.selectors.channelId' in messages[0]['detail']
    assert 'E_PARAM_INVALID' in messages[0]['detail'] and 'acquire' in messages[0]['detail']
    assert '定位节点并配置' in messages[0]['buttons']
    opened = []
    monkeypatch.setattr(window, 'openNodeParamDialog', lambda key: opened.append(key))
    dialog.locateButton.click()
    assert opened == ['acquire'] and window.activeWorkflowId == 'main'
    assert window.pageCoordinator.session.payload() == before and path.read_bytes() == disk
    assert not service.loadedProjectId and not service.deviceManager.drivers


def testUnknownDiagnosticKeepsNormalReturnAndFullDetails(ownedDesignerWindow, tmp_path, messages):  # noqa: F811
    from tests.designer.test_project_configuration_ui import setupDialog
    window, dialog, _calls, path = setupDialog(ownedDesignerWindow, tmp_path)
    before, disk = window.pageCoordinator.session.payload(), path.read_bytes()
    try:
        error = ValueError('new validation rule')
        error.diagnostics = {'issues': [dict(code='E_FUTURE_RULE', message='new validation rule', fieldPath='future.setting')]}
        dialog._notify('工程校验未通过', str(error), dialog._errorDetails(error), diagnostic=error)
        assert '查看详细信息' in messages[0]['text'] and 'E_FUTURE_RULE' in messages[0]['detail']
        assert dialog.locateButton.text() == '返回配置'
        dialog.locateButton.click()
        assert window.pageCoordinator.session.payload() == before and path.read_bytes() == disk
    finally:
        dialog.reject()


def testDeviceDiagnosticOpensNamedDeviceConnection(runtime, ownedDesignerWindow, tmp_path, messages):  # noqa: F811
    service, client = runtime
    payload = draft(service)
    payload['devices']['definitions']['server']['connection']['port'] = -1
    path = tmp_path / 'pending-device.emoproj'
    path.write_text(json.dumps(payload), encoding='utf-8')
    window = ownedDesignerWindow(client)
    assert window.loadProjectDirectory(str(path), confirmed=True)
    window.pageCoordinator.sync()
    before = deepcopy(window.pageCoordinator.session.payload())
    dialog = ProjectConfigurationDialog(window.pageCoordinator, window)
    dialog.show()
    waitUntil(lambda: dialog.pending is None)
    dialog.validateButton.click()
    waitUntil(lambda: dialog.pending is None)
    assert '设备待配置' in messages[0]['text']
    assert dialog.locateButton.text() == '配置设备'
    dialog.locateButton.click()
    devices = window.pageCoordinator.devices
    waitUntil(lambda: devices.pending is None)
    assert devices.selectedId == 'server' and devices.detailTitle.text() == '机器人通信服务'
    assert devices.tabs.currentWidget() is devices.formScrolls['connection']
    assert window.pageCoordinator.session.payload() == before
