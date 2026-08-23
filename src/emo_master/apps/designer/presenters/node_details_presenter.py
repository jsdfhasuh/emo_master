from __future__ import annotations


class NodeDetailsPresenter:
    def __init__(
        self,
        flowModel,
        nodeRuntimeState: dict[str, dict[str, str]],
    ) -> None:
        self.flowModel = flowModel
        self.nodeRuntimeState = nodeRuntimeState

    def buildSummary(self) -> str:
        detailModel = self.buildModel()
        state = detailModel.get("state")
        if state == "empty":
            return str(detailModel.get("message", "未选中节点"))

        paramLinesRaw = detailModel.get("params", [])
        paramLines = paramLinesRaw if isinstance(paramLinesRaw, list) else []
        lines: list[str] = [
            str(detailModel.get("title", "")),
            f"ID: {detailModel.get('nodeId', '')}",
            f"算子: {detailModel.get('operatorId', '')}",
            f"输入: {detailModel.get('inputs', '无')}",
            f"输出: {detailModel.get('outputs', '无')}",
            f"必填: {detailModel.get('required', '无')}",
            f"未填: {detailModel.get('missing', '无')}",
            f"运行状态: {detailModel.get('runtimeStatus', '-')}",
            f"分支命中: {detailModel.get('branch', '-')}",
            "参数:",
        ]
        for item in paramLines:
            lines.append(str(item))
        return "\n".join(lines)

    def buildModel(self) -> dict[str, object]:
        selectedNodeId = getattr(self.flowModel, "selectedNodeId", None)
        if selectedNodeId is None:
            return {
                "state": "empty",
                "message": "未选中节点\n点击画布节点或左侧当前节点列表查看详情",
            }
        nodes = getattr(self.flowModel, "nodes", {})
        if not isinstance(nodes, dict):
            return {
                "state": "empty",
                "message": "未选中节点\n点击画布节点或左侧当前节点列表查看详情",
            }
        node = nodes.get(selectedNodeId)
        if node is None:
            return {
                "state": "empty",
                "message": "未选中节点\n点击画布节点或左侧当前节点列表查看详情",
            }

        inputSummary = ", ".join(
            [f"{name}:{portType}" for name, portType in node.inputPorts.items()]
        )
        outputSummary = ", ".join(
            [f"{name}:{portType}" for name, portType in node.outputPorts.items()]
        )
        requiredRawObj = (
            node.paramSchema.get("required", [])
            if isinstance(node.paramSchema, dict)
            else []
        )
        requiredRaw = requiredRawObj if isinstance(requiredRawObj, list) else []
        requiredParams = [item for item in requiredRaw if isinstance(item, str)]
        missingRequired = [
            name
            for name in requiredParams
            if name not in node.params or node.params.get(name) in (None, "", [])
        ]
        paramLines: list[str] = []
        for key, value in node.params.items():
            paramLines.append(f"- {key}: {value}")
        if len(paramLines) == 0:
            paramLines.append("- 无")

        runtimeInfo = self.nodeRuntimeState.get(selectedNodeId, {})
        runtimeStatus = runtimeInfo.get("status", "-")
        branchHit = runtimeInfo.get("branch", "-")
        return {
            "state": "selected",
            "title": node.displayName,
            "nodeId": node.nodeId[:8],
            "operatorId": node.operatorId,
            "inputs": inputSummary if inputSummary != "" else "无",
            "outputs": outputSummary if outputSummary != "" else "无",
            "required": "无" if len(requiredParams) == 0 else ", ".join(requiredParams),
            "missing": "无"
            if len(missingRequired) == 0
            else ", ".join(missingRequired),
            "runtimeStatus": runtimeStatus,
            "branch": branchHit,
            "params": paramLines,
        }
