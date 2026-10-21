"""Pure device declarations and bounded metadata discovery; never import drivers."""
from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
import ipaddress
import json
import re
from typing import Literal

from pydantic import Field, model_validator

from emo_master.core.project.devices import (
    DeviceDefinition, DeviceId, DeviceModel, DeviceValueBinding,
    leafPaths, mergeDefaults, parameterPath, validateSources,
)
from emo_master.core.workflow.errors import ValidationIssue, WorkflowCompileError


def _deviceInvalid(message, path):
    return WorkflowCompileError([ValidationIssue('E_DEVICE_PARAMETERS', message, fieldPath=path)])


class DeviceRequirement(DeviceModel):
    deviceTypeId: DeviceId
    requiredCapabilities: list[str] = Field(default_factory=list)
    selectorSchema: dict = Field(default_factory=lambda: dict(
        type="object", properties={}, additionalProperties=False))

    @model_validator(mode="after")
    def validateSelectors(self):
        if (self.selectorSchema.get("type") != "object"
                or not isinstance(self.selectorSchema.get("properties"), dict)
                or self.selectorSchema.get("additionalProperties") is not False):
            raise ValueError("slot selectorSchema must be a closed object")
        return self


class ParameterRule(DeviceModel):
    present: str
    requires: dict[str, object]
    whenOverridden: bool = False


class CancellableWait(DeviceModel):
    action: str
    timeoutPath: str
    requires: dict[str, object] = Field(min_length=1)


def _validateResourceValues(value, schema, path='defaults'):
    if "xUtf8MaxBytes" in schema:
        if not isinstance(value, str) or len(value.encode("utf-8")) > schema["xUtf8MaxBytes"]:
            raise _deviceInvalid("device string exceeds its UTF-8 byte limit", path)
    if schema.get("xAddressLiteral"):
        if not isinstance(value, str) or "%" in value:
            raise _deviceInvalid("device address must be an unscoped IP literal", path)
        try:
            ipaddress.ip_address(value)
        except ValueError as error:
            raise _deviceInvalid("device address must be an IP literal; DNS aliases cannot establish device identity", path) from error
    if schema.get("xResourcePurpose") == "secret_ref" and value:
        if not isinstance(value, str) or not re.fullmatch(r"secret://[A-Za-z0-9_./-]+@[A-Za-z0-9_.-]+", value):
            raise _deviceInvalid("device secrets must be versioned references, never plaintext", path)
    if isinstance(value, dict):
        for key, child in schema.get("properties", {}).items():
            if key in value:
                _validateResourceValues(value[key], child, path+'.'+key)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _validateResourceValues(item, schema.get("items", {}), path+'.'+str(index))


