from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4


SCHEMA_VERSION = "2.1"
LEGACY_V2_SCHEMA_VERSION = "2.0"

_LEGACY_TOP_LEVEL_FIELDS = {
    "version",
    "schemaVersion",
    "project",
    "projectId",
    "meta",
    "designer",
    "workflows",
    "entryWorkflowId",
    "workflowOrder",
    "runtime",
    "dependencies",
    "devices",
}
_LEGACY_WORKFLOW_FIELDS = {"name", "inputs", "outputs", "nodes", "edges", "layout"}
_LEGACY_DESIGNER_FIELDS = {"nodes", "edges"}
_LEGACY_NODE_FIELDS = {
    "nodeId",
    "kind",
    "operatorId",
    "displayName",
    "inputPorts",
    "outputPorts",
    "paramSchema",
    "params",
    "targetWorkflowId",
    "loop",
    "x",
    "y",
}
_LEGACY_EDGE_FIELDS = {"fromNode", "fromPort", "toNode", "toPort"}
_LEGACY_METADATA_FIELDS = {"projectId", "name", "revision", "createdAt", "updatedAt"}
_LEGACY_RUNTIME_FIELDS = {
    "sourceImagePath",
    "maxConcurrentJobs",
    "gracefulStopTimeoutMs",
    "heartbeatTimeoutMs",
    "eventRetentionPerJob",
}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def migrateProjectPayload(payload: dict[str, object]) -> dict[str, object]:
    """Return a canonical v2.1 document without modifying the caller's payload."""
    source = deepcopy(payload)
    if source.get("schemaVersion") == SCHEMA_VERSION:
        # v2.1 is already the canonical source. Validate it before returning so
        # unknown fields and invalid kinds cannot disappear in normalization.
        from emo_master.core.project.models import ProjectDocument

        return ProjectDocument.model_validate(source).model_dump(mode="python")
    sourceSchema = source.get("schemaVersion")
    if sourceSchema == LEGACY_V2_SCHEMA_VERSION:
        from emo_master.core.project.models import ProjectDocument

        upgraded = ProjectDocument.model_validate(source).model_dump(mode="python")
        upgraded["schemaVersion"] = SCHEMA_VERSION
        _markLegacyLoopContracts(upgraded.get("workflows"))
        return ProjectDocument.model_validate(upgraded).model_dump(mode="python")
    if sourceSchema is not None and sourceSchema not in {"1.0", "1"}:
        raise ValueError(f"unsupported project schemaVersion: {sourceSchema!r}")
    _validateLegacyPayload(source, sourceSchema)

    rawProject = source.get("project")
    project = rawProject if isinstance(rawProject, dict) else {}
    rawMeta = source.get("meta")
    meta = rawMeta if isinstance(rawMeta, dict) else {}
    projectId = _first_string(
        project.get("projectId"), meta.get("projectId"), source.get("projectId")
    ) or str(uuid4())
    projectName = _first_string(project.get("name"), meta.get("name")) or "project"
    createdAt = _first_string(
        project.get("createdAt"), meta.get("createdAt")
    ) or utc_now_iso()
    updatedAt = _first_string(
        project.get("updatedAt"), meta.get("updatedAt")
    ) or createdAt
    revision = project.get("revision", 1)
    if not isinstance(revision, int) or isinstance(revision, bool) or revision < 1:
        revision = 1

    workflows = _extract_workflows(source)
    _ensure_legacy_boundaries(workflows)
    _markLegacyLoopContracts(workflows)
    workflowOrder = list(workflows.keys())
    entryWorkflowId = _first_string(source.get("entryWorkflowId")) or workflowOrder[0]
    if entryWorkflowId not in workflows:
        entryWorkflowId = workflowOrder[0]

    runtimeRaw = source.get("runtime")
    runtime = dict(runtimeRaw) if isinstance(runtimeRaw, dict) else {}
    dependenciesRaw = source.get("dependencies")
    dependencies = (
        dict(dependenciesRaw) if isinstance(dependenciesRaw, dict) else {}
    )
    devicesRaw = source.get("devices")
    devices = dict(devicesRaw) if isinstance(devicesRaw, dict) else {}
    return {
        "schemaVersion": SCHEMA_VERSION,
        "project": {
            "projectId": projectId,
            "name": projectName,
            "revision": revision,
            "createdAt": createdAt,
            "updatedAt": updatedAt,
        },
        "entryWorkflowId": entryWorkflowId,
        "workflowOrder": workflowOrder,
        "workflows": workflows,
        "runtime": _runtime_defaults(runtime),
        "dependencies": {"operators": dependencies.get("operators", [])},
        "devices": {"bindings": devices.get("bindings", {})},
    }


