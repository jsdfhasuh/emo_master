"""Prepare an in-memory project using its directory, not the process CWD."""
from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from emo_master.core.plugin.models import PluginDescriptor
from emo_master.core.project.models import ProjectDocument, WorkflowDefinition, WorkflowNode
from emo_master.core.project.files import projectFiles
from emo_master.core.project.global_variables import validateBindings
from emo_master.core.project.snapshots import _setPath, verifyResources
from emo_master.core.project.resources import DeviceParameterTarget
from emo_master.core.project.device_resources import deviceDeclaration, deviceTarget
from emo_master.core.workflow.errors import ValidationIssue, WorkflowCompileError, locatedValidationError


def nodeFileSchema(node: WorkflowNode, workflow: WorkflowDefinition, schema: dict[str, Any]) -> dict[str, Any]:
    """Exclude only a connected reader's dormant literal from file I/O preparation."""
    if node.operatorId == "vision.io.video_loader" and any(
        edge.toNode == node.nodeId and edge.toPort == "videoPath" for edge in workflow.edges
    ):
        return {**schema, "properties": {key: value for key, value in schema.get("properties", {}).items()
                                         if key != "videoPath"}}
    return schema


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
        raise WorkflowCompileError([ValidationIssue('E_DEVICE_SITE', 'undeclared device site field: '+key,
            projectId=document.project.projectId, fieldPath='siteValues.'+key) for key in sorted(set(sites)-deviceSites)])
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
                    raise _targetError(document, target, 'E_DEVICE_RESOURCE', 'device resource reference missing')
                params = deviceTarget(prepared, target, deviceTypes, item.purpose)
                _setPath(params, target.parameterPath, resources[binding.resourceId])
                continue
            node = nodes.get((target.workflowId, target.nodeId))
            if node is None or node.kind != "operator" or binding.resourceId not in resources:
                raise _targetError(document, target, 'E_RESOURCE_TARGET', 'resource reference/parameter target missing')
            descriptor = registry.get(node.operatorId or "")
            if descriptor is None:
                raise WorkflowCompileError([ValidationIssue('E_OPERATOR_UNKNOWN', f'operator missing: {node.operatorId}',
                    projectId=document.project.projectId, workflowId=target.workflowId, nodeId=target.nodeId, fieldPath='operatorId')])
            schema: Any = descriptor.manifest.paramSchema
            for part in target.parameterPath:
                schema = schema.get("properties", {}).get(part, {})
            if schema.get("xWidget") != "file" or schema.get("xFileMode") != "open":
                raise _targetError(document, target, 'E_RESOURCE_TARGET', 'resource binding must target an input file parameter')
            _setPath(node.params, target.parameterPath, resources[binding.resourceId])
        for siteBinding in prepared.resources.siteBindings:
            target = siteBinding.target
            if isinstance(target, DeviceParameterTarget):
                params = deviceTarget(prepared, target, deviceTypes, siteBinding.purpose)
                if siteBinding.field in sites:
                    if not isinstance(sites[siteBinding.field], str) or not sites[siteBinding.field]:
                        raise _targetError(document, target, 'E_DEVICE_SITE', 'device site field must be a nonempty string')
                    _setPath(params, target.parameterPath, sites[siteBinding.field])
                value: object = params
                for part in target.parameterPath:
                    value = value.get(part) if isinstance(value, dict) else None
                if siteBinding.required and (not isinstance(value, str) or not value):
                    raise _targetError(document, target, 'E_DEVICE_SITE', f'required device site field missing: {siteBinding.field}')
                if siteBinding.purpose == "secret_ref" and value:
                    import re
                    if not isinstance(value, str) or not re.fullmatch(r"secret://[A-Za-z0-9_./-]+@[A-Za-z0-9_.-]+", value):
                        raise _targetError(document, target, 'E_DEVICE_SITE', 'device secrets must be versioned references, never plaintext')
                continue
            node = nodes.get((target.workflowId, target.nodeId))
            if node is None or node.kind != "operator":
                raise _targetError(document, target, 'E_SITE_TARGET', 'site parameter target missing')
            value = node.params if node is not None else None
            for part in target.parameterPath:
                value = value.get(part) if isinstance(value, dict) else None
            if siteBinding.required and (not isinstance(value, str) or not value):
                raise _targetError(document, target, 'E_SITE_TARGET', f'project parameter missing: {target.workflowId}/{target.nodeId}/'
                                   + '/'.join(target.parameterPath))

    outputs: list[tuple[Path, str]] = []
    for deviceId, definition in prepared.devices.definitions.items():
        try:
            declaration = deviceDeclaration(definition, deviceTypes, deviceId=deviceId)
        except WorkflowCompileError as error:
            raise locatedValidationError(error, 'E_DEVICE_DEPENDENCY', projectId=document.project.projectId) from error
        try:
            definition = declaration.validateDefinition(definition)
        except ValueError as error:
            raise locatedValidationError(error, 'E_DEVICE_DEFINITION', projectId=document.project.projectId,
                prefix=f'devices.definitions.{deviceId}') from error
        for section, schema in (("connection", declaration.connectionSchema), ("defaults", declaration.defaultsSchema)):
            try:
                _resolveFiles(getattr(definition, section), schema, root, inputs, outputs,
                              f'devices.definitions.{deviceId}.{section}')
            except ValueError as error:
                raise locatedValidationError(error, 'E_DEVICE_RESOURCE', projectId=document.project.projectId) from error
        prepared.devices.definitions[deviceId] = definition
    for (wid, nid), node in nodes.items():
        if node.kind != "operator":
            continue
        descriptor = registry.get(node.operatorId or "")
        if descriptor is None:
            raise WorkflowCompileError([ValidationIssue('E_OPERATOR_UNKNOWN', f'operator missing: {node.operatorId}',
                projectId=document.project.projectId, workflowId=wid, nodeId=nid, fieldPath='operatorId')])
        # Reject unsupported file substitution before inspecting dormant literal
        # paths, including at export/install time (before any package is written).
        validateBindings(node.globalVariableBindings, prepared.globalVariables, descriptor.manifest.paramSchema)
        try:
            schema = nodeFileSchema(node, prepared.workflows[wid], descriptor.manifest.paramSchema)
            _resolveFiles(node.params, schema, root, inputs, outputs, 'params')
            if node.operatorId == "vision.io.detection_archive" or (node.operatorId == "vision.io.image_saver" and any(
                edge.toNode == nid and edge.toPort == "fileName" for edge in prepared.workflows[wid].edges
            )):
                outputPath = node.params.get("outputPath")
                if isinstance(outputPath, str) and outputPath:
                    # A dynamic basename can target any file in this directory.
                    outputs.append((Path(outputPath).parent, 'params.outputPath'))
        except ValueError as error:
            raise locatedValidationError(error, 'E_RESOURCE_INPUT', projectId=document.project.projectId,
                workflowId=wid, nodeId=nid) from error
        for slot, deviceBinding in node.deviceBindings.items():
            if deviceBinding.deviceId not in prepared.devices.definitions:
                raise WorkflowCompileError([ValidationIssue('E_DEVICE_REFERENCE', f'device missing: {deviceBinding.deviceId}',
                    document.project.projectId, wid, nid, f'deviceBindings.{slot}.deviceId')])
            definition = prepared.devices.definitions[deviceBinding.deviceId]
            declaration = deviceDeclaration(definition, deviceTypes)
            overrides = node.deviceOverrides.get(slot, {})
            try:
                _resolveFiles(overrides, declaration.defaultsSchema, root, inputs, outputs,
                              f'deviceOverrides.{slot}', materializeDefaults=False)
                declaration.validateNodeSources(definition, overrides, node.deviceValueBindings.get(slot, {}), slot=slot)
            except ValueError as error:
                raise locatedValidationError(error, 'E_DEVICE_PARAMETERS', projectId=document.project.projectId,
                    workflowId=wid, nodeId=nid, fieldPath=f'deviceOverrides.{slot}') from error
    protected = [path.resolve() for path in (*inputs, *protectedPaths)]
    for output, location in outputs:
        if any(output == path or output.is_relative_to(path) or path.is_relative_to(output)
               or (output.exists() and path.is_file() and output.samefile(path)) for path in protected):
            raise WorkflowCompileError([ValidationIssue('E_RESOURCE_OUTPUT',
                f'output overlaps project input or Runtime data: {output}', projectId=document.project.projectId, fieldPath=location)])
    return prepared


