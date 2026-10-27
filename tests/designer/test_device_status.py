from concurrent.futures import Future
from copy import deepcopy
from threading import Event
from time import monotonic
from types import SimpleNamespace

import pytest
from PySide2.QtCore import QEvent, QPoint
from PySide2.QtGui import QHelpEvent
from PySide2.QtWidgets import QPushButton, QToolTip

from emo_master.apps.designer.ui.device_status import DeviceStatusObserver, duration, statusPresentation
from emo_master.apps.designer.ui.runtime_panel import RuntimePanelState
from tests.designer.test_canvas_navigation_bounds import readableFont  # noqa: F401
from tests.designer.test_project_communication_ui import spin
from tests.runtime.test_device_overview import overviewRuntime  # noqa: F401


def snapshot():
    return dict(runtimeInstanceId='runtime-test', projectId='project', available=True, uptimeMs=3600000,
        states=dict(camera=dict(name='相机 <A>', deviceTypeId='device.simulated.camera', lifecycleConnected=True),
            plc=dict(name='PLC', deviceTypeId='device.plc.mitsubishi_slmp', lifecycleConnected=False),
            server=dict(name='工位服务', deviceTypeId='device.tcp.server', lifecycleConnected=True,
                driverState=dict(state='LISTENING', channels=[dict(channelId='a', name='工位 A', connected=True,
                    peerAddress=['127.0.0.1', 12345], connectedAtMs=1792713600000)])),
            once=dict(name='单次事务', deviceTypeId='device.tcp.client_once', connectionLifetime='per_operation', state='IDLE')))


def testSummaryUsesAllOnlineDevicesAndEscapedPeerDetails():
    value = snapshot()
    text, tip = statusPresentation(value, {'unpublished': {}}, elapsedMs=2000)
    assert text == '设备 2/4 · 监听 1 · 接入 1 · 本次运行 未开始'
    assert '01:00:02' not in tip and 'Runtime 已运行' not in tip
    for detail in ('相机 &lt;A&gt;', 'PLC', '工位服务 / 工位 A', '127.0.0.1:12345', '接入于', '按次连接', '本地另有 1 台'):
        assert detail in tip
    assert '<A>' not in tip
    value['states']['server']['driverState']['channels'][0]['connected'] = False
    text, tip = statusPresentation(value, {})
    assert '设备 1/4 · 监听 1 · 接入 0' in text and '监听中，等待客户端' in tip
    assert '12345' not in tip


@pytest.mark.parametrize('state,label', [('STOPPED', '未启动'), ('STARTING', '启动中'),
    ('LISTENING', '监听中，等待客户端'), ('STOPPING', '停止中'), ('FAILED', '服务失败'),
    ('UNKNOWN', '状态未知')])
def testTcpServiceStateAndCurrentFailureAreVisible(state, label):
    server = dict(name='Server', deviceTypeId='device.tcp.server',
                  driverState=dict(state=state, channels=[], error='listen <failed>' if state == 'FAILED' else ''))
    text, tip = statusPresentation(dict(snapshot(), states={'server': server}), {})
    assert label in tip
    assert ('异常 1' in text) == (state == 'FAILED')
    if state == 'FAILED':
        assert 'listen &lt;failed&gt;' in tip and '<failed>' not in tip


def testCurrentFaultPrecedesTruncationAndConnectionClassesRemainDistinct():
    states = {f'camera-{i}': dict(name=f'Camera {i}', lifecycleConnected=True) for i in range(12)}
    states.update(failed=dict(name='Fault camera', state='QUARANTINED', lastError='capture failed'),
                  once=dict(name='Once', connectionLifetime='per_operation'),
                  disabled=dict(name='Disabled', enabled=False))
    text, tip = statusPresentation(dict(snapshot(), states=states), {}, elapsedMs=2300)
    assert '异常 1' in text and '本次运行 未开始' in text
    assert 'Fault camera' in tip and 'capture failed' in tip
    assert tip.index('Fault camera') < tip.index('Camera 0')
    for label in ('已连接 12', '未连接 1', '按次连接 1', '已停用 1', '状态更新于 2 秒前', '另有 3 台设备'):
        assert label in tip


def testHistoricalErrorDoesNotKeepRecoveredDeviceInAlarm():
    camera = dict(name='Camera', state='IDLE', lifecycleConnected=True,
                  lastError='old failure', recovery=dict(state='CONNECTED', lastError=''))
    text, tip = statusPresentation(dict(snapshot(), states={'camera': camera}), {})
    assert '异常' not in text and '上次错误：old failure' in tip
    camera.update(lifecycleConnected=False, recovery=dict(state='FAILED', lastError='connect failed'))
    text, tip = statusPresentation(dict(snapshot(), states={'camera': camera}), {})
    assert '异常 1' in text and 'connect failed' in tip


