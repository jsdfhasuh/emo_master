from __future__ import annotations

from PySide2.QtWidgets import QComboBox, QFormLayout, QLabel, QSpinBox

from emo_master.apps.designer.ui.param_form import SchemaParamForm


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
    ("触发与重试", ("triggerMode", "triggerSource", "triggerActivation", "captureTimeoutMs", "retryCount", "retryDelayMs")),
    ("图像输出", ("outputColor", "pixelFormat", "demosaic")),
    ("曝光、增益与帧率", ("exposureMode", "exposureUs", "gainMode", "gainRaw", "frameRateMode", "frameRate")),
    ("采集区域", ("roiMode", "width", "height", "offsetX", "offsetY")),
)

ENUM_LABELS = {
    "ip": "按 IP 地址", "cameraKey": "按设备唯一标识",
    "userId": "按用户自定义名称", "index": "按设备索引",
    "hardware": "硬件触发", "software": "软件触发", "freeRun": "自由采集",
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
    "triggerMode": "用于工作流采集。编辑器预览始终临时使用自由采集。",
    "triggerSource": "仅硬件触发有效，需与实际接线一致。",
    "triggerActivation": "仅硬件触发有效，且需要相机支持。",
    "captureTimeoutMs": "工作流按此值等待图像；编辑器预览最多等待 1000 毫秒。",
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


class CameraParameterForm(SchemaParamForm):
    def __init__(self) -> None:
        super().__init__()
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
                label.setText(FIELD_LABELS.get(name, name))
                label.setWordWrap(True)
            control.setObjectName(name)
            hint = FIELD_HINTS.get(name, FIELD_LABELS.get(name, name))
            control.setToolTip(f"{hint}\n参数键：{name}")
            if isinstance(control, QComboBox):
                for index in range(control.count()):
                    value = str(control.itemData(index))
                    control.setItemText(index, ENUM_LABELS.get(value, value))
                control.currentIndexChanged.connect(self._updateEnabledFields)
            elif isinstance(control, QSpinBox) and name == "retryCount":
                control.valueChanged.connect(self._updateEnabledFields)
        self._updateEnabledFields()

    def _updateEnabledFields(self, *_args: object) -> None:
        values = self.getValues()
        selection = values.get("selectionMode", "ip")
        retryCount = values.get("retryCount", 0)
        enabled = {
            "ipAddress": selection == "ip",
            "cameraKey": selection == "cameraKey",
            "userId": selection == "userId",
            "deviceIndex": selection == "index",
            "triggerSource": values.get("triggerMode") == "hardware",
            "triggerActivation": values.get("triggerMode") == "hardware",
            "retryDelayMs": isinstance(retryCount, int) and retryCount > 0,
            "exposureUs": values.get("exposureMode") == "manual",
            "gainRaw": values.get("gainMode") == "manual",
            "frameRate": values.get("frameRateMode") == "manual",
            "demosaic": str(values.get("pixelFormat", "keep")) not in {"Mono8", "RGB8", "BGR8"},
        }
        enabled.update({name: values.get("roiMode") == "custom" for name in ("width", "height", "offsetX", "offsetY")})
        for name, active in enabled.items():
            control = self._controls.get(name)
            if control is not None:
                control.setEnabled(active)
                label = self._layout.labelForField(control)
                if label is not None:
                    label.setEnabled(active)
