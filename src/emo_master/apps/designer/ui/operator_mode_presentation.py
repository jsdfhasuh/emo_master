"""Present applicable built-in ports without altering their persisted contracts."""
from dataclasses import replace

from emo_master.plugins.builtins._operator_modes import FEATURE_INPUTS, INPUT_CHOICES, inferredInputType


CONDITIONAL_OUTPUTS = {
    "vision.inference.yolo": {
        "drawOverlay": (True, {"overlay"}),
        "outputCrop": (False, {"croppedImage", "croppedFrame"}),
    },
    "vision.color.rgb_statistics": {"emitMask": (False, {"mask"})},
    "vision.io.coordinate_reader": {"asPolygon": (False, {"polygon"})},
    "vision.analysis.blob": {"drawOverlay": (False, {"overlay"})},
}
MODE_PORT_OPERATORS = set(INPUT_CHOICES) | set(CONDITIONAL_OUTPUTS) | {
    "vision.mask.logic", "vision.geometry.coordinate_calculator", "vision.collection.filter",
}


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
    inputs, outputs = set(node.inputPorts), set(node.outputPorts)
    lines = list(model.summaryLines)
    if node.operatorId in INPUT_CHOICES:
        selector, choices = INPUT_CHOICES[node.operatorId]
        selected = inferredInputType(node.operatorId, node.params, node.nodeId, edges, dynamic)
        inputs = set(choices) if selector in dynamic else ({selected} if selected in choices else set())
        if node.operatorId == "vision.geometry.extract_points":
            if "feature" in dynamic and not node.params.get(selector):
                inputs = set(choices)
            elif "feature" not in dynamic:
                inputs.intersection_update(FEATURE_INPUTS.get(node.params.get("feature", "circleCenter"), []))
        prefix = {"vision.collection.sort": "sorted", "vision.collection.select": "selected"}.get(node.operatorId)
        if prefix:
            outputs = {prefix + name.capitalize() for name in inputs}
            if node.operatorId == "vision.collection.select":
                outputs.add("selectedCount")
                if node.params.get("mode", "first") == "index" or "mode" in dynamic:
                    inputs.add("index")
                    if any(edge.toNode == node.nodeId and edge.toPort == "index" for edge in edges):
                        lines.append(("索引：index 输入优先", None))
        if selector in dynamic or "feature" in dynamic:
            lines.append(("处理对象由绑定决定，显示可能使用的引脚", None))
        else:
            lines.append(("处理对象：" + choices[selected] if selected in choices else "请选择处理对象", None))
    for flag, (default, ports) in CONDITIONAL_OUTPUTS.get(node.operatorId, {}).items():
        if flag not in dynamic and not node.params.get(flag, default):
            outputs.difference_update(ports)
    if node.operatorId == "vision.mask.logic" and node.params.get("operation", "and") == "not" and "operation" not in dynamic:
        inputs.difference_update({"maskB", "frameB"})
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
