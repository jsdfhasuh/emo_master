"""Device edits use the existing project transaction, never Runtime actions."""
from __future__ import annotations

from copy import deepcopy
from uuid import uuid4

from emo_master.core.contracts.port_types import normalizePortType
from emo_master.core.project.devices import DeviceDefinition
from emo_master.core.project.global_variables import compatible
from emo_master.core.project.migration import migrateProjectPayload
from emo_master.core.project.models import WorkflowNode


def importedDeviceName(name, used):
    """Allocate display names without changing package identities or defaults."""
    if name not in used:
        return name
    index = 1
    while True:
        suffix = '（导入副本）' if index == 1 else f'（导入副本 {index}）'
        candidate = name[:160 - len(suffix)] + suffix
        if candidate not in used:
            return candidate
        index += 1


class DeviceCatalogEditor:
    def __init__(self, session, deviceTypes, operatorRequirements=None):
        self.session = session
        self.deviceTypes = deviceTypes
        self.operatorRequirements = operatorRequirements or {}

    def references(self, deviceId):
        return [(wid, node["nodeId"], slot)
                for wid, workflow in self.session.workflows.workflows.items()
                for node in workflow.nodes
                for slot, binding in node.get("deviceBindings", {}).items()
                if binding["deviceId"] == deviceId]

    def _enable(self):
        if self.session.workflows.devices.get("schemaVersion") == "1.0":
            return
        self.session._restore(migrateProjectPayload(self.session.payload(), enableDevices=True))

    def _definition(self, value):
        checked = DeviceDefinition.model_validate(value)
        declaration = self.deviceTypes.get(checked.deviceTypeId)
        if declaration is None:
            raise ValueError("设备类型插件不可用；已存配置保留，但不能新建或修改")
        return declaration.validateDefinition(checked)

    def put(self, value, *, deviceId=None):
        checked = self._definition(value)
        deviceId = deviceId or "device-" + uuid4().hex
        with self.session.transaction():
            self._enable()
            catalog = self.session.workflows.devices["definitions"]
            previous = catalog.get(deviceId)
            if previous and (previous["deviceTypeId"], previous["driverVersion"]) != (
                    checked.deviceTypeId, checked.driverVersion):
                raise ValueError("已有设备不能通过属性编辑替换类型或驱动版本")
            self._checkIdentity(checked.model_dump(), catalog, deviceId)
            catalog[deviceId] = checked.model_dump()
            for workflowId, workflow in self.session.workflows.workflows.items():
                for node in workflow.nodes:
                    if any(b["deviceId"] == deviceId for b in node.get("deviceBindings", {}).values()):
                        try:
                            self._validateNode(WorkflowNode.model_validate(node), allowIncomplete=True)
                        except ValueError as error:
                            raise ValueError(f"{workflowId}/{node['nodeId']}: {error}") from error
        return deviceId

    def _checkIdentity(self, value, catalog, deviceId):
        from emo_master.core.project.device_identity import matchingDevices
        matches = matchingDevices(value, catalog, self.deviceTypes[value['deviceTypeId']], excluding=deviceId)
        if matches:
            names = ', '.join(f"{catalog[key]['name']} ({key})" for key in matches)
            raise ValueError('相同设备标识已存在，请复用已有设备，不会自动合并参数：' + names)

    def remove(self, deviceId):
        references = self.references(deviceId)
        if references:
            raise ValueError(f"设备仍被节点引用：{references}")
        resources = self.session.document().resources
        if resources and any(getattr(binding.target, "deviceId", None) == deviceId
                             for binding in (*resources.parameterBindings, *resources.siteBindings)):
            raise ValueError("设备仍被资源或现场绑定引用，请先移除绑定")
        with self.session.transaction():
            del self.session.workflows.devices["definitions"][deviceId]

    def commitNode(self, workflowId, node, *, newDevice=None, deviceId=None, newDevices=None, position=None,
                   resourcePlan=None, allowIncomplete=False):
        """Publish a wizard draft only on Finish, including any new definition."""
        with self.session.transaction():
            self._enable()
            for key, value in (newDevices or {}).items():
                if key in self.session.workflows.devices["definitions"]:
                    raise ValueError("新设备必须使用未占用的内部 ID")
                if not any(binding["deviceId"] == key for binding in node.get("deviceBindings", {}).values()):
                    raise ValueError("向导创建的新设备必须被本节点引用")
                self.session.workflows.devices["definitions"][key] = self._definition(value).model_dump()
            if newDevice is not None:
                if not deviceId or deviceId in self.session.workflows.devices["definitions"]:
                    raise ValueError("新设备必须使用未占用的内部 ID")
                self.session.workflows.devices["definitions"][deviceId] = self._definition(newDevice).model_dump()
            catalog = self.session.workflows.devices['definitions']
            for key in set(newDevices or {}) | ({deviceId} if newDevice is not None else set()):
                self._checkIdentity(catalog[key], catalog, key)
            checked = WorkflowNode.model_validate(node)
            if checked.displayName is None:
                checked.displayName = checked.operatorId or checked.kind
            if newDevice is not None and not any(binding.deviceId == deviceId for binding in checked.deviceBindings.values()):
                raise ValueError("向导创建的新设备必须被本节点引用")
            self._validateNode(checked, allowIncomplete=allowIncomplete)
            if resourcePlan is not None:
                from emo_master.core.project.resources import ResourcePlan
                self.session.workflows.projectExtensions["resources"] = ResourcePlan.model_validate(resourcePlan).model_dump()
            workflow = self.session.workflows.get(workflowId)
            for index, existing in enumerate(workflow.nodes):
                if existing["nodeId"] == checked.nodeId:
                    workflow.nodes[index] = checked.model_dump()
                    break
            else:
                workflow.nodes.append(checked.model_dump())
            if position is not None:
                workflow.layout.setdefault("nodePositions", {})[checked.nodeId] = position

    def _validateNode(self, node, *, allowIncomplete=False):
        catalog = self.session.workflows.devices["definitions"]
        variables = self.session.document().globalVariables
        requirements = self.operatorRequirements.get(node.operatorId, {})
        if set(node.deviceBindings) - set(requirements) or (not allowIncomplete and set(requirements) - set(node.deviceBindings)):
            raise ValueError("节点设备槽必须与算子声明一致")
        for slot, binding in node.deviceBindings.items():
            if binding.deviceId not in catalog:
                raise ValueError(f"槽位 {slot}：设备引用不存在：{binding.deviceId}")
            definition = DeviceDefinition.model_validate(catalog[binding.deviceId])
            requirement = requirements[slot]

            declaration = self.deviceTypes.get(definition.deviceTypeId)
            if declaration is None:
                raise ValueError("绑定设备的插件不可用")
            from emo_master.core.plugin.devices import validateDeviceBinding
            from emo_master.core.workflow.errors import locatedValidationError
            try:
                binding.selectors = validateDeviceBinding(requirement, binding, definition, declaration,
                    allowMissingSelectors=allowIncomplete)
            except ValueError as error:
                raise locatedValidationError(error, 'E_DEVICE_BINDING', nodeId=node.nodeId,
                    prefix='deviceBindings.' + slot) from error
            values = node.deviceValueBindings.get(slot, {})
            declaration.validateNodeSources(definition, node.deviceOverrides.get(slot, {}), values)
            for path, source in values.items():
                expected = declaration.parameterSpec(path)["type"]
                if source.source == "variable":
                    variable = variables.get(source.variableId)
                    if variable is None or not compatible(variable.type, expected):
                        raise ValueError("设备动态变量不存在或类型不兼容")
                elif not compatible(normalizePortType(node.inputPorts.get(source.inputName)), expected):
                    raise ValueError("设备动态输入不存在或类型不兼容")


