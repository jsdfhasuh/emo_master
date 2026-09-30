"""Independent Qt shell. No Designer, toolbox or Runtime executor imports."""
import argparse
from pathlib import Path
import queue
import re
import threading

import grpc
from PySide2.QtCore import QTimer, Qt
from PySide2.QtWidgets import QApplication, QLabel, QPushButton, QVBoxLayout, QWidget

from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb
from emo_master.apps.runtime.grpc_server.generated import runtime_pb2_grpc as rpc
from emo_master.clients.runtime.display_session import DisplaySession
from emo_master.core.project.delivery_store import DeliveryStore
from emo_master.core.project.test_delivery import readJson
from emo_master.ui.presentation.hub import DisplayHub
from emo_master.ui.presentation.renderer import RuntimePages


class OperatorView(QWidget):
    def __init__(self, readyFile, jobId=None):
        super().__init__()
        self.setWindowTitle('EmoMaster · 测试项目运行端')
        self.resize(650, 260)
        self.readyFile, self.jobId = Path(readyFile), jobId
        self.session = self.hub = self.document = self.info = None
        self.waitingPage = None
        self.busy = self.closing = self.done = False
        self.error = None
        self.worker = None
        self.events = queue.Queue(maxsize=1)
        layout = QVBoxLayout(self)
        self.message = QLabel('Runtime 未确认 · 项目未确认 · 页面未就绪')
        self.message.setWordWrap(True)
        layout.addWidget(self.message)
        self.startButton = QPushButton('明确启动本地图像检测（测试 release）')
        self.startButton.setEnabled(False)
        self.startButton.clicked.connect(self.startDetection)
        layout.addWidget(self.startButton)
        self.secondButton = QPushButton('打开共享运行页面')
        self.secondButton.setEnabled(False)
        self.secondButton.clicked.connect(self.openPage)
        layout.addWidget(self.secondButton)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.poll)
        self.timer.start(25)
        self.launch('ready', self.connect)

    def launch(self, kind, operation):
        if self.busy:
            return
        self.busy = True
        def work():
            try:
                operation()
                self.events.put((kind, None))
            except BaseException as error:
                self.events.put(('error', str(error)))
        self.worker = threading.Thread(target=work, name='operator-view-operation')
        self.worker.start()

    def connect(self):
        if self.readyFile.stat().st_size > 16 * 1024:
            raise ValueError('host descriptor budget')
        info = readJson(self.readyFile.read_bytes())
        if (info.get('format') != 'emo-test-host-1' or not info.get('runtimeReady') or
                not info.get('projectReady') or not re.fullmatch(r'127\.0\.0\.1:[0-9]{1,5}', info['address'])):
            raise ValueError('unsupported or unready loopback host')
        store = DeliveryStore(info['store'])
        document, _ = store.verify(info['revision'])
        with grpc.insecure_channel(info['address']) as channel:
            stub = rpc.DisplayServiceStub(channel)
            caps = stub.Capabilities(pb.DisplayEmpty(), timeout=3)
            if caps.runtime_instance_id != info['runtimeInstanceId'] or caps.protocol_version != '1.0':
                raise ValueError('stale host descriptor / incompatible Runtime')
            if self.jobId and self.jobId not in {j.job_id for j in stub.ListJobs(pb.DisplayEmpty(), timeout=3).jobs}:
                raise ValueError('explicit Job is not available on this Runtime')
        self.info, self.document = info, document
        if self.jobId:
            self.session = DisplaySession(info['address'], self.jobId, imageDemand=True)

    def startDetection(self):
        if self.busy or self.closing or self.session is not None or self.info is None:
            return
        self.startButton.setEnabled(False)
        self.message.setText('正在明确启动已冻结的测试 release；页面不是启动触发器')
        def start():
            with grpc.insecure_channel(self.info['address']) as channel:
                stub = rpc.DisplayServiceStub(channel)
                caps = stub.Capabilities(pb.DisplayEmpty(), timeout=3)
                if caps.runtime_instance_id != self.info['runtimeInstanceId']:
                    raise ValueError('Runtime changed; reopen using its new ready descriptor')
                self.jobId = stub.Start(pb.DisplayStartRequest(prepared_id=self.info['preparedId']), timeout=20).job_id
            self.session = DisplaySession(self.info['address'], self.jobId, imageDemand=True)
        self.launch('started', start)

    def openPage(self):
        if self.closing or self.document is None:
            return
        if self.hub is None:
            if self.waitingPage is None:
                self.waitingPage = RuntimePages(self.document.presentation, label='测试项目 · 页面就绪 · 尚未开始检测')
            self.waitingPage.show()
            return
        if len(self.hub.windows) >= 2:
            self.message.setText('每会话最多两个共享窗口；关闭一个窗口后可再打开')
            return
        page = RuntimePages(self.document.presentation, hub=self.hub, label='测试 release · 只读 · 非现场验收')
        page.setAttribute(Qt.WA_DeleteOnClose)
        page.show()

    def poll(self):
        try:
            kind, value = self.events.get_nowait()
        except queue.Empty:
            return
        self.worker.join()
        self.busy = False
        if kind == 'closed':
            self.done = True
            self.close()
            return
        if kind == 'error':
            self.error = value
            self.message.setText('操作失败：' + value + '\n不会自动重试 Start；Runtime 仍由独立宿主管理。')
            self.closing = False
            return
        if self.closing:
            self.shutdown()
            return
        if self.session:
            self.hub = DisplayHub(self.session, self)
            if self.waitingPage:
                self.waitingPage.close()
                self.waitingPage.deleteLater()
                self.waitingPage = None
        self.message.setText('Runtime 就绪 · 项目就绪 · 页面就绪\n' +
            ('已连接 Job ' + self.jobId if self.session else '尚未开始检测；打开页面和切页不创建 Job。') +
            '\n关闭界面只断开显示；独立 Runtime 使用 --stop 命令明确停止。')
        self.startButton.setEnabled(self.session is None)
        self.secondButton.setEnabled(True)
        self.openPage()

    def shutdown(self):
        if self.busy:
            return
        self.startButton.setEnabled(False)
        self.secondButton.setEnabled(False)
        if self.waitingPage:
            self.waitingPage.close()
            self.waitingPage.deleteLater()
            self.waitingPage = None
        if self.hub:
            for page in tuple(self.hub.windows):
                page.close()
        self.message.setText('正在断开只读会话；不停止外部 Runtime 或 Job')
        def close():
            if self.session:
                self.session.close()
                self.session = None
        self.launch('closed', close)

    def closeEvent(self, event):
        if self.done:
            self.timer.stop()
            event.accept()
        else:
            event.ignore()
            self.closing = True
            self.shutdown()


def main(arguments=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ready-file', type=Path, required=True)
    parser.add_argument('--job', help='Read-only observation of this explicitly selected existing Job')
    args = parser.parse_args(arguments)
    app = QApplication.instance() or QApplication([])
    window = OperatorView(args.ready_file, args.job)
    window.show()
    return app.exec_()


if __name__ == '__main__':
    raise SystemExit(main())
