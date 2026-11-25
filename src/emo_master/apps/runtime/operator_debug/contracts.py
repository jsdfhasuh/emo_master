from __future__ import annotations

import hashlib
import importlib
import json
import math
from pathlib import Path

from emo_master.core.contracts.port_types import isPortRequired, matchesPortSpec, normalizePortType
from emo_master.core.project.global_variables import (
    definitions, resolveParams, validateBindings, validateEffectiveParams, variablePorts,
)
from emo_master.core.project.models import WorkflowNode
from emo_master.core.project.node_input_files import NODE_INPUT_FILES
from emo_master.core.project.resources import ResourceBinding, ResourcePlan

MAX_REQUEST_BYTES = 768 * 1024
MAX_INLINE_BYTES = 64 * 1024
MAX_DEPTH = 32
# Each entry is reviewed code, not an operator-ID naming heuristic.
SUPPORTED_FOLDERS = (
    "number_value", "number_compare", "absdiff", "add_weighted", "apply_mask",
    "affine", "annotate", "blob_analysis", "blur", "canny_edge", "clahe",
    "collection_count", "collection_filter", "collection_select", "collection_sort",
    "color_convert", "contour", "crop", "detection_bbox", "empty_operator", "equalize",
    "flip", "flow_error", "flow_if", "flow_switch", "gateway_ack_validate",
    "gateway_coordinate_format", "geometry_points", "global_counter", "histogram",
    "hough_circle", "hough_line", "in_range", "line_from_points", "mask_logic", "minimum_enclosing_circle",
    "morphology", "perspective", "points_reframe", "resize", "rgb_statistics", "roi",
    "rotate", "shape_measurement", "template_match", "threshold", "variable_read", "variable_write",
    "coordinate_calculator", "value_select",
    "point_components", "number_absolute", "number_exact_integer", "number_pair", "number_truncate",
    "coordinate_message_format", "elapsed_time", "percentage",
)
# Explicit user-triggered resource operations. Runtime owns file/socket access and
# its debug reservation excludes competing execution owners.
# These entries must not also admit automatic workflow execution or trials.
SINGLE_NODE_FOLDERS = (
    "plc_slmp_read", "image_loader", "coordinate_reader", "image_batch_loader", "video_loader", "file_list",
    "yolo_inference", "yolo_classification", "patchcore_inference", "image_saver", "result_writer", "sqlite_writer",
    "tcp_client", "tcp_receive_once", "plc_slmp_write", "huaray_camera",
    "rtsp_camera", "hikvision_audio",
)

EXECUTION_WARNINGS = {
    "vision.io.rtsp_camera": "本次将从 Runtime 连接 RTSP 摄像头并读取一帧，请确认设备可用于调试。",
    "communication.camera.hikvision_audio": "本次可能使摄像头现场发声，使用设备当前音量和播放次数。取消不能撤销已发送的播放请求；结果不明时禁止自动重试。",
    "communication.plc.slmp_write": "本次将解锁一次 PLC 写入并真实写入配置的设备。取消或超时不能撤销已发送的数据；禁止自动重试。",
    "communication.tcp.client": "本次将向配置的 TCP 地址真实发送数据。取消或超时不能撤销已发送的数据。",
    "communication.tcp.receive_once": "本次将在 Runtime 监听 TCP 端口，并可能发送配置的应答。请确认监听地址及应答内容。",
    "vision.io.image_saver": "本次将在 Runtime 电脑的指定输出路径真实写入图片，相对路径以会话工程目录为基准，可能覆盖原文件。取消不能撤销写入。",
    "vision.io.huaray_camera": "本次将连接真实相机、应用当前配置并采集一帧。请确认设备可用于调试。",
}