def _markLegacyLoopContracts(workflowsValue: object) -> None:
    if not isinstance(workflowsValue, dict):
        return
    for workflowValue in workflowsValue.values():
        if not isinstance(workflowValue, dict):
            continue
        nodes = workflowValue.get("nodes")
        if not isinstance(nodes, list):
            continue
        for node in nodes:
            if not isinstance(node, dict) or node.get("kind") != "loop":
                continue
            loop = node.get("loop")
            if isinstance(loop, dict):
                loop.setdefault("contractVersion", 1)


def loadProjectPayload(projectPath: Path) -> dict[str, object]:
    import json

    parsed = json.loads(projectPath.read_text(encoding="utf-8"))
    if not isinstance(parsed, dict):
        raise ValueError("project.json must contain an object")
    return migrateProjectPayload(parsed)


def _extract_workflows(source: dict[str, object]) -> dict[str, dict[str, object]]:
    rawWorkflows = source.get("workflows")
    if rawWorkflows is not None and not isinstance(rawWorkflows, dict):
        raise ValueError("workflows must be an object")
    if isinstance(rawWorkflows, dict) and rawWorkflows:
        result: dict[str, dict[str, object]] = {}
        for workflowId, rawWorkflow in rawWorkflows.items():
            if not isinstance(workflowId, str) or not isinstance(rawWorkflow, dict):
                raise ValueError("workflows must map string ids to objects")
            result[workflowId] = _normalize_workflow(workflowId, rawWorkflow)
        if result:
            return result

    rawDesigner = source.get("designer")
    if rawDesigner is not None and not isinstance(rawDesigner, dict):
        raise ValueError("designer must be an object")
    designer = rawDesigner if isinstance(rawDesigner, dict) else {}
    _rejectUnknownFields(designer, _LEGACY_DESIGNER_FIELDS, "designer")
    rawNodes = designer.get("nodes", [])
    rawEdges = designer.get("edges", [])
    nodes = _normalize_nodes(rawNodes)
    edges = _normalize_edges(rawEdges)
    return {
        "main": {
            "name": "Main",
            "inputs": {},
            "outputs": {},
            "nodes": nodes,
            "edges": edges,
            "layout": _layout_from_nodes(rawNodes),
        }
    }


def _normalize_workflow(workflowId: str, rawWorkflow: dict[str, object]) -> dict[str, object]:
    unknownFields = sorted(set(rawWorkflow) - _LEGACY_WORKFLOW_FIELDS)
    if unknownFields:
        raise ValueError(
            f"legacy workflow {workflowId!r} contains unsupported fields: "
            + ", ".join(unknownFields)
        )
    rawName = rawWorkflow.get("name")
    if rawName is not None and not isinstance(rawName, str):
        raise ValueError(f"legacy workflow {workflowId!r} name must be a string")
    rawInputs = rawWorkflow.get("inputs")
    if rawInputs is not None and not isinstance(rawInputs, dict):
        raise ValueError(f"legacy workflow {workflowId!r} inputs must be an object")
    rawOutputs = rawWorkflow.get("outputs")
    if rawOutputs is not None and not isinstance(rawOutputs, dict):
        raise ValueError(f"legacy workflow {workflowId!r} outputs must be an object")
    rawNodes = rawWorkflow.get("nodes", [])
    nodes = _normalize_nodes(rawNodes)
    rawEdges = rawWorkflow.get("edges", [])
    edges = _normalize_edges(rawEdges)
    layoutRaw = rawWorkflow.get("layout")
    if layoutRaw is not None and not isinstance(layoutRaw, dict):
        raise ValueError(f"legacy workflow {workflowId!r} layout must be an object")
    layout = dict(layoutRaw) if isinstance(layoutRaw, dict) else _layout_from_nodes(rawNodes)
    return {
        "name": _first_string(rawName) or workflowId,
        "inputs": _copy_object_map(rawInputs),
        "outputs": _copy_object_map(rawOutputs),
        "nodes": nodes,
        "edges": edges,
        "layout": layout,
    }


