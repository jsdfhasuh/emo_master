"""One temporary engineering draft progresses from pending bindings to real Jobs."""
from copy import deepcopy
import json
import socket

import numpy as np
import pytest
from PySide2.QtCore import QTimer, Qt
from PySide2.QtWidgets import QMessageBox

from emo_master.apps.designer.state.device_catalog import DeviceCatalogEditor
from emo_master.apps.designer.state.workflow_package import buildWorkflowPackage, importWorkflowPackage
from emo_master.apps.designer.state.workflow_store import WorkflowStore
from emo_master.apps.designer.ui.device_wizard import DeviceDefinitionDialog, DeviceNodeDialog
from emo_master.apps.designer.ui.project_configuration import ProjectConfigurationDialog
from emo_master.core.project.configuration import selectedCandidate
from emo_master.core.project.runtime_package import buildRuntimePackage
from emo_master.core.workflow.compiler import WorkflowCompiler
from emo_master.core.workflow.errors import WorkflowCompileError
from tests.designer.test_device_workspace import waitUntil
from tests.runtime.test_tcp_device_closure import runtime, thirdParty  # noqa: F401
from tests.runtime.test_tcp_device_integration import definition, freePort, graph, node, edge, run, waitWrite


def testC1ThroughC6PendingDraftPublicationAndRun(runtime, ownedDesignerWindow, tmp_path, monkeypatch):  # noqa: F811
    service, client = runtime
    types, requirements, _ = client.listDeviceTypes()
    requirements = {operator: {slot: value.model_dump() for slot, value in slots.items()}
                    for operator, slots in requirements.items()}
    root = tmp_path/'engineering'
    root.mkdir()
    source = root/'usability.emoproj'
    source.write_text(json.dumps(WorkflowStore().toPayload()), encoding='utf-8')
    window = ownedDesignerWindow(client)
    window.show()
    assert window.loadProjectDirectory(str(source), confirmed=True)
    c = window.pageCoordinator
    draftNode = node('acquire', 'communication.tcp.session', 'unused')
    draftNode.pop('deviceBindings')
    manifest = service.pluginScanResult.activeOperators[draftNode['operatorId']].manifest
    def ports(values):
        return {key: value if isinstance(value, str) else value['type'] for key, value in values.items()}
    draftNode.update(inputPorts=ports(manifest.inputPorts), outputPorts=ports(manifest.outputPorts),
                     paramSchema=manifest.paramSchema)
    wizard = DeviceNodeDialog(c, draftNode, requirements[draftNode['operatorId']], window, position=dict(x=20., y=30.))
    wizard.show()
    waitUntil(lambda: wizard.pendingButton.isEnabled())
    def create(dialog):
        dialog.enabled.setChecked(False)
        dialog.forms['connection'].fields._controls['port'].setValue(freePort())
        dialog.accept()
        return dialog.result()
    with monkeypatch.context() as patch:
        patch.setattr(DeviceDefinitionDialog, 'exec_', create)
        wizard._create('tcp')
    server = wizard.slots['tcp'][0].currentData()
    assert server and wizard.selectors['tcp']['channelId'].currentData() is None
    before = len(c.session._undo)
    wizard.accept()
    assert wizard.isVisible() and 'selectors.channelId' in wizard.error.text()
    wizard.savePending()
    assert wizard.result() == wizard.Accepted and len(c.session._undo) == before+1
    pending = c.session.document().workflows['main'].nodes[-1]
    assert pending.deviceBindings['tcp'].deviceId == server and not pending.deviceBindings['tcp'].selectors
    assert not c.session.document().devices.definitions[server].enabled
    assert window.saveProjectToDirectory(str(source))
    assert window.loadProjectDirectory(str(source), confirmed=True)
    savedPending = source.read_bytes()
    assert c.session.document().workflows['main'].nodes[-1].nodeId == 'acquire'
    with pytest.raises(WorkflowCompileError) as invalid:
        WorkflowCompiler(service.pluginScanResult.activeOperators, deviceTypes=types).compile(c.session.payload())
    assert any(issue.nodeId == 'acquire' and issue.fieldPath == 'deviceBindings.tcp.selectors.channelId'
               for issue in invalid.value.issues)
    with pytest.raises(WorkflowCompileError):
        buildRuntimePackage(root, tmp_path/'incomplete-runtime', registry=service.pluginScanResult.activeOperators, deviceTypes=types)
    template = buildWorkflowPackage(c.session.workflows, 'main', sourceRoot=root)
    target = WorkflowStore()
    imported = importWorkflowPackage(target, template, deviceMapping={server: None})
    copied = next(n for n in target.get(imported.rootWorkflowId).nodes if n['nodeId'] == 'acquire')
    assert copied['deviceBindings']['tcp']['deviceId'] == imported.deviceIdMap[server]
    assert copied['deviceBindings']['tcp'].get('selectors', {}) == {}
    # A disabled, empty-channel definition is a valid executable configuration
    # when no incomplete node is selected into that configuration.
    online = c.session.payload()
    online['workflows']['main'] = graph([])
    client.initializeProjectConfiguration(online, root, 'usability-initial', runtimeInstanceId=service.runtimeInstanceId)
    assert waitWrite(service, client, 'usability-initial')['state'] == 'COMMITTED'
    port = online['devices']['definitions'][server]['connection']['port']
    assert not service.deviceManager.drivers[server].snapshot().get('ready')
    with socket.socket() as available:
        available.bind(('127.0.0.1', port))
    tested = service.deviceManager.testConnection(online['devices']['definitions'][server])
    assert '未验证外部客户端' in tested['message']
    createTest = DeviceDefinitionDialog(types['device.tcp.server'], window,
        initial=online['devices']['definitions'][server], client=client)
    createTest.show()
    try:
        with monkeypatch.context() as patch:
            patch.setattr(QMessageBox, 'question', lambda *_: QMessageBox.Yes)
            createTest.testConnection()
            waitUntil(lambda: createTest.setupPending is None)
        assert createTest.error.text() == tested['message']
        assert createTest.value is None
    finally:
        createTest.reject()
    assert not service.deviceManager.maintenanceOwners
    assert set(service.deviceManager.drivers) == {server}
    editor = DeviceCatalogEditor(c.session, types, requirements)
    value = deepcopy(c.session.payload()['devices']['definitions'][server])
    value['connection']['channels'] = [dict(channelId='a', name='本机测试', allowedIps=['127.0.0.1'])]
    value['enabled'] = True
    value['policies']['connect'] = 'on_project_open'
    editor.put(value, deviceId=server)  # Missing channel in the node does not block a legal device edit.
    pending = next(n.model_dump() for n in c.session.document().workflows['main'].nodes if n.nodeId == 'acquire')
    pending['deviceBindings']['tcp']['selectors'] = dict(channelId='a')
    editor.commitNode('main', pending)
    payload = c.session.payload()
    payload['workflows']['main'] = graph([pending, node('send', 'communication.tcp.session_send', server)],
        [edge('acquire', 'session', 'send', 'session'), edge('input', 'text', 'send', 'data'),
         edge('send', 'receipt', 'output', 'receipt')], outputs=dict(receipt='tcpSendReceipt'), inputs=dict(text='string'))
    payload['devices']['definitions']['camera'] = definition(types['device.simulated.camera'], serial='USABILITY-SIM')
    payload['devices']['definitions']['camera']['defaults'].update(width=8, height=6, exposureUs=1000, gain=0.)
    payload['workflows']['camera'] = graph([dict(nodeId='capture', operatorId='vision.io.simulated_camera',
        deviceBindings={'camera': dict(deviceId='camera')})], [edge('capture', 'image', 'output', 'image')], outputs=dict(image='image'))
    payload['workflowOrder'].append('camera')
    for workflow in payload['workflows'].values():
        for stepNode in workflow['nodes']:
            operator = stepNode.get('operatorId')
            if operator:
                stepManifest = service.pluginScanResult.activeOperators[operator].manifest
                stepNode.update(inputPorts=ports(stepManifest.inputPorts), outputPorts=ports(stepManifest.outputPorts),
                                paramSchema=stepManifest.paramSchema)
    with c.session.transaction():
        c.session._restore(payload)
    window.workflowController._renderActive()
    c.showDevices()
    observer = c.devices
    waitUntil(lambda: observer.pending is None)
    observer.selectedId = server
    observer.refresh()
    observer.resize(1200, 760)
    assert observer.grab().save(str(tmp_path/'C2-fixed-actions.png'))
    modalErrors, dialogs = [], []
    originalExec = ProjectConfigurationDialog.exec_
    monkeypatch.setattr(QMessageBox, 'question', lambda *_: QMessageBox.Yes)
    def publish(dialog):
        dialogs.append(dialog)
        stage = 0
        def step():
            nonlocal stage
            try:
                if dialog.pending is not None or dialog.writePending:
                    QTimer.singleShot(30, step)
                    return
                assert not observer.isVisible() and dialog.parentWidget() is window
                if stage == 0:
                    assert not dialog.initial
                    for row in range(dialog.table.rowCount()):
                        dialog.table.item(row, 0).setCheckState(Qt.Checked)
                    assert dialog.grab().save(str(tmp_path/'C3-publication-summary.png'))
                    dialog.write(validate=True)
                elif stage == 1:
                    dialog.write()
                else:
                    assert client.getProjectWriteStatus(dialog.requestId)['state'] == 'COMMITTED'
                    dialog.reject()
                    return
                stage += 1
                QTimer.singleShot(30, step)
            except BaseException as error:
                modalErrors.append(error)
                dialog.reject()
        QTimer.singleShot(30, step)
        return originalExec(dialog)
    with monkeypatch.context() as patch:
        patch.setattr(ProjectConfigurationDialog, 'exec_', publish)
        c.configureFromDevices()
        waitUntil(lambda: bool(dialogs) and observer.isVisible() and not c._devicePublishPending)
    assert not modalErrors, modalErrors
    assert c.session.dirty and source.read_bytes() == savedPending
    waitUntil(lambda: observer.pending is None)
    observer.closeButton.click()
    waitUntil(lambda: bool(service.deviceManager.drivers[server].snapshot().get('ready')))
    with socket.create_connection(('127.0.0.1', port), timeout=5) as peer:
        job = run(service, client, 'main', dict(text='USABILITY-BYTES'))
        assert peer.recv(100) == b'USABILITY-BYTES'
    cameraJob = run(service, client, 'camera')
    cameraOutput = next(json.loads(event.payloadJson)['outputs']['image']
        for event in service.eventStore.read(cameraJob) if event.eventType == 'workflow.completed')
    expected = np.array([[[x, 1, 1] for x in range(8)] for _ in range(6)], dtype=np.uint8)
    assert cameraOutput == str(expected)
    # Keep a legal online version while unrelated local edits remain incomplete.
    snapshot = client.getOnlineProjectSnapshot('')
    local = deepcopy(snapshot['payload'])
    local['devices']['definitions']['camera']['defaults']['gain'] = 2.0
    local['devices']['definitions']['camera']['connection']['serial'] = 'UNSELECTED-DRAFT'
    local['workflows']['camera']['nodes'].append(dict(nodeId='pending-camera', operatorId='vision.io.simulated_camera'))
    selected = ['/devices/definitions/camera/defaults/gain']
    candidate = selectedCandidate(snapshot['payload'], local, selected)
    checked = client.writeProjectConfiguration(snapshot, local, selected, 'usability-partial', validate=True)
    assert checked['candidate'] == candidate
    client.writeProjectConfiguration(snapshot, local, selected, 'usability-partial')
    assert waitWrite(service, client, 'usability-partial')['state'] == 'COMMITTED'
    assert client.getOnlineProjectSnapshot('')['payload']['devices']['definitions']['camera']['connection']['serial'] == 'USABILITY-SIM'
    with pytest.raises(RuntimeError) as rejected:
        client.writeProjectConfiguration(client.getOnlineProjectSnapshot(''), local,
            ['/workflows/camera/nodes/pending-camera'], 'usability-pending-rejected', validate=True)
    assert any(issue['workflowId'] == 'camera' and issue['nodeId'] == 'pending-camera'
               and issue['fieldPath'] == 'deviceBindings.camera' for issue in rejected.value.diagnostics['issues'])
    assert run(service, client, 'camera')
    state = client.getDeviceStates(service.loadedProjectId)[1][server]
    scope = {key: state[key] for key in ('runtimeInstanceId', 'projectId', 'projectLoadGeneration')}
    logs = client.getDeviceDiagnostics(scope, server)
    assert any(row['eventType'] == 'device.tcp.tx' and row.get('bytes') == len(b'USABILITY-BYTES') for row in logs['records'])
    (tmp_path/'usability-results.json').write_text(json.dumps(dict(server=server, jobId=job,
        cameraJobId=cameraJob, cameraImage=cameraOutput, template=template.toPayload(), diagnostics=logs), ensure_ascii=False, indent=2), encoding='utf-8')


def testConnectionRefusalReachesBothEditorsAsActionableFeedback(runtime, retainedQtApplication, monkeypatch):  # noqa: F811
    from emo_master.apps.designer.ui.device_wizard import deviceErrorMessage
    service, client = runtime
    value = definition(service.pluginScanResult.deviceTypes['device.tcp.client_once'], port=freePort())
    with pytest.raises(RuntimeError) as refused:
        client.deviceNetworkCommand('', service.runtimeInstanceId, 'test_connection',
                                    payload=dict(confirmed=True, definition=value))
    assert refused.value.code == 'E_DEVICE_CONNECTION_REFUSED'
    assert deviceErrorMessage(refused.value) == '无法连接到目标地址和端口，请检查设备端服务与端口设置'
    assert not service.deviceManager.drivers and not service.deviceManager.maintenanceOwners
