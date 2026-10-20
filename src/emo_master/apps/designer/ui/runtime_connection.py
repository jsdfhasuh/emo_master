"""Explicit transport selection. Probe metadata before replacing a live client."""
from concurrent.futures import ThreadPoolExecutor

from PySide2.QtCore import QTimer
from PySide2.QtWidgets import QDialog, QVBoxLayout, QFormLayout, QLineEdit, QLabel, QDialogButtonBox


def connectCandidate(target):
    import grpc
    from emo_master.apps.runtime.grpc_server.generated.runtime_pb2_grpc import RuntimeServiceStub
    from emo_master.apps.designer.services.runtime_client import RuntimeClient
    channel = grpc.insecure_channel(target)
    client = RuntimeClient(RuntimeServiceStub(channel), ownedChannel=channel, runtimeTarget=target, deadlineMs=5000)
    try:
        info = client.deviceNetworkCommand('', '', 'info')
        snapshot = client.getOnlineProjectSnapshot() if info['projectId'] else None
        types, _requirements, _rejected = client.listDeviceTypes()
        return client, info, snapshot, types
    except BaseException:
        client.close()
        raise


class RuntimeConnectionDialog(QDialog):
    def __init__(self, coordinator):
        super().__init__(coordinator.window)
        self.c = coordinator
        self.communication = getattr(coordinator.window, 'communicationController', None)
        self.previousPause = self.communication.observationPaused if self.communication else False
        if self.communication:
            self.communication.observationPaused = True
        self.candidate = None
        self.pending = None
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='runtime-connect')
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.ready)
        self.setWindowTitle('连接 Runtime')
        self.resize(560, 220)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.target = QLineEdit(getattr(coordinator.window.runtimeClient, '_runtimeTarget', ''))
        self.target.setPlaceholderText('主机:端口')
        form.addRow('Runtime 地址', self.target)
        layout.addLayout(form)
        self.status = QLabel('未读取目标 Runtime')
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.button(QDialogButtonBox.Ok).setText('读取并连接')
        self.buttons.accepted.connect(self.connect)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

    def connect(self):
        if self.pending is not None:
            return
        if self.candidate is not None:
            try:
                self.c.attachRuntime(self.candidate)
                self.candidate = None
                self.accept()
            except (ValueError, RuntimeError) as error:
                self.status.setText(str(error))
            return
        target = self.target.text().strip()
        if not target or ':' not in target:
            self.status.setText('请输入 Runtime 主机:端口')
            return
        self.target.setEnabled(False)
        self.buttons.button(QDialogButtonBox.Ok).setEnabled(False)
        self.status.setText('正在读取实例、在线配置与设备能力')
        self.pending = self.pool.submit(connectCandidate, target)
        self.timer.start(40)

    def ready(self):
        if self.pending is None or not self.pending.done():
            return
        future, self.pending = self.pending, None
        self.timer.stop()
        self.buttons.button(QDialogButtonBox.Ok).setEnabled(True)
        try:
            self.candidate, info, snapshot, types = future.result()
            local = self.c.session.document().project.projectId
            detail = ('未加载在线工程' if snapshot is None else
                      f"在线工程 {info['projectId']} · 文档 R{snapshot['documentRevision']} / 执行 R{snapshot['executionRevision']}")
            self.status.setText(f"实例 {info['runtimeInstanceId']}\n{detail}\n{len(types)} 个设备类型"
                + ('\n在线工程与本地不同；连接后可比较或读回，不会自动覆盖。' if info['projectId'] != local else ''))
            self.buttons.button(QDialogButtonBox.Ok).setText('连接此 Runtime')
        except Exception as error:
            self.target.setEnabled(True)
            self.status.setText('读取失败，原连接未改变：' + str(error))

    def done(self, result):
        if self.pending is not None:
            self.status.setText('等待只读探测返回或超时后可关闭')
            return
        if self.candidate:
            self.candidate.close()
            self.candidate = None
        if self.communication:
            self.communication.observationPaused = self.previousPause
            self.communication.nextPoll = 0
        self.timer.stop()
        self.pool.shutdown(wait=False, cancel_futures=True)
        super().done(result)
