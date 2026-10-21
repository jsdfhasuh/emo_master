"""Current 2.7 TCP vertical path through publication and spawned production Jobs."""
from copy import deepcopy
import json
import hashlib
from pathlib import Path
import socket
from threading import Event, Thread
import time

import pytest

from emo_master.apps.designer.services.runtime_client import RuntimeClient
from emo_master.apps.designer.state.schema_utils import applySchemaDefaults
from emo_master.apps.designer.state.workflow_store import WorkflowStore
from emo_master.apps.runtime.grpc_server.service import RuntimeService
from emo_master.core.project.models import ProjectDocument
from tests.runtime.runtime_test_utils import waitForTerminal


def freePort():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def definition(declaration, **connection):
    return dict(name=declaration.displayName, deviceTypeId=declaration.deviceTypeId,
        driverVersion=declaration.version,
        connection=applySchemaDefaults(declaration.connectionSchema, connection),
        defaults=applySchemaDefaults(declaration.defaultsSchema, {}),
        policies=applySchemaDefaults(declaration.policiesSchema, {}), enabled=True)


def graph(nodes, edges=(), outputs=None, inputs=None):
    return dict(name='TCP acceptance', inputs=inputs or {}, outputs=outputs or {},
        nodes=[dict(nodeId='input', kind='workflow_input'), *nodes, dict(nodeId='output', kind='workflow_output')],
        edges=list(edges), layout=dict(nodePositions={}))


def edge(a, ap, b, bp):
    return dict(fromNode=a, fromPort=ap, toNode=b, toPort=bp)


def node(key, operator, device, *, selectors=None, params=None):
    return dict(nodeId=key, operatorId=operator, params=params or {},
                deviceBindings={'tcp': dict(deviceId=device, selectors=selectors or {})})


def waitWrite(service, client, request):
    end = time.monotonic()+15
    while time.monotonic() < end:
        result = client.getProjectWriteStatus(request)
        if result['state'] in {'COMMITTED', 'FAILED', 'CANCELLED'} and not service.projectConfiguration.active:
            return result
        time.sleep(.01)
    raise AssertionError(result)


def run(service, client, workflow, inputs=None):
    reply = client.startJob(service.loadedProjectId, workflow, inputs=inputs or {})
    assert reply.ok, reply
    job = reply.job_id
    result = waitForTerminal(service, job, timeoutSeconds=30)
    assert result.status == 'COMPLETED', result
    end = time.monotonic()+5
    while service.jobSupervisor.ownsJobResources(job) and time.monotonic() < end:
        service.jobSupervisor.finishRetirements()
        time.sleep(.01)
    return job


