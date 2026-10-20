from __future__ import annotations
from dataclasses import replace
from typing import Any

from PySide2.QtCore import Signal, QSignalBlocker
from PySide2.QtWidgets import QComboBox, QDoubleSpinBox, QFormLayout, QLabel, QLineEdit, QSpinBox, QVBoxLayout, QWidget

from emo_master.apps.designer.ui.param_form import SchemaParamForm, applyParameterLabel, _valueError


FIELD_LABELS = {
    "selectionMode": "连接方式",
    "ipAddress": "相机 IP 地址",
    "cameraKey": "设备唯一标识",
    "userId": "用户自定义名称",
    "deviceIndex": "设备索引",
    "triggerMode": "触发模式",
    "triggerSource": "触发输入线",
    "triggerActivation": "触发边沿",
    "captureTimeoutMs": "取图超时（毫秒）",
    "waitMode": "取图等待方式",
    "sequencePolicy": "设备帧号检查",
    "retryCount": "异常重试次数",
    "retryDelayMs": "重试间隔（毫秒）",
    "outputColor": "输出颜色",
    "demosaic": "去马赛克算法",
    "pixelFormat": "相机像素格式",
    "exposureMode": "曝光设置",
    "exposureUs": "曝光时间（微秒）",
    "gainMode": "增益设置",
    "gainRaw": "增益值",
    "frameRateMode": "帧率设置",
    "frameRate": "采集帧率（帧/秒）",
    "roiMode": "采集区域",
    "width": "图像宽度（像素）",
    "height": "图像高度（像素）",
    "offsetX": "水平偏移（像素）",
    "offsetY": "垂直偏移（像素）",
}

GROUPS = (
    ("连接设置", ("selectionMode", "ipAddress", "cameraKey", "userId", "deviceIndex")),
    ("触发与重试", ("triggerMode", "triggerSource", "triggerActivation", "waitMode", "captureTimeoutMs", "sequencePolicy", "retryCount", "retryDelayMs")),
    ("图像输出", ("outputColor", "pixelFormat", "demosaic")),
    ("曝光、增益与帧率", ("exposureMode", "exposureUs", "gainMode", "gainRaw", "frameRateMode", "frameRate")),
    ("采集区域", ("roiMode", "width", "height", "offsetX", "offsetY")),
)

ENUM_LABELS = {
    "Continuous": "连续自动", "Once": "单次自动", "Off": "关闭", "On": "开启",
    "FrameStart": "帧开始", "SingleFrame": "单帧",
    "ip": "按 IP 地址", "cameraKey": "按设备唯一标识",
    "userId": "按用户自定义名称", "index": "按设备索引",
    "hardware": "硬件触发", "software": "软件触发", "freeRun": "自由采集",
    "bounded": "有界超时（兼容默认）", "contiguous": "严格连续，重复或缺帧停止", "off": "不检查（兼容默认）",
    "Line1": "输入线 1", "Line2": "输入线 2", "Line3": "输入线 3", "Line4": "输入线 4",
    "RisingEdge": "上升沿", "FallingEdge": "下降沿",
    "bgr": "BGR 三通道", "gray": "灰度图像",
    "nearest": "最近邻", "bilinear": "双线性插值", "edgeSensing": "边缘感知",
    "keep": "保持相机当前设置", "manual": "手动设置",
    "custom": "自定义区域", "full": "完整画幅",
    "Mono8": "8 位灰度（Mono8）", "RGB8": "8 位 RGB", "BGR8": "8 位 BGR",
    "BayerGR8": "8 位拜耳 GR", "BayerRG8": "8 位拜耳 RG",
    "BayerGB8": "8 位拜耳 GB", "BayerBG8": "8 位拜耳 BG",
}

FIELD_HINTS = {
    "deviceIndex": "从 0 开始的枚举索引；枚举顺序可能变化。",
    "triggerMode": "预览使用当前选择；软件触发由按钮发起，硬件触发等待外部信号。",
    "triggerSource": "仅硬件触发有效，需与实际接线一致。",
    "triggerActivation": "仅硬件触发有效，且需要相机支持。",
    "captureTimeoutMs": "软件触发和自由采集的取图超时；硬件预览无信号时持续等待。",
    "waitMode": "工作流持续硬件等待模式必须关闭异常重试；不会自动重连或推测下一拍。",
    "retryCount": "0 表示不重试。连接或传输异常可重试，单纯取图超时不重试。",
    "retryDelayMs": "仅异常重试次数大于 0 时有效。",
    "outputColor": "输出通道格式；单色相机输出 BGR 仍是灰度内容。",
    "demosaic": "用于拜耳原始图像的颜色插值；是否生效取决于相机实际像素格式。",
    "pixelFormat": "保持当前设置时不修改相机像素格式；其他选项需相机支持。",
    "exposureUs": "仅手动曝光时写入相机。",
    "gainRaw": "仅手动增益时写入相机，范围由相机决定。",
    "frameRate": "仅手动帧率时写入相机，不代表预览显示帧率。",
    "roiMode": "保持当前设置时不修改区域；完整画幅使用设备最大尺寸与零偏移。",
}

