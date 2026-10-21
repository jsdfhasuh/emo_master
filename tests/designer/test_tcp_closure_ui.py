"""Existing device controls retain opening intent and show authoritative state."""
import base64
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
import socket
from threading import Event
from time import monotonic, sleep
from types import SimpleNamespace

from PySide2.QtCore import Qt
from PySide2.QtWidgets import QMessageBox
import pytest

from emo_master.apps.designer.state.device_catalog import DeviceCatalogEditor
from emo_master.apps.designer.state.workflow_store import WorkflowStore
from emo_master.apps.designer.ui.project_configuration import ProjectConfigurationDialog
from emo_master.apps.runtime.devices.manager import DeviceManager
from emo_master.apps.runtime.events.jsonl_writer import RuntimeJsonlLogWriter
from emo_master.core.project.configuration import requiredProjectChanges, selectedCandidate
from emo_master.core.project.models import ProjectDocument
from emo_master.core.project.snapshots import revisionOf
from emo_master.core.workflow.compiler import WorkflowCompiler
from emo_master.core.workflow.errors import WorkflowCompileError
from emo_master.ui.communication_panel import DeviceDiagnosticsView
from tests.designer.runtime_feedback_fixture import messages  # noqa: F401
from tests.designer.test_device_workspace import waitUntil
from tests.runtime.test_tcp_device_closure import runtime, thirdParty  # noqa: F401
from tests.runtime.test_tcp_device_integration import definition, freePort, graph, node, waitWrite