class DeviceType(DeviceModel):
    schemaVersion: Literal["1.1"] = "1.1"
    driverApiVersion: Literal["1.1"] = "1.1"
    deviceTypeId: DeviceId
    version: str
    displayName: str = Field(min_length=1)
    category: str = Field(min_length=1)
    iconKey: str = "default"
    minCoreVersion: str
    maxCoreVersion: str
    platforms: list[Literal["windows", "linux"]] = Field(min_length=1)
    connectionSchema: dict[str, object]
    defaultsSchema: dict[str, object]
    policiesSchema: dict[str, object]
    overridePaths: list[str] = Field(default_factory=list)
    dynamicPaths: list[str] = Field(default_factory=list)
    nextCyclePaths: list[str] = Field(default_factory=list)
    parameterRules: list[ParameterRule] = Field(default_factory=list)
    capabilities: list[str] = Field(default_factory=list)
    controlCapabilities: list[Literal["connect", "disconnect", "maintenance", "resume", "recover", "test_connection"]] = Field(default_factory=list)
    operationScheduling: Literal["serial", "managed_service"] = "serial"
    connectionLifetime: Literal["persistent", "per_operation"] = "persistent"
    bindingSelectors: dict[str, dict] = Field(default_factory=dict)
    managementActions: list[str] = Field(default_factory=list)
    actionSchemas: dict[str, dict[str, object]] = Field(default_factory=dict)
    driverEntry: str | None = Field(default=None, pattern=r"^[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*:[A-Za-z_]\w*$")
    executionLocation: Literal["io_thread", "camera_host"] = "io_thread"
    cancellableWait: CancellableWait | None = None

    @model_validator(mode="after")
    def validateDeclaration(self):
        from emo_master.core.plugin.validator import isVersionCompatible

        if not isVersionCompatible(self.version, self.version, self.version):
            raise ValueError("device version must be an exact version")
        if not isVersionCompatible(self.minCoreVersion, self.minCoreVersion, self.maxCoreVersion):
            raise ValueError("invalid device core version range")
        for schema in (self.connectionSchema, self.defaultsSchema, self.policiesSchema):
            if schema.get("type") != "object" or not isinstance(schema.get("properties"), dict):
                raise ValueError("device schemas must declare object properties")
            if schema.get("additionalProperties") is not False:
                raise ValueError("device schemas must reject undeclared properties")
        if self.operationScheduling == "managed_service" and self.connectionLifetime != "persistent":
            raise ValueError("managed_service requires persistent lifetime")
        if self.connectionLifetime == "per_operation" and set(self.controlCapabilities) & {"connect", "disconnect"}:
            raise ValueError("per_operation devices cannot declare persistent controls")
        if len(set(self.capabilities)) != len(self.capabilities) or any(
                not re.fullmatch(r"[a-z][a-z0-9_]{0,79}", action) for action in self.capabilities):
            raise ValueError("invalid or duplicate device action")
        if set(self.capabilities) != set(self.actionSchemas):
            raise ValueError("every business action requires an action schema")
        if not set(self.managementActions) <= set(self.capabilities):
            raise ValueError("managementActions must name business actions")
        for name, selector in self.bindingSelectors.items():
            spec = {"connection": self.connectionSchema, "defaults": self.defaultsSchema,
                    "policies": self.policiesSchema}
            parts = selector.get("itemsPath", "").split(".")
            child = spec.get(parts[0], {})
            for part in parts[1:]:
                child = child.get("properties", {}).get(part, {})
            props = child.get("items", {}).get("properties", {})
            if (child.get("type") != "array" or selector.get("valueField") not in props
                    or selector.get("labelField") not in props):
                raise ValueError("invalid binding selector: " + name)
        for action, schema in self.actionSchemas.items():
            if action not in self.capabilities:
                raise ValueError("action schema must name a declared capability")
            if (schema.get("type") != "object" or not isinstance(schema.get("properties"), dict)
                    or schema.get("additionalProperties") is not False):
                raise ValueError("device action schemas must declare closed objects")
        for paths in (self.overridePaths, self.dynamicPaths):
            if len(paths) != len(set(paths)):
                raise ValueError("duplicate device parameter path")
            for path in paths:
                if self.parameterSpec(path).get("type") == "object":
                    raise ValueError("device sources must select leaf parameters, not object containers")
        if not set(self.dynamicPaths) <= set(self.overridePaths):
            raise ValueError("dynamic fields must also allow explicit override")
        if not set(self.nextCyclePaths) <= set(self.overridePaths) or len(self.nextCyclePaths) != len(set(self.nextCyclePaths)):
            raise ValueError("next-cycle fields must be unique overridable parameters")
        if any(self.parameterSpec(path).get("xWidget") == "file" for path in self.nextCyclePaths):
            raise ValueError("device file parameters require resource replacement, not next-cycle updates")
        for path in self.dynamicPaths:
            if self.parameterSpec(path).get("type") not in {"number", "integer", "string", "boolean"}:
                raise ValueError("dynamic device parameters must have a scalar type")
            if self.parameterSpec(path).get("xWidget") == "file":
                raise ValueError("device file parameters cannot use dynamic sources")
        for rule in self.parameterRules:
            self.parameterSpec(rule.present)
            if not rule.requires:
                raise ValueError("parameter rule requires a condition")
            for path in rule.requires:
                self.parameterSpec(path)
        if self.cancellableWait is not None:
            wait = self.cancellableWait
            spec = self.parameterSpec(wait.timeoutPath)
            if wait.action not in self.capabilities or spec.get("type") != "integer" or spec.get("minimum") != 0:
                raise ValueError("cancellable wait requires a declared action and zero-enabled integer timeout")
            for path in wait.requires:
                self.parameterSpec(path)
        return self

    def parameterSpec(self, path: str) -> dict:
        spec = self.defaultsSchema
        for part in parameterPath(path):
            properties = spec.get("properties", {})
            child = properties.get(part) if isinstance(properties, dict) else None
            if not isinstance(child, dict):
                raise ValueError(f"unknown device work parameter: {path}")
            spec = child
        return spec

    def validateDefinition(self, definition: DeviceDefinition) -> DeviceDefinition:
        from emo_master.core.project.snapshots import _parameters

        if (definition.deviceTypeId, definition.driverVersion) != (self.deviceTypeId, self.version):
            raise _deviceInvalid("device type/version does not match declaration", 'driverVersion')
        payload = definition.model_dump()
        for name, schema in (("connection", self.connectionSchema), ("defaults", self.defaultsSchema),
                             ("policies", self.policiesSchema)):
            payload[name] = _parameters(getattr(definition, name), schema, name)
            _validateResourceValues(payload[name], schema, name)
        self.validateRules(payload["defaults"])
        policies = payload["policies"]
        if policies.get("connect", "on_first_use") not in {"on_first_use", "on_project_open", "on_project_run", "manual"}:
            raise _deviceInvalid("unsupported device connection policy", 'policies.connect')
        if policies.get("release", "on_project_close") not in {"on_project_close", "when_unused", "idle_timeout", "manual"}:
            raise _deviceInvalid("unsupported device release policy", 'policies.release')
        if policies.get("release") == "idle_timeout" and (type(policies.get("idleTimeoutMs")) is not int
                or not 1 <= policies["idleTimeoutMs"] <= 86400000):
            raise _deviceInvalid("idle_timeout requires a bounded positive idleTimeoutMs", 'policies.idleTimeoutMs')
        if policies.get("release") == "when_unused" and policies.get("connect", "on_first_use") != "on_first_use":
            raise _deviceInvalid("when_unused requires on_first_use to avoid an immediate connect/disconnect cycle", 'policies.release')
        checked = DeviceDefinition.model_validate(payload)
        if self.deviceTypeId.startswith("device.tcp."):
            from emo_master.core.project.communication import validateTcpDefinition
            checked = validateTcpDefinition(checked)
        return checked

    def validateRules(self, values: Mapping, dynamicPaths=(), *, sourcePaths=()) -> None:
        _validateResourceValues(values, self.defaultsSchema)
        missing = object()

        def lookup(path):
            value = values
            for part in parameterPath(path):
                if not isinstance(value, Mapping) or part not in value:
                    return missing
                value = value[part]
            return value

        for rule in self.parameterRules:
            if rule.whenOverridden and rule.present not in sourcePaths:
                continue
            if set(dynamicPaths) & {rule.present, *rule.requires}:
                continue
            if lookup(rule.present) is not missing:
                for path, required in rule.requires.items():
                    if lookup(path) != required:
                        raise _deviceInvalid(f"{rule.present} requires {path}={required!r}", 'defaults.'+path)
        wait = self.cancellableWait
        if (wait is not None and not set(dynamicPaths) & {wait.timeoutPath, *wait.requires}
                and lookup(wait.timeoutPath) == 0):
            for path, required in wait.requires.items():
                if lookup(path) != required:
                    raise _deviceInvalid(f"unlimited wait requires {path}={required!r}", 'defaults.'+path)

    def isUnlimitedWait(self, action, values):
        wait = self.cancellableWait
        if wait is None or wait.action != action:
            return False
        value = values
        for part in parameterPath(wait.timeoutPath):
            value = value[part]
        return value == 0

    def validateNodeSources(self, definition: DeviceDefinition, overrides: Mapping, bindings: Mapping, *, slot='') -> dict:
        from emo_master.core.project.snapshots import _parameters
        from dataclasses import replace

        definition = self.validateDefinition(definition)
        overridePath = 'deviceOverrides' + ('.'+slot if slot else '')
        bindingPath = 'deviceValueBindings' + ('.'+slot if slot else '')
        validateSources({"slot": object()}, {"slot": overrides}, {"slot": bindings})
        for path in leafPaths(overrides):
            if ".".join(path) not in self.overridePaths:
                raise _deviceInvalid(f"device parameter cannot be overridden: {'.'.join(path)}", overridePath+'.'+'.'.join(path))
        for path, binding in bindings.items():
            if path not in self.dynamicPaths:
                raise _deviceInvalid(f"device parameter cannot be bound dynamically: {path}", bindingPath+'.'+path)
            if not isinstance(binding, DeviceValueBinding):
                DeviceValueBinding.model_validate(binding)
        effective = mergeDefaults(definition.defaults, overrides)
        # Dynamic values are validated again after resolution, before device admission.
        try:
            effective = _parameters(effective, self.defaultsSchema, 'defaults')
            self.validateRules(effective, bindings, sourcePaths={".".join(path) for path in leafPaths(overrides)} | set(bindings))
        except WorkflowCompileError as error:
            raise WorkflowCompileError([replace(issue, fieldPath=overridePath + issue.fieldPath.removeprefix('defaults'))
                                        for issue in error.issues]) from error
        return effective

    def resolveParameters(self, definition: DeviceDefinition, overrides: Mapping, bindings: Mapping,
                          variables: Mapping, inputs: Mapping) -> dict:
        from emo_master.core.project.devices import resolveDeviceValues
        from emo_master.core.project.snapshots import _parameters

        effective = self.validateNodeSources(definition, overrides, bindings)
        resolved = resolveDeviceValues(effective, {}, bindings, variables, inputs)
        resolved = _parameters(resolved, self.defaultsSchema, "resolved device parameters")
        self.validateRules(resolved, sourcePaths={".".join(path) for path in leafPaths(overrides)} | set(bindings))
        return resolved


