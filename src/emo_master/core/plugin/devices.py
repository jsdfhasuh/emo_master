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


class DeviceRequirement(DeviceModel):
    deviceTypeId: DeviceId


class ParameterRule(DeviceModel):
    present: str
    requires: dict[str, object]
    whenOverridden: bool = False


class CancellableWait(DeviceModel):
    action: str
    timeoutPath: str
    requires: dict[str, object] = Field(min_length=1)


def _validateResourceValues(value, schema):
    if "xUtf8MaxBytes" in schema:
        if not isinstance(value, str) or len(value.encode("utf-8")) > schema["xUtf8MaxBytes"]:
            raise ValueError("device string exceeds its UTF-8 byte limit")
    if schema.get("xAddressLiteral"):
        if not isinstance(value, str) or "%" in value:
            raise ValueError("device address must be an unscoped IP literal")
        try:
            ipaddress.ip_address(value)
        except ValueError as error:
            raise ValueError("device address must be an IP literal; DNS aliases cannot establish device identity") from error
    if schema.get("xResourcePurpose") == "secret_ref" and value:
        if not isinstance(value, str) or not re.fullmatch(r"secret://[A-Za-z0-9_./-]+@[A-Za-z0-9_.-]+", value):
            raise ValueError("device secrets must be versioned references, never plaintext")
    if isinstance(value, dict):
        for key, child in schema.get("properties", {}).items():
            if key in value:
                _validateResourceValues(value[key], child)
    elif isinstance(value, list):
        for item in value:
            _validateResourceValues(item, schema.get("items", {}))


class DeviceType(DeviceModel):
    schemaVersion: Literal["1.0"] = "1.0"
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
    capabilities: list[Literal["discover", "connect", "disconnect", "capture", "read", "write"]] = Field(default_factory=list)
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
            raise ValueError("device type/version does not match declaration")
        payload = definition.model_dump()
        for name, schema in (("connection", self.connectionSchema), ("defaults", self.defaultsSchema),
                             ("policies", self.policiesSchema)):
            payload[name] = _parameters(getattr(definition, name), schema, name)
            _validateResourceValues(payload[name], schema)
        self.validateRules(payload["defaults"])
        policies = payload["policies"]
        if policies.get("connect", "on_first_use") not in {"on_first_use", "on_project_open", "on_project_run", "manual"}:
            raise ValueError("unsupported device connection policy")
        if policies.get("release", "on_project_close") not in {"on_project_close", "when_unused", "idle_timeout", "manual"}:
            raise ValueError("unsupported device release policy")
        if policies.get("release") == "idle_timeout" and (type(policies.get("idleTimeoutMs")) is not int
                or not 1 <= policies["idleTimeoutMs"] <= 86400000):
            raise ValueError("idle_timeout requires a bounded positive idleTimeoutMs")
        if policies.get("release") == "when_unused" and policies.get("connect", "on_first_use") != "on_first_use":
            raise ValueError("when_unused requires on_first_use to avoid an immediate connect/disconnect cycle")
        return DeviceDefinition.model_validate(payload)

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
                        raise ValueError(f"{rule.present} requires {path}={required!r}")
        wait = self.cancellableWait
        if (wait is not None and not set(dynamicPaths) & {wait.timeoutPath, *wait.requires}
                and lookup(wait.timeoutPath) == 0):
            for path, required in wait.requires.items():
                if lookup(path) != required:
                    raise ValueError(f"unlimited wait requires {path}={required!r}")

    def isUnlimitedWait(self, action, values):
        wait = self.cancellableWait
        if wait is None or wait.action != action:
            return False
        value = values
        for part in parameterPath(wait.timeoutPath):
            value = value[part]
        return value == 0

    def validateNodeSources(self, definition: DeviceDefinition, overrides: Mapping, bindings: Mapping) -> dict:
        from emo_master.core.project.snapshots import _parameters

        definition = self.validateDefinition(definition)
        validateSources({"slot": object()}, {"slot": overrides}, {"slot": bindings})
        for path in leafPaths(overrides):
            if ".".join(path) not in self.overridePaths:
                raise ValueError(f"device parameter cannot be overridden: {'.'.join(path)}")
        for path, binding in bindings.items():
            if path not in self.dynamicPaths:
                raise ValueError(f"device parameter cannot be bound dynamically: {path}")
            if not isinstance(binding, DeviceValueBinding):
                DeviceValueBinding.model_validate(binding)
        effective = mergeDefaults(definition.defaults, overrides)
        # Dynamic values are validated again after resolution, before device admission.
        effective = _parameters(effective, self.defaultsSchema, "effective device parameters")
        self.validateRules(effective, bindings, sourcePaths={".".join(path) for path in leafPaths(overrides)} | set(bindings))
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