@pytest.mark.parametrize('kind', ['client_once', 'receive_once'])
@pytest.mark.parametrize('length', [512, 1025])
def testTcpOnceResultLogsKeepBoundedPreview(runtime, retainedQtApplication, tmp_path, monkeypatch, kind, length):  # noqa: F811
    service, _ = runtime
    manager = DeviceManager(service.pluginScanResult.deviceTypes)
    writer = RuntimeJsonlLogWriter(tmp_path/'once-byte-logs')
    manager.diagnostics.writer = writer
    queued = []
    enqueue = writer.enqueue
    def capture(event):
        queued.append(event)
        return enqueue(event)
    monkeypatch.setattr(writer, 'enqueue', capture)
    data = (b'ordinary single transaction text 0123456789 ' * 30)[:length]
    source = dict(workflowId='main', nodeId='once-node', slot='tcp')
    value = definition(service.pluginScanResult.deviceTypes['device.tcp.'+kind], port=freePort())
    value['defaults'].update(expectedBytes=length)
    value['defaults']['responseFraming' if kind == 'client_once' else 'framing'] = 'fixedLength'
    payload = WorkflowStore().toPayload()
    payload['devices']['definitions'] = dict(once=value)
    view = DeviceDiagnosticsView()
    listener = socket.socket()
    try:
        manager.load(ProjectDocument.model_validate(payload))
        manager.authorize('once-job', ['once'])
        if kind == 'client_once':
            listener.bind(('127.0.0.1', value['connection']['port']))
            listener.listen(1)
            listener.settimeout(5)

        def peer():
            if kind == 'client_once':
                connection = listener.accept()[0]
            else:
                deadline = monotonic()+5
                while True:
                    try:
                        connection = socket.create_connection(('127.0.0.1', value['connection']['port']), timeout=1)
                        break
                    except ConnectionRefusedError:
                        if monotonic() >= deadline:
                            raise
                        sleep(.01)
            with connection:
                connection.settimeout(5)
                if kind == 'client_once':
                    received = b''
                    while len(received) < 4:
                        block = connection.recv(4-len(received))
                        assert block
                        received += block
                    assert received == b'PING'
                connection.sendall(data)

        with ThreadPoolExecutor(max_workers=1) as peers:
            remote = peers.submit(peer)
            result = manager.execute('once-job', 'once', 'exchange' if kind == 'client_once' else 'receive',
                manager.definitions['once'].defaults, cycleId='once-cycle', source=source, requestId='once-request',
                arguments=dict(dataBase64=base64.b64encode(b'PING').decode('ascii')) if kind == 'client_once' else {})
            remote.result(timeout=5)
        assert base64.b64decode(result['dataBase64']) == data
        manager.waitOwner('once-job')
        state = manager.drivers['once'].snapshot()
        assert state['state'] == 'TRANSACTION_READY' and not state['ownsResources']
        assert not manager.drivers['once'].sockets and not manager.busy()
        waitUntil(lambda: bool(manager.diagnostics.query(manager.scope, 'once')['records']))
        page = manager.diagnostics.query(manager.scope, 'once')
        row, = page['records']
        assert row['eventType'] == 'device.action.completed'
        assert row['ownerId'] == 'once-job' and row['cycleId'] == 'once-cycle'
        assert row['source'] == source and row['requestId'] == 'once-request'
        assert row['deviceGeneration'] == manager.executors['once'].generation
        assert all(row[key] == val for key, val in manager.scope.items())
        preview = row['result']
        expected = data[:1024]
        assert preview['bytes'] == length
        assert bytes.fromhex(preview['dataHex']) == expected
        assert preview['dataTruncated'] is (length > 1024)
        assert len(bytes.fromhex(preview['dataHex'])) <= 1024
        assert row['fieldsTruncated'] is True
        assert len(row['preview'].encode('utf-8')) <= 1000
        assert 'dataBase64' not in json.dumps(row)
        if kind == 'client_once':
            assert result['sentBytes'] == preview['sentBytes'] == state['lastResult']['bytesSent'] == 4
        view.setPage(page)
        assert view.records.topLevelItemCount() == 1
        view.records.setCurrentItem(view.records.topLevelItem(0))
        for index in (0, 1, 0, 1):
            view.format.setCurrentIndex(index)
            rendered = view.details.toPlainText()
            assert rendered.splitlines()[0] == (repr(expected.decode('utf-8', errors='replace')) if index else expected.hex())
            assert ('[原始数据已截断]' in rendered) is (length > 1024)
            assert '[部分诊断字段已截断]' in rendered
        manager.diagnostics.query(manager.scope, 'once')
        (tmp_path/'once-preview.json').write_text(json.dumps(dict(kind=kind, responseBytes=length,
            row=row, driverState=state, hex=expected.hex(), utf8=rendered), ensure_ascii=False, indent=2), encoding='utf-8')
    finally:
        listener.close()
        view.close()
        manager.close()
        assert writer.close() is None
    persisted = [json.loads(line) for path in (tmp_path/'once-byte-logs').glob('runtime-*.jsonl')
                 for line in path.read_text(encoding='utf-8').splitlines()]
    event, = persisted
    queuedEvent, = queued
    assert json.loads(queuedEvent.payloadJson) == row
    # The operational writer summarizes *.completed payloads; viewing must not
    # enqueue or persist the original device event a second time.
    assert event['eventType'] == queuedEvent.eventType == 'device.action.completed'
    assert event['timestampMs'] == row['timestampMs']


