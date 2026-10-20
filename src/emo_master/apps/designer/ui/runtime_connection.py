"""Explicit transport selection. Probe metadata before replacing a live client."""
from concurrent.futures import ThreadPoolExecutor

from PySide2.QtCore import Qt, QTimer
from PySide2.QtWidgets import QDialog, QVBoxLayout, QFormLayout, QLineEdit, QLabel, QDialogButtonBox, QGroupBox, QMessageBox

from emo_master.apps.designer.services.runtime_client import runtimeConnectionDescription


def readCurrentConnection(client):
    info = client.deviceNetworkCommand('', '', 'info')
    snapshot, versionError = None, ''
    if info['projectId']:
        try:
            snapshot = client.getOnlineProjectSnapshot()
        except Exception as error:
            versionError = str(error)
    return info, snapshot, versionError


def discardCandidate(future):
    """A closed dialog must release a late candidate without touching Qt."""
    if not future.cancelled():
        try:
            future.result()[0].close()
        except Exception:
            pass  # connectCandidate already closes failed probes


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
        self.client = coordinator.window.runtimeClient
        self.session = coordinator.session
        self.projectId = self.session.document().project.projectId
        self.scope = self._scope()
        self.closed = False
        self.notifying = False
        self.communication = getattr(coordinator.window, 'communicationController', None)
        self.previousPause = self.communication.observationPaused if self.communication else False
        if self.communication:
            self.communication.observationPaused = True
        self.candidate = None
        self.pending = None
        self.currentPending = None
        self.pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix='runtime-connect')
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.ready)
        self.setWindowTitle('连接 Runtime')
        self.resize(620, 420)
        layout = QVBoxLayout(self)
        self.currentGroup = QGroupBox('当前连接')
        currentLayout = QVBoxLayout(self.currentGroup)
        description = runtimeConnectionDescription(self.client)
        self.currentType = QLabel('类型：' + description['label'])
        self.currentType.setWordWrap(True)
        self.currentStatus = QLabel('状态：正在读取当前连接信息…')
        self.currentStatus.setWordWrap(True)
        self.currentStatus.setTextInteractionFlags(Qt.TextSelectableByMouse)
        currentLayout.addWidget(self.currentType)
        currentLayout.addWidget(self.currentStatus)
        layout.addWidget(self.currentGroup)
        candidateGroup = QGroupBox('切换到独立 Runtime')
        candidateLayout = QVBoxLayout(candidateGroup)
        form = QFormLayout()
        self.target = QLineEdit(description['target'])
        self.target.setPlaceholderText('主机:端口')
        form.addRow('候选地址', self.target)
        candidateLayout.addLayout(form)
        self.target.textChanged.connect(self.targetChanged)
        self.status = QLabel('尚未填写或读取候选目标；不会改变当前连接。')
        self.status.setWordWrap(True)
        self.status.setTextInteractionFlags(Qt.TextSelectableByMouse)
        candidateLayout.addWidget(self.status)
        layout.addWidget(candidateGroup)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.button(QDialogButtonBox.Ok).setText('读取目标信息')
        self.buttons.button(QDialogButtonBox.Cancel).setText('取消')
        self.buttons.accepted.connect(self.connect)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self.currentPending = self.pool.submit(readCurrentConnection, self.client)
        self.timer.start(40)

    def _scope(self):
        client = self.c.window.runtimeClient
        return id(client), getattr(client, 'runtimeScope', None), id(getattr(client, 'runtimeService', None))

    def _valid(self):
        return (not self.closed and self._scope() == self.scope and self.c.session is self.session
                and self.session.workflows.project.get('projectId') == self.projectId)

    def _error(self, title, text, error=None):
        self.status.setText(text)
        if not self._valid() or self.notifying:
            return
        box = QMessageBox(self)
        box.setWindowTitle(title)
        box.setIcon(QMessageBox.Critical)
        box.setTextFormat(Qt.PlainText)
        box.setText(text)
        if error is not None:
            box.setDetailedText(str(getattr(error, 'code', '')) + '\n' + str(error))
        box.addButton('关闭', QMessageBox.RejectRole)
        self.notifying = True
        self.buttons.button(QDialogButtonBox.Ok).setEnabled(False)
        try:
            box.exec_()
        finally:
            self.notifying = False
            self.buttons.button(QDialogButtonBox.Ok).setEnabled(self._valid() and self.pending is None)

    def targetChanged(self):
        if self.candidate is not None:
            self.candidate.close()
            self.candidate = None
        self.buttons.button(QDialogButtonBox.Ok).setText('读取目标信息')
        self.status.setText('候选地址已修改，请读取目标信息；原连接未改变。')

    def connect(self):
        if self.pending is not None or self.notifying or not self._valid():
            return
        if self.candidate is not None:
            try:
                self.c.attachRuntime(self.candidate)
                self.candidate = None
                self.accept()
            except (ValueError, RuntimeError) as error:
                self._error('切换 Runtime 未完成', '原连接未改变：' + str(error), error)
            return
        target = self.target.text().strip()
        from urllib.parse import urlsplit
        try:
            address = urlsplit('//' + target)
            valid = bool(address.hostname and address.port and not address.path and not address.username and not address.password)
        except ValueError:
            valid = False
        if not valid:
            self._error('候选地址不正确', '请输入独立 Runtime 的主机:端口，例如 主机名:端口。当前连接未改变；不切换可点取消。')
            return
        self.target.setEnabled(False)
        self.buttons.button(QDialogButtonBox.Ok).setEnabled(False)
        self.status.setText('正在读取实例、在线配置与设备能力')
        self.pending = self.pool.submit(connectCandidate, target)
        self.timer.start(40)

    def ready(self):
        if self.closed or self.notifying:
            return
        if self.currentPending is not None and self.currentPending.done():
            current, self.currentPending = self.currentPending, None
            if self._valid():
                try:
                    info, snapshot, versionError = current.result()
                    detail = '尚未加载工程' if not info['projectId'] else '在线工程：' + info['projectId']
                    if snapshot is not None:
                        detail += f" · 文档 R{snapshot['documentRevision']} / 执行 R{snapshot['executionRevision']}"
                    if versionError:
                        detail += '\n版本信息读取失败：' + versionError
                    self.currentStatus.setText('状态：已连接\n实例：' + info['runtimeInstanceId'] + '\n' + detail)
                except Exception as error:
                    self.currentStatus.setText('当前连接信息读取失败：' + str(error) + '\n未切换连接，可关闭后重试。')
            else:
                self.currentStatus.setText('连接上下文已变化，请重新打开窗口。')
        if self.pending is None or not self.pending.done():
            if self.currentPending is None and self.pending is None:
                self.timer.stop()
            return
        future, self.pending = self.pending, None
        if self.currentPending is None:
            self.timer.stop()
        self.target.setEnabled(True)
        self.buttons.button(QDialogButtonBox.Ok).setEnabled(self._valid())
        if not self._valid():
            discardCandidate(future)
            return
        try:
            self.candidate, info, snapshot, types = future.result()
            local = self.c.session.document().project.projectId
            detail = ('未加载在线工程' if snapshot is None else
                      f"在线工程 {info['projectId']} · 文档 R{snapshot['documentRevision']} / 执行 R{snapshot['executionRevision']}")
            self.status.setText(f"实例 {info['runtimeInstanceId']}\n{detail}\n{len(types)} 个设备类型"
                + ('\n在线工程与本地不同；连接后可比较或读回，不会自动覆盖。' if info['projectId'] != local else ''))
            self.buttons.button(QDialogButtonBox.Ok).setText('切换到此 Runtime')
        except Exception as error:
            self.target.setEnabled(True)
            self._error('读取目标 Runtime 失败', '读取失败，原连接未改变：' + str(error), error)

    def done(self, result):
        self.closed = True
        if self.pending is not None:
            pending, self.pending = self.pending, None
            pending.add_done_callback(discardCandidate)
            pending.cancel()
        if self.candidate:
            self.candidate.close()
            self.candidate = None
        if self.communication:
            self.communication.observationPaused = self.previousPause
            self.communication.nextPoll = 0
        self.timer.stop()
        self.pool.shutdown(wait=False, cancel_futures=True)
        super().done(result)
