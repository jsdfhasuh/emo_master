from __future__ import annotations

from queue import Empty
from threading import Event, Thread
from time import perf_counter
from typing import Any, Callable

from PySide2.QtCore import QEventLoop, QTimer, Qt
from PySide2.QtWidgets import QDialog, QHBoxLayout, QLabel, QProgressBar, QPushButton, QStyle, QVBoxLayout

from emo_master.apps.designer.startup import StartupTiming


STAGE_LABELS = {
    "runtime-imports": "正在加载运行组件",
    "runtime-channel": "正在准备 Runtime 连接",
    "runtime-database": "正在初始化运行数据库",
    "runtime-maintenance": "正在检查历史运行数据",
    "runtime-logs": "正在初始化运行日志",
    "runtime-workspaces": "正在整理已结束任务的临时目录",
    "runtime-plugins": "正在扫描算子和设备类型",
    "runtime-preview": "正在恢复预览索引",
    "runtime-managers": "正在初始化运行服务",
    "runtime-online-configuration": "正在恢复在线工程和资源",
    "runtime-client": "正在准备设计器连接",
    "editor-imports": "正在加载编辑器组件",
    "editor-ui": "正在创建编辑器界面",
    "project-entry": "正在准备项目入口",
}


class DesignerStartupDialog(QDialog):
    def __init__(self, timing: StartupTiming) -> None:
        super().__init__()
        self.timing = timing
        self.cancelled = Event()
        self._done = Event()
        self._thread: Thread | None = None
        self._action: Callable[[Event], Any] | None = None
        self._result: Any = None
        self._error: BaseException | None = None
        self._painted = False
        self._finished = False
        self._waiting = False
        self._loop = QEventLoop(self)
        self.setWindowTitle("EmoMaster · 正在启动")
        self.setWindowFlag(Qt.WindowContextHelpButtonHint, False)
        self.setMinimumWidth(420)
        self.resize(500, 230)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(14)
        title = QLabel("EmoMaster")
        font = title.font()
        font.setPointSize(20)
        font.setBold(True)
        title.setFont(font)
        layout.addWidget(title)
        self.statusLabel = QLabel("正在启动设计器")
        self.statusLabel.setWordWrap(True)
        self.statusLabel.setMinimumHeight(32)
        layout.addWidget(self.statusLabel)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(8)
        layout.addWidget(self.progress)
        footer = QHBoxLayout()
        self.elapsedLabel = QLabel()
        self.elapsedLabel.setObjectName("mutedText")
        footer.addWidget(self.elapsedLabel, 1)
        self.cancelButton = QPushButton("取消启动")
        self.cancelButton.setIcon(self.style().standardIcon(QStyle.SP_DialogCancelButton))
        self.cancelButton.clicked.connect(self.reject)
        footer.addWidget(self.cancelButton)
        layout.addLayout(footer)
        self._timer = QTimer(self)
        self._timer.setInterval(50)
        self._timer.timeout.connect(self._poll)
        self.refreshStatus()

    def prepare(self, action: Callable[[Event], Any]) -> Any:
        self._action = action
        self._waiting = True
        self._timer.start()
        self.show()
        self._loop.exec_()
        if self._thread is not None:
            self._thread.join()
        if self._error is not None:
            raise self._error
        return self._result

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        if not self._painted:
            self._painted = True
            self.timing.finish()
            self.timing.mark("startup-visible")
            # Do not start imports until the first native paint has completed.
            QTimer.singleShot(0, self._startWorker)

    def _startWorker(self) -> None:
        if self.cancelled.is_set():
            self._done.set()
        else:
            self._thread = Thread(target=self._prepare, name="designer-startup")
            self._thread.start()

    def _prepare(self) -> None:
        try:
            assert self._action is not None
            self._result = self._action(self.cancelled)
        except BaseException as error:
            self._error = error
        finally:
            self._done.set()

    def refreshStatus(self) -> None:
        while True:
            try:
                stage = self.timing.updates.get_nowait()
            except Empty:
                break
            if not self.cancelled.is_set():
                self.statusLabel.setText(STAGE_LABELS.get(stage, stage))
        self.elapsedLabel.setText(f"已用 {perf_counter() - self.timing.started:.1f} 秒")

    def _poll(self) -> None:
        self.refreshStatus()
        if self._waiting and self._done.is_set():
            self._waiting = False
            self._loop.quit()

    def reject(self) -> None:
        if not self.cancelled.is_set():
            self.cancelled.set()
            self.timing.mark("cancel-requested")
            self.statusLabel.setText("正在结束当前初始化并退出")
            self.cancelButton.setText("正在退出")

    def closeEvent(self, event) -> None:
        if self._finished:
            event.accept()
        else:
            self.reject()
            event.ignore()

    def finish(self) -> None:
        self._finished = True
        self._timer.stop()
        self.close()