def selectorCandidates(declaration, definition, name):
    spec = declaration.bindingSelectors[name]
    value = definition.model_dump() if hasattr(definition, "model_dump") else definition
    for part in spec["itemsPath"].split("."):
        value = value[part]
    return [(item[spec["valueField"]], item[spec["labelField"]]) for item in value]


def validateDeviceBinding(requirement, binding, definition, declaration):
    """One offline binding rule for compiler, editors and package reuse."""
    from emo_master.core.project.devices import DeviceBinding
    from emo_master.core.project.snapshots import _parameters
    def invalid(message, path='deviceId'):
        return WorkflowCompileError([ValidationIssue('E_DEVICE_BINDING', message, fieldPath=path)])
    if not isinstance(requirement, DeviceRequirement):
        requirement = DeviceRequirement.model_validate(dict(requirement))
    if not isinstance(binding, DeviceBinding):
        binding = DeviceBinding.model_validate(dict(binding))
    if definition.deviceTypeId != requirement.deviceTypeId:
        raise invalid("device type does not match operator slot")
    if not set(requirement.requiredCapabilities) <= set(declaration.capabilities):
        raise invalid("device lacks required capabilities")
    if not set(requirement.selectorSchema["properties"]) <= set(declaration.bindingSelectors):
        raise invalid("slot names an undeclared device selector", 'selectors')
    selectors = _parameters(binding.selectors, requirement.selectorSchema, "selectors")
    for name, value in selectors.items():
        if value not in {key for key, _ in selectorCandidates(declaration, definition, name)}:
            raise invalid(f"device {binding.deviceId}: selectors.{name}={value!r} does not exist", 'selectors.'+name)
    return selectors


