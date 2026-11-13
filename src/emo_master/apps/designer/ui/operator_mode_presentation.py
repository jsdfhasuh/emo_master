"""Present applicable built-in ports without altering their persisted contracts."""
from dataclasses import replace

from emo_master.plugins.builtins._operator_modes import INPUT_CHOICES, inferredInputType
from emo_master.apps.designer.state.port_applicability import (
    CONDITIONAL_OUTPUTS as CONDITIONAL_OUTPUTS, MODE_PORT_OPERATORS as MODE_PORT_OPERATORS, activeOperatorPorts,
)


def operatorModePresentation(model, node, edges, dynamicParameters=()):
    if node.operatorId == "vision.geometry.coordinate_calculator":
        from .coordinate_port_presentation import coordinatePortPresentation
        return coordinatePortPresentation(model, node, edges, dynamicParameters)
    if node.operatorId == "vision.collection.filter":
        from .collection_filter_presentation import collectionFilterPresentation
        return collectionFilterPresentation(model, node, edges, dynamicParameters)
    dynamic = set(dynamicParameters)
    dynamic.update(binding["parameterPath"][0] for binding in node.globalVariableBindings
                   if binding.get("parameterPath"))
    inputs, outputs = activeOperatorPorts(node, edges, dynamic)
    lines = list(model.summaryLines)
    if node.operatorId in INPUT_CHOICES:
        selector, choices = INPUT_CHOICES[node.operatorId]
        selected = inferredInputType(node.operatorId, node.params, node.nodeId, edges, dynamic)
        if node.operatorId == "vision.collection.select" and (node.params.get("mode", "first") == "index" or "mode" in dynamic):
            if any(edge.toNode == node.nodeId and edge.toPort == "index" for edge in edges):
                lines.append(("索引：index 输入优先", None))
        if selector in dynamic or "feature" in dynamic:
            lines.append(("处理对象由绑定决定，显示可能使用的引脚", None))
        else:
            lines.append(("处理对象：" + choices[selected] if selected in choices else "请选择处理对象", None))
    connectedInputs = {edge.toPort for edge in edges if edge.toNode == node.nodeId}
    connectedOutputs = {edge.fromPort for edge in edges if edge.fromNode == node.nodeId}
    inactiveInputs, inactiveOutputs = connectedInputs - inputs, connectedOutputs - outputs
    if inactiveInputs or inactiveOutputs:
        lines.append(("存在不适用的连线，请断开或调整当前配置", None))
    return replace(
        model,
        inputPorts={name: kind for name, kind in node.inputPorts.items() if name in inputs | connectedInputs},
        outputPorts={name: kind for name, kind in node.outputPorts.items() if name in outputs | connectedOutputs},
        inputPortLabels={name: f"{name}（当前配置不适用）" for name in inactiveInputs},
        outputPortLabels={name: f"{name}（当前配置未启用）" for name in inactiveOutputs},
        summaryLines=tuple(lines),
    )
