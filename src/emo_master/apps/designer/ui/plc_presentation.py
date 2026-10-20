"""PLC display labels; persisted parameter keys and protocol values stay unchanged."""
from copy import deepcopy


NUMBER_TYPES = {
    "uint16": "无符号整数（16 位）",
    "int16": "有符号整数（16 位）",
    "uint32": "无符号整数（32 位）",
    "int32": "有符号整数（32 位）",
    "float32": "浮点数（32 位）",
    "bit": "位状态（开 / 关）",
}
FRAME_ENCODINGS = {"binary": "MC 3E · 二进制（Binary）", "ascii": "MC 3E · ASCII"}


def frameTitle(params):
    value = params.get("frameEncoding", "binary")
    return FRAME_ENCODINGS.get(value, str(value))


ADVANCED_FIELDS = (
    "connectTimeoutMs", "responseTimeoutMs", "retryCount", "retryDelayMs",
    "networkNo", "pcNo", "moduleIoNo", "moduleStationNo", "monitoringTimer", "outputIndex",
)
FIELD_TITLES = {"host": "PLC 地址", "device": "存储区", "dataType": "数值类型",
                "count": "读取数量", "values": "写入数值", "outputIndex": "单值输出位置（从 0 开始）"}
PORT_TITLES = {"values": "读取数据", "numberValue": "选定数值", "booleanValue": "选定位状态",
               "receipt": "写入回执"}
STATES = {"STARTING": "正在准备", "READY": "就绪", "RUNNING": "执行中",
          "SUCCEEDED": "执行成功", "FAILED": "执行失败", "CANCELLED": "已取消",
          "CANCELLING": "正在取消", "RESETTING": "正在重置", "FAULTED": "会话异常",
          "CLOSED": "已结束", "CLOSING": "正在结束"}


def plcSchema(schema):
    result = deepcopy(schema)
    properties = result.get("properties", {})
    for name, title in FIELD_TITLES.items():
        if name in properties:
            properties[name]["title"] = title
    if "dataType" in properties:
        properties["dataType"]["xOptionLabels"] = NUMBER_TYPES
        properties["dataType"]["description"] = "数值解释方式，与通信报文的 Binary / ASCII 格式无关。D 区 16 位占 1 个字，32 位占 2 个字。"
    if "frameEncoding" in properties:
        properties["frameEncoding"]["xOptionLabels"] = FRAME_ENCODINGS
        properties["frameEncoding"]["description"] = "需与 PLC 端口配置一致；此处为所选格式，不是自动检测结果。ASCII 的 D/M 地址范围为 0..999999。"
    result["properties"] = {
        **({"frameEncoding": properties["frameEncoding"]} if "frameEncoding" in properties else {}),
        **{k: v for k, v in properties.items() if k not in ADVANCED_FIELDS},
        **{k: v for k, v in properties.items() if k in ADVANCED_FIELDS},
    }
    return result


def plcLog(event):
    kind = event.get("type", "")
    common = {"session.opened": "调试会话已建立", "session.ready": "就绪",
              "execution.started": "开始执行", "session.closed": "调试会话已结束"}
    if kind in common:
        return common[kind]
    if kind == "execution.finished":
        return STATES.get(event.get("status"), event.get("status", "执行结束"))
    detail = event.get("event", {})
    if kind == "node.log" and isinstance(detail, dict):
        message = detail.get("message", "")
        data = detail.get("payload", {}).get("data", {})
        if message == "starting SLMP read":
            message = f"开始读取 {data.get('host', '')}:{data.get('port', '')} · {data.get('device', '')}{data.get('startAddress', '')} · {data.get('valueCount', '')} 个数值"
        elif message == "SLMP read completed":
            message = f"读取完成 · {data.get('valueCount', '')} 个数值 · {data.get('wordCount', '')} 个字"
        elif message == "starting SLMP write":
            message = "开始写入 PLC"
        elif message == "SLMP write completed":
            message = "PLC 写入完成"
        elif message == "SLMP read failed":
            message = "PLC 读取失败"
        elif message == "SLMP write failed":
            message = "PLC 写入失败，写入结果需另行确认"
        level = {"INFO": "信息", "WARN": "警告", "WARNING": "警告", "ERROR": "错误"}.get(detail.get("level"), detail.get("level", "信息"))
        return f"{level} | {detail.get('code', '')} {message}".strip()
    # Keep unfamiliar events visible rather than hiding new errors or diagnostics.
    return str(event)