def _normalize_nodes(rawNodes: object) -> list[dict[str, object]]:
    if not isinstance(rawNodes, list):
        raise ValueError("workflow nodes must be a list")
    normalized: list[dict[str, object]] = []
    for rawNode in rawNodes:
        if not isinstance(rawNode, dict):
            raise ValueError("workflow nodes must contain objects")
        _rejectUnknownFields(rawNode, _LEGACY_NODE_FIELDS, "workflow node")
        node = dict(rawNode)
        nodeId = node.get("nodeId")
        if not isinstance(nodeId, str) or nodeId == "":
            raise ValueError("workflow node nodeId must be a non-empty string")
        kind = node.get("kind")
        if kind is None:
            node["kind"] = "operator"
        elif not isinstance(kind, str) or kind == "":
            raise ValueError("workflow node kind must be a non-empty string")
        for fieldName in ("inputPorts", "outputPorts", "paramSchema", "params", "loop"):
            value = node.get(fieldName)
            if value is not None and not isinstance(value, dict):
                raise ValueError(f"workflow node {fieldName} must be an object")
        for fieldName in ("operatorId", "displayName", "targetWorkflowId"):
            value = node.get(fieldName)
            if value is not None and not isinstance(value, str):
                raise ValueError(f"workflow node {fieldName} must be a string")
        # Coordinates belong to workflow.layout in v2, never to execution nodes.
        node.pop("x", None)
        node.pop("y", None)
        normalized.append(node)
    return normalized


def _normalize_edges(rawEdges: object) -> list[dict[str, object]]:
    if not isinstance(rawEdges, list):
        raise ValueError("workflow edges must be a list")
    edges: list[dict[str, object]] = []
    for rawEdge in rawEdges:
        if not isinstance(rawEdge, dict):
            raise ValueError("workflow edges must contain objects")
        _rejectUnknownFields(rawEdge, _LEGACY_EDGE_FIELDS, "workflow edge")
        if any(not isinstance(rawEdge.get(fieldName), str) for fieldName in _LEGACY_EDGE_FIELDS):
            raise ValueError("workflow edge endpoints and ports must be strings")
        edges.append(dict(rawEdge))
    return edges


def _ensure_legacy_boundaries(workflows: dict[str, dict[str, object]]) -> None:
    for workflowId, workflow in workflows.items():
        rawNodes = workflow.get("nodes")
        nodes = rawNodes if isinstance(rawNodes, list) else []
        kinds = {
            node.get("kind")
            for node in nodes
            if isinstance(node, dict)
        }
        if "workflow_input" not in kinds:
            inputId = "__workflow_input__" if workflowId == "main" else f"__workflow_input__:{workflowId}"
            nodes.append({"nodeId": inputId, "kind": "workflow_input"})
        if "workflow_output" not in kinds:
            outputId = "__workflow_output__" if workflowId == "main" else f"__workflow_output__:{workflowId}"
            nodes.append({"nodeId": outputId, "kind": "workflow_output"})
        workflow["nodes"] = nodes


def _layout_from_nodes(rawNodes: object) -> dict[str, object]:
    positions: dict[str, dict[str, float]] = {}
    if isinstance(rawNodes, list):
        for rawNode in rawNodes:
            if not isinstance(rawNode, dict):
                continue
            nodeId = rawNode.get("nodeId")
            if not isinstance(nodeId, str):
                continue
            x = rawNode.get("x", 20.0)
            y = rawNode.get("y", 20.0)
            if isinstance(x, (int, float)) and not isinstance(x, bool) and isinstance(y, (int, float)) and not isinstance(y, bool):
                positions[nodeId] = {"x": float(x), "y": float(y)}
    return {"nodePositions": positions}


