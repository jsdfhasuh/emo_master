"""Static, call-site-specific data routes for the workflow relationship tree."""
from __future__ import annotations

from typing import TYPE_CHECKING

from emo_master.core.contracts.port_types import isPortRequired, normalizePortType
from emo_master.core.workflow.loop_contracts import deriveLoopContract, whileConditionMode

if TYPE_CHECKING:
    from .workflow_store import WorkflowState


def workflowTransfers(
    source: WorkflowState,
    target: WorkflowState,
    node: dict[str, object],
    relation: str,
    workflows: dict[str, WorkflowState],
) -> list[dict[str, object]]:
    nodeId = str(node.get("nodeId", ""))
    nodes = {str(item.get("nodeId")): item for item in source.nodes}
    rawLoop = node.get("loop")
    loop = rawLoop if isinstance(rawLoop, dict) else {}
    mode = loop.get("mode") if node.get("kind") == "loop" else None
    body = workflows.get(str(loop.get("bodyWorkflowId", "")))
    condition = workflows.get(str(loop.get("conditionWorkflowId", "")))
    contract = deriveLoopContract(
        loop, body.inputs if body else {}, body.outputs if body else {},
        condition.inputs if condition else {}, condition.outputs if condition else {},
    ) if mode else None
    config = contract.normalizedConfig if contract else {}
    legacy = config.get("contractVersion") == 1
    conditionPort = config.get("conditionPort") if whileConditionMode(config) == "boolean" else None
    isCondition = relation.endswith("condition")
    rows: list[dict[str, object]] = []

    def endpoint(otherId: object, port: object) -> str:
        other = nodes.get(str(otherId), {})
        label = other.get("displayName") or otherId
        if other.get("kind") == "workflow_input":
            label = "输入"
        elif other.get("kind") == "workflow_output":
            label = "输出"
        elif other.get("kind") == "subflow":
            called = workflows.get(str(other.get("targetWorkflowId", "")))
            if called:
                label = f"{label} ({called.name})"
        return f"{source.name} / {label}.{port}"

    def incoming(port: str) -> list[str]:
        return [endpoint(edge.get("fromNode"), edge.get("fromPort"))
                for edge in source.edges
                if edge.get("toNode") == nodeId and edge.get("toPort") == port]

    def outgoing(port: str) -> list[str]:
        targets = [endpoint(edge.get("toNode"), edge.get("toPort"))
                   for edge in source.edges
                   if edge.get("fromNode") == nodeId and edge.get("fromPort") == port]
        # SQLite field bindings consume outputs without a canvas edge.
        for other in source.nodes:
            if other.get("operatorId") != "vision.io.sqlite_writer":
                continue
            params = other.get("params")
            mappings = params.get("mappings", []) if isinstance(params, dict) else []
            for mapping in mappings if isinstance(mappings, list) else []:
                if not isinstance(mapping, dict):
                    continue
                binding = mapping.get("source")
                if (isinstance(binding, dict) and binding.get("kind") == "node_output"
                        and binding.get("nodeId") == nodeId and binding.get("port") == port):
                    targets.append(endpoint(other.get("nodeId"), mapping.get("column", "字段")))
        return targets

    def add(direction: str, port: str, spec: object, origin: str,
            destination: str, note: str = "") -> None:
        rows.append({"direction": direction, "port": port,
                     "portType": normalizePortType(spec), "source": origin,
                     "target": destination, "note": note})

    for port, spec in target.inputs.items():
        publicPort = port
        note = ""
        generated = None
        if mode and not isCondition and port == "__iteration__":
            generated = "循环迭代序号"
        elif mode == "foreach":
            itemPort = "item" if legacy else config.get("itemInputPort")
            indexPort = "index" if legacy else config.get("indexInputPort")
            if port == indexPort:
                generated = "循环迭代序号"
            elif port == itemPort:
                publicPort = "items"
                note = "逐项传入 items[i]"
            elif not legacy and port == "items":
                generated = "无对应输入"
                note = "保留端口冲突"
        origins = [generated] if generated else incoming(publicPort)
        if mode == "while" and not generated:
            if legacy and port != "state":
                origins = ["无对应输入"] if not generated else origins
            elif body:
                origins = [f"初始：{value}" for value in origins]
                origins.append(f"后续：{body.name} / 输出.{port}")
                note = "循环状态；每轮先判断条件，再执行循环体"
                if port == conditionPort:
                    note += "；继续条件是此布尔值：true 继续，false 结束"
                if not incoming(publicPort):
                    note += "；初始值未连接"
        if not origins:
            origins = ["未连接"]
            note = "必需输入" if isPortRequired(spec, default=True) else "可选输入"
        if mode == "repeat" and not generated:
            note = "每轮使用相同输入" + (f"；{note}" if note else "")
        for origin in origins:
            add("input", port, spec, origin, f"{target.name} / 输入.{port}", note)

    for port, spec in target.outputs.items():
        origin = f"{target.name} / 输出.{port}"
        note = ""
        publicPort = port
        if isCondition:
            add("output", port, spec, origin,
                f"{source.name} / {node.get('displayName') or nodeId} / 循环判断"
                if port == "continue" else "不参与循环判断",
                "true：执行循环体；false：结束循环" if port == "continue" else "未使用输出")
            continue
        if mode == "foreach":
            publicPort = "results" if legacy else port
            note = (f"按轮汇总到 results[i].{port}" if legacy
                    else f"按轮汇总为 list<{normalizePortType(spec)}>")
        elif mode == "repeat":
            note = "返回最后一轮输出；0 次时返回同名输入"
        elif mode == "while":
            if legacy and port != "state":
                add("output", port, spec, origin, "不参与状态回传", "未使用输出")
                continue
            note = "回传下一轮状态；循环结束后返回"
            if port == conditionPort:
                add("output", port, spec, origin,
                    f"{source.name} / {node.get('displayName') or nodeId} / 下一轮继续条件.{port}",
                    "布尔值；true：继续下一轮，false：结束；当前图片仍会处理完成")
        destinations = outgoing(publicPort)
        if not destinations:
            destinations = [endpoint(nodeId, publicPort)]
            note += ("；" if note else "") + "无下游连接"
        for destination in destinations:
            add("output", port, spec, origin, destination, note)
    return rows
