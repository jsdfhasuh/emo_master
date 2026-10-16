from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import queue
import threading
from typing import Any
from uuid import uuid4

from PySide2.QtCore import Qt, QSignalBlocker, QTimer
from PySide2.QtWidgets import (QDialog, QDialogButtonBox, QLabel, QListWidget, QListWidgetItem,
                              QPushButton, QTableWidget, QTableWidgetItem,
                              QHeaderView, QAbstractItemView, QVBoxLayout, QWidget)

from emo_master.apps.designer.ui.icon_map import icon
from emo_master.plugins.builtins._editor_support import requiredChild, setImageLabel
from emo_master.plugins.builtins.huaray_camera.configuration import PARAM_GROUPS, SOFTWARE_FIELDS
from emo_master.plugins.builtins.huaray_camera.operator import DEFAULTS, HuarayCameraOperator
from emo_master.plugins.builtins.huaray_camera.parameter_form import CameraParameterForm, ENUM_LABELS, FIELD_LABELS, GROUPS, PARAM_FEATURES


STATES = {"connecting": "正在连接", "connected": "已连接，采集已停止", "acquiring": "自由采集中",
          "waiting": "等待图片", "waitingNext": "等待下一张图片", "softwareReady": "等待软件触发",
          "softwareWaiting": "软件触发已发送，等待图片", "fault": "故障，采集已停止",
          "closed": "未连接", "abandoned": "已关闭，存在未恢复调整"}