@pytest.fixture(scope='module')
def chain(tmp_path_factory):
    root = tmp_path_factory.mktemp('tcp-device-chain')
    service = RuntimeService(dbPath=root/'runtime/runtime.db', workspaceRoot=root/'runtime/jobs')
    client = RuntimeClient(runtimeService=service)
    types = service.pluginScanResult.deviceTypes
    payload = WorkflowStore().toPayload()
    server = definition(types['device.tcp.server'], port=freePort(), channels=[
        dict(channelId='a', name='Station A', allowedIps=['127.0.0.1']),
        dict(channelId='b', name='Station B', allowedIps=['127.0.0.2'])])
    server['policies']['connect'] = 'on_project_open'
    server['policies'].update(maxMessageBytes=16777216, sendTimeoutMs=10000)
    clientOnce = definition(types['device.tcp.client_once'], port=freePort())
    receiver = definition(types['device.tcp.receive_once'], port=freePort())
    receiver['defaults'].update(messageTextEncoding='utf-8', ackText='ACK')
    camera = definition(types['device.simulated.camera'], serial='ACCEPTANCE-SIMULATOR')
    camera['defaults'].update(width=8, height=6)
    plc = definition(types['device.plc.mitsubishi_slmp'], port=freePort())
    payload['devices']['definitions'] = dict(server=server, client=clientOnce, receiver=receiver, camera=camera, plc=plc)
    payload['globalVariables'] = dict(wait=dict(name='等待客户端', type='integer', kind='constant', initialValue=5000))
    payload['workflows']['main'] = graph([
        node('acquire', 'communication.tcp.session', 'server', selectors=dict(channelId='a')),
        node('send', 'communication.tcp.session_send', 'server')],
        [edge('acquire', 'session', 'send', 'session'), edge('input', 'text', 'send', 'data'),
         edge('send', 'receipt', 'output', 'receipt')], outputs=dict(receipt='tcpSendReceipt'), inputs=dict(text='string'))
    payload['workflows']['single'] = graph([node('client', 'communication.tcp.client', 'client', params=dict(operation='exchange', text='PING'))],
        [edge('client', 'response', 'output', 'response')], outputs=dict(response='tcpMessage'))
    payload['workflows']['receive'] = graph([node('receive', 'communication.tcp.receive_once', 'receiver')],
        [edge('receive', 'message', 'output', 'message')], outputs=dict(message='tcpMessage'))
    payload['workflows']['other'] = graph([
        node('wait-b', 'communication.tcp.session', 'server', selectors=dict(channelId='b'))],
        [edge('wait-b', 'session', 'output', 'session')], outputs=dict(session='tcpSessionRef'))
    payload['workflows']['main']['nodes'][1]['deviceValueBindings'] = {'tcp': {'timeoutMs': dict(source='variable', variableId='wait')}}
    payload['workflows']['camera-plc'] = graph([
        dict(nodeId='camera', operatorId='vision.io.simulated_camera', deviceBindings={'camera': dict(deviceId='camera')}),
        dict(nodeId='process', operatorId='vision.analysis.blob', params=dict(threshold=0)),
        dict(nodeId='count', operatorId='vision.collection.count'),
        dict(nodeId='plc', operatorId='communication.plc.slmp_write', params=dict(startAddress=100),
             deviceBindings={'plc': dict(deviceId='plc')})],
        [edge('camera', 'image', 'process', 'image'), edge('process', 'blobs', 'count', 'blobs'),
         edge('count', 'count', 'plc', 'value'),
         edge('process', 'mask', 'output', 'image'), edge('plc', 'receipt', 'output', 'receipt')],
        outputs=dict(image='image', receipt='plcWriteReceipt'))
    payload['workflows']['main']['nodes'].extend([
        dict(nodeId='load', operatorId='vision.io.image_loader', params=dict(imagePath='sample.png')),
        dict(nodeId='mask', operatorId='vision.preprocess.threshold', params=dict(mode='fixed', threshold=64))])
    payload['workflows']['main']['edges'].append(edge('load', 'image', 'mask', 'image'))
    payload['workflowOrder'] = list(payload['workflows'])
    for workflow in payload['workflows'].values():
        for item in workflow['nodes']:
            if item.get('operatorId'):
                manifest = service.pluginScanResult.activeOperators[item['operatorId']].manifest
                for field in ('inputPorts', 'outputPorts'):
                    item[field] = {key: value if isinstance(value, str) else value['type']
                                   for key, value in getattr(manifest, field).items()}
    directory = root/'engineering'
    directory.mkdir()
    import cv2
    import numpy as np
    cv2.imwrite(str(directory/'sample.png'), np.arange(24, dtype=np.uint8).reshape(4, 6))
    data = (directory/'sample.png').read_bytes()
    payload['resources'] = dict(items={'sample': dict(path='sample.png', size=len(data),
        sha256=hashlib.sha256(data).hexdigest(), purpose='input_image')}, parameterBindings=[
        dict(resourceId='sample', target=dict(workflowId='main', nodeId='load', parameterPath=['imagePath']))],
        siteBindings=[dict(field='camera-serial', purpose='camera_serial', target=dict(deviceId='camera',
            section='connection', parameterPath=['serial']))])
    path = directory/'acceptance.emoproj'
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    print('TCP_ACCEPTANCE_ROOT=' + str(root))
    yield dict(root=root, service=service, client=client, payload=payload, path=path, types=types, jobs=[])
    client.close()
    service.close()