def _targetError(document, target, code, message):
    device = isinstance(target, DeviceParameterTarget)
    return WorkflowCompileError([ValidationIssue(code, message, projectId=document.project.projectId,
        workflowId='' if device else target.workflowId, nodeId='' if device else target.nodeId,
        fieldPath=(f'devices.definitions.{target.deviceId}.{target.section}.' if device else 'params.')
                  + '.'.join(target.parameterPath))])


def _resolveFiles(value, schema, root, inputs, outputs, location, *, materializeDefaults=True):
    if schema.get("xWidget") == "file":
        if not isinstance(value, str) or not value:
            return value  # Leave optional/default values to the compiler and operator.
        path = Path(value).expanduser()
        path = (root / path).resolve() if not path.is_absolute() else path.resolve()
        mode = schema.get("xFileMode")
        if mode == "open":
            if not path.is_file():
                raise WorkflowCompileError([ValidationIssue('E_RESOURCE_INPUT', f'{location}: input file not found: {path}', fieldPath=location)])
            inputs.add(path)
        elif mode == "directory":
            if not path.is_dir():
                raise WorkflowCompileError([ValidationIssue('E_RESOURCE_INPUT', f'{location}: input directory not found: {path}', fieldPath=location)])
            inputs.add(path)
        elif mode == "save":
            outputs.append((path, location))
        else:
            raise WorkflowCompileError([ValidationIssue('E_RESOURCE_INPUT', f'{location}: unsupported file mode: {mode}', fieldPath=location)])
        return str(path)
    if schema.get("type") == "object" or "properties" in schema:
        if not isinstance(value, dict):
            return value
        for key, child in schema.get("properties", {}).items():
            if materializeDefaults and key not in value and child.get("xWidget") == "file" and "default" in child:
                value[key] = child["default"]
            if key not in value:
                continue
            value[key] = _resolveFiles(value[key], child, root, inputs, outputs, f"{location}.{key}",
                                      materializeDefaults=materializeDefaults)
    elif schema.get("type") == "array" and isinstance(value, list):
        for index, item in enumerate(value):
            value[index] = _resolveFiles(item, schema.get("items", {}), root, inputs, outputs, f"{location}.{index}",
                                        materializeDefaults=materializeDefaults)
    return value