@pytest.mark.parametrize('length', [32, 512, 1025])
def testTcpByteLogsKeepChannelAndExactBoundedPreview(runtime, retainedQtApplication, tmp_path, length):  # noqa: F811
    service, _ = runtime
    manager = DeviceManager(service.pluginScanResult.deviceTypes)
    writer = RuntimeJsonlLogWriter(tmp_path/'byte-logs')
    manager.diagnostics.writer = writer
    payload = WorkflowStore().toPayload()
    channels = [dict(channelId='a', name='Channel A', allowedIps=['127.0.0.1'])]
    payload['devices']['definitions'] = dict(server=definition(
        service.pluginScanResult.deviceTypes['device.tcp.server'], channels=channels))
    view = DeviceDiagnosticsView()
    try:
        manager.load(ProjectDocument.model_validate(payload))
        backend = manager.drivers['server'].backend
        data = (b'ordinary TCP text 0123456789 ' * 40)[:length]
        assert len(data) == length
        backend._record('rx', 'bounded byte preview', 'a', data, connectionGeneration=7)
        page = manager.diagnostics.query(manager.scope, 'server')
        row, = page['records']
        assert row['channelId'] == 'a' and row['bytes'] == length
        assert row['serviceGeneration'] == backend.serviceGeneration
        assert row['connectionGeneration'] == 7
        assert row['deviceGeneration'] == manager.executors['server'].generation
        assert all(row[key] == value for key, value in manager.scope.items())
        assert row['timestampMs'] == backend._records[-1][0]['timestampMs']
        assert row['truncated'] is (length > 1024)
        assert bool(row.get('fieldsTruncated')) is (length >= 512)
        assert bytes.fromhex(row['dataHex']) == data[:1024]
        assert len(bytes.fromhex(row['dataHex'])) <= 1024
        assert len(row.get('preview', '').encode('utf-8')) <= 1000
        view.setPage(page, channels)
        view.filter.setCurrentIndex(view.filter.findData('a'))
        assert view.records.topLevelItemCount() == 1
        view.records.setCurrentItem(view.records.topLevelItem(0))
        expected = data[:1024]
        assert view.details.toPlainText().splitlines()[0] == expected.hex(' ').upper()
        view.format.setCurrentIndex(1)
        rendered = view.details.toPlainText()
        assert rendered.splitlines()[0] == repr(expected.decode('utf-8'))
        assert ('[原始数据已截断]' in rendered) is (length > 1024)
        assert ('[部分诊断字段已截断]' in rendered) is (length >= 512)
        # Re-observing and formatting the event must never persist it again.
        manager.diagnostics.query(manager.scope, 'server')
    finally:
        view.close()
        manager.close()
        assert writer.close() is None
    persisted = [json.loads(line) for path in (tmp_path/'byte-logs').glob('runtime-*.jsonl')
                 for line in path.read_text(encoding='utf-8').splitlines()]
    event, = [row for row in persisted if row['eventType'] == 'device.tcp.rx']
    assert event['payload']['channelId'] == 'a' and event['payload']['bytes'] == length
    assert bytes.fromhex(event['payload']['dataHex']) == data[:1024]


def testTcpShortcutAppliesAfterMetadataAndDoesNotChooseFirstServer(runtime, ownedDesignerWindow, tmp_path):  # noqa: F811
    service, client = runtime
    entered, release = Event(), Event()
    def metadata():
        entered.set()
        assert release.wait(10)
        return client.listDeviceTypes()
    adapter = SimpleNamespace(listOperators=lambda: [], listDeviceTypes=metadata)
    window = ownedDesignerWindow(adapter)
    payload = WorkflowStore().toPayload()
    types = service.pluginScanResult.deviceTypes
    payload['devices']['definitions'] = dict(server=definition(types['device.tcp.server']))
    source = tmp_path/'draft.emoproj'
    source.write_text(json.dumps(payload), encoding='utf-8')
    assert window.loadProjectDirectory(str(source), confirmed=True)
    window.show()
    try:
        window.openCommunicationPanel()
        workspace = window.pageCoordinator.devices
        assert entered.wait(5) and workspace.pending is not None
        release.set()
        waitUntil(lambda: workspace.pending is None)
        assert workspace.tree.currentItem().text(0) == types['device.tcp.server'].category
        assert workspace.selectedId is None and workspace.table.currentRow() == -1
        workspace.close()
        window.openCommunicationPanel()
        assert workspace.selectedId is None
    finally:
        release.set()