def testA1SavedCurrentProjectValidatesWithoutListeningThenInitialPublication(chain, ownedDesignerWindow, monkeypatch):
    from PySide2.QtCore import Qt
    from PySide2.QtTest import QTest
    from PySide2.QtWidgets import QDialogButtonBox, QMessageBox
    from emo_master.apps.designer.ui.device_wizard import DeviceDefinitionDialog, DeviceNodeDialog
    from tests.designer.test_device_workspace import waitUntil
    from tests.designer.test_runtime_menu_publication import menuDialog
    c = chain
    window = ownedDesignerWindow(RuntimeClient(runtimeService=c['service']))
    window.resize(1400, 900)
    window.show()
    assert window.loadProjectDirectory(str(c['path']), confirmed=True)
    coordinator = window.pageCoordinator
    coordinator.chrome.deviceAction.trigger()
    workspace = coordinator.devices
    waitUntil(lambda: workspace.pending is None)
    # Use a fixed ID in this harness so every subsequent group uses this same project.
    declaration = c['types']['device.tcp.server']
    create = DeviceDefinitionDialog(declaration, workspace, initial=c['payload']['devices']['definitions']['server'],
        client=c['client'], beforeAccept=lambda value: workspace.catalog().put(value, deviceId='server'))
    create.show()
    address = create.forms['connection'].channels.table.item(0, 2)
    address.setText('999.0.0.1')
    QTest.mouseClick(create.findChild(QDialogButtonBox).button(QDialogButtonBox.Ok), Qt.LeftButton)
    assert create.isVisible() and create.error.text()
    address.setText('127.0.0.1')
    QTest.mouseClick(create.findChild(QDialogButtonBox).button(QDialogButtonBox.Ok), Qt.LeftButton)
    assert create.result() == create.Accepted, create.error.text()
    workspace.refresh()
    row = next(row for row in range(workspace.table.rowCount()) if workspace.table.item(row, 0).data(Qt.UserRole) == 'server')
    workspace.table.setCurrentCell(row, 0)
    original = deepcopy(coordinator.session.payload())
    channels = workspace.forms['connection'].channels
    channels.table.removeRow(0)
    workspace.apply()
    assert 'main/acquire' in workspace.status.text(), workspace.status.text()
    assert coordinator.session.payload() == original and channels.table.rowCount() == 1
    channels.setValue(original['devices']['definitions']['server']['connection']['channels'])
    channels.table.item(0, 1).setText('Station A Qt')
    workspace.apply()
    assert coordinator.session.payload()['devices']['definitions']['server']['connection']['channels'][0]['channelId'] == 'a'
    workspace.tabs.setCurrentIndex(1)
    assert workspace.grab().save(str(c['root']/'qt-device-connection.png'))
    workspace.closeButton.click()
    for _ in range(2):
        acquire = deepcopy(next(item for item in coordinator.session.workflows.get('main').nodes if item['nodeId'] == 'acquire'))
        acquire['paramSchema'] = c['service'].pluginScanResult.activeOperators['communication.tcp.session'].manifest.paramSchema
        requirements = {'tcp': c['service'].pluginScanResult.activeOperators['communication.tcp.session'].manifest.deviceRequirements['tcp'].model_dump()}
        wizard = DeviceNodeDialog(coordinator, acquire, requirements, window)
        wizard.show()
        waitUntil(lambda: wizard.buttons.button(QDialogButtonBox.Ok).isEnabled())
        assert wizard.selectors['tcp']['channelId'].currentData() == 'a'
        QTest.mouseClick(wizard.buttons.button(QDialogButtonBox.Ok), Qt.LeftButton)
        assert wizard.result() == wizard.Accepted, wizard.error.text()
    assert window.saveProjectToDirectory(str(c['path']))
    assert window.loadProjectDirectory(str(c['path']), confirmed=True)
    c['payload'] = json.loads(c['path'].read_text(encoding='utf-8'))
    stored = ProjectDocument.model_validate_json(c['path'].read_bytes())
    assert stored.schemaVersion == '2.7' and stored.communication is None
    reopened = WorkflowStore(stored.model_dump(mode='json')).toPayload()
    assert reopened['devices'] == stored.model_dump()['devices']
    dialog = menuDialog(window, monkeypatch)
    dialog.validateButton.click()
    waitUntil(lambda: dialog.pending is None)
    assert dialog.initialValidated, dialog.status.text()
    assert not c['service'].deviceManager.drivers
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', reopened['devices']['definitions']['server']['connection']['port']))
    monkeypatch.setattr(QMessageBox, 'question', lambda *_args: QMessageBox.Yes)
    dialog.writeButton.click()
    waitUntil(lambda: dialog.pending is None and not dialog.writePending and not dialog.initial)
    request = dialog.requestId
    receipt = waitWrite(c['service'], c['client'], request)
    assert receipt['state'] == 'COMMITTED', receipt
    end = time.monotonic()+5
    while receipt.get('connections', {}).get('server') == 'PENDING' and time.monotonic() < end:
        time.sleep(.01)
        receipt = c['client'].getProjectWriteStatus(request)
    assert receipt['connections']['server'] == 'LISTENING', receipt
    assert not c['service'].jobRepository.all()
    assert json.loads(c['path'].read_text(encoding='utf-8')) == c['payload']
    assert dialog.grab().save(str(c['root']/'qt-first-write.png'))
    dialog.reject()

    # A published managed project can be saved under a new root without replacing Runtime.
    runtimeDocument = c['client'].getOnlineProjectSnapshot()['payload']
    backend = c['service'].deviceManager.drivers['server']
    generation = dict(c['service'].deviceManager.scope)
    jobs = list(c['service'].jobRepository.all())
    copy = c['root']/'saved-copy'
    monkeypatch.setattr(QMessageBox, 'warning', lambda *_args: QMessageBox.Ok)
    assert window.saveProjectToDirectory(str(copy)), window.projectController.lastSaveError
    saved = window.projectController.currentProjectFile
    assert saved.parent == copy and (copy/'sample.png').read_bytes() == (c['path'].parent/'sample.png').read_bytes()
    assert c['client'].getOnlineProjectSnapshot()['payload'] == runtimeDocument
    assert c['service'].deviceManager.drivers['server'] is backend
    assert c['service'].deviceManager.scope == generation and list(c['service'].jobRepository.all()) == jobs
    assert backend.snapshot()['ready']
    assert window.loadProjectDirectory(str(saved), confirmed=True)
    assert coordinator.directory == copy
    assert coordinator.session.payload()['resources']['items']['sample']['path'] == 'sample.png'


