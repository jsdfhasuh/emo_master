"""User-facing explanations do not relax Runtime debug admission."""
import sys


def explainDebugError(code, message, operatorId=""):
    if code == "E_DEBUG_UNSUPPORTED" and any(reason in message for reason in (
        "No reviewed debug resource or side-effect adapter", "not admitted for isolated debug",
        "Operator unavailable")):
        details = "此算子尚未提供经过安全审查的通用调试适配，已阻止执行；这不代表正式流程中不可用。配置设备地址或模型不会解除该限制。"
        if "camera" in operatorId or "imv" in operatorId:
            details += "请使用相机专用采集预览；真实设备需另行授权与验证。"
            if ("imv" in operatorId or "huaray" in operatorId) and sys.platform != "win32":
                details += "当前 IMV 驱动另有 Windows 平台限制，和通用调试准入是两个独立条件。"
        elif "plc" in operatorId:
            details += "如需设备验证，请使用 PLC 专用运行面板；写入需明确解锁，不会自动执行。"
        elif "tcp" in operatorId or "writer" in operatorId:
            details += "网络发送和文件写入具有外部副作用，不会为解除调试拒绝而自动启动正式任务。"
        elif "yolo" in operatorId:
            details += "请先在模型配置入口检查模型与推理环境；通用调试仍需专门适配。"
        return details + "\n技术原因：" + message
    return message