def testDraftAndConnectedServerSummaryUseSameSnapshot(runtime, ownedDesignerWindow, tmp_path):  # noqa: F811
    service, client = runtime
    window = ownedDesignerWindow(client)
    payload = WorkflowStore().toPayload()
    types = service.pluginScanResult.deviceTypes
    payload['devices']['definitions'] = {key: definition(types['device.tcp.server'], channels=[
        dict(channelId='a', name='A', allowedIps=['127.0.0.1'])]) for key in ('server', 'draft')}
    source = tmp_path/'draft.emoproj'
    source.write_text(json.dumps(payload), encoding='utf-8')
    assert window.loadProjectDirectory(str(source), confirmed=True)
    window.pageCoordinator.showDevices()
    workspace = window.pageCoordinator.devices
    waitUntil(lambda: workspace.pending is None)
    workspace.stateScope = workspace._scope()
    workspace.stateReadAt = 1.0
    workspace.stateRuntimeId = 'runtime'
    workspace.stateError = workspace.stateNotice = ''
    checked = types['device.tcp.server'].validateDefinition(
        window.pageCoordinator.session.document().devices.definitions['server'])
    state = dict(state='IDLE', activeOwner=None, activeOwners=[], queued=0, generation='backend',
        definitionRevision=revisionOf(checked.model_dump()), driverState=dict(ready=True, channels=[dict(connected=True)]))
    workspace.states = dict(server=state)
    workspace._showStates()
    rows = {workspace.table.item(row, 0).data(Qt.UserRole): row for row in range(workspace.table.rowCount())}
    assert '尚未写入 Runtime' in workspace.table.item(rows['draft'], 3).text()
    assert '已连接 1 个客户端' in workspace.table.item(rows['server'], 3).text()
    state['driverState']['channels'] = []
    workspace._showStates()
    assert '等待客户端' in workspace.table.item(rows['server'], 3).text()
    workspace.states = {}
    workspace.stateError = 'read failed'
    workspace.stateNotice = '设备信息读取失败：read failed'
    workspace._showStates()
    assert '读取失败' in workspace.table.item(rows['draft'], 3).text()


@pytest.mark.parametrize('owners', [[], ['job<a>'], ['job<a>', 'job-b']])
def testDeviceDetailShowsAllActiveOwners(runtime, ownedDesignerWindow, tmp_path, owners):  # noqa: F811
    service, client = runtime
    window = ownedDesignerWindow(client)
    payload = WorkflowStore().toPayload()
    payload['devices']['definitions'] = dict(server=definition(service.pluginScanResult.deviceTypes['device.tcp.server']))
    source = tmp_path/'draft.emoproj'
    source.write_text(json.dumps(payload), encoding='utf-8')
    assert window.loadProjectDirectory(str(source), confirmed=True)
    window.pageCoordinator.showDevices()
    workspace = window.pageCoordinator.devices
    waitUntil(lambda: workspace.pending is None)
    workspace.selectedId = 'server'
    workspace.stateScope = workspace._scope()
    workspace.states = dict(server=dict(state='BUSY' if owners else 'IDLE', activeOwners=owners,
        activeOwner=owners[0] if len(owners) == 1 else None, queued=0, generation='backend',
        driverState=dict(ready=True, channels=[])))
    workspace.showDiagnostics()
    rendered = workspace.onlineState.toPlainText()
    assert '当前任务\n' + (', '.join(owners) or '-') in rendered
    assert ('执行中' if owners else '空闲') in rendered
    if owners:
        assert '当前任务\n-' not in rendered
    # The same path stays bounded even when every service operation has a distinct owner.
    workspace.states['server']['activeOwners'] = ['owner-' + str(i) for i in range(64)]
    workspace.showDiagnostics()
    assert len(workspace.onlineState.toPlainText()) < 1000


