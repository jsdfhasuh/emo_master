"""Definite conditional-output conflicts; unknown dynamic values stay unknown."""
from __future__ import annotations


def disabledOutputConflicts(payload, roots=None):
    from .loop_contracts import loopWorkflowReferenceFields
    workflows = payload.get('workflows', {})
    pending = list(roots if roots is not None else [payload.get('entryWorkflowId')])
    reachable = set()
    while pending:
        key = pending.pop()
        if key in reachable or key not in workflows:
            continue
        reachable.add(key)
        for node in workflows[key].get('nodes', []):
            if node.get('kind') == 'subflow':
                pending.append(node.get('targetWorkflowId'))
            elif node.get('kind') == 'loop':
                config = node.get('loop', {})
                pending.extend(config.get(field) for field in loopWorkflowReferenceFields(config))
    problems = []
    for workflowId, workflow in payload.get("workflows", {}).items():
        if workflowId not in reachable:
            continue
        nodes = {node["nodeId"]: node for node in workflow.get("nodes", [])}
        for edge in workflow.get("edges", []):
            source, target = nodes.get(edge["fromNode"], {}), nodes.get(edge["toNode"], {})
            if source.get("operatorId") != "vision.analysis.blob" or edge["fromPort"] != "overlay":
                continue
            if any(binding.get("parameterPath", binding.get("targetPath")) == ["drawOverlay"]
                   for binding in source.get("globalVariableBindings", [])):
                continue
            if source.get("params", {}).get("drawOverlay", False) is not False:
                continue
            if target.get("kind") != "workflow_output":
                continue  # other adapters may intentionally consume missing values
            spec = workflow.get("outputs", {}).get(edge["toPort"], {})
            if isinstance(spec, dict) and not spec.get("required", True):
                continue
            problems.append(dict(workflowId=workflowId, nodeId=edge["fromNode"], parameter="drawOverlay",
                message=f'{workflow.get("name", workflowId)} / {source.get("displayName") or edge["fromNode"]}：'
                    f'overlay 已连接必填出口 {edge["toPort"]}，但“绘制叠加图”关闭。请启用 drawOverlay，或将允许缺失的出口设为可选；可空不等于可缺失。'))
    return problems
