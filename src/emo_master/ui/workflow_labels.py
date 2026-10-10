"""Display-only workflow names and launch roles; never infer roles from names."""
from collections import Counter


# Runtime status is separate from the static entry role and editor selection.
WORKFLOW_RUN_STYLES = {
    'RUNNING': ('执行中', '#b45309', '#fef3c7'),
    'WAITING_CHILD': ('调用中', '#6d28d9', '#ede9fe'),
    'COMPLETED': ('已完成', '#047857', '#d1fae5'),
    'FAILED': ('失败', '#b91c1c', '#fee2e2'),
    'REJECTED': ('已拒绝', '#b91c1c', '#fee2e2'),
    'ABORTED': ('已停止', '#626b78', '#e5e7eb'),
    'STOPPING': ('停止中', '#b45309', '#fef3c7'),
    'ACCEPTED': ('待运行', '#626b78', '#e5e7eb'),
    'STARTING': ('启动中', '#626b78', '#e5e7eb'),
    'START_UNCERTAIN': ('待确认', '#b45309', '#fef3c7'),
}


def aggregateWorkflowStatus(statuses):
    # A failure must not disappear behind another Job executing the same flow.
    order = ('FAILED', 'REJECTED', 'START_UNCERTAIN', 'STOPPING', 'RUNNING',
             'WAITING_CHILD', 'STARTING', 'ACCEPTED', 'ABORTED', 'COMPLETED')
    return next((status for status in order if status in statuses), '')


def workflowDisplayNames(workflows):
    names = {key: (workflow.name if hasattr(workflow, "name") else workflow.get("name", key))
             for key, workflow in workflows.items()}
    counts = Counter(names.values())
    return {key: (f"{name} · {key}" if counts[name] > 1 else name)
            for key, name in names.items()}


def workflowEntryMarker(workflowId, entryWorkflowId, selected):
    if workflowId == entryWorkflowId:
        return "默认入口"
    return "运行入口" if workflowId in selected else ""


def workflowEntryToolTip(name, workflowId, entryWorkflowId, selected):
    lines = [name, f"工作流 ID：{workflowId}"]
    if workflowId == entryWorkflowId:
        lines.append("工程默认入口：未另选运行目标时，从这里启动。")
    if workflowId in selected:
        lines.append("本次运行入口：点击“运行流程”时作为独立 Job 启动；不表示已经运行。")
    else:
        lines.append("未选为本次运行入口；仍可被其他流程调用。")
    lines.append("切换标签只切换编辑视图，不改变运行目标。")
    return "\n".join(lines)