def scanDeviceTypes(pluginRoots, coreVersion):
    from emo_master.core.plugin.models import ValidationIssue
    from emo_master.core.plugin.validator import isVersionCompatible

    active, rejected, seen, poisoned = {}, {}, set(), set()
    for root in pluginRoots:
        try:
            root = Path(root).resolve()
            paths = sorted(root.glob("**/device_types/*.json"))
        except (OSError, RuntimeError) as error:
            rejected.setdefault(str(root), []).append(ValidationIssue(
                "D01", "E_DEVICE_DECLARATION", f"{root}: {error}"))
            continue
        for path in paths:
            key = str(path)
            try:
                resolved = path.resolve(strict=True)
                if resolved in seen:
                    continue
                seen.add(resolved)
                if not resolved.is_relative_to(root) or resolved.stat().st_size > 256 * 1024:
                    raise ValueError("device declaration escapes plugin root or exceeds size limit")
                with resolved.open("rb") as stream:
                    content = stream.read(256 * 1024 + 1)
                if len(content) > 256 * 1024:
                    raise ValueError("device declaration exceeds size limit")
                raw = json.loads(content.decode("utf-8"))
                if isinstance(raw, dict) and isinstance(raw.get("deviceTypeId"), str):
                    key = raw["deviceTypeId"]
                declaration = DeviceType.model_validate(raw)
                if not isVersionCompatible(coreVersion, declaration.minCoreVersion, declaration.maxCoreVersion):
                    raise ValueError("device declaration is incompatible with this core")
                if key in poisoned:
                    continue
                if key in active and active[key] != declaration:
                    raise ValueError("conflicting device type declarations")
                active[key] = declaration
            except (OSError, ValueError, TypeError, RecursionError) as error:
                active.pop(key, None)
                poisoned.add(key)
                rejected.setdefault(key, []).append(ValidationIssue(
                    "D01", "E_DEVICE_DECLARATION", f"{path}: {error}"))
    return active, rejected
