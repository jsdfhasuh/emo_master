"""Explicit native Qt launcher: --sample or --address/--job/--project."""
import argparse
from pathlib import Path
import queue
import sys
import threading

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'src'),str(ROOT)]
import emo_master  # noqa: E402,F401 - Windows DLL bootstrap before Qt
from PySide2.QtCore import QTimer, Qt  # noqa: E402
from PySide2.QtWidgets import QApplication, QWidget, QPushButton, QLabel, QVBoxLayout  # noqa: E402
from emo_master.core.project.models import ProjectDocument  # noqa: E402
from emo_master.clients.runtime.display_session import DisplaySession  # noqa: E402
from emo_master.ui.presentation.hub import DisplayHub  # noqa: E402
from emo_master.ui.presentation.renderer import RuntimePages  # noqa: E402


class Launcher(QWidget):
    def __init__(self, address=None, job=None, project=None):
        super().__init__()
        self.setWindowTitle('EmoMaster · P3 本地功能演示 / 只读连接')
        self.resize(520,220)
        self.backend=self.session=self.hub=None
        self.windows=[]
        self.events=queue.Queue(maxsize=2)
        self.busy=False
        self.closing=False
        self.done=False
        self.error=None
        self.worker=None
        self.address,self.job,self.project=address,job,project
        layout=QVBoxLayout(self)
        self.message=QLabel('本地图像功能样例，非性能验收。\n仅点击下方按钮后启动临时 Runtime 与检测。' if not address else '只读连接指定 Job，不启动或停止外部 Runtime。')
        self.message.setWordWrap(True)
        layout.addWidget(self.message)
        self.start=QPushButton('启动本地图像示例' if not address else '打开已有任务页面')
        self.start.clicked.connect(self.begin)
        layout.addWidget(self.start)
        self.second=QPushButton('打开第二个共享窗口')
        self.second.setEnabled(False)
        self.second.clicked.connect(self.openWindow)
        layout.addWidget(self.second)
        self.timer=QTimer(self)
        self.timer.timeout.connect(self.poll)
        self.timer.start(30)
        if address:
            self.begin()

    def begin(self):
        if self.busy or self.session or self.closing:
            return
        self.busy=True
        self.start.setEnabled(False)
        self.message.setText('正在准备；界面仍可响应…')
        def work():
            try:
                if not self.address:
                    from examples.runtime_pages_p3 import LocalDemo
                    self.backend=LocalDemo()
                    self.address,self.job,self.project=self.backend.address,self.backend.jobId,self.backend.project
                self.session=DisplaySession(self.address,self.job)
                self.events.put(('ready',None))
            except Exception as error:
                self.events.put(('error',str(error)))
        self.worker=threading.Thread(target=work,name='p3-launch')
        self.worker.start()

    def openWindow(self):
        if self.hub is None or len(self.hub.windows)>=2 or self.closing:
            return
        window=RuntimePages(self.project.presentation,hub=self.hub,
            label='本地图像功能演示 · 只读页面' if self.backend else '已有任务 · 只读页面')
        window.setAttribute(Qt.WA_DeleteOnClose)
        self.windows.append(window)
        window.show()

    def poll(self):
        try:
            kind,value=self.events.get_nowait()
        except queue.Empty:
            return
        self.busy=False
        if kind=='ready':
            self.hub=DisplayHub(self.session,self)
            self.message.setText(f'已连接 {self.job}\n本地样例交替选择两幅图，真实 Blob/Count 产生变化；不会随一轮结果自动退出。')
            self.second.setEnabled(True)
            if not self.closing:
                self.openWindow()
            else:
                self.shutdown()
        elif kind=='closed':
            self.done=True
            self.close()
        else:
            self.error=value
            self.message.setText('操作失败，尚未确认收尾：'+str(value))
            self.start.setEnabled(False)

    def shutdown(self):
        if self.busy:
            return
        self.busy=True
        for window in tuple(self.hub.windows) if self.hub else ():
            window.close()
        self.message.setText('正在断开展示并回收本启动器拥有的资源…')
        self.second.setEnabled(False)
        def work():
            try:
                if self.session:
                    self.session.close()
                if self.backend:
                    self.backend.close()
                self.events.put(('closed',None))
            except Exception as error:
                self.events.put(('error',str(error)))
        self.worker=threading.Thread(target=work,name='p3-shutdown')
        self.worker.start()

    def closeEvent(self,event):
        if self.done or (not self.busy and self.session is None and self.backend is None):
            self.timer.stop()
            event.accept()
        else:
            event.ignore()
            self.closing=True
            self.shutdown()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sample',action='store_true')
    parser.add_argument('--address')
    parser.add_argument('--job')
    parser.add_argument('--project',type=Path)
    args=parser.parse_args()
    if any((args.address,args.job,args.project)) and not all((args.address,args.job,args.project)):
        parser.error('已有任务模式必须同时指定 --address --job --project')
    if args.sample and args.address:
        parser.error('--sample 不可与已有任务模式混用')
    project=ProjectDocument.model_validate_json(args.project.read_text(encoding='utf-8')) if args.project else None
    app=QApplication.instance() or QApplication(sys.argv[:1])
    window=Launcher(args.address,args.job,project)
    window.show()
    return app.exec_()


if __name__=='__main__':
    import multiprocessing
    multiprocessing.freeze_support()
    raise SystemExit(main())
