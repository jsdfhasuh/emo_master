"""Device-only portable packages reuse workflow resource validation and staging."""
from copy import deepcopy
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from emo_master.core.project.devices import DeviceDefinition, DeviceId
from emo_master.core.project.resources import ResourcePlan, DeviceParameterTarget
from . import workflow_package_resources as resources


class DevicePackage(BaseModel):
    model_config = ConfigDict(extra='forbid')
    schemaVersion: Literal['1.0'] = '1.0'
    devices: dict[DeviceId, DeviceDefinition]
    resources: ResourcePlan = Field(default_factory=ResourcePlan)
    resourceData: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode='after')
    def deviceTargets(self):
        for binding in [*self.resources.parameterBindings, *self.resources.siteBindings]:
            if not isinstance(binding.target, DeviceParameterTarget) or binding.target.deviceId not in self.devices:
                raise ValueError('设备包只能引用包内设备属性')
        return self


def exportDevices(session, ids, root):
    catalog = session.document().devices.definitions
    if not ids or set(ids) - set(catalog):
        raise ValueError('请选择有效设备')
    package = DevicePackage(devices={key: catalog[key].model_copy(deep=True) for key in ids},
        resources=resources.dependencyPlan(session.workflows, {}, ids))
    resources.embedDependencies(package, root)
    resources.validateDependencies(package)
    return package


def importDevices(session, package, deviceTypes, root):
    if not package.devices or len(package.devices) > 256:
        raise ValueError('设备包必须包含 1 至 256 个设备')
    resources.validateDependencies(package)
    checked = {}
    for key, definition in package.devices.items():
        from emo_master.core.project.device_resources import deviceDeclaration
        declaration = deviceDeclaration(definition, deviceTypes, deviceId=key)
        declaration = declaration.model_copy(deep=True)
        for binding in package.resources.siteBindings:
            if binding.target.deviceId != key:
                continue
            schema = getattr(declaration, binding.target.section + 'Schema')
            for part in binding.target.parameterPath[:-1]:
                schema = schema.get('properties', {}).get(part, {})
            properties = schema.get('properties', {})
            name = binding.target.parameterPath[-1]
            if properties.get(name, {}).get('type') != 'string':
                raise ValueError('现场字段不属于设备类型的字符串属性')
            properties[name] = {'type': 'string'}
        checked[key] = declaration.validateDefinition(definition).model_copy(update={'enabled': False})
    from .device_catalog import DeviceCatalogEditor
    editor = DeviceCatalogEditor(session, deviceTypes)
    deviceIds = {key: 'device-' + uuid4().hex for key in package.devices}
    directory = None
    try:
        with session.transaction():
            editor._enable()
            for key, definition in checked.items():
                value = definition.model_dump()
                value['enabled'] = False
                # Disabled portable definitions may have empty declared site fields.
                session.workflows.devices['definitions'][deviceIds[key]] = deepcopy(value)
            plan, resourceIds = resources.remapDependencies(session.workflows, package, {}, {}, deviceIds)
            directory = resources.installDependencies(root, package, plan, resourceIds)
            resources.applyImportedPaths(session.workflows, plan, resourceIds)
            session.workflows.projectExtensions['resources'] = plan.model_dump()
    except BaseException:
        resources.discardInstalled(directory)
        raise
    return deviceIds


def readDevicePackage(path):
    path = Path(path)
    if path.stat().st_size > resources.MAX_PACKAGE_BYTES:
        raise ValueError('设备包过大')
    return DevicePackage.model_validate_json(path.read_bytes())
