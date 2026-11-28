"""Readable presentation of known Runtime event types and values."""

from emo_master.core.contracts.run_inspection import normalizeInspection


_EXECUTION_STATUS = {
    "ACCEPTED": "已受理", "STARTING": "启动中", "RUNNING": "运行中", "STOPPING": "停止中",
    "COMPLETED": "已完成", "FAILED": "失败", "ABORTED": "已停止", "SKIPPED": "已跳过",
    "SUCCEEDED": "执行成功", "CANCELLED": "已取消", "CANCELLING": "正在取消",
    "TIMED_OUT": "已超时", "INTERRUPTED": "已中断",
    "REJECTED": "未受理",
}
_SQLITE_STATUS = {
    "COMMITTED": "SQLite 写入已入库", "QUEUED": "SQLite 写入已排队",
    "SKIPPED": "已跳过 SQLite 写入", "CANCELLED": "SQLite 写入已取消",
    "FAILED": "SQLite 写入失败",
}
_COMPONENTS = {
    "application": "应用", "execution": "执行", "operator": "算子", "device": "设备",
    "presentation": "页面", "storage": "存储", "editor": "编辑器",
    "flow_designer": "流程编辑", "page_designer": "页面编辑", "runtime_client": "Runtime 客户端",
}
_DEFAULT_MESSAGES = {
    "loop.started": {"loop started": "循环开始"},
    "loop.completed": {"loop completed": "循环结束"},
    "job.accepted": {"job accepted": "任务已受理"},
    "job.process.started": {"job.process.started": "任务进程已启动"},
    "job.started": {"job.started": "任务开始执行"},
    "job.completed": {"job.completed": "任务已完成"},
    "job.stopping": {
        "job stopping (graceful)": "任务正在正常停止", "job stopping (force)": "任务正在强制停止",
        "runtime shutting down": "Runtime 正在关闭，任务正在停止",
    },
    "job.aborted": {
        "job.aborted": "任务已停止", "job force aborted": "任务已强制停止",
        "execution cancelled": "任务执行已取消", "job aborted after process exit": "任务进程退出后停止",
        "graceful stop timed out; worker terminated": "正常停止超时，任务进程已终止",
        "runtime shutting down": "Runtime 关闭，任务已停止",
    },
    "job.failed": {
        "job.failed": "任务失败", "runtime restarted while job was active": "任务运行期间 Runtime 已重启",
        "worker heartbeat timed out": "任务进程心跳超时", "worker exited without terminal event": "任务进程退出，未报告结束结果",
    },
    "process.terminated": {
        "worker process terminated": "任务进程已终止",
        "worker process terminated after heartbeat timeout": "心跳超时，任务进程已终止",
        "worker process terminated after graceful stop timeout": "正常停止超时，任务进程已终止",
        "worker process terminated during runtime shutdown": "Runtime 关闭时终止了任务进程",
        "worker process terminated after event bridge failure": "事件转发失败，任务进程已终止",
    },
    "dispatch.accepted": {"dispatch admission receipts": "已记录派发受理回执"},
    "dispatch.skipped": {"dispatch skipped: queue full": "派发队列已满，跳过"},
    "dispatch.joined": {"dispatch target outcomes": "已记录派发目标结果"},
    "dispatch.group.started": {"persistent consumer group admitted": "派发消费者组已受理"},
    "dispatch.group.retired": {"all run-group processes retired": "派发任务组已退出"},
    "dispatch.source.ended": {"source ended; downstream group remains managed": "派发入口已结束，下游仍由 Runtime 管理"},
    "dispatch.invocation.completed": {"consumer invocation completed": "消费者调用已完成"},
}


def executionStatusTitle(value: object) -> str | None:
    """Translate only a value already identified as a framework status by its caller."""
    return _EXECUTION_STATUS.get(value) if isinstance(value, str) else None


def runtimeLogComponentTitle(component: str) -> str:
    return _COMPONENTS.get(component, component)


def frameworkSkipReason(payload) -> str | None:
    if not isinstance(payload, dict):
        return None
    code, message = payload.get("code"), payload.get("message")
    if code == "E_BRANCH_NOT_SELECTED" and message == "upstream branch was not selected":
        return "上游分支未被选中"
    if code == "E_INPUT_NOT_PRODUCED":
        default = "upstream did not produce input"
        missing = payload.get("missingInputs")
        if message == default:
            return "上游未产生输入"
        if isinstance(missing, list) and missing and all(isinstance(port, str) for port in missing):
            if message == default + ": " + ", ".join(missing):
                return "上游未产生输入 · " + "、".join(missing)
    return None