def migrateLegacySources(node, slot, deviceId, declaration, actionFields):
    """Convert declared dynamic work parameters, never connection variables."""
    draft = deepcopy(node)
    draft.setdefault("deviceBindings", {})[slot] = {"deviceId": deviceId}
    bindings = []
    for binding in draft.get("globalVariableBindings", []):
        path = ".".join(binding["parameterPath"])
        if binding["parameterPath"][0] in actionFields:
            bindings.append(binding)
        elif path in declaration.dynamicPaths:
            draft = changeParameterSource(draft, slot, path, source="variable", value=binding["variableId"])
        else:
            raise ValueError("旧变量目标不能迁移为设备动态参数：" + path)
    draft["globalVariableBindings"] = bindings
    return draft


def migrateLegacyResourceTargets(plan, workflowId, nodeId, deviceId, declaration, actionFields):
    from emo_master.core.project.resources import ResourcePlan
    result = deepcopy(plan)
    moved = []
    for binding in [*result.get("parameterBindings", []), *result.get("siteBindings", [])]:
        target = binding["target"]
        if target.get("workflowId") != workflowId or target.get("nodeId") != nodeId:
            continue
        path = target["parameterPath"]
        if path[0] in actionFields:
            continue
        matches = []
        for section in ("connection", "defaults"):
            schema = getattr(declaration, section + "Schema")
            for part in path:
                schema = schema.get("properties", {}).get(part, {})
            if schema:
                matches.append(section)
        if len(matches) != 1:
            raise ValueError("旧资源/现场目标无法唯一映射：" + ".".join(path))
        binding["target"] = dict(deviceId=deviceId, section=matches[0], parameterPath=path)
        moved.append(f"{workflowId}/{nodeId}/{'.'.join(path)} -> {deviceId}/{matches[0]}/{'.'.join(path)}")
    return ResourcePlan.model_validate(result).model_dump(), moved


def changeParameterSource(node, slot, path, *, source, value=None):
    """Return a new draft; deleting both source records means inheritance."""
    from emo_master.core.project.devices import parameterPath, DeviceValueBinding

    if source not in {"inherit", "constant", "variable", "input"}:
        raise ValueError("unknown device parameter source")
    draft = deepcopy(node)
    if slot not in draft.get("deviceBindings", {}):
        raise ValueError("device slot must be bound before setting a source")
    parts = parameterPath(path)
    overrides = draft.setdefault("deviceOverrides", {}).setdefault(slot, {})
    dynamic = draft.setdefault("deviceValueBindings", {}).setdefault(slot, {})
    dynamic.pop(path, None)

    def remove(container, remaining):
        key = remaining[0]
        if len(remaining) == 1:
            container.pop(key, None)
        elif isinstance(container.get(key), dict):
            remove(container[key], remaining[1:])
            if not container[key]:
                container.pop(key)

    remove(overrides, parts)
    if source == "constant":
        target = overrides
        for part in parts[:-1]:
            target = target.setdefault(part, {})
            if not isinstance(target, dict):
                raise ValueError("parameter parent is not an object")
        target[parts[-1]] = deepcopy(value)
    elif source != "inherit":
        binding = {"source": source, "variableId" if source == "variable" else "inputName": value}
        dynamic[path] = DeviceValueBinding.model_validate(binding).model_dump(exclude_none=True)
    for key in ("deviceOverrides", "deviceValueBindings"):
        if not draft[key][slot]:
            draft[key].pop(slot)
        if not draft[key]:
            draft.pop(key)
    return WorkflowNode.model_validate(draft).model_dump()
