"""Failure popup backed by the same verified identity as runtime log navigation."""
import json
from typing import cast

from PySide2.QtCore import Qt
from PySide2.QtWidgets import QMessageBox, QTextEdit

from emo_master.apps.designer.ui.log_dialog import StructuredLogEntry


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
    reason = entry.message
    if len(reason) > 1200:
        reason = reason[:1200] + '\n…完整原因见“详细信息”。'
    lines.append(f'失败原因：{reason}')

    box = QMessageBox(window)
    box.setObjectName('runFailureDialog')
    box.setAttribute(Qt.WA_DeleteOnClose)
    box.setIcon(QMessageBox.Critical)
    box.setWindowTitle('运行失败')
    box.setTextFormat(Qt.PlainText)
    box.setText('作业执行失败' if entry.nodeId else '作业未能完成')
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