@pytest.mark.parametrize('state', [dict(enabled=False), dict(connectionLifetime='per_operation'),
    dict(admissionState='PAUSED', recovery=dict(state='FAILED', lastError='old failure')),
    dict(deviceTypeId='device.tcp.server', driverState=dict(state='STOPPED'),
         recovery=dict(state='FAILED', lastError='old failure'))])
def testStoppedDisabledAndOnDemandDevicesAreNotFaults(state):
    text, _ = statusPresentation(dict(snapshot(), states={'device': dict(name='Device', **state)}), {})
    assert '异常' not in text


@pytest.mark.parametrize('milliseconds,expected', [(0, '00:00:00'), (3661000, '01:01:01'),
    (90061000, '1天 01:01:01')])
def testDuration(milliseconds, expected):
    assert duration(milliseconds) == expected


def testUnknownAndUnloadedNeverReportZeroConnections():
    text, tip = statusPresentation(None, {'local': {}}, error='connection lost')
    assert text == '设备未知 · 接入未知 · 本次运行 未开始' and 'connection lost' in tip
    value = dict(snapshot(), available=False, message='当前工程未下发')
    text, tip = statusPresentation(value, {})
    assert '设备未知' in text and '本次运行 未开始' in text and '当前工程未下发' in tip
    value = dict(snapshot(), uptimeMs=None, states={})
    text, tip = statusPresentation(value, {})
    assert '设备 0/0' in text and '本次运行 未开始' in text and '无在线设备' in tip


@pytest.mark.parametrize('status,label', [('READY', '未开始'), ('STARTING', '启动中'),
    ('ACCEPTED', '等待启动'), ('REJECTED', '启动被拒绝'), ('FAILED', '失败')])
def testNotStartedRunNeverBorrowsRuntimeUptime(status, label):
    state = RuntimePanelState()
    state.updateJob(status)
    text, tip = statusPresentation(snapshot(), {}, runState=state, runName='Flow <A>')
    assert '本次运行 ' + label in text
    assert '本次运行时间：未开始' in tip
    assert '当前流程：Flow &lt;A&gt;' in tip
    assert '01:00:00' not in tip


def observerFor(client):
    workflows = SimpleNamespace(project={'projectId': 'project'}, devices={'definitions': {}})
    coordinator = SimpleNamespace(window=SimpleNamespace(runtimeClient=client), session=SimpleNamespace(workflows=workflows))
    button = QPushButton()
    return DeviceStatusObserver(coordinator, button), coordinator, button


def hoverStatus(app, button):
    app.sendEvent(button, QHelpEvent(QEvent.ToolTip, QPoint(1, 1), button.mapToGlobal(QPoint(1, 1))))
    app.processEvents()
    assert QToolTip.isVisible() and QToolTip.text() == button.toolTip()


def testTooltipCurrentJobClockTicksThenFreezesAndFollowsSelection(retainedQtApplication, monkeypatch):
    now = [10.0]
    monkeypatch.setattr('emo_master.apps.designer.ui.runtime_panel.monotonic', lambda: now[0])
    observer, coordinator, button = observerFor(object())
    observer.timer.stop()
    state = RuntimePanelState()
    coordinator.window.runtimePanelState = state
    state.setActiveJob('job-current')
    state.updateJob('RUNNING')
    try:
        observer.snapshot, observer.observedAt = snapshot(), monotonic()
        observer.render()
        button.show()
        retainedQtApplication.processEvents()
        hoverStatus(retainedQtApplication, button)
        now[0] += 2
        observer.render()
        assert '本次运行 00:00:02' in button.text()
        assert '本次运行时间：00:00:02' in QToolTip.text()
        assert 'job-current' in QToolTip.text() and 'Runtime 已运行' not in QToolTip.text()
        state.updateJob('FAILED')
        now[0] += 100
        observer.render()
        assert '失败耗时 00:00:02' in button.text()
        assert '本次运行时间：00:00:02' in QToolTip.text()
        assert '运行状态：失败' in QToolTip.text()
        observer.snapshot = None
        observer.error = 'connection lost'
        observer.render()
        assert '失败耗时 00:00:02' in button.text() and '设备未知' in button.text()
        other = RuntimePanelState()
        other.setActiveJob('job-other')
        other.updateJob('RUNNING')
        coordinator.window.runtimePanelState = other
        now[0] += 5
        observer.render()
        assert '本次运行 00:00:05' in button.text() and 'job-current' not in QToolTip.text()
        coordinator.window.runtimePanelState = state
        observer.render()
        assert '失败耗时 00:00:02' in button.text()
        state.setActiveJob('job-new')
        state.updateJob('RUNNING')
        observer.render()
        assert '本次运行 00:00:00' in button.text()
        state.updateJob('START_UNCERTAIN')
        observer.render()
        assert '本次运行 待确认' in button.text()
        state.setActiveJob(None)
        observer.render()
        assert '本次运行 未开始' in button.text()
    finally:
        observer.close()
        button.close()
        button.deleteLater()
        QToolTip.hideText()