def frameworkLogMessage(eventType: str, message: str, *, nodeId="", workflowId="", code="",
                        iterationPath=(), payload=None) -> str:
    """Recognize a producer's default message; business messages remain verbatim."""
    if eventType == "sqlite.write.started":
        receipt = payload.get("receipt") if isinstance(payload, dict) else None
        if message == "SQLite write started" or (
                message == "UNKNOWN" and isinstance(receipt, dict) and receipt.get("status") == "UNKNOWN"):
            return "开始写入"
        return message
    if eventType == "sqlite.write.finished":
        receipt = payload.get("receipt") if isinstance(payload, dict) else None
        if isinstance(receipt, dict) and message == receipt.get("status"):
            return _SQLITE_STATUS.get(message, message)
        return message
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
    }
    if eventType in {"subflow.started", "subflow.completed"}:
        state = eventType.split(".")[1]
        data = payload if isinstance(payload, dict) else {}
        prefix = f"subflow {state}: "
        target = data.get("targetWorkflowId") or data.get("targetWorkflow")
        if not target and message.startswith(prefix):
            candidate = message.removeprefix(prefix)
            if candidate and all(char.isalnum() or char in "_-" for char in candidate):
                target = candidate
        if isinstance(target, str) and target and message == prefix + target:
            title = "开始调用子工作流" if state == "started" else "子工作流调用完成"
            return f"{title} · {data.get('targetWorkflowName') or target}"
    if eventType == "artifact.created" and isinstance(payload, dict):
        artifact = payload.get("artifact")
        path = artifact.get("path") if isinstance(artifact, dict) else payload.get("path")
        prefix = "artifact created: "
        sourcePath = message.removeprefix(prefix)
        if message.startswith(prefix) and sourcePath and (sourcePath == path
                or (isinstance(artifact, dict) and artifact.get("artifactId"))):
            return f"已生成产物 · {sourcePath}"
    if (eventType == "node.log" and code == "W_OPERATOR_LOG_DROPPED"
            and message == "operator logs were dropped by runtime limits"):
        return "算子日志因 Runtime 限额被丢弃"
    if eventType == "job.failed" and code == "E_EVENT_PERSISTENCE" and message.startswith("event bridge failed: "):
        return "事件转发失败 · " + message.removeprefix("event bridge failed: ")
    default = defaults.get(eventType)
    return default[1] if default and message == default[0] else _DEFAULT_MESSAGES.get(eventType, {}).get(message, message)


def runtimeEventMessage(record: dict) -> str:
    return frameworkLogMessage(str(record.get("eventType") or ""), str(record.get("message") or ""),
        nodeId=record.get("nodeId") or "", workflowId=record.get("workflowId") or "", code=record.get("code") or "",
        iterationPath=record.get("iterationPath"), payload=record.get("payload"))


def _capacityFailureSummary(diagnostics: dict) -> str:
    if diagnostics.get('code') != 'E_MAX_CONCURRENT_JOBS':
        return ''
    capacity = diagnostics.get('capacity')
    fields = ('requiredJobs', 'consumerJobs', 'activeJobs', 'maxConcurrentJobs', 'availableJobs')
    if not isinstance(capacity, dict) or any(
            type(capacity.get(key)) is not int or capacity[key] < 0 for key in fields):
        return ''
    required, consumers, active, limit, available = (capacity[key] for key in fields)
    lines = ['Runtime 并发 Job 容量不足。',
             f'本次需要 {required} 个 Job（入口 1 个 + 自动消费者 {consumers} 个）。',
             f'Runtime 已占用 {active} 个，上限 {limit} 个，剩余 {available} 个。']
    if active:
        lines += ['', '已有任务仍在运行或释放资源。若要重新运行，请先在运行视图确认并停止对应入口，待资源释放后重试。']
    if required > limit:
        lines += ['', f'本次运行至少需要上限 {required}。请在“运行目标与并发”中提高上限，或减少并行目标。',
                  '修改后通过“工程与 Runtime 配置”写入 Runtime；仅修改本地工程不会改变在线上限。']
    elif active:
        lines += ['', f'若需保留现有任务并额外启动，并发上限至少需要 {active + required}。',
                  '请在“运行目标与并发”中调整，并通过“工程与 Runtime 配置”写入 Runtime。']
    return '\n'.join(lines)


