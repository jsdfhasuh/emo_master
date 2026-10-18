"""Prepare an in-memory project using its directory, not the process CWD."""
from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from emo_master.core.plugin.models import PluginDescriptor
from emo_master.core.project.models import ProjectDocument
from emo_master.core.project.files import projectFiles
from emo_master.core.project.global_variables import validateBindings
from emo_master.core.project.snapshots import _setPath, verifyResources
from emo_master.core.project.resources import DeviceParameterTarget
from emo_master.core.project.device_resources import deviceDeclaration, deviceTarget


def prepareRuntimeProject(
    document: ProjectDocument, root: Path,
    registry: Mapping[str, PluginDescriptor], *, protectedPaths: tuple[Path, ...] = (),
    deviceTypes=None, siteValues: Mapping[str, str] | None = None, resourcePaths=None, allowMissingRoot=False,
) -> ProjectDocument:
    from emo_master.core.project.dependencies import validateOperatorDependencies
    validateOperatorDependencies(document, registry)
    root = root.resolve(strict=not allowMissingRoot)
    prepared = document.model_copy(deep=True)
    nodes = {(wid, node.nodeId): node for wid, workflow in prepared.workflows.items()
             for node in workflow.nodes}
    inputs = {root / "project.json", *(projectFiles(root) if root.is_dir() else [])}
    sites = dict(siteValues or {})
    deviceSites = {b.field for b in prepared.resources.siteBindings
                   if isinstance(b.target, DeviceParameterTarget)} if prepared.resources else set()
    if set(sites) - deviceSites:
        raise ValueError("undeclared device site field")
    if prepared.resources is not None:
        resources = verifyResources(prepared, root, resourcePaths=resourcePaths)
        prepared._pageResourcePaths = {key: path for key, path in resources.items()
            if prepared.resources.items[key].purpose in {"page_image", "icon"}}
        inputs.update(Path(path).resolve() for path in resources.values())
        for binding in prepared.resources.parameterBindings:
            target = binding.target
            if isinstance(target, DeviceParameterTarget):
                item = prepared.resources.items.get(binding.resourceId)
                if item is None:
                    raise ValueError("device resource reference missing")
                params = deviceTarget(prepared, target, deviceTypes, item.purpose)
                _setPath(params, target.parameterPath, resources[binding.resourceId])
                continue
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
            if isinstance(target, DeviceParameterTarget):
                params = deviceTarget(prepared, target, deviceTypes, siteBinding.purpose)
                if siteBinding.field in sites:
                    if not isinstance(sites[siteBinding.field], str) or not sites[siteBinding.field]:
                        raise ValueError("device site field must be a nonempty string")
                    _setPath(params, target.parameterPath, sites[siteBinding.field])
                value: object = params
                for part in target.parameterPath:
                    value = value.get(part) if isinstance(value, dict) else None
                if siteBinding.required and (not isinstance(value, str) or not value):
                    raise ValueError(f"required device site field missing: {siteBinding.field}")
                if siteBinding.purpose == "secret_ref" and value:
                    import re
                    if not isinstance(value, str) or not re.fullmatch(r"secret://[A-Za-z0-9_./-]+@[A-Za-z0-9_.-]+", value):
                        raise ValueError("device secrets must be versioned references, never plaintext")
                continue
            node = nodes.get((target.workflowId, target.nodeId))
            if node is None or node.kind != "operator":
                raise ValueError("site parameter target missing")
            value = node.params if node is not None else None
            for part in target.parameterPath:
                value = value.get(part) if isinstance(value, dict) else None
            if siteBinding.required and (not isinstance(value, str) or not value):
                raise ValueError(f"project parameter missing: {target.workflowId}/{target.nodeId}/"
                                 + "/".join(target.parameterPath))

    outputs: list[Path] = []
    for deviceId, definition in prepared.devices.definitions.items():
        declaration = deviceDeclaration(definition, deviceTypes)
        definition = declaration.validateDefinition(definition)
        for section, schema in (("connection", declaration.connectionSchema), ("defaults", declaration.defaultsSchema)):
            _resolveFiles(getattr(definition, section), schema, root, inputs, outputs, f"devices/{deviceId}/{section}")
        prepared.devices.definitions[deviceId] = declaration.validateDefinition(definition)
    for (wid, nid), node in nodes.items():
        if node.kind != "operator":
            continue
        descriptor = registry.get(node.operatorId or "")
        if descriptor is None:
            raise ValueError(f"operator missing: {node.operatorId}")
        # Reject unsupported file substitution before inspecting dormant literal
        # paths, including at export/install time (before any package is written).
        validateBindings(node.globalVariableBindings, prepared.globalVariables, descriptor.manifest.paramSchema)
        _resolveFiles(node.params, descriptor.manifest.paramSchema, root, inputs, outputs, f"{wid}/{nid}")
        for slot, deviceBinding in node.deviceBindings.items():
            definition = prepared.devices.definitions[deviceBinding.deviceId]
            declaration = deviceDeclaration(definition, deviceTypes)
            overrides = node.deviceOverrides.get(slot, {})
            _resolveFiles(overrides, declaration.defaultsSchema, root, inputs, outputs,
                          f"{wid}/{nid}/{slot}", materializeDefaults=False)
            declaration.validateNodeSources(definition, overrides, node.deviceValueBindings.get(slot, {}))
    protected = [path.resolve() for path in (*inputs, *protectedPaths)]
    for output in outputs:
        if any(output == path or output.is_relative_to(path) or path.is_relative_to(output)
               or (output.exists() and path.is_file() and output.samefile(path)) for path in protected):
            raise ValueError(f"output overlaps project input or Runtime data: {output}")
    return prepared


def _resolveFiles(value, schema, root, inputs, outputs, location, *, materializeDefaults=True):
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
        elif mode == "directory":
            if not path.is_dir():
                raise ValueError(f"{location}: input directory not found: {path}")
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
            if materializeDefaults and key not in value and child.get("xWidget") == "file" and "default" in child:
                value[key] = child["default"]
            if key not in value:
                continue
            value[key] = _resolveFiles(value[key], child, root, inputs, outputs, f"{location}/{key}",
                                      materializeDefaults=materializeDefaults)
    elif schema.get("type") == "array" and isinstance(value, list):
        for index, item in enumerate(value):
            value[index] = _resolveFiles(item, schema.get("items", {}), root, inputs, outputs, f"{location}/{index}",
                                        materializeDefaults=materializeDefaults)
    return value