class HuarayCameraEditorController:
    def __init__(self) -> None:
        self.root: Any = None
        self.context: Any = None
        self.form: CameraParameterForm | None = None
        self._sessionId = ""
        self._savedParams: dict = {}
        self._draftParams: dict = {}
        self.previewLabel: Any = None
        self.connectionStatusLabel: Any = None
        self.blockIdLabel: Any = None
        self.timestampLabel: Any = None
        self.exposureLabel: Any = None
        self._snapshot: dict = {}
        self._previewMode = "freeRun"
        self._pending: Any = None
        self._unknownRequest: Any = None
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="camera-editor-request")
        self._frames: queue.Queue = queue.Queue(maxsize=1)
        self._stopEvent = threading.Event()
        self._streamThread: threading.Thread | None = None
        self._streamCall: Any = None
        self._pollTimer: Any = None
        self._restoreFailed = False
        self._disposed = False
        self._modalBusy = False

    def bind(self, rootWidget: Any, context: Any) -> None:
        self.root, self.context = rootWidget, context
        for name in ("previewLabel", "connectionStatusLabel", "blockIdLabel", "timestampLabel", "exposureLabel"):
            setattr(self, name, requiredChild(rootWidget, QLabel, name))
        placeholder = requiredChild(rootWidget, QWidget, "paramsPlaceholder")
        self.form = CameraParameterForm()
        self.form.setWorkflowOptions(context.workflowOptions)
        placeholder.layout().addWidget(QLabel("常用属性"))
        placeholder.layout().addWidget(self.form)
        self.form.committed.connect(self._committed)
        self.connectButton = requiredChild(rootWidget, QPushButton, "connectButton")
        self.singleFrameButton = requiredChild(rootWidget, QPushButton, "singleFrameButton")
        self.stopButton = requiredChild(rootWidget, QPushButton, "stopButton")
        self.connectButton.setText("连接相机")
        self.connectButton.clicked.connect(self._connect)
        self.singleFrameButton.clicked.connect(self._single)
        self.stopButton.clicked.connect(self._stopPreview)
        controls = QVBoxLayout()
        from PySide2.QtWidgets import QHBoxLayout
        row = QHBoxLayout()
        self.previewButton = self._button("开始预览", "camera", lambda: self._startPreview(False), row)
        self.refreshButton = self._button("核查实际值", "refresh-cw", self._refresh, row)
        self.tryButton = self._button("试用节点配置", "settings", self._trySaved, row)
        self.disconnectButton = self._button("断开相机", "unplug", self._disconnect, row)
        row.addStretch(1)
        controls.addLayout(row)
        self.root.layout().insertLayout(1, controls)
        self._pollTimer = QTimer(rootWidget)
        self._pollTimer.setInterval(30)
        self._pollTimer.timeout.connect(self._pollFrame)
        self._pollTimer.start()
        self._updateControls()

    def _button(self, text: str, iconName: str, handler: Any, layout: Any) -> QPushButton:
        button = QPushButton(text)
        button.setIcon(icon(iconName))
        button.setToolTip(text)
        button.clicked.connect(handler)
        layout.addWidget(button)
        return button

    def loadParams(self, params: dict[str, object]) -> None:
        self._savedParams = dict(params)
        self._draftParams = dict(params)
        if self.form is not None and not self._sessionId and self._pending is None:
            self.form.setSchema(self.context.paramSchema, {**params, "triggerMode": "freeRun"})

    def collectParams(self) -> dict[str, object]:
        # Actual device values and preview defaults are never implicit project edits.
        return dict(self._draftParams)

    def validate(self) -> object:
        if self._pending is not None or self._unknownRequest is not None:
            return "相机请求尚未完成"
        if self.form is not None:
            message = self.form.validationMessage()
            if message:
                return message
        return HuarayCameraOperator().validateParams(self._draftParams)

    def onOpen(self) -> None:
        self.context.setStatus("未连接")

    def _connectTask(self, operation: Any) -> dict:
        sessionId = operation()
        try:
            result = self.context.cameraConfiguration(sessionId, "", "", "read", {})
        except Exception as err:
            result = {"ok": False, "code": "E_CAMERA_RESULT_UNKNOWN", "message": str(err)}
        result["sessionId"] = sessionId
        return result

    def _connect(self) -> None:
        if self._sessionId or self._pending is not None or self.form is None:
            return
        try:
            values = self.form.getValues()
            params = {key: values[key] for key in GROUPS[0][1] if key in values}
            operation = self.context.prepareCameraConfiguration(params)
        except Exception as err:
            self.context.setError(str(err))
            return
        self._pending = ("connect", self._executor.submit(self._connectTask, operation))
        self.connectionStatusLabel.setText("正在连接")
        self._updateControls()

    def _request(self, action: str, payload: dict | None = None, requestId: str = "") -> None:
        if not self._sessionId or self._pending is not None or self._modalBusy:
            return
        requestId = requestId or str(uuid4())
        data = dict(payload or {})
        self._pending = (action, self._executor.submit(self.context.cameraConfiguration,
            self._sessionId, self._snapshot.get("identity", ""), requestId, action, data))
        self._lastRequest = (action, data, requestId)
        self._updateControls()

    def _committed(self, key: str, value: object) -> None:
        if self._pending is not None or self._modalBusy:
            return
        if key == "triggerMode":
            self._previewMode = str(value)
            self._updateControls()
        if self._sessionId and (key in PARAM_GROUPS or key in SOFTWARE_FIELDS):
            try:
                values = self.form.getValues() if self.form else {}
            except ValueError as error:
                self.context.setError(str(error))
                return
            group = PARAM_GROUPS.get(key)
            fields = {name: values[name] for name, g in PARAM_GROUPS.items() if g == group and name in values} if group else {key: value}
            if group == "trigger":
                fields = {key: value}
                if key == "triggerMode" and value == "hardware":
                    fields["triggerSource"] = values["triggerSource"]
            # Do not write an unrelated mode merely because its defaults are visible.
            self._request("apply", fields)
        elif not self._sessionId or key in {"waitMode", "sequencePolicy"}:
            if self._draftParams.get(key, DEFAULTS.get(key)) == value:
                return
            self._draftParams[key] = value
            self.context.markDirty()

    def describeUnsavedChanges(self, original: dict, current: dict) -> list[tuple]:
        rows = [(FIELD_LABELS.get(key, key), ENUM_LABELS.get(str(original.get(key, DEFAULTS.get(key))), str(original.get(key, DEFAULTS.get(key)))),
                 ENUM_LABELS.get(str(current.get(key, DEFAULTS.get(key))), str(current.get(key, DEFAULTS.get(key)))), "节点待保存值")
                for key in sorted(original.keys() | current.keys())
                if original.get(key, DEFAULTS.get(key)) != current.get(key, DEFAULTS.get(key))]
        labels = {feature: FIELD_LABELS[key] for key, feature in PARAM_FEATURES.items()}
        for key, before in self._snapshot.get("original", {}).items():
            actual = self._snapshot.get("actual", {}).get(key, "未知")
            if actual != before:
                rows.append((labels.get(key, key), before, actual, "相机会话实际值"))
        return rows

    def _startPreview(self, singleFrame: bool = False) -> None:
        self._request("start", {"triggerMode": self._previewMode, "single": singleFrame})

    def _single(self) -> None:
        if self._previewMode == "software":
            self._request("trigger")
        elif self._previewMode == "freeRun":
            self._startPreview(True)

    def _stopPreview(self) -> None:
        self._request("stop")

    def _refresh(self) -> None:
        if self._unknownRequest:
            action, payload, requestId = self._unknownRequest
            self._request(action, payload, requestId)
        else:
            self._request("recover" if self._snapshot.get("failed") else "read")

    def _trySaved(self) -> None:
        self._previewMode = str(self._savedParams.get("triggerMode", "freeRun"))
        self._request("apply", {key: value for key, value in self._savedParams.items()
                                if key in PARAM_GROUPS or key in SOFTWARE_FIELDS})

    def _acceptResult(self, result: dict) -> bool:
        snapshot = result.get("snapshot", {})
        if snapshot:
            self._snapshot = snapshot
            if self.form is not None:
                self.form.showSnapshot(snapshot)
                control = self.form._controls.get("triggerMode")
                if control is not None:
                    blocker = QSignalBlocker(control)
                    control.setCurrentIndex(control.findData(self._previewMode))
                    del blocker
            self.connectionStatusLabel.setText(STATES.get(snapshot.get("state", "connected"), "已连接"))
            if result.get("ok") and not snapshot.get("failed"):
                self.context.setStatus(self.connectionStatusLabel.text())
        if result.get("warnings"):
            warnings = result["warnings"]
            self.context.setError("发现上次未完成的相机调整，请核查实际配置：" + str(warnings))
        if not result.get("ok"):
            self.context.setError("相机操作失败：" + str(result.get("message", "未知错误")))
            if result.get("code") == "E_CAMERA_RESULT_UNKNOWN" and not snapshot:
                self._unknownRequest = getattr(self, "_lastRequest", None)
            else:
                self._unknownRequest = None
            return False
        self._unknownRequest = None
        return True

    def _pollFrame(self) -> None:
        if self._pending is not None and self._pending[1].done():
            action, future = self._pending
            self._pending = None
            try:
                result = future.result()
                if action == "connect" and result.get("sessionId"):
                    self._sessionId = result["sessionId"]
                    self._stopEvent = threading.Event()
                    self._streamThread = threading.Thread(target=self._streamFrames,
                        args=(self._sessionId, self._stopEvent), daemon=True, name="camera-preview-stream")
                    self._streamThread.start()
                wasUnknown = self._unknownRequest is not None
                accepted = self._acceptResult(result)
                if accepted and wasUnknown and action != "read":
                    if action == "close":
                        self._finishSession()
                    else:
                        self._request("read")
            except Exception as err:
                self.context.setError(str(err))
                if action != "connect":
                    self._unknownRequest = getattr(self, "_lastRequest", None)
            self._updateControls()
        latest = None
        while True:
            try:
                latest = self._frames.get_nowait()
            except queue.Empty:
                break
        if latest is None:
            return
        if isinstance(latest, tuple):
            self.context.setError(str(latest[1]))
            self._snapshot.update(state="fault", failed=True)
            self._updateControls()
            return
        if str(getattr(latest, "session_id", "")) != self._sessionId:
            return
        if int(getattr(latest, "generation", 0)) < int(self._snapshot.get("generation", 0)):
            return
        sequence = int(getattr(latest, "sequence", 0))
        if sequence <= int(self._snapshot.get("sequence", 0)):
            return
        state = str(getattr(latest, "state", ""))
        self._snapshot.update(state=state, generation=int(getattr(latest, "generation", 0)), sequence=sequence)
        self.connectionStatusLabel.setText(STATES.get(state, "已连接"))
        if getattr(latest, "code", ""):
            self._snapshot["failed"] = True
            self.context.setError(str(latest.message))
        elif self._unknownRequest is None:
            self.context.setStatus(self.connectionStatusLabel.text())
        if getattr(latest, "jpeg", b""):
            setImageLabel(self.previewLabel, bytes(latest.jpeg))
            self.blockIdLabel.setText(f"帧号：{int(latest.block_id)}")
            self.timestampLabel.setText(f"设备时间戳：{int(latest.device_timestamp)}")
            self.exposureLabel.setText(f"实际曝光：{float(latest.actual_exposure_us):.3f} 微秒")
        self._updateControls()

    def _streamFrames(self, sessionId: str, stopped: threading.Event | None = None) -> None:
        stopped = stopped or self._stopEvent
        try:
            stream = self.context.streamCameraConfiguration(sessionId)
            self._streamCall = stream
            for frame in stream:
                if stopped.is_set():
                    break
                self._putFrame(frame)
            if not stopped.is_set() and self._snapshot.get("state") not in {"closed", "abandoned"}:
                self._putFrame(("error", "相机预览流已结束，请核查设备和 Runtime 状态"))
        except Exception as err:
            if not stopped.is_set():
                self._putFrame(("error", f"相机预览流失败：{err}"))

    def _putFrame(self, frame: object) -> None:
        try:
            self._frames.put_nowait(frame)
        except queue.Full:
            try:
                self._frames.get_nowait()
            except queue.Empty:
                pass
            try:
                self._frames.put_nowait(frame)
            except queue.Full:
                pass

    def _updateControls(self) -> None:
        if self.root is None:
            return
        busy = self._pending is not None or self._modalBusy
        connected = bool(self._sessionId)
        failed = bool(self._snapshot.get("failed")) or self._unknownRequest is not None
        state = self._snapshot.get("state", "")
        self.connectButton.setEnabled(not connected and not busy)
        self.previewButton.setEnabled(connected and not busy and not failed and state == "connected")
        self.stopButton.setEnabled(connected and not busy)
        self.tryButton.setEnabled(connected and not busy and not failed)
        self.refreshButton.setEnabled(connected and not busy)
        self.disconnectButton.setEnabled(connected and not busy)
        self.singleFrameButton.setVisible(self._previewMode != "hardware")
        self.singleFrameButton.setText("软件触发一次" if self._previewMode == "software" else "单帧")
        self.singleFrameButton.setEnabled(connected and not busy and not failed and
                                         (self._previewMode != "software" or state == "softwareReady"))
        if self.form is not None:
            self.form.setBusy(busy or (connected and failed))

    def _modalAction(self, action: str, payload: dict, requestId: str = "") -> dict:
        self._modalBusy = True
        progress = QDialog(self.root.window())
        progress.setWindowTitle("相机配置")
        QVBoxLayout(progress).addWidget(QLabel("正在读取或处理相机配置…"))
        progress.setWindowFlag(Qt.WindowCloseButtonHint, False)
        progress.setWindowModality(Qt.ApplicationModal)
        requestId = requestId or str(uuid4())
        self._lastRequest = (action, dict(payload), requestId)
        future = self._executor.submit(self.context.cameraConfiguration, self._sessionId,
                                      self._snapshot.get("identity", ""), requestId, action, payload)
        timer = QTimer(progress)
        timer.setInterval(20)
        timer.timeout.connect(lambda: progress.accept() if future.done() else None)
        self.root.setEnabled(False)
        timer.start()
        while not future.done():
            progress.exec_()
        timer.stop()
        self.root.setEnabled(True)
        self._modalBusy = False
        try:
            result = future.result()
        except Exception as err:
            result = {"ok": False, "code": "E_CAMERA_RESULT_UNKNOWN", "message": str(err)}
        self._acceptResult(result)
        return result

    def requestClose(self) -> bool:
        if self._pending is not None or self._modalBusy:
            self.context.setError("相机请求处理中，请稍后关闭")
            return False
        if not self._sessionId:
            return True
        if self._unknownRequest is not None:
            action, payload, requestId = self._unknownRequest
            result = self._modalAction(action, payload, requestId)
            if not result.get("ok") and self._unknownRequest is not None:
                return False
            if action == "close" and result.get("ok"):
                self._finishSession()
                return True
            if action == "close":
                self._restoreFailed = True
        if not self._modalAction("read", {}).get("ok"):
            self._restoreFailed = True
        decision = ""
        if self._snapshot.get("pending") or self._restoreFailed:
            dialog = QDialog(self.root.window())
            dialog.setWindowTitle("确认相机调整")
            dialog.resize(700, 440)
            layout = QVBoxLayout(dialog)
            message = QLabel("恢复失败或设备不可达，部分属性可能仍未恢复。" if self._restoreFailed else "本次试调已实时生效。请选择保留或恢复原配置。")
            message.setWordWrap(True)
            layout.addWidget(message)
            original, actual = self._snapshot.get("original", {}), self._snapshot.get("actual", {})
            records = {record["property"]: record for record in self._snapshot.get("records", [])}
            labels = {feature: FIELD_LABELS[key] for key, feature in PARAM_FEATURES.items()}
            labels["TriggerSelector"] = "触发选择器"
            labels["AcquisitionMode"] = "设备采集方式"
            statuses = {"confirmed": "已确认", "failed": "失败，已回读", "unknown": "结果未知"}
            rows = [(labels.get(key, key), value, actual.get(key, "未知"),
                     statuses.get(records.get(key, {}).get("status", ""), "未写入")) for key, value in original.items()]
            rows += [(FIELD_LABELS.get(key, key), value, self._snapshot.get("software", {}).get(key, "未知"), "软件处理设置")
                     for key, value in self._snapshot.get("originalSoftware", {}).items()
                     if value != self._snapshot.get("software", {}).get(key)]
            table = QTableWidget(len(rows), 4)
            table.setHorizontalHeaderLabels(["属性", "原始值", "当前实际值", "写入状态"])
            table.setEditTriggers(QAbstractItemView.NoEditTriggers)
            table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
            for row, values in enumerate(rows):
                for column, value in enumerate(values):
                    display = "连续采集" if values[0] == "设备采集方式" and value == "Continuous" else ENUM_LABELS.get(str(value), str(value))
                    table.setItem(row, column, QTableWidgetItem(display))
            layout.addWidget(table)
            buttons = QDialogButtonBox()
            retain = buttons.addButton("放弃恢复并关闭" if self._restoreFailed else "保留调整", QDialogButtonBox.DestructiveRole)
            restore = buttons.addButton("重试恢复" if self._restoreFailed else "放弃并恢复", QDialogButtonBox.AcceptRole)
            back = buttons.addButton("返回" if self._restoreFailed else "继续调整", QDialogButtonBox.RejectRole)
            back.setDefault(True)
            selected = [""]

            def choose(decision: str) -> None:
                selected[0] = decision
                dialog.accept()

            retain.clicked.connect(lambda: choose("retain"))
            restore.clicked.connect(lambda: choose("restore"))
            back.clicked.connect(dialog.reject)
            layout.addWidget(buttons)
            dialog.exec_()
            if selected[0] == "restore":
                decision = "restore"
            elif selected[0] == "retain":
                decision = "abandon" if self._restoreFailed else "retain"
                if decision == "abandon" and not self._confirmAbandon():
                    return False
            else:
                return False
        result = self._modalAction("close", {"decision": decision})
        if not result.get("ok"):
            self._restoreFailed = True
            return False
        self._finishSession()
        return True

    def _confirmAbandon(self) -> bool:
        dialog = QDialog(self.root.window())
        dialog.setWindowTitle("未恢复的相机配置")
        layout = QVBoxLayout(dialog)
        message = QLabel("放弃恢复不会撤销已生效的修改，设备可能仍处于试调状态。仍然关闭？")
        message.setWordWrap(True)
        layout.addWidget(message)
        buttons = QDialogButtonBox()
        buttons.addButton("放弃恢复并关闭", QDialogButtonBox.DestructiveRole).clicked.connect(dialog.accept)
        buttons.addButton("返回", QDialogButtonBox.RejectRole).clicked.connect(dialog.reject)
        layout.addWidget(buttons)
        return dialog.exec_() == QDialog.Accepted

    def _finishSession(self) -> None:
        self._endStream()
        self._sessionId = ""
        self._snapshot = {}
        self._restoreFailed = False
        if self.form:
            self.form.showSnapshot({})
        self._updateControls()

    def _disconnect(self) -> None:
        self.requestClose()

    def collectParamsForSave(self) -> dict | None:
        if self._pending is not None or self._unknownRequest is not None:
            self.context.setError("相机请求尚未完成")
            return None
        if not self._sessionId:
            return dict(self._draftParams)
        if self._snapshot.get("failed") or not self._modalAction("read", {}).get("ok"):
            self.context.setError("请先核查相机实际结果，再保存节点配置")
            return None
        actual = dict(self._snapshot.get("params", {}))
        if self.form:
            inputs = self.form.getValues()
            actual.update({key: inputs[key] for key in (*GROUPS[0][1], "waitMode", "sequencePolicy") if key in inputs})
        dialog = QDialog(self.root.window())
        dialog.setWindowTitle("选择加入节点配置的属性")
        dialog.resize(560, 420)
        layout = QVBoxLayout(dialog)
        choices = QListWidget()
        groups = [("连接设置", GROUPS[0][1]), ("触发设置", ("triggerMode", "triggerSource", "triggerActivation")),
                  ("曝光设置", ("exposureMode", "exposureUs")), ("增益设置", ("gainMode", "gainRaw")),
                  ("帧率设置", ("frameRateMode", "frameRate")),
                  ("采集区域与像素格式", ("roiMode", "width", "height", "offsetX", "offsetY", "pixelFormat"))]
        groups.append(("工作流取图策略", ("waitMode", "sequencePolicy")))
        groups.extend((FIELD_LABELS[key], (key,)) for key in sorted(SOFTWARE_FIELDS))
        for title, keys in groups:
            if all(key in actual for key in keys):
                item = QListWidgetItem(title + "：" + ", ".join(str(actual[key]) for key in keys))
                item.setData(Qt.UserRole, list(keys))
                item.setCheckState(Qt.Unchecked)
                choices.addItem(item)
        layout.addWidget(choices)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setText("加入节点配置")
        buttons.button(QDialogButtonBox.Cancel).setText("取消")
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec_() != QDialog.Accepted:
            return None
        params = dict(self._draftParams)
        for index in range(choices.count()):
            item = choices.item(index)
            if item.checkState() == Qt.Checked:
                params.update({key: actual[key] for key in item.data(Qt.UserRole)})
        return params

    def onParamsSaved(self, params: dict) -> None:
        self._savedParams = dict(params)
        self._draftParams = dict(params)

    def _endStream(self) -> None:
        self._stopEvent.set()
        cancel = getattr(self._streamCall, "cancel", None)
        if callable(cancel):
            cancel()
        thread = self._streamThread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=0.5)
            if not thread.is_alive():
                self._streamThread = None
        self._streamCall = None

    def onClose(self) -> None:
        self._endStream()

    def dispose(self) -> None:
        if self._disposed:
            return
        self._disposed = True
        self._endStream()
        if self._pollTimer:
            self._pollTimer.stop()
        # requestClose has completed device work; release worker-held controller
        # references before Qt destroys the editor's widgets.
        self._executor.shutdown(wait=True)
