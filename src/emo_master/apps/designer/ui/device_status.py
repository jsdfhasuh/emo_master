"""Read-only status-bar observation, independent of the device editor's lifetime."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from html import escape
from time import monotonic

from PySide2.QtCore import QEvent, QObject, QTimer
from PySide2.QtWidgets import QToolTip

from emo_master.clients.runtime.calls import DisplayCallContext


def duration(milliseconds):
    seconds = max(0, int(milliseconds // 1000))
    days, seconds = divmod(seconds, 86400)
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)
    return (f'{days}天 ' if days else '') + f'{hours:02}:{minutes:02}:{seconds:02}'


def addressText(address):
    host = str(address.get('host') or address.get('bindAddress') or address.get('bindHost') or '')
    if ':' in host:
        host = '[' + host + ']'
    return (host + (':' + str(address['port']) if address.get('port') else '')
            if host else str(address.get('cameraKey') or address.get('serial') or ''))


def runPresentation(state, name=''):
    status = state.jobStatus if state is not None else 'IDLE'
    label = {'IDLE': '未开始', 'READY': '未开始', 'STARTING': '启动中', 'ACCEPTED': '等待启动',
             'RUNNING': '运行中', 'STOPPING': '停止中', 'COMPLETED': '完成', 'FAILED': '失败',
             'ABORTED': '已取消', 'REJECTED': '启动被拒绝'}.get(status, '待确认')
    elapsed = state.jobElapsedMs() if state is not None else None
    clock = duration(elapsed) if elapsed is not None else '未开始'
    if label == '待确认':
        clock = '待确认'
    summary = {'COMPLETED': '完成耗时', 'FAILED': '失败耗时', 'ABORTED': '取消耗时'}.get(status, '本次运行')
    text = summary + ' ' + clock
    if elapsed is None or label == '待确认':
        text = '本次运行 ' + label
    lines = ['<b>当前运行</b>']
    if name:
        lines.append('当前流程：' + escape(name))
    if state is not None and state.activeJobId:
        lines.append('当前作业：' + escape(state.activeJobId))
    lines.extend(['运行状态：' + label, '本次运行时间：' + clock])
    return text, lines


def statusPresentation(snapshot, definitions, *, elapsedMs=0, error='', runState=None, runName=''):
    """Counts describe the online configuration, never an unpublished draft."""
    runText, lines = runPresentation(runState, runName)
    lines.extend(['', '<b>设备与连接 · Runtime 在线状态</b>'])
    if snapshot:
        lines.append('实例：' + escape(snapshot['runtimeInstanceId']))
        lines.append(f'状态更新于 {max(0, int(elapsedMs // 1000))} 秒前')
    if snapshot is None or not snapshot['available']:
        message = error or (snapshot.get('message') if snapshot else '') or '正在读取在线状态'
        lines.append(escape(message))
        lines.append(f'本地草稿：{len(definitions)} 台设备；在线连接尚未确认')
        return f'设备未知 · 接入未知 · {runText}', '<br>'.join(lines)

    states = snapshot['states']
    connected = listening = peers = abnormal = 0
    connectionCounts = dict.fromkeys(('已连接', '未连接', '按次连接', '已停用', '等待接入'), 0)
    deviceLines, peerLines = [], []
    for key, state in states.items():
        name = str(state.get('name') or key)
        driver = state.get('driverState', {})
        server = state.get('deviceTypeId') == 'device.tcp.server'
        perOperation = state.get('connectionLifetime') == 'per_operation'
        enabled = state.get('enabled', True)
        driverState = driver.get('state')
        channels = [channel for channel in driver.get('channels', []) if channel.get('connected')]
        peers += len(channels)
        isConnected = bool(channels) if server else not perOperation and bool(state.get('lifecycleConnected'))
        connected += isConnected
        if server:
            listening += driverState == 'LISTENING'
            label = {'STOPPED': '未启动', 'STARTING': '启动中', 'STOPPING': '停止中',
                     'FAILED': '服务失败', 'LISTENING': '监听中，等待客户端'}.get(driverState, '状态未知')
            if channels:
                label = ('监听中' if driverState == 'LISTENING' else label) + f'，已接入 {len(channels)} 个客户端'
        elif perOperation:
            label = '事务执行中' if state.get('state') == 'BUSY' else '按次连接'
        else:
            label = '已连接' if isConnected else '未连接'
        category = ('已连接' if isConnected else '已停用' if not enabled else '按次连接' if perOperation else
                    '等待接入' if server and driverState == 'LISTENING' else '未连接')
        connectionCounts[category] += 1
        if not enabled:
            label += ' · 已停用'
        if state.get('admissionState') == 'PAUSED':
            label += ' · 已暂停'
        quarantined = state.get('state') == 'QUARANTINED'
        if quarantined:
            label += ' · 已隔离'
        recovery = state.get('recovery', {})
        connectionFailed = (not server and enabled and not isConnected and recovery.get('state') == 'FAILED'
                            and state.get('admissionState') != 'PAUSED')
        if connectionFailed:
            label += ' · 连接失败'
        isAbnormal = bool(driverState == 'FAILED' or quarantined or state.get('lifecycleError') or connectionFailed)
        abnormal += isAbnormal
        endpoint = addressText(state.get('connectionAddress', {}))
        detail = name + ' (' + key + ') · ' + ('异常 · ' if isAbnormal else '') + label + (' · ' + endpoint if endpoint else '')
        failure = ((driver.get('error') if driverState == 'FAILED' else '') or state.get('lifecycleError')
                   or (recovery.get('lastError') if connectionFailed else '')
                   or (state.get('lastError') if quarantined else ''))
        if failure:
            detail += ' · 错误：' + str(failure)
        elif state.get('lastError'):
            # Executors retain the last operation error even after recovery.
            detail += ' · 上次错误：' + str(state['lastError'])
        deviceLines.append((0 if isAbnormal else 1 if isConnected else 2, escape(detail)))
        for channel in channels:
            peer = channel.get('peerAddress')
            endpoint = addressText(dict(host=peer[0], port=peer[1])) if peer else '地址未知'
            title = str(channel.get('name') or channel.get('channelId') or '客户端')
            detail = f'{name} / {title} · {endpoint}'
            if channel.get('connectedAtMs'):
                stamp = datetime.fromtimestamp(channel['connectedAtMs'] / 1000).strftime('%Y-%m-%d %H:%M:%S')
                detail += ' · 接入于 ' + stamp
            peerLines.append(escape(detail))
    alarm = f' · 异常 {abnormal}' if abnormal else ''
    lines.extend(['', f'<b>设备：已连接 {connected} / {len(states)} · TCP 监听 {listening}{alarm}</b>'])
    if states:
        lines.append('连接分类：' + ' · '.join(f'{label} {count}' for label, count in connectionCounts.items() if count))
    deviceLines.sort(key=lambda item: item[0])
    lines.extend([line for _, line in deviceLines[:12]] or ['无在线设备'])
    if len(deviceLines) > 12:
        lines.append(f'另有 {len(deviceLines) - 12} 台设备')
    unpublished = definitions.keys() - states.keys()
    if unpublished:
        lines.append(f'本地另有 {len(unpublished)} 台设备尚未写入 Runtime')
    lines.extend(['', f'<b>TCP 接入客户端：{peers}</b>'])
    lines.extend(peerLines[:12] or ['无已接入客户端'])
    if len(peerLines) > 12:
        lines.append(f'另有 {len(peerLines) - 12} 个客户端')
    text = f'设备 {connected}/{len(states)}{alarm} · 监听 {listening} · 接入 {peers} · {runText}'
    return text, '<br>'.join(lines)


class DeviceStatusObserver(QObject):
    def __init__(self, coordinator, button):
        super().__init__(button)
        self.c, self.button = coordinator, button
        self.reader = ThreadPoolExecutor(max_workers=1, thread_name_prefix='device-status')
        self.pending = None
        self.token = None
        self.scope = None
        self.pendingScope = None
        self.snapshot = None
        self.observedAt = 0.0
        self.nextPoll = 0.0
        self.error = ''
        self.closed = False
        self.tooltipPosition = None
        self.tooltipText = ''
        button.installEventFilter(self)
        self.timer = QTimer(self)
        self.timer.setInterval(200)
        self.timer.timeout.connect(self.poll)
        self.timer.start()
        self.render()

    def _scope(self):
        client = self.c.window.runtimeClient
        return (id(client), getattr(client, 'runtimeScope', None), id(getattr(client, 'runtimeService', None)),
                id(getattr(client, 'stub', None)), self.c.session.workflows.project.get('projectId', ''))

    def refresh(self):
        if self.closed:
            return
        current = self._scope()
        if current != self.scope:
            if self.token:
                self.token.cancel()
            self.scope = current
            self.snapshot, self.error, self.observedAt = None, '', 0.0
            self.nextPoll = 0.0
        self.render()

    def poll(self):
        if self.closed:
            return
        if self.scope != self._scope():
            self.refresh()
        now = monotonic()
        if self.pending is not None and self.pending.done():
            future, self.pending = self.pending, None
            self.token = None
            if self.pendingScope == self.scope:
                try:
                    self.snapshot = future.result()
                    self.observedAt, self.error = now, ''
                except Exception as error:
                    self.snapshot, self.error = None, '在线状态不可用：' + str(error)
                self.nextPoll = now + 2.0
        if self.snapshot is not None and now - self.observedAt > 6.0:
            self.snapshot, self.error = None, '在线状态已过期；连接状态未知'
        self.render()
        if self.pending is not None or now < self.nextPoll:
            return
        reader = getattr(self.c.window.runtimeClient, 'getDeviceOverview', None)
        if not callable(reader):
            self.error, self.nextPoll = 'Runtime 不支持设备状态概览', now + 2.0
            return
        self.pendingScope = self.scope
        self.token = DisplayCallContext()
        self.pending = self.reader.submit(reader, self.scope[-1], cancellation=self.token)

    def render(self):
        elapsed = max(0, (monotonic() - self.observedAt) * 1000) if self.snapshot else 0
        window = self.c.window
        state = getattr(window, 'runtimePanelState', None)
        controller = getattr(window, 'runtimeController', None)
        workflowId = getattr(controller, 'selected', '')
        workflows = getattr(self.c.session.workflows, 'workflows', {})
        workflow = workflows.get(workflowId)
        name = workflow.name if workflow is not None else workflowId
        text, tooltip = statusPresentation(self.snapshot, self.c.session.workflows.devices.get('definitions', {}),
                                          elapsedMs=elapsed, error=self.error, runState=state, runName=name)
        if self.button.text() != text:
            self.button.setText(text)
        if self.button.toolTip() != tooltip:
            visible = self._ownsTooltip()
            self.button.setToolTip(tooltip)
            if visible:
                self._showTooltip()

    def _ownsTooltip(self):
        return self.tooltipPosition is not None and QToolTip.isVisible() and QToolTip.text() == self.tooltipText

    def _showTooltip(self):
        self.tooltipText = self.button.toolTip()
        QToolTip.showText(self.tooltipPosition, self.tooltipText, self.button, self.button.rect())

    def _hideTooltip(self):
        if self._ownsTooltip():
            QToolTip.hideText()
        self.tooltipPosition, self.tooltipText = None, ''

    def eventFilter(self, watched, event):
        if watched is self.button and not self.closed:
            if event.type() == QEvent.ToolTip:
                self.render()
                self.tooltipPosition = event.globalPos()
                self._showTooltip()
                event.accept()
                return True
            if event.type() in (QEvent.Leave, QEvent.Hide, QEvent.MouseButtonPress):
                self._hideTooltip()
        return super().eventFilter(watched, event)

    def close(self):
        self._hideTooltip()
        self.closed = True
        self.timer.stop()
        if self.token:
            self.token.cancel()
        self.reader.shutdown(wait=False, cancel_futures=True)
