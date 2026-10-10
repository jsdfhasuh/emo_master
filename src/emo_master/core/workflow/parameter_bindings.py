"""Declared parameter dependencies, independent of canvas input ports."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from emo_master.core.contracts.sqlite_writer import OPERATOR_ID, parseConfig, acceptsSource, SqliteWriterError, toStorage
from emo_master.core.workflow.errors import ValidationIssue


@dataclass(frozen=True)
class CompiledBinding:
    fromNode: str
    fromPort: str
    toNode: str
    mappingIndex: int
    storageType: str


@dataclass(frozen=True)
class BoundValue:
    workflowRunId: str
    nodeRunId: str
    value: Any = None
    errorCode: str = ""
    errorMessage: str = ""


def compileBindings(nodes, projectId: str, workflowId: str):
    bindings = []
    issues = []
    outgoing = defaultdict(list)
    for node in nodes.values():
        if node.operatorId != OPERATOR_ID:
            continue
        try:
            config = parseConfig(node.params)
        except SqliteWriterError as error:
            issues.append(ValidationIssue(error.code, str(error), projectId, workflowId, node.nodeId,
                                          f"params.mappings.{error.row}" if error.row is not None else "params"))
            continue
        for index, row in enumerate(config["mappings"]):
            source = row["source"]
            if source["kind"] != "node_output":
                continue
            target = nodes.get(source["nodeId"])
            code = message = ""
            if target is None:
                code, message = "E_BINDING_NODE", "来源节点不存在或不在同一流程：" + source["nodeId"]
            elif source["port"] not in target.outputPorts:
                code, message = "E_BINDING_PORT", "来源输出端口不存在：" + source["port"]
            elif not acceptsSource(target.outputPorts[source["port"]], row["storageType"]):
                code, message = "E_BINDING_TYPE", "来源类型与存储类型不兼容"
            elif row["storageType"] == "FILE_REFERENCE" and target.operatorId != "vision.io.image_saver":
                code, message = "E_BINDING_TYPE", "图片文件引用仅支持正式 Image Saver 的持久保存结果"
            if code:
                issues.append(ValidationIssue(code, f"映射第 {index + 1} 行：{message}", projectId, workflowId, node.nodeId, f"params.mappings.{index}.source"))
                continue
            binding = CompiledBinding(source["nodeId"], source["port"], node.nodeId, index, row["storageType"])
            bindings.append(binding)
            outgoing[binding.fromNode].append(binding)
    return tuple(bindings), {key: tuple(value) for key, value in outgoing.items()}, issues


def captureBoundValue(value, binding: CompiledBinding, context) -> BoundValue:
    try:
        stored = toStorage(value, binding.storageType)
        return BoundValue(context.workflowRunId, context.nodeRunId, stored)
    except SqliteWriterError as error:
        return BoundValue(context.workflowRunId, context.nodeRunId, errorCode=error.code, errorMessage=str(error))