def testSelectiveChannelRemovalLocatesOldBindingAndUiCanPublishAfterSelection(
        runtime, ownedDesignerWindow, tmp_path, monkeypatch, messages):  # noqa: F811
    service, client = runtime
    types = service.pluginScanResult.deviceTypes
    operators = service.pluginScanResult.activeOperators
    payload = WorkflowStore().toPayload()
    payload['devices']['definitions'] = dict(server=definition(types['device.tcp.server'], channels=[
        dict(channelId=key, name=key.upper(), allowedIps=[f'127.0.0.{i}']) for i, key in enumerate(('a', 'c'), 1)]))
    payload['workflows']['main'] = graph([
        node('acquire', 'communication.tcp.session', 'server', selectors=dict(channelId='a'))])
    source = tmp_path/'project.emoproj'
    source.write_text(json.dumps(payload), encoding='utf-8')
    window = ownedDesignerWindow(client)
    assert window.loadProjectDirectory(str(source), confirmed=True)
    client.initializeProjectConfiguration(window.pageCoordinator.session.payload(), tmp_path, 'r02-initialize',
                                          runtimeInstanceId=service.runtimeInstanceId)
    initialized = waitWrite(service, client, 'r02-initialize')
    assert initialized['state'] == 'COMMITTED', initialized
    window.pageCoordinator.sync()
    session = window.pageCoordinator.session
    requirements = client.listDeviceTypes()[1]
    editor = DeviceCatalogEditor(session, types, requirements)
    changed = next(n.model_dump() for n in session.document().workflows['main'].nodes if n.nodeId == 'acquire')
    changed['deviceBindings']['tcp']['selectors']['channelId'] = 'c'
    editor.commitNode('main', changed)
    device = deepcopy(session.payload()['devices']['definitions']['server'])
    device['connection']['channels'] = [c for c in device['connection']['channels'] if c['channelId'] == 'c']
    editor.put(device, deviceId='server')
    # Refresh the existing view before its normal capture back into the session.
    window.workflowController._renderActive()
    local = session.payload()
    WorkflowCompiler(operators, deviceTypes=types).compile(local)
    online = client.getOnlineProjectSnapshot('')
    channelPath = '/devices/definitions/server/connection/channels'
    bindingPath = '/workflows/main/nodes/acquire/deviceBindings/tcp/selectors/channelId'
    with pytest.raises(WorkflowCompileError) as caught:
        requiredProjectChanges(online['payload'], local, [channelPath], deviceTypes=types, operatorRegistry=requirements)
    dialog = ProjectConfigurationDialog(window.pageCoordinator, window)
    dialog.show()
    waitUntil(lambda: dialog.pending is None)
    calls = []
    write = client.writeProjectConfiguration
    def record(*args, **kwargs):
        calls.append((args[2], kwargs.get('validate', False)))
        return write(*args, **kwargs)
    monkeypatch.setattr(client, 'writeProjectConfiguration', record)
    try:
        for row in range(dialog.table.rowCount()):
            item = dialog.table.item(row, 0)
            item.setCheckState(Qt.Checked if item.data(Qt.UserRole) == channelPath else Qt.Unchecked)
        before = session.payload()
        dialog.validateButton.click()
        assert not calls and len(messages) == 1
        assert client.getOnlineProjectSnapshot('') == online
        assert session.payload() == before and dialog.validateButton.isEnabled()
        detail = messages[0]['detail']
        assert 'main' in detail and 'acquire' in detail
        assert 'deviceBindings.tcp.selectors.channelId' in detail
        issue = caught.value.issues[0]
        assert issue.projectId == local['project']['projectId']
        assert issue.workflowId == 'main' and issue.nodeId == 'acquire'
        assert issue.fieldPath == 'deviceBindings.tcp.selectors.channelId'
        assert issue.code == 'E_DEVICE_BINDING' and 'server' in issue.message and "'a'" in issue.message
        checked = [dialog.table.item(r, 0).data(Qt.UserRole) for r in range(dialog.table.rowCount())
                   if dialog.table.item(r, 0).checkState() == Qt.Checked]
        assert checked == [channelPath]
        assert not requiredProjectChanges(online['payload'], local, [channelPath, bindingPath],
            deviceTypes=types, operatorRegistry=requirements)
        WorkflowCompiler(operators, deviceTypes=types).compile(selectedCandidate(online['payload'], local, [channelPath, bindingPath]))
        for row in range(dialog.table.rowCount()):
            item = dialog.table.item(row, 0)
            if item.data(Qt.UserRole) == bindingPath:
                item.setCheckState(Qt.Checked)
        dialog.validateButton.click()
        waitUntil(lambda: dialog.pending is None)
        assert calls[-1][1] and set(calls[-1][0]) == {channelPath, bindingPath}
        monkeypatch.setattr(QMessageBox, 'question', lambda *_args: QMessageBox.Yes)
        dialog.writeButton.click()
        waitUntil(lambda: dialog.pending is None and not dialog.writePending)
        assert not calls[-1][1] and set(calls[-1][0]) == {channelPath, bindingPath}
        committed = client.getOnlineProjectSnapshot('')
        assert committed['documentRevision'] == online['documentRevision'] + 1
        assert committed['payload']['devices']['definitions']['server']['connection']['channels'] == device['connection']['channels']
        assert next(n for n in committed['payload']['workflows']['main']['nodes'] if n['nodeId'] == 'acquire')['deviceBindings']['tcp']['selectors'] == dict(channelId='c')
    finally:
        dialog.reject()
