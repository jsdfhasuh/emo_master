"""Explicit, immutable P1 preparation records. Does not create or start a Job.

The runtime workflow compiler, file materialization and device permission checks
remain mandatory at P2 integration; this module never imports an operator class.
"""
from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Literal
from uuid import uuid4

from emo_master.core.plugin.models import PluginManifest
from emo_master.core.presentation.models import walkComponents
from emo_master.core.presentation.validation import validateBindings
from emo_master.core.project.models import ProjectDocument
from emo_master.core.project.resources import ResourceBinding, SiteBinding, safeRelativePath
from emo_master.core.workflow.validation import validateProjectDocument


# Application-owned table, never read from project JSON. Extension requires code review.
PARAMETER_PURPOSES: dict[tuple[str, tuple[str, ...]], str] = {
    ("vision.io.image_loader", ("imagePath",)): "input_image",
    ("vision.io.image_saver", ("outputPath",)): "output_file",
    ("vision.io.huaray_camera", ("ipAddress",)): "device_address",
    ("vision.io.huaray_camera", ("cameraKey",)): "camera_serial",
    ("communication.tcp.client", ("host",)): "device_address",
}


def canonicalJson(value: object) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":"), allow_nan=False)


def revisionOf(value: object) -> str:
    return hashlib.sha256(canonicalJson(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ProjectSnapshot:
    snapshotId: str
    mode: Literal["debug", "release"]
    projectId: str
    draftRevision: int | None
    releaseRevision: str | None
    executionRevision: str
    capturePlanRevision: str
    siteBindingRevision: str
    projectJson: str
    capturePlanJson: str
    parametersJson: str
    runtimeDbPath: str
    outputRoot: str


def _inside(root: Path, relative: str) -> Path:
    safeRelativePath(relative)
    root = root.resolve(strict=True)
    candidate = root / relative
    resolved = candidate.resolve()
    if not resolved.is_relative_to(root):
        raise ValueError("resource or site path escapes declared root")
    current = candidate
    while current != root:
        if current.is_symlink() or (current.exists() and current.resolve() != current.absolute()):
            raise ValueError("resource or site path crosses a link")
        current = current.parent
    return candidate


def verifyResources(project: ProjectDocument, root: Path) -> dict[str, str]:
    assert project.resources is not None
    resolved: dict[str, str] = {}
    for resourceId, item in project.resources.items.items():
        path = _inside(root, item.path)
        if not path.is_file() or path.stat().st_size != item.size:
            raise ValueError(f"resource missing or size mismatch: {resourceId}")
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
        if digest.hexdigest() != item.sha256:
            raise ValueError(f"resource digest mismatch: {resourceId}")
        resolved[resourceId] = str(path)
    return resolved


def _parameters(value: Any, schema: Mapping, path: str) -> Any:
    """Normalize and check the supported manifest parameter-schema subset.

    Reject unsupported validation keywords rather than silently skipping them.
    Operator-specific semantic validation still belongs to the runtime compiler.
    """
    supported = {"type", "properties", "required", "additionalProperties", "default", "enum",
                 "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "items",
                 "minLength", "maxLength", "minItems", "maxItems", "pattern", "title", "description"}
    unknown = {key for key in schema if key not in supported and not key.startswith("x")}
    if unknown:
        raise ValueError(f"{path}: unsupported parameter constraints {sorted(unknown)}")
    kind = schema.get("type", "object")
    valid = {"object": isinstance(value, dict), "array": isinstance(value, list),
             "string": isinstance(value, str), "integer": type(value) is int,
             "number": type(value) in (int, float), "boolean": type(value) is bool}
    if not valid.get(kind, False):
        raise ValueError(f"{path}: expected {kind}")
    if "enum" in schema and value not in schema["enum"]:
        raise ValueError(f"{path}: value outside enum")
    if kind == "object":
        properties = schema.get("properties", {})
        if not schema.get("additionalProperties", True) and set(value) - set(properties):
            raise ValueError(f"{path}: unknown parameter")
        value = deepcopy(value)
        for key, definition in properties.items():
            if key not in value and "default" in definition:
                value[key] = deepcopy(definition["default"])
            if key in value:
                value[key] = _parameters(value[key], definition, f"{path}.{key}")
        if set(schema.get("required", [])) - set(value):
            raise ValueError(f"{path}: required parameter missing")
    if kind == "array":
        value = [_parameters(item, schema.get("items", {}), f"{path}[]") for item in value]
    if kind in {"integer", "number"}:
        if kind == "number":
            value = float(value)
        for key, invalid in [("minimum", lambda a, b: a < b), ("maximum", lambda a, b: a > b),
                             ("exclusiveMinimum", lambda a, b: a <= b),
                             ("exclusiveMaximum", lambda a, b: a >= b)]:
            if key in schema and invalid(value, schema[key]):
                raise ValueError(f"{path}: {key}")
    if kind in {"array", "string"}:
        suffix = "Length" if kind == "string" else "Items"
        if len(value) < schema.get("min" + suffix, 0) or len(value) > schema.get("max" + suffix, float("inf")):
            raise ValueError(f"{path}: size constraint")
    if kind == "string" and "pattern" in schema and re.search(schema["pattern"], value) is None:
        raise ValueError(f"{path}: pattern constraint")
    canonicalJson(value)  # reject non-finite/opaque values even in unconstrained attributes
    return value


def _setPath(params: dict, path: list[str], value: object) -> None:
    current = params
    for part in path[:-1]:
        child = current.get(part)
        if not isinstance(child, dict):
            raise ValueError("parameter target parent does not exist")
        current = child
    current[path[-1]] = value


def freezeProjectSnapshot(
    project: ProjectDocument, manifests: Mapping[str, PluginManifest], *,
    mode: Literal["debug", "release"], resourceRoot: Path, siteDataRoot: Path,
    siteValues: Mapping[str, str] | None = None, releaseRevision: str | None = None,
    counterNames: frozenset[str] = frozenset(),
    parameterPurposes: Mapping[tuple[str, tuple[str, ...]], str] | None = None,
) -> ProjectSnapshot:
    project = ProjectDocument.model_validate(project.model_dump())
    if mode not in {"debug", "release"} or (mode == "release") != bool(releaseRevision):
        raise ValueError("release requires releaseRevision; debug forbids it")
    if project.presentation is None or project.resources is None:
        raise ValueError("explicit 2.2 project required")
    workflowIssues = validateProjectDocument(project, manifests)
    if workflowIssues:
        raise ValueError("; ".join(f"{i.workflowId}/{i.nodeId}: {i.message}" for i in workflowIssues))
    issues = validateBindings(project, manifests, publish=mode == "release", counterNames=counterNames)
    if issues:
        raise ValueError("; ".join(f"{i.path}: {i.message}" for i in issues))
    resources = verifyResources(project, resourceRoot)
    sites = dict(siteValues or {})
    if set(sites) - {binding.field for binding in project.resources.siteBindings}:
        raise ValueError("undeclared site field")
    purposes: dict[tuple[str, tuple[str, ...]], str] = dict(
        PARAMETER_PURPOSES if parameterPurposes is None else parameterPurposes)
    snapshotId = str(uuid4())
    # Project ids need not be filesystem-safe; use their stable digest as the directory key.
    projectKey = hashlib.sha256(project.project.projectId.encode()).hexdigest()[:32]
    namespace = f"{projectKey}/{mode}" + (f"/{snapshotId}" if mode == "debug" else "")
    stateRoot = _inside(siteDataRoot, namespace)
    outputRoot = stateRoot / "outputs"
    canonicalParams: dict[tuple[str, str], dict] = {}
    resolvedParams: dict[tuple[str, str], dict] = {}
    schemas: dict[tuple[str, str], dict] = {}
    operatorIds: dict[tuple[str, str], str] = {}
    versions: dict[str, str] = {}
    for workflowId, workflow in project.workflows.items():
        for node in workflow.nodes:
            if node.kind != "operator":
                continue
            manifest = manifests.get(node.operatorId or "")
            if manifest is None:
                raise ValueError(f"plugin missing: {node.operatorId}")
            key = (workflowId, node.nodeId)
            versions[manifest.operatorId] = manifest.version
            schemas[key] = manifest.paramSchema
            operatorIds[key] = manifest.operatorId
            # Apply locked defaults before declared resource and site overrides. Validate finally.
            properties = manifest.paramSchema.get("properties", {})
            defaults = {name: deepcopy(spec["default"]) for name, spec in properties.items()
                        if "default" in spec} if isinstance(properties, dict) else {}
            params = {**defaults, **deepcopy(node.params)}
            canonicalParams[key] = deepcopy(params)
            resolvedParams[key] = params

    for binding in project.resources.parameterBindings:
        target = binding.target
        key = (target.workflowId, target.nodeId)
        item = project.resources.items.get(binding.resourceId)
        if item is None or key not in operatorIds:
            raise ValueError("resource reference/parameter target missing")
        if purposes.get((operatorIds[key], tuple(target.parameterPath))) != item.purpose:
            raise ValueError("resource parameter purpose is not approved by application metadata")
        _setPath(resolvedParams[key], target.parameterPath, resources[binding.resourceId])
        _setPath(canonicalParams[key], target.parameterPath, {"resourceId": binding.resourceId})

    for siteBinding in project.resources.siteBindings:
        target = siteBinding.target
        key = (target.workflowId, target.nodeId)
        if key not in operatorIds or purposes.get((operatorIds[key], tuple(target.parameterPath))) != siteBinding.purpose:
            raise ValueError("site override is not on the trusted parameter whitelist")
        value = sites.get(siteBinding.field)
        if value is None:
            if siteBinding.required or siteBinding.purpose in {"output_directory", "output_file"}:
                raise ValueError(f"required site field missing: {siteBinding.field}")
            # Omitted optional fields retain locked/draft parameters and their fingerprint.
            continue
        if not isinstance(value, str) or not value:
            raise ValueError("site field must be a nonempty string")
        if siteBinding.purpose in {"output_directory", "output_file"}:
            safeRelativePath(value)
            value = str(_inside(siteDataRoot, f"{namespace}/outputs/{value}"))
        if siteBinding.purpose == "secret_ref" and not re.fullmatch(r"secret://[A-Za-z0-9_./-]+@[A-Za-z0-9_.-]+", value):
            raise ValueError("secrets must be versioned local references, never plaintext")
        if siteBinding.purpose == "secret_ref":
            original: object = canonicalParams[key]
            for part in target.parameterPath:
                original = original.get(part) if isinstance(original, dict) else None
            if original and (not isinstance(original, str) or not re.fullmatch(
                r"secret://[A-Za-z0-9_./-]+@[A-Za-z0-9_.-]+", original
            )):
                raise ValueError("draft secret parameter must also be a reference")
        _setPath(resolvedParams[key], target.parameterPath, value)
        _setPath(canonicalParams[key], target.parameterPath, {"siteField": siteBinding.field})

    for key, params in resolvedParams.items():
        resolvedParams[key] = _parameters(params, schemas[key], "/".join(key))
        canonicalParams[key] = deepcopy(resolvedParams[key])
        # Reject undeclared known file parameters; never retain a developer path fallback.
        for (operatorId, path), purpose in purposes.items():
            if operatorIds[key] == operatorId and purpose in {"input_image", "model", "output_directory", "output_file"}:
                declarations: list[ResourceBinding | SiteBinding] = [
                    *project.resources.parameterBindings, *project.resources.siteBindings]
                if not any((b.target.workflowId, b.target.nodeId) == key
                           and tuple(b.target.parameterPath) == path for b in declarations):
                    raise ValueError("file parameter requires explicit resource/site declaration")
        properties = schemas[key].get("properties", {})
        if isinstance(properties, dict):
            for name, spec in properties.items():
                if isinstance(spec, dict) and spec.get("xWidget") == "file":
                    if (operatorIds[key], (name,)) not in purposes:
                        raise ValueError("custom file parameter has no trusted purpose adapter")

    # Replace expanded locations after normalization; machine paths never enter content revisions.
    for resourceBinding in project.resources.parameterBindings:
        target = resourceBinding.target
        _setPath(canonicalParams[(target.workflowId, target.nodeId)], target.parameterPath,
                 {"resourceId": resourceBinding.resourceId})
    for siteBinding in project.resources.siteBindings:
        if siteBinding.field in sites:
            target = siteBinding.target
            _setPath(canonicalParams[(target.workflowId, target.nodeId)], target.parameterPath,
                     {"siteField": siteBinding.field})

    algorithms = {}
    for workflowId, workflow in project.workflows.items():
        nodes = []
        for node in workflow.nodes:
            graphNode = node.model_dump(exclude={"displayName", "paramSchema"})
            graphNode["params"] = canonicalParams.get((workflowId, node.nodeId), node.params)
            nodes.append(graphNode)
        algorithms[workflowId] = {"inputs": workflow.inputs, "outputs": workflow.outputs,
                                  "nodes": nodes, "edges": [e.model_dump() for e in workflow.edges]}
    presentation = project.presentation
    used = {sourceId for page in presentation.pages.values()
            for component in walkComponents(page.components) for sourceId in component.bindings.values()}
    sources = sorted({canonicalJson(presentation.dataSources[key].model_dump()) for key in used})
    scopes = {presentation.dataSources[key].resultScopeId for key in used}
    for page in presentation.pages.values():
        scopes.update(page.resultScopeIds)
    capture = {"ruleVersion": "1.0", "sources": [json.loads(value) for value in sources],
               "scopes": {key: presentation.resultScopes[key].model_dump() for key in sorted(scopes)}}
    execution = {"entry": project.entryWorkflowId, "workflows": algorithms, "plugins": versions,
                 "resources": {key: item.model_dump(exclude={"path"})
                               for key, item in project.resources.items.items()
                               if item.purpose in {"input_image", "model"}}}
    return ProjectSnapshot(
        snapshotId, mode, project.project.projectId,
        project.project.revision if mode == "debug" else None, releaseRevision,
        revisionOf(execution), revisionOf(capture), revisionOf(sites),
        canonicalJson(project.model_dump()), canonicalJson(capture),
        canonicalJson({workflowId: {nodeId: params for (wid, nodeId), params in resolvedParams.items()
                                   if wid == workflowId} for workflowId in project.workflows}),
        str(stateRoot / "runtime.sqlite3"), str(outputRoot),
    )
