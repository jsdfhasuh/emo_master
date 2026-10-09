"""Display-only descriptions of control flow; IDs and port contracts stay intact."""
from __future__ import annotations

from collections import Counter
import json

from emo_master.core.workflow.loop_contracts import whileConditionMode


def controlFlowDetails(node, workflows, variables=None):
    lines = []
    outputs = {}

    def reference(role, workflowId):
        workflow = workflows.get(workflowId)
        name = workflow.name if workflow else f"未找到工作流 ({workflowId or '未设置'})"
        lines.append((f"{role}：{name}", workflowId if workflow else None))

    if node.kind == "subflow":
        reference("调用", node.targetWorkflowId)
    elif node.kind == "loop":
        mode = node.loop.get("mode")
        if mode == "while":
            if whileConditionMode(node.loop) == "boolean":
                lines.append((f"继续条件：{node.loop.get('conditionPort') or '未设置'}（布尔值）", None))
            elif whileConditionMode(node.loop) == "globalVariable":
                variableId = node.loop.get("conditionVariableId")
                variable = (variables or {}).get(variableId, {})
                lines.append((f"每轮读取：{variable.get('name', variableId)}（全局布尔值）", None))
            else:
                reference("条件来源", node.loop.get("conditionWorkflowId"))
                lines.append(("读取 continue（布尔值）", None))
            lines.append(("true：执行循环体；false：结束", None))
            reference("循环体", node.loop.get("bodyWorkflowId"))
        elif mode == "repeat":
            reference(f"重复 {node.loop.get('repeatCount', 0)} 次", node.loop.get("bodyWorkflowId"))
        elif mode == "foreach":
            reference("逐项循环", node.loop.get("bodyWorkflowId"))
            item = node.loop.get("itemInputPort") or "未设置"
            index = node.loop.get("indexInputPort") or "不传入"
            lines.append((f"元素 → {item}；索引 → {index}", None))
    elif node.operatorId == "vision.flow.if":
        mode = node.params.get("mode", "bool")
        comparison = json.dumps(str(node.params.get("compareValue", "")), ensure_ascii=False)
        text = {"bool": "判断输入值的真假", "equals": f"输入值等于 {comparison}",
                "not_equals": f"输入值不等于 {comparison}"}.get(mode, str(mode))
        lines.append((text, None))
        outputs = {"true": "条件成立 (true)", "false": "条件不成立 (false)"}
    elif node.operatorId == "vision.flow.switch":
        values = []
        for index in range(4):
            key = f"case{index}Value"
            value = str(node.params.get(key, ""))
            outputs[f"case{index}"] = (f"分支 {index}：{json.dumps(value, ensure_ascii=False)}"
                                      if key in node.params else f"分支 {index}：未配置")
            if key in node.params:
                values.append(value)
        outputs["default"] = "未匹配 (default)"
        lines.append(("按分支 0 → 3 的顺序匹配", None))
        if any(count > 1 for count in Counter(values).values()):
            lines.append(("匹配值重复：采用编号最小的分支", None))
    return tuple(lines), outputs
