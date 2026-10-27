"""Resolve device targets against installed metadata, never project-supplied schemas."""
from collections.abc import Mapping
from typing import Any

from emo_master.core.plugin.devices import DeviceType
from emo_master.core.project.resources import DeviceParameterTarget
from emo_master.core.workflow.errors import ValidationIssue, WorkflowCompileError


def trustedDeviceTypes():
    from pathlib import Path
    from emo_master import __version__
    from emo_master.core.plugin.devices import scanDeviceTypes

    root = Path(__file__).resolve().parents[2] / "plugins" / "builtins"
    return scanDeviceTypes([root], __version__)[0]


def deviceDeclaration(definition, deviceTypes: Mapping[str, DeviceType] | None, *, deviceId='') -> DeviceType:
    declaration = (deviceTypes or {}).get(definition.deviceTypeId)
    if declaration is None or declaration.version != definition.driverVersion:
        raise WorkflowCompileError([ValidationIssue('E_DEVICE_DEPENDENCY',
            f'device metadata missing or version mismatch: {definition.deviceTypeId}；要求 {definition.driverVersion}；'
            f'当前安装 {declaration.version if declaration else "未安装"}',
            fieldPath=f'devices.definitions.{deviceId}.driverVersion' if deviceId else 'dependencies.deviceTypes.'+definition.deviceTypeId)])
    return declaration


def deviceTarget(document, target: DeviceParameterTarget, deviceTypes, purpose: str):
    path = f'devices.definitions.{target.deviceId}.{target.section}.' + '.'.join(target.parameterPath)
    def invalid(message):
        return WorkflowCompileError([ValidationIssue('E_DEVICE_RESOURCE', message,
            projectId=document.project.projectId, fieldPath=path)])
    definition = document.devices.definitions.get(target.deviceId)
    if definition is None:
        raise invalid(f"device target missing: {target.deviceId}")
    declaration = deviceDeclaration(definition, deviceTypes, deviceId=target.deviceId)
    schema: Any = declaration.connectionSchema if target.section == "connection" else declaration.defaultsSchema
    for part in target.parameterPath:
        schema = schema.get("properties", {}).get(part, {})
    if schema.get("type") != "string" or schema.get("xResourcePurpose") != purpose:
        raise invalid("device parameter purpose is not approved by installed metadata")
    if purpose in {"input_image", "model"} and (
            schema.get("xWidget") != "file" or schema.get("xFileMode") != "open"):
        raise invalid("device resource must target a declared input file")
    if purpose in {"output_file", "output_directory"} and (
            schema.get("xWidget") != "file" or schema.get("xFileMode") != "save"):
        raise invalid("device output must target a declared output path")
    return getattr(definition, target.section)


def deviceFileContract(document, deviceTypes):
    result = {}

    def visit(value, schema, path):
        if schema.get("xWidget") == "file":
            result[path] = value
        elif isinstance(value, dict):
            for key, child in schema.get("properties", {}).items():
                if key in value:
                    visit(value[key], child, (*path, key))
        elif isinstance(value, list):
            for index, item in enumerate(value):
                visit(item, schema.get("items", {}), (*path, str(index)))

    for deviceId, definition in document.devices.definitions.items():
        declaration = deviceDeclaration(definition, deviceTypes)
        visit(definition.connection, declaration.connectionSchema, ("device", deviceId, "connection"))
        visit(definition.defaults, declaration.defaultsSchema, ("device", deviceId, "defaults"))
    for wid, flow in document.workflows.items():
        for node in flow.nodes:
            for slot, binding in node.deviceBindings.items():
                declaration = deviceDeclaration(document.devices.definitions[binding.deviceId], deviceTypes)
                visit(node.deviceOverrides.get(slot, {}), declaration.defaultsSchema, ("node", wid, node.nodeId, slot))
    return result