def testA2CameraProcessingThenPlcWriteInTheSameSavedProject(chain):
    from tests.plugins.test_communication_operators import _ScriptedSlmpServer, _slmpResponse
    import struct
    c = chain
    server = _ScriptedSlmpServer([_slmpResponse()])
    server.server.close()
    server.server = socket.socket()
    server.server.bind(('127.0.0.1', c['payload']['devices']['definitions']['plc']['connection']['port']))
    server.server.listen(1)
    server.server.settimeout(10)
    with server:
        c['jobs'].append(run(c['service'], c['client'], 'camera-plc'))
    assert struct.unpack_from('<H', server.requests[0], 21)[0] == 1
    assert c['service'].deviceManager.drivers['camera'].sequence == 1
    assert c['service'].deviceManager.executors['camera'].thread is not c['service'].deviceManager.executors['plc'].thread


def testA3ServerAndBothSingleTransactionsUseRealJobEntry(chain):
    c = chain
    serverPort = c['payload']['devices']['definitions']['server']['connection']['port']
    waiting = c['client'].startJob(c['service'].loadedProjectId, 'other')
    assert waiting.ok, waiting
    deadline = time.monotonic()+10
    while not c['service'].deviceManager.services['server'].busy(waiting.job_id) and time.monotonic() < deadline:
        time.sleep(.01)
    assert c['service'].deviceManager.services['server'].busy(waiting.job_id)
    with socket.create_connection(('127.0.0.1', serverPort), timeout=5) as peer:
        job = run(c['service'], c['client'], 'main', dict(text='RESULT'))
        assert peer.recv(100) == b'RESULT'
        c['jobs'].append(job)
    c['client'].stopJob(waiting.job_id)
    assert waitForTerminal(c['service'], waiting.job_id).status == 'ABORTED'
    assert c['service'].deviceManager.drivers['server'].snapshot()['ready']
    responses = []
    def exchange():
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', c['payload']['devices']['definitions']['client']['connection']['port']))
            listener.listen(1)
            ready.set()
            conn, _ = listener.accept()
            with conn:
                responses.append(conn.recv(100))
                conn.sendall(b'PONG\n')
    ready = Event()
    thread = Thread(target=exchange, daemon=True)
    thread.start()
    assert ready.wait(5)
    c['jobs'].append(run(c['service'], c['client'], 'single'))
    thread.join(5)
    assert responses == [b'PING'] and not thread.is_alive()
    received = []
    def sendOnce():
        until = time.monotonic()+15
        port = c['payload']['devices']['definitions']['receiver']['connection']['port']
        while time.monotonic() < until:
            try:
                conn = socket.create_connection(('127.0.0.1', port), timeout=.2)
                break
            except OSError:
                time.sleep(.02)
        else:
            raise AssertionError('receiver never listened')
        with conn:
            conn.settimeout(5)
            conn.sendall(b'ONE\n')
            received.append(conn.recv(100))
    thread = Thread(target=sendOnce, daemon=True)
    thread.start()
    c['jobs'].append(run(c['service'], c['client'], 'receive'))
    thread.join(5)
    assert received == [b'ACK\n'] and not thread.is_alive()


def testNormal16MiBMessageCrossesTheBoundedTcpBridge(chain):
    c = chain
    data = 'X' * 16777216
    received = bytearray()
    with socket.create_connection(('127.0.0.1', c['payload']['devices']['definitions']['server']['connection']['port']), timeout=5) as peer:
        peer.settimeout(15)
        def drain():
            while len(received) < len(data):
                part = peer.recv(min(1024*1024, len(data)-len(received)))
                if not part:
                    break
                received.extend(part)
        thread = Thread(target=drain, daemon=True)
        thread.start()
        job = run(c['service'], c['client'], 'main', dict(text=data))
        c['jobs'].append(job)
        thread.join(15)
        assert not thread.is_alive() and received == data.encode('ascii')
    records = c['client'].getDeviceDiagnostics(c['service'].deviceManager.scope, 'server')['records']
    sent = next(row for row in records if row['eventType'] == 'device.tcp.tx' and row.get('bytes') == len(data))
    assert sent['channelId'] == 'a' and sent['receipt']['jobId'] == job
    assert sent['receipt']['bytesRequested'] == sent['receipt']['bytesSent'] == len(data)
    assert sent['receipt']['outcome'] == sent['outcome'] == 'SENT_TO_TRANSPORT'
    assert sent['serviceGeneration'] == sent['receipt']['serviceGeneration']
    assert sent['connectionGeneration'] == sent['receipt']['connectionGeneration']
    assert sent['truncated'] and sent['fieldsTruncated']
    assert bytes.fromhex(sent['dataHex']) == b'X'*1024


