"""Mode-specific collection filter ports; saved contracts remain complete."""
from dataclasses import replace

from emo_master.plugins.builtins.collection_filter.configuration import COLLECTION_TYPES
from emo_master.apps.designer.state.port_applicability import activeFilterPorts, filterCollectionType


OPERATOR_ID = "vision.collection.filter"


def collectionFilterPresentation(model, node, edges, dynamicParameters=()):
    dynamic = set(dynamicParameters)
    dynamic.update(binding["parameterPath"][0] for binding in node.globalVariableBindings
                   if binding.get("parameterPath"))
    selected = filterCollectionType(node, edges)
    inputs, outputs = activeFilterPorts(node.params, selected, dynamic)
    connectedInputs = {edge.toPort for edge in edges if edge.toNode == node.nodeId}
    connectedOutputs = {edge.fromPort for edge in edges if edge.fromNode == node.nodeId}
    inactiveInputs, inactiveOutputs = connectedInputs - inputs, connectedOutputs - outputs
    lines = list(model.summaryLines)
    if "collectionType" in dynamic:
        lines.append(("筛选对象由绑定决定，显示可能使用的引脚", None))
    elif selected in COLLECTION_TYPES:
        lines.append(("筛选对象：" + COLLECTION_TYPES[selected], None))
    else:
        lines.append(("请选择筛选对象", None))
    if inactiveInputs or inactiveOutputs:
        lines.append(("存在不适用的连线，请断开或调整筛选配置", None))
    return replace(
        model,
        inputPorts={key: value for key, value in node.inputPorts.items()
                    if key in inputs or key in connectedInputs},
        outputPorts={key: node.outputPorts[key] for key in sorted(
            node.outputPorts, key=lambda name: name in {"keptCount", "rejectedCount"})
                     if key in outputs or key in connectedOutputs},
        inputPortLabels={key: f"{key}（当前模式不适用）" for key in inactiveInputs},
        outputPortLabels={key: f"{key}（当前模式不适用）" for key in inactiveOutputs},
        summaryLines=tuple(lines),
    )
