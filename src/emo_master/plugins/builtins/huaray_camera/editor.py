from __future__ import annotations

import queue
import threading
from typing import Any, cast

from PySide2.QtCore import QObject, QTimer
from PySide2.QtWidgets import QLabel, QPushButton, QWidget

from emo_master.apps.designer.ui.param_form import SchemaParamForm
from emo_master.plugins.builtins._editor_support import requiredChild, setImageLabel


class HuarayCameraEditorController:
    def __init__(self) -> None:
        self.root: Any = None
        self.context: Any = None
        self.form: SchemaParamForm | None = None
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
        form = SchemaParamForm()
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
        self.context.setStatus("相机预览待连接；预览会临时使用 freeRun")

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
            "正在采集单帧" if singleFrame else "实时预览中（最高 10 FPS）"
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
            self.context.log("ERROR", f"相机预览流失败：{err}")
        finally:
            with self._streamLock:
                if self._streamCall is stream:
                    self._streamCall = None
            if not self._stopEvent.is_set() and not self._singleFrame:
                while True:
                    try:
                        self._frames.get_nowait()
                    except queue.Empty:
                        break
                try:
                    self._frames.put_nowait("__stream_ended__")
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
        if latest == "__stream_ended__":
            self.context.setError("相机预览流已结束，请检查设备连接和 Runtime 日志")
            self._stopPreview()
            return
        if self.previewLabel is not None:
            setImageLabel(self.previewLabel, bytes(getattr(latest, "jpeg", b"")))
        if self.blockIdLabel is not None:
            self.blockIdLabel.setText(f"blockId: {int(getattr(latest, 'block_id', 0))}")
        if self.timestampLabel is not None:
            self.timestampLabel.setText(
                f"timestamp: {int(getattr(latest, 'device_timestamp', 0))}"
            )
        if self.exposureLabel is not None:
            self.exposureLabel.setText(
                "actualExposureUs: "
                f"{float(getattr(latest, 'actual_exposure_us', 0.0)):.3f}"
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