def testBindingRejectsMissingChannelAndClientIdentityIsNotExclusive(chain):
    from emo_master.core.workflow.compiler import WorkflowCompiler
    c = chain
    payload = deepcopy(c['payload'])
    payload['workflows']['main']['nodes'][1]['deviceBindings']['tcp']['selectors']['channelId'] = 'missing'
    with pytest.raises(ValueError, match='selectors.channelId'):
        WorkflowCompiler(c['service'].pluginScanResult.activeOperators, deviceTypes=c['types']).compile(payload)
    payload = deepcopy(c['payload'])
    payload['devices']['definitions']['client2'] = deepcopy(payload['devices']['definitions']['client'])
    c['service'].deviceManager._validatedDefinitions(ProjectDocument.model_validate(payload))


def control(c, deviceId, action):
    runtimeId, states = c['client'].getDeviceStates(c['service'].loadedProjectId)
    return c['client'].controlDevice(c['service'].loadedProjectId, runtimeId, deviceId, states[deviceId], action, confirmed=True)


def publish(c, candidate, selected, request):
    snapshot = c['client'].getOnlineProjectSnapshot()
    c['client'].writeProjectConfiguration(snapshot, candidate, selected, request, replacementPolicy='stopAffected')
    result = waitWrite(c['service'], c['client'], request)
    assert result['state'] == 'COMMITTED', result
    deadline = time.monotonic()+5
    while 'PENDING' in result.get('connections', {}).values() and time.monotonic() < deadline:
        result = c['client'].getProjectWriteStatus(request)
        time.sleep(.01)
    return result


def testA4ControlsSelectiveChannelUpdateAndCommittedConnectionFailure(chain):
    from emo_master.clients.runtime.devices import DeviceManagementClient
    from emo_master.core.project.configuration import projectChanges, requiredProjectChanges
    c = chain
    shared = DeviceManagementClient(c['service'])
    runtimeId, states = shared.getDeviceStates(c['service'].loadedProjectId)
    assert states['server']['driverState']['ready']
    assert states['server']['availableControls'] == c['client'].getDeviceStates(c['service'].loadedProjectId)[1]['server']['availableControls']
    assert not {'connect', 'disconnect'} & set(states['receiver']['availableControls'])
    control(c, 'receiver', 'maintenance')
    assert 'resume' in shared.getDeviceStates(c['service'].loadedProjectId)[1]['receiver']['availableControls']
    control(c, 'receiver', 'resume')
    waiting = c['client'].startJob(c['service'].loadedProjectId, 'other')
    assert waiting.ok
    deadline = time.monotonic()+10
    while not c['service'].deviceManager.services['server'].busy(waiting.job_id) and time.monotonic() < deadline:
        time.sleep(.01)
    control(c, 'server', 'disconnect')
    assert waitForTerminal(c['service'], waiting.job_id).status == 'FAILED'
    assert not shared.getDeviceStates(c['service'].loadedProjectId)[1]['server']['driverState']['ready']
    control(c, 'server', 'connect')
    before = c['client'].getOnlineProjectSnapshot()['payload']
    candidate = deepcopy(before)
    candidate['devices']['definitions']['server']['connection']['channels'][1]['channelId'] = 'c'
    target = next(node for node in candidate['workflows']['other']['nodes'] if node['nodeId'] == 'wait-b')
    target['deviceBindings']['tcp']['selectors']['channelId'] = 'c'
    candidate['devices']['definitions']['client']['description'] = 'unrelated draft must stay local'
    selected = [item['path'] for item in projectChanges(before, candidate) if tuple(item['parts'][:2]) == ('workflows', 'other')]
    dependencies = requiredProjectChanges(before, candidate, selected, deviceTypes=c['types'],
        operatorRegistry=c['service'].pluginScanResult.activeOperators)
    assert set(dependencies) == {'/devices/definitions/server/connection/channels'}
    receipt = publish(c, candidate, selected+list(dependencies), 'a4-channels')
    assert receipt['connections']['server'] == 'LISTENING'
    online = c['client'].getOnlineProjectSnapshot()['payload']
    assert online['devices']['definitions']['client'].get('description', '') != 'unrelated draft must stay local'
    with socket.socket() as occupied:
        occupied.bind(('127.0.0.1', 0))
        occupied.listen(1)
        candidate = deepcopy(online)
        candidate['devices']['definitions']['server']['connection']['port'] = occupied.getsockname()[1]
        receipt = publish(c, candidate, ['/devices/definitions/server/connection/port'], 'a4-occupied')
        assert receipt['state'] == 'COMMITTED' and receipt['connections']['server'].startswith('FAILED:'), receipt
    control(c, 'server', 'connect')
    c['payload'] = c['client'].getOnlineProjectSnapshot()['payload']
    c['path'].write_text(json.dumps(c['payload'], ensure_ascii=False, indent=2), encoding='utf-8')
    with socket.create_connection(('127.0.0.1', candidate['devices']['definitions']['server']['connection']['port']), timeout=5) as peer:
        c['jobs'].append(run(c['service'], c['client'], 'main', dict(text='UPDATED')))
        assert peer.recv(100) == b'UPDATED'
    state = shared.getDeviceStates(c['service'].loadedProjectId)[1]['server']
    scope = {field: state[field] for field in ('runtimeInstanceId', 'projectId', 'projectLoadGeneration')}
    logs = shared.getDeviceDiagnostics(scope, 'server')
    assert any(row['eventType'] == 'device.connect.failed' for row in logs['records'])
    assert any(row['eventType'] == 'node.failed' for row in logs['records'])
    (c['root']/'device-diagnostics-before-export.json').write_text(
        json.dumps(logs, ensure_ascii=False, indent=2), encoding='utf-8')