def testVisibleTooltipTracksTimePeerDisconnectFailureAndScope(retainedQtApplication):
    app = retainedQtApplication
    observer, coordinator, button = observerFor(object())
    observer.timer.stop()
    observer.refresh()
    try:
        observer.snapshot, observer.observedAt = snapshot(), monotonic()
        observer.render()
        button.show()
        app.processEvents()
        hoverStatus(app, button)
        observer.observedAt -= 2
        observer.render()
        app.processEvents()
        assert '本次运行时间：未开始' in QToolTip.text() and '状态更新于 2 秒前' in QToolTip.text()
        observer.snapshot['states']['server']['driverState']['channels'][0]['connected'] = False
        observer.render()
        assert '127.0.0.1:12345' not in QToolTip.text()
        observer.snapshot, observer.error = None, 'connection lost'
        observer.render()
        assert 'connection lost' in QToolTip.text() and '设备未知' in button.text()
        observer.snapshot, observer.observedAt = snapshot(), monotonic()
        observer.render()
        assert '127.0.0.1:12345' in QToolTip.text()
        coordinator.session.workflows.project['projectId'] = 'new-project'
        observer.refresh()
        assert 'runtime-test' not in QToolTip.text() and '127.0.0.1:12345' not in QToolTip.text()
        observer.close()
        spin(app, lambda: not QToolTip.isVisible())
    finally:
        observer.close()
        button.close()
        button.deleteLater()
        QToolTip.hideText()


def testTooltipRefreshDoesNotReopenDismissedTipOrReplaceOtherWidgetTip(retainedQtApplication):
    app = retainedQtApplication
    observer, _, button = observerFor(object())
    observer.timer.stop()
    other = QPushButton()
    try:
        observer.snapshot, observer.observedAt = snapshot(), monotonic()
        observer.render()
        button.show()
        app.processEvents()
        hoverStatus(app, button)
        app.sendEvent(button, QEvent(QEvent.Leave))
        spin(app, lambda: not QToolTip.isVisible())
        observer.observedAt -= 1
        observer.render()
        assert not QToolTip.isVisible()
        hoverStatus(app, button)
        QToolTip.showText(other.mapToGlobal(QPoint()), 'Other control', other)
        observer.observedAt -= 1
        observer.render()
        assert QToolTip.text() == 'Other control'
        observer.close()
        app.processEvents()
        assert QToolTip.isVisible() and QToolTip.text() == 'Other control'
    finally:
        observer.close()
        button.close()
        button.deleteLater()
        other.deleteLater()
        QToolTip.hideText()


def testObserverDiscardsOldProjectReplyAndReadsWithoutEditor(retainedQtApplication):
    entered, release = Event(), Event()
    class Client:
        def getDeviceOverview(self, projectId, *, cancellation):
            entered.set()
            assert release.wait(3)
            return dict(snapshot(), projectId=projectId, runtimeInstanceId=projectId)
    observer, coordinator, button = observerFor(Client())
    try:
        observer.poll()
        assert entered.wait(1)
        token = observer.token
        coordinator.session.workflows.project['projectId'] = 'next'
        observer.poll()
        assert observer.snapshot is None and '设备未知' in button.text()
        assert not token.is_active()
        release.set()
        spin(retainedQtApplication, lambda: observer.snapshot is not None)
        assert observer.snapshot['runtimeInstanceId'] == 'next'
        assert token is not observer.token
    finally:
        release.set()
        observer.close()
        observer.reader.shutdown(wait=True)
        button.deleteLater()