def debugInputPorts(operatorId, params, ports):
    """Debug-only mapped inputs; never change the persisted operator contract."""
    if operatorId != "vision.io.sqlite_writer":
        return ports
    from emo_master.core.contracts.sqlite_writer import parseConfig
    result = dict(ports)
    for index, row in enumerate(parseConfig(params)["mappings"]):
        if row["source"]["kind"] == "node_output":
            kind = {"INTEGER": "number", "REAL": "number", "BOOLEAN": "boolean", "TEXT": "string", "UTC_TIME": "string"}.get(row["storageType"], "any")
            source = row["source"]
            result[f"mapping_{index + 1}"] = {"type": kind, "required": row.get("missing", "error") == "error",
                "nullable": True, "title": f"字段 {row['column']}（{source['nodeId']}.{source['port']}）"}
    return result


class DebugError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


def fail(code, message):
    raise DebugError(code, message)


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            fail("E_DEBUG_CONTEXT_INVALID", "duplicate JSON key: " + key)
        result[key] = value
    return result


def _finite(value):
    result = float(value)
    if not math.isfinite(result):
        fail("E_DEBUG_CONTEXT_INVALID", "non-finite JSON number")
    return result


def _depth(value, level=0):
    if level > MAX_DEPTH:
        fail("E_DEBUG_LIMIT", "JSON nesting exceeds 32 levels")
    if isinstance(value, dict):
        for item in value.values():
            _depth(item, level + 1)
    elif isinstance(value, list):
        for item in value:
            _depth(item, level + 1)


def parse(raw: str, limit=MAX_INLINE_BYTES):
    if not isinstance(raw, str) or len(raw.encode("utf-8")) > limit:
        fail("E_DEBUG_LIMIT", "JSON byte budget exceeded")
    try:
        value = json.loads(raw, object_pairs_hook=_pairs, parse_float=_finite,
                           parse_constant=lambda _: fail("E_DEBUG_CONTEXT_INVALID", "non-finite JSON number"))
        _depth(value)
        return value
    except (ValueError, RecursionError) as error:
        if isinstance(error, DebugError):
            raise
        raise DebugError("E_DEBUG_CONTEXT_INVALID", "invalid JSON") from error


def encode(value, limit=MAX_INLINE_BYTES):
    try:
        _depth(value)
        raw = json.dumps(value, ensure_ascii=True, allow_nan=False, sort_keys=True, separators=(",", ":"))
    except (ValueError, TypeError, RecursionError) as error:
        raise DebugError("E_DEBUG_LIMIT", "value cannot be represented as bounded JSON") from error
    if len(raw.encode("utf-8")) > limit:
        fail("E_DEBUG_LIMIT", "JSON byte budget exceeded")
    return raw


def digest(value):
    return hashlib.sha256(encode(value, MAX_REQUEST_BYTES).encode()).hexdigest()


def identifier(value):
    if not isinstance(value, str) or not 1 <= len(value) <= 128:
        fail("E_DEBUG_CONTEXT_INVALID", "identity must contain 1..128 characters")
    return value


def trustedEntries(registry):
    admitted = {}
    root = Path(__file__).resolve().parents[3] / "plugins" / "builtins"
    for folder in (*SUPPORTED_FOLDERS, *SINGLE_NODE_FOLDERS):
        original = json.loads((root / folder / "manifest.json").read_text(encoding="utf-8"))
        operatorId, entry = original["operatorId"], original["entry"]
        module, name = entry.split(":")
        operator = getattr(importlib.import_module(module), name)
        descriptor = registry.get(operatorId)
        manifest = getattr(descriptor, "manifest", None)
        resource = getattr(descriptor, "resourceRoot", None)
        if manifest is not None and manifest.deviceRequirements:
            continue
        if (getattr(descriptor, "operatorClass", None) is operator
                and manifest is not None and manifest.entry == entry
                and manifest.version == operator.meta.version
                and manifest.inputPorts == operator.meta.inputPorts
                and manifest.outputPorts == operator.meta.outputPorts
                and manifest.paramSchema == operator.meta.paramSchema
                and resource is not None and resource.resolve() == root / folder):
            resultSchema = manifest.resultSchema
            inspectionSchema = {
                "captureParams": resultSchema.get("captureParams", []),
                "diagnostics": {
                    key: field for key, field in resultSchema.get("diagnostics", {}).items()
                    if key == "effectiveParameters"
                },
            }
            admitted[operatorId] = {
                "entry": entry, "version": manifest.version,
                "inputPorts": manifest.inputPorts, "outputPorts": manifest.outputPorts,
                "paramSchema": manifest.paramSchema, "displayName": manifest.displayName,
                "inspectionSchema": inspectionSchema,
                "runtimeCapabilities": list(manifest.runtimeCapabilities),
                "stateful": folder in {"global_counter", "variable_read", "variable_write", "image_batch_loader", "video_loader", "coordinate_reader", "patchcore_inference", "yolo_classification", "elapsed_time"},
                "singleNodeOnly": folder in SINGLE_NODE_FOLDERS,
                "executionWarning": EXECUTION_WARNINGS.get(operatorId, ""),
            }
    return admitted


