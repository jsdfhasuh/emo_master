"""Static output discovery using project instances and trusted manifest metadata."""
from __future__ import annotations

from dataclasses import dataclass, field
from collections.abc import Mapping

from emo_master.core.contracts.port_types import normalizePortSpec, normalizePortType
from emo_master.core.plugin.models import PluginManifest
from emo_master.core.project.models import ProjectDocument
from emo_master.core.presentation.models import CallStep, DataSource


@dataclass(frozen=True)
class Issue:
    path: str
    code: str
    message: str


@dataclass(frozen=True)
class OutputEntry:
    workflowId: str
    nodeId: str | None
    displayName: str
    operatorId: str | None
    port: str
    spec: dict[str, object]
    fields: dict[tuple[str, ...], str] = field(default_factory=dict)
    hint: str = ""
    issues: tuple[Issue, ...] = ()


# Projections retain collection cardinality. No implicit [0] or arbitrary JSON paths.
# Field names follow core/contracts/geometry2d.py, Blob/DetectionCollection.toPayload.
_FIELDS = {
    "blobCollection": {("items", "*", "area"): "collection",
                       ("items", "*", "centroid", "x"): "collection",
                       ("items", "*", "centroid", "y"): "collection"},
    "detectionCollection": {("items", "*", "classId"): "collection",
                            ("items", "*", "label"): "collection",
                            ("items", "*", "confidence"): "collection"},
    "point2d": {("x",): "number", ("y",): "number"},
}


def presentationType(spec: object) -> str:
    value = normalizePortType(spec)
    if value in {"integer", "number", "string", "boolean", "image"}:
        return value
    if value.endswith("Collection") or value.startswith("list<"):
        return "collection"
    if value in {"geometry2d", "point2d", "bbox2d", "rotatedBox2d", "polygon2d", "line2d", "circle2d"}:
        return "geometry"
    return "json"


def buildOutputCatalog(
    project: ProjectDocument, manifests: Mapping[str, PluginManifest]
) -> list[OutputEntry]:
    entries: list[OutputEntry] = []
    for workflowId, workflow in project.workflows.items():
        for port, spec in workflow.outputs.items():
            entries.append(_entry(workflowId, None, workflow.name, None, port, spec))
        for node in workflow.nodes:
            if node.kind != "operator":
                continue
            manifest = manifests.get(node.operatorId or "")
            ports = manifest.outputPorts if manifest else node.outputPorts
            for port in sorted(set(ports) | set(node.outputPorts)):
                issues: list[Issue] = []
                path = f"workflows.{workflowId}.nodes.{node.nodeId}.outputPorts.{port}"
                if manifest is None:
                    issues.append(Issue(path, "plugin_missing", "trusted plugin manifest unavailable"))
                elif port not in ports or (port in node.outputPorts and
                        normalizePortType(node.outputPorts[port]) != normalizePortType(ports[port])):
                    issues.append(Issue(path, "port_conflict", "node and plugin output contracts disagree"))
                spec = ports.get(port, node.outputPorts.get(port, "any"))
                hint = ""
                if node.operatorId == "vision.analysis.blob" and port == "overlay":
                    hint = "optional overlay; drawOverlay defaults to false (parameters unchanged)"
                entries.append(_entry(workflowId, node.nodeId, node.displayName or node.nodeId,
                                      node.operatorId, port, spec, hint, tuple(issues)))
    return entries


def _entry(workflowId, nodeId, name, operatorId, port, spec, hint="", issues=()):
    try:
        normalized = normalizePortSpec(spec)
    except ValueError as error:
        normalized = {"type": "object"}
        issues = (*issues, Issue(f"{workflowId}.{nodeId}.{port}", "port_invalid", str(error)))
    return OutputEntry(workflowId, nodeId, name, operatorId, port, normalized,
                       dict(_FIELDS.get(normalizePortType(normalized), {})), hint, issues)


def resolveCallPath(project: ProjectDocument, entry: str, path: list[CallStep]) -> str:
    current = entry
    if current not in project.workflows:
        raise ValueError("entry workflow missing")
    for step in path:
        target: object
        node = next((n for n in project.workflows[current].nodes if n.nodeId == step.nodeId), None)
        if node is None:
            raise ValueError(f"call node missing: {current}/{step.nodeId}")
        if step.relation == "subflow" and node.kind == "subflow":
            target = node.targetWorkflowId
        elif step.relation.startswith("loop_") and node.kind == "loop":
            key = "bodyWorkflowId" if step.relation == "loop_body" else "conditionWorkflowId"
            target = node.loop.get(key)
        else:
            raise ValueError("call relation does not match node kind")
        if not isinstance(target, str) or target not in project.workflows:
            raise ValueError("call target missing")
        current = target
    return current


def sourceType(source: DataSource, entries: list[OutputEntry]) -> str:
    if source.kind == "runtime_status":
        if source.name not in {"job_state", "connection_state"}:
            raise ValueError("unknown runtime status")
        return "string"
    if source.kind == "global_counter":
        return "integer"
    entry = next((item for item in entries if (item.workflowId, item.nodeId, item.port) ==
                  (source.workflowId, source.nodeId, source.port)), None)
    if entry is None:
        raise ValueError("output node/port missing")
    if entry.issues:
        raise ValueError("; ".join(issue.message for issue in entry.issues))
    if source.fieldPath:
        resolved = entry.fields.get(tuple(source.fieldPath))
        if resolved is None:
            raise ValueError("unknown field or implicit collection element selection")
        return resolved
    return presentationType(entry.spec)