def testObserverExpiresFailedStateAndResetsOnNewRuntime(retainedQtApplication):
    class Client:
        value = snapshot()
        failed = False
        def getDeviceOverview(self, projectId, *, cancellation):
            if self.failed:
                raise ConnectionError('connection lost')
            return deepcopy(self.value)
    client = Client()
    observer, coordinator, button = observerFor(client)
    try:
        spin(retainedQtApplication, lambda: observer.snapshot is not None)
        nextPoll = observer.nextPoll
        observer.refresh()
        assert observer.nextPoll == nextPoll
        observer.observedAt -= 2
        observer.render()
        assert '本次运行 未开始' in button.text()
        observer.pending = Future()
        observer.observedAt -= 6
        observer.poll()
        assert '设备未知' in button.text() and '已过期' in button.toolTip()
        observer.pending = None
        client.failed = True
        observer.nextPoll = 0
        spin(retainedQtApplication, lambda: 'connection lost' in observer.error)
        assert '设备未知' in button.text()
        client.failed = False
        client.value = dict(snapshot(), runtimeInstanceId='restarted', uptimeMs=0)
        observer.nextPoll = 0
        spin(retainedQtApplication, lambda: observer.snapshot is not None)
        assert '本次运行 未开始' in button.text()
        coordinator.window.runtimeClient = Client()
        observer.refresh()
        assert observer.snapshot is None and '设备未知' in button.text()
    finally:
        observer.close()
        observer.reader.shutdown(wait=True)
        button.deleteLater()


@pytest.mark.usefixtures('readableFont')
def testNativeStatusRefreshesWithoutDeviceDialogAndClickOpensAllDevices(
        retainedQtApplication, ownedDesignerWindow, overviewRuntime, tmp_path):  # noqa: F811
    service, client, path = overviewRuntime
    window = ownedDesignerWindow(client)
    assert window.loadProjectDirectory(str(path), confirmed=True)
    assert client.loadProject(str(path)).ok
    observer = window.deviceStatusObserver
    observer.nextPoll = 0
    spin(retainedQtApplication, lambda: observer.snapshot is not None and observer.snapshot['available'])
    assert window.pageCoordinator.devices is None
    assert '设备 0/2' in window.communicationStatus.text()
    assert 'STATUS-SIMULATOR' in window.communicationStatus.toolTip()
    window.communicationStatus.click()
    panel = window.pageCoordinator.devices
    spin(retainedQtApplication, lambda: panel.pending is None and bool(panel.deviceTypes))
    assert panel.isVisible() and panel.table.rowCount() == 2
    panel.closeButton.click()
    assert not panel.isVisible()
    runtimeId, states = client.getDeviceStates(service.loadedProjectId)
    client.controlDevice(service.loadedProjectId, runtimeId, 'camera', states['camera'], 'connect', confirmed=True)
    observer.nextPoll = 0
    spin(retainedQtApplication, lambda: observer.snapshot['states']['camera']['lifecycleConnected'])
    assert '设备 1/2' in window.communicationStatus.text()
    observer.observedAt = monotonic() - 2
    observer.render()
    assert '本次运行 未开始' in window.communicationStatus.text()
    observer.timer.stop()
    observer.snapshot, observer.observedAt = snapshot(), monotonic()
    observer.snapshot['states']['plc'].update(state='QUARANTINED', lastError='连接超时')
    observer.snapshot['states']['disabled'] = dict(name='备用相机', enabled=False)
    workflowId = next(iter(window.workflowStore.workflows))
    run = window.runtimeController._create(workflowId)
    window.runtimeController._setJob(run, 'job-current')
    run.state.updateJobTiming(100000, 114000)
    run.state.updateJob('FAILED')
    window.runtimeController.select(workflowId)
    observer.render()
    assert '失败耗时 00:00:14' in window.communicationStatus.text()
    assert '当前流程：' + window.workflowStore.workflows[workflowId].name in window.communicationStatus.toolTip()
    window.showNormal()
    retainedQtApplication.processEvents()
    for width in (1024, 1920):
        window.resize(width, 900)
        retainedQtApplication.processEvents()
        assert window.width() == width
        button = window.communicationStatus
        assert button.fontMetrics().horizontalAdvance(button.text()) < button.width()
        assert window.statusBar().rect().contains(button.geometry())
        assert window.grab().save(str(tmp_path / f'status-{width}.png'))
    hoverStatus(retainedQtApplication, button)
    tips = [widget for widget in retainedQtApplication.topLevelWidgets() if widget.objectName() == 'qtooltip_label']
    assert tips and tips[0].isVisible()
    assert tips[0].grab().save(str(tmp_path / 'status-tooltip.png'))
    print('STATUS_SCREENSHOTS', tmp_path)
    QToolTip.hideText()