def runImported(c, directory, payload, workflow, label):
    from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb
    path = directory/'imported.emoproj'
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    service = RuntimeService(dbPath=directory/'runtime/runtime.db', workspaceRoot=directory/'runtime/jobs')
    client = RuntimeClient(runtimeService=service)
    try:
        loaded = service.LoadProject(pb.LoadProjectRequest(project_path=str(path)), None)
        assert loaded.ok, loaded
        deviceId = next(binding['deviceId'] for node in payload['workflows'][workflow]['nodes']
                        for binding in node.get('deviceBindings', {}).values())
        deadline = time.monotonic()+5
        while time.monotonic() < deadline:
            if client.getDeviceStates(service.loadedProjectId)[1][deviceId]['driverState']['ready']:
                break
            time.sleep(.01)
        port = payload['devices']['definitions'][deviceId]['connection']['port']
        with socket.create_connection(('127.0.0.1', port), timeout=5) as peer:
            job = run(service, client, workflow, dict(text=label))
            assert peer.recv(100) == label.encode()
        c.setdefault('importJobs', {})[label] = job
        state = client.getDeviceStates(service.loadedProjectId)[1][deviceId]
        scope = {field: state[field] for field in ('runtimeInstanceId', 'projectId', 'projectLoadGeneration')}
        diagnostics = client.getDeviceDiagnostics(scope, deviceId)
        (directory/'device-diagnostics.json').write_text(json.dumps(diagnostics, ensure_ascii=False, indent=2), encoding='utf-8')
        c.setdefault('importEvidence', {})[label] = dict(jobId=job, workflowId=workflow, deviceId=deviceId,
            scope=scope, diagnostics=str(directory/'device-diagnostics.json'), state=state)
    finally:
        client.close()
        service.close()


def testUnavailableReplyPreservesUnknownReceiptAndNeverReplays():
    import base64
    import multiprocessing
    from emo_master.apps.runtime.communication.ipc import CommunicationProxy, MAX_IPC_BYTES
    from emo_master.apps.runtime.communication.pipe_io import receiveBytes
    from emo_master.core.contracts.device_driver import DeviceError
    from emo_master.core.contracts.tcp_service import TcpSessionRef, TcpSendReceipt
    parent, child = multiprocessing.Pipe()
    requests = []
    def loseReply():
        requests.append(json.loads(receiveBytes(parent, MAX_IPC_BYTES, lambda: False, time.monotonic()+5)))
        parent.close()
    thread = Thread(target=loseReply, daemon=True)
    thread.start()
    proxy = CommunicationProxy(child, Event())
    session = TcpSessionRef(deviceId='server', runtimeInstanceId='runtime', projectId='project',
        projectLoadGeneration='load', serviceId='tcp-server', serviceGeneration=1, configRevision='config',
        channelId='a', connectionGeneration=1, leaseId='lease-1234567890123456', jobId='job').toPayload()
    try:
        with pytest.raises(DeviceError) as error:
            proxy.call('send', deviceTypeId='device.tcp.server', policies=dict(sendTimeoutMs=1000),
                deviceId='server', params={}, cycleId='cycle', source={}, selectors={},
                arguments=dict(session=session, dataBase64=base64.b64encode(b'once').decode('ascii')))
        receipt = TcpSendReceipt.fromPayload(error.value.diagnostics['tcpReceipt'])
        thread.join(5)
        assert receipt.outcome == 'OUTCOME_UNKNOWN' and receipt.bytesRequested == 4
        assert len(requests) == 1 and receipt.requestId == requests[0]['requestId']
        assert receipt.deviceId == 'server' and receipt.channelId == 'a' and not receipt.deviceConfirmed
        assert proxy.closed
    finally:
        proxy.close()


