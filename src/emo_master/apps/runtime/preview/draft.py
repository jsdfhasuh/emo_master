"""Validate an ephemeral preview node without compiling or loading a Job."""
from __future__ import annotations

import json

from emo_master.core.project.migration import migrateProjectPayload
from emo_master.core.project.models import ProjectDocument


# Stay below the Runtime's 1 MiB inbound RPC quota, including protobuf framing.
MAX_DRAFT_PREVIEW_BYTES = 768 * 1024


def draftPreviewProjectId(request: object) -> tuple[str | None, str | None]:
    raw = str(getattr(request, "project_json", ""))
    params = str(getattr(request, "params_json", ""))
    if not raw or len(raw.encode("utf-8")) + len(params.encode("utf-8")) > MAX_DRAFT_PREVIEW_BYTES:
        return None, "预览需要有效的当前工程草稿；请求大小不得超过 768 KiB"
    try:
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise ValueError("工程草稿必须是 JSON 对象")
        document = ProjectDocument.model_validate(migrateProjectPayload(payload))
    except (ValueError, TypeError, KeyError, RecursionError) as error:
        return None, "当前工程草稿无效：" + str(error)
    projectId = str(getattr(request, "project_id", ""))
    if not projectId or projectId != document.project.projectId:
        return None, "预览窗口与当前工程不一致，请关闭配置窗口后重新打开"
    workflowId = str(getattr(request, "workflow_id", ""))
    workflow = document.workflows.get(workflowId)
    if workflow is None:
        return None, "当前工程草稿中没有该工作流，请重新打开节点配置"
    nodeId = str(getattr(request, "node_id", ""))
    node = next((item for item in workflow.nodes if item.nodeId == nodeId), None)
    if node is None:
        return None, "该节点已删除或移到其他工作流，请重新打开节点配置"
    if node.kind != "operator" or not node.operatorId or node.operatorId != str(getattr(request, "operator_id", "")):
        return None, "预览算子与当前草稿节点不一致，请重新打开节点配置"
    return projectId, None
