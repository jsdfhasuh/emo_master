"""Readable presentation of known Runtime event types and values."""


_EXECUTION_STATUS = {
    "ACCEPTED": "已受理", "STARTING": "启动中", "RUNNING": "运行中", "STOPPING": "停止中",
    "COMPLETED": "已完成", "FAILED": "失败", "ABORTED": "已停止", "SKIPPED": "已跳过",
    "SUCCEEDED": "执行成功", "CANCELLED": "已取消", "CANCELLING": "正在取消",
    "TIMED_OUT": "已超时", "INTERRUPTED": "已中断",
}


def executionStatusTitle(value: object) -> str | None:
    """Translate only a value already identified as a framework status by its caller."""
    return _EXECUTION_STATUS.get(value) if isinstance(value, str) else None


def frameworkLogMessage(eventType: str, message: str, *, nodeId="", workflowId="",
                        iterationPath=(), payload=None) -> str:
    """Recognize a producer's default message; business messages remain verbatim."""
    if eventType in {"loop.iteration.started", "loop.iteration.completed"}:
        data = payload if isinstance(payload, dict) else {}
        iteration = data.get("iteration")
        if (type(iteration) is not int and isinstance(iterationPath, (tuple, list)) and iterationPath):
            iteration = iterationPath[-1]
        if type(iteration) is int and iteration >= 0 and message == f"loop iteration {iteration}":
            if eventType == "loop.iteration.started":
                return "该轮开始"
            return "退出条件检查结束" if data.get("bodyExecuted") is False else "该轮结束"
        return message
    defaults = {
        "node.started": (f"node started: {nodeId}", "开始执行节点"),
        "node.completed": (f"node completed: {nodeId}", "节点执行完成"),
        "node.skipped": (f"node skipped: {nodeId}", "跳过节点"),
        "workflow.started": (f"workflow started: {workflowId}", "工作流开始执行"),
        "workflow.completed": (f"workflow completed: {workflowId}", "工作流执行完成"),
        "loop.started": ("loop started", "循环开始"),
        "loop.completed": ("loop completed", "循环结束"),
        "job.accepted": ("job accepted", "任务已受理"),
        "job.started": ("job.started", "任务开始执行"),
        "job.completed": ("job.completed", "任务已完成"),
    }
    default = defaults.get(eventType)
    return default[1] if default and message == default[0] else message


def describePort(value: dict) -> str:
    kind = value.get('kind')
    if kind == 'scalar':
        item = value.get('value')
        text = 'true' if item is True else 'false' if item is False else '空值' if item is None else str(item)
        return text + ('…（已截断）' if value.get('truncated') else '')
    if kind in {'image', 'array'}:
        shape = value.get('shape', [])
        return ('图像 ' if kind == 'image' else '数组 ') + ' × '.join(str(n) for n in shape) + '（仅显示尺寸摘要）'
    if kind == 'collection':
        return f"集合 · {value.get('count', 0)} 项（仅显示数量）"
    if kind == 'object':
        return f"对象 · {value.get('count', 0)} 个字段（未展开）"
    if kind == 'redacted':
        return '敏感字段已隐藏'
    if kind == 'integer':
        return f"大整数 · {value.get('bits', 0)} 位（未展开）"
    return '不可展示 · ' + str(value.get('reason', '未提供'))
