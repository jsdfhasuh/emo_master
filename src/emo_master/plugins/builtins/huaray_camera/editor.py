from __future__ import annotations

import queue
import threading
from typing import Any, cast

from PySide2.QtCore import QObject, QTimer
from PySide2.QtWidgets import QLabel, QPushButton, QWidget

from emo_master.plugins.builtins._editor_support import requiredChild, setImageLabel
from emo_master.plugins.builtins.huaray_camera.parameter_form import CameraParameterForm


class HuarayCameraEditorController:
    def __init__(self) -> None:
        self.root: Any = None
        self.context: Any = None
        self.form: CameraParameterForm | None = None
        self.previewLabel: QLabel | None = None
        self.connectionStatusLabel: QLabel | None = None
        self.blockIdLabel: QLabel | None = None
        self.timestampLabel: QLabel | None = None
        self.exposureLabel: QLabel | None = None
        self._sessionId = ""
        self._streamThread: threading.Thread | None = None
        self._streamLock = threading.Lock()
        self._streamCall: object | None = None
        self._stopEvent = threading.Event()
        self._frames: queue.Queue[object] = queue.Queue(maxsize=1)
        self._pollTimer: QTimer | None = None
        self._singleFrame = False

    def bind(self, rootWidget: object, context: Any) -> None:
        self.root = rootWidget
        self.context = context
        self.previewLabel = requiredChild(rootWidget, QLabel, "previewLabel")
        self.connectionStatusLabel = requiredChild(
            rootWidget, QLabel, "connectionStatusLabel"
        )
        self.blockIdLabel = requiredChild(rootWidget, QLabel, "blockIdLabel")
        self.timestampLabel = requiredChild(rootWidget, QLabel, "timestampLabel")
        self.exposureLabel = requiredChild(rootWidget, QLabel, "exposureLabel")
        placeholder = requiredChild(rootWidget, QWidget, "paramsPlaceholder")
        form = CameraParameterForm()
        form.setWorkflowOptions(context.workflowOptions)
        placeholder.layout().addWidget(form)
        self.form = form

        requiredChild(rootWidget, QPushButton, "connectButton").clicked.connect(
            lambda: self._startPreview(False)
        )
        requiredChild(rootWidget, QPushButton, "singleFrameButton").clicked.connect(
            lambda: self._startPreview(True)
        )
        requiredChild(rootWidget, QPushButton, "stopButton").clicked.connect(
            self._stopPreview
        )
        self._pollTimer = QTimer(cast(QObject, rootWidget))
        self._pollTimer.setInterval(50)
        self._pollTimer.timeout.connect(self._pollFrame)
        self._pollTimer.start()

    def loadParams(self, params: dict[str, object]) -> None:
        if self.form is not None:
            self.form.setSchema(self.context.paramSchema, params)

    def collectParams(self) -> dict[str, object]:
        return self.form.getValues() if self.form is not None else {}

    def validate(self) -> object:
        return None

    def onOpen(self) -> None:
        self.context.setStatus("相机预览待连接；预览临时使用自由采集")

    def onClose(self) -> None:
        self._stopPreview()

    def dispose(self) -> None:
        self._stopPreview()
        if self._pollTimer is not None:
            self._pollTimer.stop()

    def _startPreview(self, singleFrame: bool) -> None:
        self._stopPreview()
        self._singleFrame = singleFrame
        self._stopEvent.clear()
        try:
            self._sessionId = self.context.openLivePreview(self.collectParams())
        except Exception as err:
            self.context.setError(str(err))
            return
        if self.connectionStatusLabel is not None:
            self.connectionStatusLabel.setText("正在采集")
        self.context.setStatus(
            "正在采集单帧" if singleFrame else "实时预览中（最高每秒 10 帧）"
        )
        sessionId = self._sessionId
        self._streamThread = threading.Thread(
            target=self._streamFrames,
            args=(sessionId,),
            name=f"camera-preview-{sessionId}",
            daemon=True,
        )
        self._streamThread.start()

    def _streamFrames(self, sessionId: str) -> None:
        stream: Any = None
        failure = ""
        receivedFrame = False
        try:
            stream = self.context.streamLivePreview(sessionId)
            with self._streamLock:
                if self._stopEvent.is_set() or sessionId != self._sessionId:
                    _cancelStream(stream)
                    return
                self._streamCall = stream
            for frame in stream:
                if self._stopEvent.is_set() or sessionId != self._sessionId:
                    break
                receivedFrame = True
                while True:
                    try:
                        self._frames.get_nowait()
                    except queue.Empty:
                        break
                try:
                    self._frames.put_nowait(frame)
                except queue.Full:
                    pass
                if self._singleFrame:
                    break
        except Exception as err:
            failure = f"相机预览流失败：{err}"
            if not self._stopEvent.is_set() and sessionId == self._sessionId:
                self.context.log("ERROR", failure)
        finally:
            with self._streamLock:
                if self._streamCall is stream:
                    self._streamCall = None
            if (
                not self._stopEvent.is_set()
                and sessionId == self._sessionId
                and (failure or not self._singleFrame or not receivedFrame)
            ):
                while True:
                    try:
                        self._frames.get_nowait()
                    except queue.Empty:
                        break
                try:
                    self._frames.put_nowait((
                        "__stream_ended__",
                        failure or "相机预览流已结束，请检查设备连接和 Runtime 日志",
                    ))
                except queue.Full:
                    pass

    def _pollFrame(self) -> None:
        latest = None
        while True:
            try:
                latest = self._frames.get_nowait()
            except queue.Empty:
                break
        if latest is None:
            return
        if isinstance(latest, tuple) and latest[0] == "__stream_ended__":
            self.context.setError(str(latest[1]))
            self._stopPreview()
            return
        if self.previewLabel is not None:
            setImageLabel(self.previewLabel, bytes(getattr(latest, "jpeg", b"")))
        if self.blockIdLabel is not None:
            self.blockIdLabel.setText(f"帧号：{int(getattr(latest, 'block_id', 0))}")
        if self.timestampLabel is not None:
            self.timestampLabel.setText(
                f"设备时间戳：{int(getattr(latest, 'device_timestamp', 0))}"
            )
        if self.exposureLabel is not None:
            self.exposureLabel.setText(
                "实际曝光："
                f"{float(getattr(latest, 'actual_exposure_us', 0.0)):.3f} 微秒"
            )
        if self._singleFrame:
            self._stopPreview()

    def _stopPreview(self) -> None:
        self._stopEvent.set()
        with self._streamLock:
            stream = self._streamCall
        if stream is not None:
            _cancelStream(stream)
        sessionId = self._sessionId
        self._sessionId = ""
        if sessionId:
            try:
                self.context.closeLivePreview(sessionId)
            except Exception as err:
                self.context.log("WARN", f"相机预览释放失败：{err}")
        thread = self._streamThread
        self._streamThread = None
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=1.0)
        if self.connectionStatusLabel is not None:
            self.connectionStatusLabel.setText("未连接")


def _cancelStream(stream: object) -> None:
    cancel = getattr(stream, "cancel", None)
    if callable(cancel):
        cancel()