def testA5AllThreePackagesImportAndRunFromNewDirectories(chain):
    from emo_master.apps.designer.state.project_edit_session import ProjectEditSession
    from emo_master.apps.designer.state.device_package import exportDevices, importDevices, readDevicePackage
    from emo_master.apps.designer.state.workflow_package import buildWorkflowPackage, writeWorkflowPackage, loadWorkflowPackage, importWorkflowPackage
    from emo_master.core.project.runtime_package import buildRuntimePackage, installRuntimePackage
    c = chain
    control(c, 'server', 'disconnect')
    session = ProjectEditSession(c['payload'])
    package = exportDevices(session, list(c['payload']['devices']['definitions']), c['path'].parent)
    devicePath = c['root']/'devices.emodev.json'
    devicePath.write_text(package.model_dump_json(indent=2), encoding='utf-8')
    destination = c['root']/'device-import'
    destination.mkdir()
    target = ProjectEditSession(WorkflowStore().toPayload())
    mapped = importDevices(target, readDevicePackage(devicePath), c['types'], destination)
    definitions = target.document().model_dump()['devices']['definitions']
    assert all(not value['enabled'] for value in definitions.values())
    assert definitions[mapped['camera']]['connection']['serial'] == ''
    imported = deepcopy(c['payload'])
    imported['devices']['definitions'] = definitions
    for key, value in definitions.items():
        value['enabled'] = True
    definitions[mapped['camera']]['connection']['serial'] = 'ACCEPTANCE-IMPORTED'
    for workflow in imported['workflows'].values():
        for node in workflow['nodes']:
            for binding in node.get('deviceBindings', {}).values():
                binding['deviceId'] = mapped[binding['deviceId']]
    imported['resources']['siteBindings'] = []
    (destination/'sample.png').write_bytes((c['path'].parent/'sample.png').read_bytes())
    runImported(c, destination, imported, 'main', 'DEVICE-PACKAGE')
    workflowPackage = buildWorkflowPackage(session.workflows, 'main', sourceRoot=c['path'].parent)
    workflowPath = writeWorkflowPackage(c['root']/'workflow', workflowPackage)
    assert workflowPackage.schemaVersion == '1.4'
    destination = c['root']/'workflow-import'
    destination.mkdir()
    target = WorkflowStore()
    result = importWorkflowPackage(target, loadWorkflowPackage(workflowPath), targetRoot=destination,
                                  deviceMapping={key: None for key in workflowPackage.devices})
    server = result.deviceIdMap['server']
    assert not target.devices['definitions'][server]['enabled']
    target.devices['definitions'][server]['enabled'] = True
    imported = target.toPayload()
    assert imported['globalVariables']['wait'] == c['payload']['globalVariables']['wait']
    assert imported['resources']['items']
    assert next(n for n in imported['workflows'][result.rootWorkflowId]['nodes'] if n.get('operatorId') == 'communication.tcp.session')['deviceBindings']['tcp']['selectors'] == {'channelId': 'a'}
    runImported(c, destination, imported, result.rootWorkflowId, 'WORKFLOW-PACKAGE')
    packagePath = buildRuntimePackage(c['path'].parent, c['root']/'runtime-package',
        registry=c['service'].pluginScanResult.activeOperators, deviceTypes=c['types'])
    destination = c['root']/'runtime-import'
    document = installRuntimePackage(packagePath, destination,
        registry=c['service'].pluginScanResult.activeOperators, deviceTypes=c['types'])
    assert document.schemaVersion == '2.7' and document.devices.definitions['server'].enabled
    runImported(c, destination, document.model_dump(mode='json'), 'main', 'RUNTIME-PACKAGE')
    import subprocess
    import os
    import emo_master
    sourceSha = os.environ.get('EMO_TEST_SOURCE_SHA') or subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()
    (c['root']/'acceptance-results.json').write_text(json.dumps(dict(sourceSha=sourceSha, engineering=str(c['path']),
        jobs=c['jobs'], importJobs=c['importJobs'], importEvidence=c['importEvidence'],
        packages=[str(devicePath), str(workflowPath), str(packagePath)], importPath=emo_master.__file__), indent=2), encoding='utf-8')