def _resourceFailureSummary(diagnostics: dict) -> str:
    if diagnostics.get('code') == 'E_RESOURCE_TRANSFER_DIGEST':
        return ('Runtime 已上传的工程资源缺失或损坏。\n'
                '请在“工程与 Runtime 配置”中确认修复并重新上传资源，验证通过后重试。')
    issues = diagnostics.get('issues', [])
    labels = {'E_RESOURCE_MISSING': '缺失', 'E_RESOURCE_SIZE': '大小不符',
              'E_RESOURCE_DIGEST': '内容不符', 'E_RESOURCE_INPUT': '路径无效'}
    if not isinstance(issues, list) or not issues or any(
            not isinstance(issue, dict) or issue.get('code') not in labels for issue in issues):
        return ''
    resources = diagnostics.get('resources', {})
    if not isinstance(resources, dict):
        return ''
    files = []
    for issue in issues:
        fieldPath = issue.get('fieldPath')
        if not isinstance(fieldPath, str):
            return ''
        resource = next((resources[key] for key in sorted(resources, key=len, reverse=True)
            if fieldPath.startswith('resources.items.' + key + '.')), None)
        if not isinstance(resource, dict):
            return ''  # No reliable filename: retain the original reason.
        kind = {'model': '模型', 'input_image': '输入图片'}.get(str(resource.get('purpose') or ''), '资源')
        path = str(resource.get('path') or '')
        if len(path) > 100:
            path = path[:45] + '…' + path[-45:]
        files.append(f'• {kind}：{path}（{labels[issue["code"]]}）')
    codes = {issue['code'] for issue in issues}
    title = ('Runtime 缺少工程所需的文件。' if codes == {'E_RESOURCE_MISSING'} else
             'Runtime 工程资源与工程记录不一致。' if codes <= {'E_RESOURCE_SIZE', 'E_RESOURCE_DIGEST'} else
             '工程资源路径无效。' if codes == {'E_RESOURCE_INPUT'} else 'Runtime 工程资源校验未通过。')
    action = ('请修正工程资源路径，验证并写入 Runtime 后重试。' if 'E_RESOURCE_INPUT' in codes else
              '处理方法：打开“工程与 Runtime 配置”，\n点击“上传候选资源”，验证通过后重试。')
    return '\n'.join([title, *files[:6], *([f'另有 {len(files) - 6} 项，见“详细信息”。'] if len(files) > 6 else []), '', action])


def prestartFailureSummary(payload) -> str:
    """Read reported capacity or resource diagnostics without inferring missing fields."""
    diagnostics = payload.get("diagnostics") if isinstance(payload, dict) else None
    if not isinstance(diagnostics, dict):
        return ""
    return _capacityFailureSummary(diagnostics) or _resourceFailureSummary(diagnostics)


def runtimeLogBrief(record: dict) -> str:
    """Render a few fields from the producer's existing diagnostic summaries."""
    payload = record.get("payload")
    if not isinstance(payload, dict):
        return ""
    kind = str(record.get("eventType") or "")
    skip = frameworkSkipReason(payload) if kind == "node.skipped" else None
    if skip:
        return skip
    if kind.startswith(("job.", "runtime.")) or not kind:
        failure = prestartFailureSummary(payload)
        if failure:
            return " · ".join(failure.split("\n\n", 1)[0].splitlines())
    receipt = payload.get("receipt")
    if kind.startswith("sqlite.write.") and isinstance(receipt, dict):
        pieces = []
        if isinstance(receipt.get("status"), str) and receipt["status"]:
            pieces.append(receipt["status"])
        if type(receipt.get("rowsAffected")) is int:
            pieces.append(f"影响 {receipt['rowsAffected']} 行")
        return " · ".join(pieces)
    if kind.startswith("device."):
        outcome = payload.get("outcome") or (receipt.get("outcome") if isinstance(receipt, dict) else "")
        if isinstance(outcome, str):
            return outcome
    if kind == "node.completed" and "ioSummary" in payload:
        outputs = normalizeInspection(payload["ioSummary"])["outputs"]
        if outputs:
            return " · ".join(f"{row['port']} = {describePort(row['value'])}" for row in outputs["items"][:2])
    return ""


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