PARAM_FEATURES = {
    "triggerMode": "TriggerMode", "triggerSource": "TriggerSource",
    "triggerActivation": "TriggerActivation", "pixelFormat": "PixelFormat",
    "exposureMode": "ExposureAuto", "exposureUs": "ExposureTime",
    "gainMode": "GainAuto", "gainRaw": "GainRaw",
    "frameRateMode": "AcquisitionFrameRateEnable", "frameRate": "AcquisitionFrameRate",
    "roiMode": "Width", "width": "Width", "height": "Height",
    "offsetX": "OffsetX", "offsetY": "OffsetY",
}


class CameraParameterForm(SchemaParamForm):
    committed: Any = Signal(str, object)

    def __init__(self) -> None:
        super().__init__()
        self._connected = False
        self._busy = False
        self._unavailable: set[str] = set()
        self._readbackLabels: dict[str, QLabel] = {}
        self._baseRanges: dict[str, tuple] = {}
        self._baseSchemas: dict[str, dict] = {}
        self.setStyleSheet(
            "QLabel:disabled { color: #7b8490; }"
            "QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled, "
            "QDoubleSpinBox:disabled { color: #7b8490; background-color: #edf0f3; }"
        )

    def setSchema(self, paramSchema: dict[str, object], values: dict[str, object]) -> None:
        properties = paramSchema.get("properties", {})
        if not isinstance(properties, dict):
            properties = {}
        ordered = {
            name: properties[name]
            for _, names in GROUPS for name in names if name in properties
        }
        ordered.update({name: value for name, value in properties.items() if name not in ordered})
        super().setSchema(dict(paramSchema, properties=ordered), values)
        self._baseRanges = {}
        self._baseSchemas = {name: dict(field.schema) for name, field in self._fieldsByName.items()}
        self._layout.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        self._layout.setRowWrapPolicy(QFormLayout.WrapLongRows)
        row = 0
        for title, names in GROUPS:
            present = [name for name in names if name in self._controls]
            if present:
                heading = QLabel(title)
                font = heading.font()
                font.setBold(True)
                heading.setFont(font)
                heading.setContentsMargins(0, 12 if row else 0, 0, 4)
                self._layout.insertRow(row, heading)
                row += len(present) + 1
        for name, control in self._controls.items():
            label = self._layout.labelForField(control)
            if isinstance(label, QLabel):
                applyParameterLabel(label, control, name, paramSchema, FIELD_HINTS.get(name, ""))
            control.setObjectName(name)
            if isinstance(control, QComboBox):
                for index in range(control.count()):
                    value = str(control.itemData(index))
                    labels = properties.get(name, {}).get("xOptionLabels", {})
                    control.setItemText(index, labels.get(value, ENUM_LABELS.get(value, value)))
                control.currentIndexChanged.connect(self._updateEnabledFields)
                control.activated.connect(lambda _index, key=name: self._commitField(key))
            elif isinstance(control, QSpinBox) and name == "retryCount":
                control.valueChanged.connect(self._updateEnabledFields)
            if isinstance(control, (QSpinBox, QDoubleSpinBox, QLineEdit)):
                if isinstance(control, (QSpinBox, QDoubleSpinBox)):
                    control.setKeyboardTracking(False)
                    self._baseRanges[name] = (control.minimum(), control.maximum(), control.singleStep())
                control.editingFinished.connect(lambda key=name: self._commitField(key))
        self._readbackLabels = {}
        for title, names in GROUPS[1:]:
            label = QLabel("")
            label.setWordWrap(True)
            label.setObjectName("cameraActualValues")
            summary = QWidget()
            layout = QVBoxLayout(summary)
            layout.setContentsMargins(0, 8, 0, 0)
            layout.addWidget(QLabel(title + "实际值"))
            layout.addWidget(label)
            self._layout.addRow(summary)
            for name in names:
                self._readbackLabels[name] = label
        self._updateEnabledFields()

    def _commitField(self, name: str) -> None:
        control = self._controls[name]
        if self._busy or not control.isEnabled():
            return
        field = self._fieldsByName[name]
        try:
            value = self._readControlValue(control, field)
            message = _valueError(value, field.schema, FIELD_LABELS.get(name, name))
        except ValueError as error:
            message = str(error)
        if message:
            self._showFieldError(name, message)
            return
        self._showFieldError(name, "")
        self.committed.emit(name, value)

    def setBusy(self, busy: bool) -> None:
        self._busy = busy
        self._updateEnabledFields()

    def showSnapshot(self, snapshot: dict, *, updateInputs: bool = True) -> None:
        from emo_master.plugins.builtins.huaray_camera.configuration import GROUPS as DEVICE_GROUPS, PARAM_GROUPS
        self._connected = bool(snapshot.get("identity"))
        self._unavailable.clear()
        properties = snapshot.get("properties", {})
        if self._connected:
            for key, group in PARAM_GROUPS.items():
                required = DEVICE_GROUPS[group] if group != "trigger" else {
                    "triggerMode": ("TriggerMode",),
                    "triggerSource": ("TriggerMode", "TriggerSource"),
                    "triggerActivation": ("TriggerMode", "TriggerSource", "TriggerActivation"),
                }[key]
                if any(not properties.get(feature, {}).get("readable") for feature in required):
                    self._unavailable.add(key)
                if group == "trigger" and snapshot.get("actual", {}).get("TriggerSelector") != "FrameStart":
                    self._unavailable.add(key)
                if group in {"exposure", "gain"} and snapshot.get("actual", {}).get(
                        "ExposureAuto" if group == "exposure" else "GainAuto") == "Once":
                    self._unavailable.add(key)
        acquiring = snapshot.get("state") in {"acquiring", "waiting", "waitingNext", "softwareReady", "softwareWaiting"}
        for key, feature in PARAM_FEATURES.items():
            control = self._controls.get(key)
            if control is None:
                continue
            metadata = properties.get(feature, {})
            if self._connected and not acquiring and not metadata.get("writable"):
                self._unavailable.add(key)
            hint = FIELD_HINTS.get(key, FIELD_LABELS[key])
            if key in self._unavailable:
                hint += "\n当前属性或关联属性不可写，或无法可靠恢复。"
            elif acquiring and not metadata.get("writable"):
                hint += "\n修改时暂停采集并重新检查设备权限。"
            schema = dict(self._baseSchemas[key])
            if schema.get("type") in {"integer", "number"}:
                schema.update({name: metadata[name] for name in ("minimum", "maximum") if name in metadata})
                self._fieldsByName[key] = replace(self._fieldsByName[key], schema=schema)
                if metadata.get("step"):
                    hint += "\n设备步长：" + str(metadata["step"])
            label = self._layout.labelForField(control)
            if isinstance(label, QLabel):
                applyParameterLabel(label, control, key, {"properties": {key: schema}}, hint)
            blocker = QSignalBlocker(control)
            if isinstance(control, (QSpinBox, QDoubleSpinBox)):
                low, high, step = self._baseRanges[key]
                control.setRange(metadata.get("minimum", low), metadata.get("maximum", high))
                control.setSingleStep(metadata.get("step", step) or step)
            if isinstance(control, QComboBox):
                options = metadata.get("options")
                directEnum = key in {"triggerSource", "triggerActivation", "pixelFormat"}
                for index in range(control.count()):
                    item = control.model().item(index)
                    if item is not None:
                        item.setEnabled(not directEnum or options is None or control.itemData(index) == "keep"
                                        or control.itemData(index) in options)
            del blocker
        values = snapshot.get("params", {})
        if updateInputs:
            for key, value in values.items():
                control = self._controls.get(key)
                if control is None:
                    continue
                blocker = QSignalBlocker(control)
                if isinstance(control, QComboBox):
                    index = control.findData(value)
                    if index < 0:
                        control.addItem(ENUM_LABELS.get(str(value), str(value)), value)
                        index = control.count() - 1
                    control.setCurrentIndex(index)
                elif isinstance(control, (QSpinBox, QDoubleSpinBox)):
                    control.setValue(value)
                elif isinstance(control, QLineEdit):
                    control.setText(str(value))
                del blocker
        for _, names in GROUPS[1:]:
            if names[0] in self._readbackLabels:
                display = dict(values)
                for key, feature in (("exposureMode", "ExposureAuto"), ("gainMode", "GainAuto")):
                    if feature in snapshot.get("actual", {}):
                        display[key] = snapshot["actual"][feature]
                text = "；".join(FIELD_LABELS.get(key, key) + "：" + ENUM_LABELS.get(str(display[key]), str(display[key]))
                                for key in names if key in values)
                self._readbackLabels[names[0]].setText(text or "未连接")
        self._updateEnabledFields()

    def _updateEnabledFields(self, *_args: object) -> None:
        values = self._dependencyValues()
        selection = values.get("selectionMode", "ip")
        retryCount = values.get("retryCount", 0)
        enabled = {
            "ipAddress": selection == "ip",
            "cameraKey": selection == "cameraKey",
            "userId": selection == "userId",
            "deviceIndex": selection == "index",
            "triggerSource": values.get("triggerMode") == "hardware",
            "triggerActivation": values.get("triggerMode") == "hardware",
            "captureTimeoutMs": self._connected or values.get("waitMode", "bounded") == "bounded",
            "retryDelayMs": isinstance(retryCount, int) and retryCount > 0,
            "exposureUs": values.get("exposureMode") == "manual",
            "gainRaw": values.get("gainMode") == "manual",
            "frameRate": values.get("frameRateMode") == "manual",
            "demosaic": str(values.get("pixelFormat", "keep")) not in {"Mono8", "RGB8", "BGR8"},
        }
        enabled.update({name: values.get("roiMode") == "custom" for name in ("width", "height", "offsetX", "offsetY")})
        for name in self._controls:
            active = enabled.get(name, True) and not self._busy and name not in self._unavailable
            if self._connected and name in GROUPS[0][1]:
                active = False
            control = self._controls.get(name)
            if control is not None:
                control.setEnabled(active)
                label = self._layout.labelForField(control)
                if label is not None:
                    label.setEnabled(active)