def _nodeResourcePlan(resources, workflowId, node, schema):
    if not isinstance(resources, dict):
        fail("E_DEBUG_CONTEXT_INVALID", "invalid resource plan")
    selected = []
    items = {}
    for key in ("parameterBindings", "siteBindings"):
        rows = resources.get(key, [])
        if not isinstance(rows, list):
            fail("E_DEBUG_CONTEXT_INVALID", "invalid resource bindings")
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get("target", {}), dict):
                fail("E_DEBUG_CONTEXT_INVALID", "invalid resource binding target")
            target = row.get("target", {})
            if target.get("workflowId") != workflowId or target.get("nodeId") != node.nodeId:
                continue
            if key == "parameterBindings" and target.get("parameterPath") == [NODE_INPUT_FILES.get(node.operatorId)]:
                continue
            if key == "siteBindings":
                fail("E_DEBUG_UNSUPPORTED", "当前算子调试尚不支持现场字段绑定：" + str(row.get("field", "")))
            binding = ResourceBinding.model_validate(row)
            path = binding.target.parameterPath
            field = schema.get("properties", {}).get(path[0], {})
            if len(path) != 1 or field.get("xWidget") != "file" or field.get("xFileMode") != "open":
                fail("E_RESOURCE_TARGET", "算子调试的工程资源须绑定到输入文件参数：" + ".".join(path))
            inventory = resources.get("items", {})
            if not isinstance(inventory, dict) or binding.resourceId not in inventory:
                fail("E_RESOURCE_MISSING", "工程中找不到绑定的资源：" + binding.resourceId)
            items[binding.resourceId] = inventory[binding.resourceId]
            selected.append(binding)
    if selected:
        return ResourcePlan(schemaVersion=resources.get("schemaVersion", "1.0"),
                            items=items, parameterBindings=selected)
    return None


