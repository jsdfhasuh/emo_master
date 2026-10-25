"""Failure popup backed by the same verified identity as runtime log navigation."""
import json
from typing import cast

from PySide2.QtCore import Qt
from PySide2.QtWidgets import QMessageBox, QTextEdit

from emo_master.apps.designer.ui.log_dialog import StructuredLogEntry


def _capacityFailureSummary(entry):
    diagnostics = entry.payload.get('diagnostics', {})
    if not isinstance(diagnostics, dict) or diagnostics.get('code') != 'E_MAX_CONCURRENT_JOBS':
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


def _resourceFailureSummary(entry):
    """Summarize structured pre-start issues without parsing raw error paths."""
    diagnostics = entry.payload.get('diagnostics', {})
    if not isinstance(diagnostics, dict):
        return ''
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
        resource = next((resources[key] for key in sorted(resources, key=len, reverse=True)
            if issue.get('fieldPath', '').startswith('resources.items.' + key + '.')), None)
        if not isinstance(resource, dict):
            return ''  # No reliable filename: retain the original reason.
        kind = {'model': '模型', 'input_image': '输入图片'}.get(resource.get('purpose'), '资源')
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


def showRunFailure(window, entry: StructuredLogEntry, failure: dict[str, object]) -> QMessageBox:
    current = (bool(entry.projectId) and entry.projectId == window._currentProjectId()
               and bool(entry.projectInstanceToken)
               and entry.projectInstanceToken == window._runtimeLogProjectToken)
    workflow = window.workflowStore.workflows.get(entry.workflowId) if current else None
    node = None
    if workflow is not None and entry.nodeId:
        if entry.workflowId == window.activeWorkflowId:
            model = window.flowModel.nodes.get(entry.nodeId)
            node = dict(displayName=model.displayName) if model is not None else None
        else:
            node = next((item for item in workflow.nodes if item.get('nodeId') == entry.nodeId), None)

    workflowName = f'{workflow.name}（{entry.workflowId}）' if workflow is not None else entry.workflowId
    lines = [f'工作流：{workflowName or "未提供"}']
    if entry.nodeId:
        title = str(node.get('displayName') or entry.nodeId) if node is not None else entry.nodeId
        lines.append(f'失败节点：{title}（{entry.nodeId}）' if title != entry.nodeId else f'失败节点：{title}')
    elif failure.get('status') == 'REJECTED':
        lines.append('失败阶段：启动 / 工程准备，尚未执行到节点。')
    else:
        lines.append('失败节点：未报告失败节点（Runtime 未提供节点关联）。')
    if entry.code:
        lines.append(f'错误码：{entry.code}')
    reason = (_capacityFailureSummary(entry) or _resourceFailureSummary(entry)) if not entry.nodeId else ''
    reason = reason or entry.message
    if len(reason) > 1200:
        reason = reason[:1200] + '\n…完整原因见“详细信息”。'
    lines.append(f'失败原因：{reason}')

    box = QMessageBox(window)
    box.setObjectName('runFailureDialog')
    box.setAttribute(Qt.WA_DeleteOnClose)
    box.setIcon(QMessageBox.Critical)
    box.setWindowTitle('运行失败')
    box.setTextFormat(Qt.PlainText)
    box.setText('作业执行失败' if entry.nodeId else
                '作业未能启动' if failure.get('status') == 'REJECTED' else '作业未能完成')
    box.setInformativeText('\n\n'.join(lines))
    details = entry.detailPayload()
    details.update(status=failure.get('status', 'FAILED'), jobMessage=failure.get('jobMessage', ''))
    box.setDetailedText(entry.message + '\n\n' + json.dumps(details, ensure_ascii=False, indent=2, default=str))
    detailButton = next(button for button in box.buttons() if box.buttonRole(button) == QMessageBox.ActionRole)
    detailView = cast(QTextEdit, box.findChild(QTextEdit))
    detailButton.setText('详细信息')
    detailButton.clicked.connect(lambda: detailButton.setText(
        '收起详情' if detailView.isVisible() else '详细信息'), Qt.QueuedConnection)
    if node is not None:
        locate = box.addButton('定位失败节点', QMessageBox.ActionRole)

        def navigate():
            result = window.navigateToRuntimeLogEntry(entry)
            window.appendRuntimeLog('INFO' if result.startswith('已定位') else 'WARN', result)

        locate.clicked.connect(navigate)
    close = box.addButton('关闭', QMessageBox.RejectRole)
    box.setDefaultButton(close)
    box.setEscapeButton(close)
    # Do not nest a modal event loop while other workflow Jobs are reporting.
    box.open()
    return box
