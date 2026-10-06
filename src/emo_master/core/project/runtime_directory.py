"""Prepare an in-memory project using its directory, not the process CWD."""
from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from emo_master.core.plugin.models import PluginDescriptor
from emo_master.core.project.models import ProjectDocument
from emo_master.core.project.snapshots import _setPath, verifyResources


def prepareRuntimeProject(
    document: ProjectDocument, root: Path,
    registry: Mapping[str, PluginDescriptor], *, protectedPaths: tuple[Path, ...] = (),
) -> ProjectDocument:
    root = root.resolve(strict=True)
    prepared = document.model_copy(deep=True)
    nodes = {(wid, node.nodeId): node for wid, workflow in prepared.workflows.items()
             for node in workflow.nodes}
    inputs = {root / "project.json"}
    if prepared.resources is not None:
        resources = verifyResources(prepared, root)
        inputs.update(Path(path).resolve() for path in resources.values())
        for binding in prepared.resources.parameterBindings:
            target = binding.target
            node = nodes.get((target.workflowId, target.nodeId))
            if node is None or node.kind != "operator" or binding.resourceId not in resources:
                raise ValueError("resource reference/parameter target missing")
            descriptor = registry.get(node.operatorId or "")
            if descriptor is None:
                raise ValueError(f"operator missing: {node.operatorId}")
            schema: Any = descriptor.manifest.paramSchema
            for part in target.parameterPath:
                schema = schema.get("properties", {}).get(part, {})
            if schema.get("xWidget") != "file" or schema.get("xFileMode") != "open":
                raise ValueError("resource binding must target an input file parameter")
            _setPath(node.params, target.parameterPath, resources[binding.resourceId])
        for siteBinding in prepared.resources.siteBindings:
            target = siteBinding.target
            node = nodes.get((target.workflowId, target.nodeId))
            if node is None or node.kind != "operator":
                raise ValueError("site parameter target missing")
            value: object = node.params if node is not None else None
            for part in target.parameterPath:
                value = value.get(part) if isinstance(value, dict) else None
            if siteBinding.required and (not isinstance(value, str) or not value):
                raise ValueError(f"project parameter missing: {target.workflowId}/{target.nodeId}/"
                                 + "/".join(target.parameterPath))

    outputs: list[Path] = []
    for (wid, nid), node in nodes.items():
        if node.kind != "operator":
            continue
        descriptor = registry.get(node.operatorId or "")
        if descriptor is None:
            raise ValueError(f"operator missing: {node.operatorId}")
        _resolveFiles(node.params, descriptor.manifest.paramSchema, root, inputs, outputs, f"{wid}/{nid}")
    protected = [path.resolve() for path in (*inputs, *protectedPaths)]
    for output in outputs:
        if any(output == path or output.is_relative_to(path) or path.is_relative_to(output)
               or (output.exists() and path.is_file() and output.samefile(path)) for path in protected):
            raise ValueError(f"output overlaps project input or Runtime data: {output}")
    return prepared


def _resolveFiles(value, schema, root, inputs, outputs, location):
    if schema.get("xWidget") == "file":
        if not isinstance(value, str) or not value:
            return value  # Leave optional/default values to the compiler and operator.
        path = Path(value).expanduser()
        path = (root / path).resolve() if not path.is_absolute() else path.resolve()
        mode = schema.get("xFileMode")
        if mode == "open":
            if not path.is_file():
                raise ValueError(f"{location}: input file not found: {path}")
            inputs.add(path)
        elif mode == "save":
            outputs.append(path)
        else:
            raise ValueError(f"{location}: unsupported file mode: {mode}")
        return str(path)
    if schema.get("type") == "object" or "properties" in schema:
        if not isinstance(value, dict):
            return value
        for key, child in schema.get("properties", {}).items():
            if key not in value and child.get("xWidget") == "file" and "default" in child:
                value[key] = child["default"]
            if key not in value:
                continue
            value[key] = _resolveFiles(value[key], child, root, inputs, outputs, f"{location}/{key}")
    elif schema.get("type") == "array" and isinstance(value, list):
        for index, item in enumerate(value):
            value[index] = _resolveFiles(item, schema.get("items", {}), root, inputs, outputs, f"{location}/{index}")
    return value