def draft(raw, projectId, workflowId, nodeId, operatorId, admitted):
    for value in (projectId, workflowId, nodeId, operatorId):
        identifier(value)
    payload = parse(raw, MAX_REQUEST_BYTES)
    try:
        if payload["schemaVersion"] not in {"2.0", "2.1", "2.2", "2.3", "2.4", "2.5", "2.6", "2.7", "2.8", "2.9", "2.10"}:
            raise ValueError("unsupported project version")
        if payload["project"]["projectId"] != projectId:
            raise ValueError("project identity changed")
        nodes = payload["workflows"][workflowId]["nodes"]
        ids = [item["nodeId"] for item in nodes]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate node identity")
        matches = [item for item in nodes if item["nodeId"] == nodeId]
        if len(matches) != 1:
            raise ValueError("target node not found")
        node = WorkflowNode.model_validate(matches[0])
        if node.kind != "operator" or node.operatorId != operatorId:
            raise ValueError("target is not the requested operator")
        if node.deviceBindings:
            raise ValueError("device-bound debug requires the managed device Runtime")
    except (KeyError, TypeError, ValueError) as error:
        raise DebugError("E_DEBUG_CONTEXT_INVALID", "invalid target draft: " + str(error)[:256]) from error
    if operatorId not in admitted:
        fail("E_DEBUG_UNSUPPORTED", "operator is not admitted for isolated debug execution")
    declarations = payload.get("globalVariables", {})
    variables = definitions(declarations)
    bindings = [binding.model_dump() for binding in node.globalVariableBindings]
    occupied = [["variableId"]] if operatorId in {"vision.state.variable_read", "vision.state.variable_write"} else []
    if operatorId == "vision.state.variable_write":
        occupied.append(["operation"])
    schema = admitted[operatorId]["paramSchema"]
    plan = _nodeResourcePlan(payload.get("resources") or {}, workflowId, node, schema)
    if plan:
        occupied.extend(binding.target.parameterPath for binding in plan.parameterBindings)
    validateBindings(bindings, variables, schema, occupied=occupied)
    protected = []
    if operatorId == "vision.io.sqlite_writer":
        # The rest of a draft need not be executable to debug this one node.
        for workflow in payload["workflows"].values():
            siblings = workflow.get("nodes", []) if isinstance(workflow, dict) else []
            for sibling in siblings if isinstance(siblings, list) else []:
                if not isinstance(sibling, dict) or sibling.get("operatorId") != operatorId:
                    continue
                params = sibling.get("params")
                path = params.get("databasePath") if isinstance(params, dict) else None
                if isinstance(path, str) and Path(path).is_absolute():
                    protected.append(path)
    return dict(admitted[operatorId], nodeId=nodeId, operatorId=operatorId, projectId=projectId,
                workflowId=workflowId, draftDigest=digest(payload), nodeParams=node.params,
                sqliteProtected=protected, resourcePlan=plan,
                variableDefinitions={key: value.model_dump() for key, value in variables.items()},
                variableBindings=bindings)


def executionParams(spec, raw, values):
    original = parse(raw)
    if not isinstance(original, dict):
        fail("E_PARAM_INVALID", "parameters must be an object")
    bindings = spec.get("variableBindings", [])
    effective = resolveParams(original, bindings, values)
    effective.update(spec.get("boundResourceParams", {}))
    try:
        effective = validateEffectiveParams(effective, bindings, spec["paramSchema"])
    except (ValueError, TypeError) as error:
        raise DebugError("E_PARAM_INVALID", str(error)) from error
    ports = variablePorts(spec["operatorId"], effective, definitions(spec.get("variableDefinitions", {})))
    inputPorts, outputPorts = ports if ports is not None else (spec["inputPorts"], spec["outputPorts"])
    return effective, debugInputPorts(spec["operatorId"], effective, inputPorts), outputPorts


def parameters(raw, schema):
    value = parse(raw)
    if not isinstance(value, dict):
        fail("E_PARAM_INVALID", "parameters must be an object")
    try:
        return validateEffectiveParams(value, (), schema)
    except (ValueError, TypeError) as error:
        raise DebugError("E_PARAM_INVALID", str(error)) from error


def inputs(values, ports, *, transport=True):
    if set(values) - set(ports):
        fail("E_INPUT_UNDECLARED", "input contains an undeclared port")
    for key, spec in ports.items():
        if key not in values and isPortRequired(spec, default=True):
            fail("E_INPUT_MISSING", "missing required input: " + key)
        if key in values and not matchesPortSpec(values[key], spec):
            fail("E_INPUT_TYPE", "invalid input: " + key)
        if key in values and normalizePortType(spec) == "image" and values[key] is not None:
            import numpy as np
            value = values[key]
            if (not isinstance(value, np.ndarray) or value.dtype != np.uint8 or value.ndim not in {2, 3}
                    or (value.ndim == 3 and value.shape[2] != 3)):
                fail("E_INPUT_TYPE", "image requires a complete uint8 GRAY/BGR asset: " + key)
    if transport:
        encode(values)
    return values