def _copy_v2_defaults(source: dict[str, object]) -> dict[str, object]:
    project = source.get("project")
    projectMap = dict(project) if isinstance(project, dict) else {}
    projectMap.setdefault("projectId", str(uuid4()))
    projectMap.setdefault("name", "project")
    projectMap.setdefault("revision", 1)
    now = utc_now_iso()
    projectMap.setdefault("createdAt", now)
    projectMap.setdefault("updatedAt", projectMap["createdAt"])
    workflowsRaw = source.get("workflows")
    workflows: dict[str, dict[str, object]] = {}
    if isinstance(workflowsRaw, dict):
        for workflowId, workflow in workflowsRaw.items():
            if isinstance(workflowId, str) and isinstance(workflow, dict):
                workflows[workflowId] = _normalize_workflow(workflowId, workflow)
    if not workflows:
        workflows = _extract_workflows(source)
    _ensure_legacy_boundaries(workflows)
    orderRaw = source.get("workflowOrder")
    order = [item for item in orderRaw if isinstance(item, str)] if isinstance(orderRaw, list) else []
    order = [item for item in order if item in workflows]
    order.extend(item for item in workflows if item not in order)
    entry = _first_string(source.get("entryWorkflowId")) or order[0]
    if entry not in workflows:
        entry = order[0]
    runtimeRaw = source.get("runtime")
    dependenciesRaw = source.get("dependencies")
    devicesRaw = source.get("devices")
    return {
        "schemaVersion": SCHEMA_VERSION,
        "project": projectMap,
        "entryWorkflowId": entry,
        "workflowOrder": order,
        "workflows": workflows,
        "runtime": _runtime_defaults(runtimeRaw if isinstance(runtimeRaw, dict) else {}),
        "dependencies": {
            "operators": (dependenciesRaw.get("operators", []) if isinstance(dependenciesRaw, dict) else [])
        },
        "devices": {
            "bindings": (devicesRaw.get("bindings", {}) if isinstance(devicesRaw, dict) else {})
        },
    }


def _runtime_defaults(runtime: dict[str, object]) -> dict[str, object]:
    normalized = {
        key: value for key, value in runtime.items() if key != "sourceImagePath"
    }
    normalized.setdefault("maxConcurrentJobs", 2)
    normalized.setdefault("gracefulStopTimeoutMs", 5000)
    normalized.setdefault("heartbeatTimeoutMs", 5000)
    normalized.setdefault("eventRetentionPerJob", 10000)
    return normalized


def _copy_object_map(value: object) -> dict[str, object]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError("workflow interface must be an object")
    if any(not isinstance(key, str) for key in value):
        raise ValueError("workflow interface keys must be strings")
    return {key: deepcopy(item) for key, item in value.items()}


def _first_string(*values: Any) -> str | None:
    for value in values:
        if isinstance(value, str) and value.strip() != "":
            return value
    return None


def _validateLegacyPayload(source: dict[str, object], sourceSchema: object) -> None:
    unknownFields = sorted(set(source) - _LEGACY_TOP_LEVEL_FIELDS)
    if unknownFields:
        raise ValueError(
            "legacy project contains unsupported top-level fields: "
            + ", ".join(unknownFields)
        )
    version = source.get("version")
    if version is not None and version not in {"1.0", "1"}:
        raise ValueError(f"unsupported legacy project version: {version!r}")
    if sourceSchema is None and not any(
        marker in source for marker in ("version", "designer", "meta", "projectId")
    ):
        raise ValueError(
            "project schemaVersion is missing and no recognized v1 markers were found"
        )
    for fieldName in ("meta", "project", "runtime", "dependencies", "devices"):
        value = source.get(fieldName)
        if value is not None and not isinstance(value, dict):
            raise ValueError(f"legacy project field must be an object: {fieldName}")
    for fieldName in ("meta", "project"):
        value = source.get(fieldName)
        if isinstance(value, dict):
            _rejectUnknownFields(value, _LEGACY_METADATA_FIELDS, fieldName)
    runtime = source.get("runtime")
    if isinstance(runtime, dict):
        _rejectUnknownFields(runtime, _LEGACY_RUNTIME_FIELDS, "runtime")
    dependencies = source.get("dependencies")
    if isinstance(dependencies, dict):
        _rejectUnknownFields(dependencies, {"operators"}, "dependencies")
    devices = source.get("devices")
    if isinstance(devices, dict):
        _rejectUnknownFields(devices, {"bindings"}, "devices")


def _rejectUnknownFields(value: dict[object, object], allowed: set[str], label: str) -> None:
    unknownFields = sorted(str(key) for key in value if key not in allowed)
    if unknownFields:
        raise ValueError(
            f"legacy {label} contains unsupported fields: " + ", ".join(unknownFields)
        )
