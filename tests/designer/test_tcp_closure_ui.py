"""Existing device controls retain opening intent and show authoritative state."""
import json
from threading import Event
from types import SimpleNamespace

from PySide2.QtCore import Qt

from emo_master.apps.designer.state.workflow_store import WorkflowStore
from emo_master.core.project.snapshots import revisionOf
from tests.designer.test_device_workspace import waitUntil
from tests.runtime.test_tcp_device_closure import runtime, thirdParty  # noqa: F401
from tests.runtime.test_tcp_device_integration import definition


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
