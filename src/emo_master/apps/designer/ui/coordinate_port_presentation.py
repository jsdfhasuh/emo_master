"""Project coordinate-calculator ports without changing the saved IO contract."""
from __future__ import annotations

from dataclasses import replace


from emo_master.apps.designer.state.port_applicability import activeCoordinatePorts

OPERATOR_ID = "vision.geometry.coordinate_calculator"


def coordinatePortPresentation(model, node, edges, dynamicParameters=()):
    if node.operatorId != OPERATOR_ID:
        return model
    dynamic = set(dynamicParameters)
    dynamic.update(binding["parameterPath"][0]
                   for binding in node.globalVariableBindings
                   if binding.get("parameterPath"))
    inputs, outputs = activeCoordinatePorts(node.params, dynamic)
    connectedInputs = {edge.toPort for edge in edges if edge.toNode == node.nodeId}
    connectedOutputs = {edge.fromPort for edge in edges if edge.fromNode == node.nodeId}
    inactiveInputs = connectedInputs - inputs
    inactiveOutputs = connectedOutputs - outputs
    lines = list(model.summaryLines)
    if "mode" in dynamic:
        lines.append(("计算模式由绑定决定，显示可能使用的引脚", None))
    if inactiveInputs or inactiveOutputs:
        lines.append(("存在不适用的连线，请断开或调整计算模式", None))
    # Always start from the full graph contract, not the previous filtered view.
    return replace(
        model,
        inputPorts={key: value for key, value in node.inputPorts.items()
                    if key in inputs or key in connectedInputs},
        outputPorts={key: value for key, value in node.outputPorts.items()
                     if key in outputs or key in connectedOutputs},
        inputPortLabels={key: f"{key}（当前模式不适用）" for key in inactiveInputs},
        outputPortLabels={key: f"{key}（当前模式不适用）" for key in inactiveOutputs},
        summaryLines=tuple(lines),
    )
