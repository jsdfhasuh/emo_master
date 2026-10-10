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
    "hough_circle", "hough_line", "in_range", "mask_logic", "minimum_enclosing_circle",
    "morphology", "perspective", "points_reframe", "resize", "rgb_statistics", "roi",
    "rotate", "shape_measurement", "template_match", "threshold", "variable_read", "variable_write",
)


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
    for folder in SUPPORTED_FOLDERS:
        original = json.loads((root / folder / "manifest.json").read_text(encoding="utf-8"))
        operatorId, entry = original["operatorId"], original["entry"]
        module, name = entry.split(":")
        operator = getattr(importlib.import_module(module), name)
        descriptor = registry.get(operatorId)
        manifest = getattr(descriptor, "manifest", None)
        resource = getattr(descriptor, "resourceRoot", None)
        if (getattr(descriptor, "operatorClass", None) is operator
                and manifest is not None and manifest.entry == entry
                and manifest.version == operator.meta.version
                and manifest.inputPorts == operator.meta.inputPorts
                and manifest.outputPorts == operator.meta.outputPorts
                and manifest.paramSchema == operator.meta.paramSchema
                and resource is not None and resource.resolve() == root / folder):
            admitted[operatorId] = {
                "entry": entry, "version": manifest.version,
                "inputPorts": manifest.inputPorts, "outputPorts": manifest.outputPorts,
                "paramSchema": manifest.paramSchema, "displayName": manifest.displayName,
                "stateful": folder in {"global_counter", "variable_read", "variable_write"},
            }
    return admitted


def draft(raw, projectId, workflowId, nodeId, operatorId, admitted):
    for value in (projectId, workflowId, nodeId, operatorId):
        identifier(value)
    payload = parse(raw, MAX_REQUEST_BYTES)
    try:
        if payload["schemaVersion"] not in {"2.0", "2.1", "2.2", "2.3", "2.4"}:
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
    validateBindings(bindings, variables, admitted[operatorId]["paramSchema"], occupied=occupied)
    resources = payload.get("resources") or {}
    if not isinstance(resources, dict):
        fail("E_DEBUG_CONTEXT_INVALID", "invalid resource plan")
    for key in ("parameterBindings", "siteBindings"):
        resourceBindings = resources.get(key, [])
        if not isinstance(resourceBindings, list):
            fail("E_DEBUG_CONTEXT_INVALID", "invalid resource bindings")
        for binding in resourceBindings:
            if not isinstance(binding, dict) or not isinstance(binding.get("target", {}), dict):
                fail("E_DEBUG_CONTEXT_INVALID", "invalid resource binding target")
            target = binding.get("target", {})
            if target.get("workflowId") == workflowId and target.get("nodeId") == nodeId:
                fail("E_DEBUG_UNSUPPORTED", "resource bindings require the A3 resource adapter")
    return dict(admitted[operatorId], nodeId=nodeId, operatorId=operatorId, projectId=projectId,
                workflowId=workflowId, draftDigest=digest(payload), nodeParams=node.params,
                variableDefinitions={key: value.model_dump() for key, value in variables.items()},
                variableBindings=bindings)


def executionParams(spec, raw, values):
    original = parse(raw)
    if not isinstance(original, dict):
        fail("E_PARAM_INVALID", "parameters must be an object")
    bindings = spec.get("variableBindings", [])
    effective = resolveParams(original, bindings, values)
    effective = validateEffectiveParams(effective, bindings, spec["paramSchema"])
    ports = variablePorts(spec["operatorId"], effective, definitions(spec.get("variableDefinitions", {})))
    inputPorts, outputPorts = ports if ports is not None else (spec["inputPorts"], spec["outputPorts"])
    return effective, inputPorts, outputPorts


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