def testA6ThirdPartyServiceUsesSameFactoryJobStateAndDeviceLogs(tmp_path, retainedQtApplication, monkeypatch):
    from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb
    repo = Path(__file__).resolve().parents[2]
    roots = (str(repo/'src/emo_master/plugins/builtins'), str(repo/'examples/device_service'))
    service = RuntimeService(dbPath=tmp_path/'runtime/runtime.db', workspaceRoot=tmp_path/'runtime/jobs', pluginRootPaths=roots)
    client = RuntimeClient(runtimeService=service)
    try:
        assert 'example.ticker.sample' in service.pluginScanResult.activeOperators, service.pluginScanResult.rejectedOperators
        payload = WorkflowStore().toPayload()
        payload['devices']['definitions']['ticker'] = definition(service.pluginScanResult.deviceTypes['device.example.ticker'])
        payload['workflows']['main'] = graph([dict(nodeId='sample', operatorId='example.ticker.sample', deviceBindings={'service': dict(deviceId='ticker')})],
            [edge('sample', 'ticks', 'output', 'ticks')], outputs=dict(ticks='integer'))
        payload['workflows']['reject'] = deepcopy(payload['workflows']['main'])
        payload['workflows']['reject']['nodes'][1]['params'] = dict(reject=True)
        payload['workflowOrder'].append('reject')
        path = tmp_path/'service.emoproj'
        path.write_text(json.dumps(payload), encoding='utf-8')
        assert service.LoadProject(pb.LoadProjectRequest(project_path=str(path)), None).ok
        job = run(service, client, 'main')
        rejected = client.startJob(service.loadedProjectId, 'reject')
        assert waitForTerminal(service, rejected.job_id).error_code == 'E_SAMPLE_REJECTED'
        runtimeId, states = client.getDeviceStates(service.loadedProjectId)
        state = states['ticker']
        assert state['driverState']['ready'] and state['driverState']['ticks'] > 0
        assert state['availableManagementActions'] == ['sample']
        sampled = client.executeDeviceAction(service.loadedProjectId, runtimeId, 'ticker', state, 'sample', {})
        assert sampled['ticks'] > 0
        assert not service.deviceManager.maintenanceOwners and not service.projectConfiguration.cycles
        with pytest.raises(RuntimeError, match='示例采样被拒绝'):
            client.executeDeviceAction(service.loadedProjectId, runtimeId, 'ticker', state, 'sample', {'reject': True})
        with pytest.raises(RuntimeError, match='not declared'):
            client.executeDeviceAction(service.loadedProjectId, runtimeId, 'ticker', state, 'undeclared', {})
        scope = {key: state[key] for key in ('runtimeInstanceId', 'projectId', 'projectLoadGeneration')}
        records = client.getDeviceDiagnostics(scope, 'ticker')['records']
        assert {'device.ticker.started', 'node.log', 'node.failed'} <= {row['eventType'] for row in records}
        assert any(row.get('slot') == 'service' for row in records if row['eventType'] == 'node.log')
        projected = next(row for row in records if row['eventType'] == 'node.log' and row['jobId'] == job)
        original = next(event for event in service.eventStore.read(job) if event.eventType == 'node.log')
        assert projected['deviceGeneration'] == state['generation']
        assert projected['timestampMs'] == original.timestampMs and projected['level'] == original.level
        assert projected['workflowRunId'] == original.workflowRunId and projected['nodeRunId'] == original.nodeRunId
        assert projected['phase'] == 'execute' and projected['cycleId'] and projected['planRevision'] >= 1
        management = [row for row in records if row.get('source', {}).get('kind') == 'management']
        assert {row['eventType'] for row in management} == {'device.action.completed', 'device.action.failed'}
        assert all(not row.get('jobId') and not row.get('nodeId') and row['ownerId'].startswith('device-page-') for row in management)
        from emo_master.clients.runtime.devices import DeviceManagementClient
        from emo_master.ui.device_management import DeviceManagementDialog
        from tests.designer.test_device_workspace import waitUntil
        view = DeviceManagementDialog(DeviceManagementClient(service), lambda: service.loadedProjectId)
        backend = service.deviceManager.drivers['ticker']
        generation = states['ticker']['generation']
        try:
            view.show()
            waitUntil(lambda: 'ticker' in view.states)
            view.selector.setCurrentIndex(view.selector.findData('ticker'))
            view.close()
            assert not view.timer.isActive() and backend.snapshot()['ready']
            view.show()
            waitUntil(lambda: view.states.get('ticker', {}).get('generation') == generation)
            assert service.deviceManager.drivers['ticker'] is backend
            assert sum(row['eventType'] == 'device.ticker.started' for row in client.getDeviceDiagnostics(scope, 'ticker')['records']) == 1
            from emo_master.ui.device_actions import DeviceActionDialog
            def executeOnce(dialog):
                dialog.accept()
                return dialog.result()
            monkeypatch.setattr(DeviceActionDialog, 'exec_', executeOnce)
            waitUntil(lambda: view.pending is None)
            view.business.click()
            waitUntil(lambda: '业务动作完成' in view.status.text())
            assert 'ticks' in view.status.text()
            assert not service.deviceManager.maintenanceOwners and not service.projectConfiguration.cycles
        finally:
            view.dispose()
        run(service, client, 'main')
        (tmp_path/'third-party-diagnostics.json').write_text(json.dumps(client.getDeviceDiagnostics(scope, 'ticker'),
            ensure_ascii=False, indent=2), encoding='utf-8')
        print('THIRD_PARTY_ACCEPTANCE_ROOT=' + str(tmp_path), flush=True)
    finally:
        client.close()
        service.close()
