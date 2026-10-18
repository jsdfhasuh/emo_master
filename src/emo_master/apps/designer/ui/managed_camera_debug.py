"""Read online identity, then perform explicitly confirmed managed captures."""
from concurrent.futures import ThreadPoolExecutor

from PySide2.QtCore import Qt, QTimer
from PySide2.QtGui import QPixmap
from PySide2.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QMessageBox

from emo_master.core.project.snapshots import revisionOf


class ManagedCameraDebugDialog(QDialog):
    def __init__(self, client, projectId, deviceId, definition, parent=None):
        super().__init__(parent)
        self.client, self.projectId, self.deviceId = client, projectId, deviceId
        self.definition = definition
        self.runtimeId = ""
        self.state = None
        self.sessionId = ""
        self.sequence = 0
        self.closed = False
        self.pending = None
        self.pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="camera-diagnostic-ui")
        self.setWindowTitle("相机诊断 · " + definition.name)
        self.resize(800, 600)
        layout = QVBoxLayout(self)
        self.preview = QLabel()
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setMinimumSize(240, 180)
        layout.addWidget(self.preview, 1)
        self.status = QLabel("尚未打开诊断会话")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        row = QHBoxLayout()
        self.captureButton = QPushButton("采集一帧")
        self.captureButton.setEnabled(False)
        self.captureButton.clicked.connect(self.capture)
        close = QPushButton("关闭")
        close.clicked.connect(self.reject)
        row.addWidget(self.captureButton)
        row.addStretch()
        row.addWidget(close)
        layout.addLayout(row)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.ready)
        self.finished.connect(self.dispose)
        self.pending = self.pool.submit(self.open)
        self.timer.start(40)

    def open(self):
        runtimeId, states = self.client.getDeviceStates(self.projectId)
        state = states.get(self.deviceId)
        if state is None or state["definitionRevision"] != revisionOf(self.definition.model_dump()):
            raise ValueError("本地与在线设备不同，请先比较配置")
        self.runtimeId, self.state = runtimeId, state
        result = self.call("open")
        self.sessionId = result["sessionId"]
        if self.closed:
            self.call("close")
        return result

    def call(self, action):
        return self.client.cameraDiagnostic(self.projectId, self.runtimeId, self.deviceId, self.state, action,
            sessionId=self.sessionId, sequence=self.sequence, confirmed=action == "capture")

    def capture(self):
        if self.pending is not None or not self.sessionId or self.closed:
            return
        if QMessageBox.question(self, "确认相机采集", "将使用在线设备默认参数采集一帧。软件触发会实际触发曝光；"
                "硬件触发会消费采集队列中的一帧。确认现场允许诊断采集？",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        self.sequence += 1
        self.captureButton.setEnabled(False)
        self.status.setText("采集中")
        self.pending = self.pool.submit(self.call, "capture")
        self.timer.start(40)

    def ready(self):
        if self.pending is None or not self.pending.done():
            return
        self.timer.stop()
        future, self.pending = self.pending, None
        try:
            result = future.result()
            if result.get("preview"):
                pixmap = QPixmap()
                if not pixmap.loadFromData(result["preview"], "JPEG"):
                    raise ValueError("诊断预览图像无效")
                self.preview.setPixmap(pixmap.scaled(self.preview.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))
                metadata = result["metadata"]
                acquisition = metadata["acquisition"]
                self.status.setText(f"帧 {acquisition['blockId']} · 周期 {metadata['cycleId']}\n"
                    f"曝光回读 {acquisition['actualExposureUs']} us（非逐帧曝光证据）")
            else:
                self.status.setText("诊断会话已就绪；尚未连接或触发相机")
        except Exception as error:
            self.status.setText(str(error))
        self.captureButton.setEnabled(bool(self.sessionId) and not self.closed)

    def dispose(self, _result=0):
        self.closed = True
        self.timer.stop()
        if self.sessionId:
            self.pool.submit(self.call, "close")
        self.pool.shutdown(wait=False)
