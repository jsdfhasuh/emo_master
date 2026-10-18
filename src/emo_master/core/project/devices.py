"""Serializable device configuration, independent of Qt and hardware ownership."""
from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, StringConstraints, model_validator


DeviceId = Annotated[str, StringConstraints(min_length=1, max_length=160, pattern=r"^\S+$")]


class DeviceModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


class DeviceDefinition(DeviceModel):
    name: str = Field(min_length=1, max_length=160)
    deviceTypeId: DeviceId
    driverVersion: str = Field(min_length=1, max_length=64)
    enabled: bool = True
    description: str = ""
    connection: dict[str, JsonValue] = Field(default_factory=dict)
    defaults: dict[str, JsonValue] = Field(default_factory=dict)
    policies: dict[str, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validateName(self):
        if self.name != self.name.strip():
            raise ValueError("device name must not have surrounding whitespace")
        return self


class DeviceBinding(DeviceModel):
    deviceId: DeviceId


def workflowDeviceIds(document, workflowId):
    """Static capability scope includes explicit subflows and loop workflows only."""
    from emo_master.core.workflow.loop_contracts import loopWorkflowReferenceFields

    pending, visited, devices = [workflowId], set(), set()
    while pending:
        key = pending.pop()
        if key in visited:
            continue
        visited.add(key)
        for node in document.workflows[key].nodes:
            devices.update(binding.deviceId for binding in node.deviceBindings.values())
            if node.kind == "subflow" and node.targetWorkflowId:
                pending.append(node.targetWorkflowId)
            if node.kind == "loop":
                pending.extend(node.loop[field] for field in loopWorkflowReferenceFields(node.loop) if node.loop.get(field))
    return tuple(sorted(devices))


class DeviceValueBinding(DeviceModel):
    source: Literal["variable", "input"]
    variableId: DeviceId | None = None
    inputName: DeviceId | None = None

    @model_validator(mode="after")
    def validateSource(self):
        if self.source == "variable":
            valid = self.variableId is not None and self.inputName is None
        else:
            valid = self.inputName is not None and self.variableId is None
        if not valid:
            raise ValueError("device value binding must identify exactly one source")
        return self


def parameterPath(value: str) -> tuple[str, ...]:
    parts = tuple(value.split("."))
    if len(parts) > 16 or any(not part or part != part.strip() for part in parts):
        raise ValueError("invalid device parameter path")
    return parts


def leafPaths(values: Mapping, prefix=()):
    for key, value in values.items():
        if not isinstance(key, str) or len(parameterPath(key)) != 1:
            raise ValueError("device parameter object keys must be individual path segments")
        path = (*prefix, key)
        if len(path) > 16:
            raise ValueError("device parameter path is too deep")
        if isinstance(value, Mapping) and value:
            yield from leafPaths(value, path)
        else:
            yield path


def validateSources(bindings: Mapping, overrides: Mapping, values: Mapping) -> None:
    if (set(overrides) | set(values)) - set(bindings):
        raise ValueError("device parameter sources require a bound device slot")
    for slot in bindings:
        paths = list(leafPaths(overrides.get(slot, {})))
        paths.extend(parameterPath(key) for key in values.get(slot, {}))
        for index, path in enumerate(paths):
            for other in paths[:index]:
                if path[:len(other)] == other or other[:len(path)] == path:
                    raise ValueError(f"overlapping device parameter sources in {slot}: {'.'.join(path)}")


def mergeDefaults(defaults: Mapping, overrides: Mapping) -> dict:
    """Presence, including zero/false/null, determines an explicit override."""
    result = deepcopy(dict(defaults))
    for key, value in overrides.items():
        if isinstance(value, Mapping) and isinstance(result.get(key), Mapping):
            result[key] = mergeDefaults(result[key], value)
        else:
            result[key] = deepcopy(value)
    return result


def resolveDeviceValues(defaults: Mapping, overrides: Mapping, bindings: Mapping,
                        variables: Mapping, inputs: Mapping) -> dict:
    """Resolve once when constructing a request; never re-read a queued source."""
    from emo_master.core.project.global_variables import resolveParams

    validateSources({"slot": object()}, {"slot": overrides}, {"slot": bindings})
    resolved = mergeDefaults(defaults, overrides)
    variableBindings = []
    sourceValues = {}
    for index, (path, raw) in enumerate(bindings.items()):
        binding = raw if isinstance(raw, DeviceValueBinding) else DeviceValueBinding.model_validate(raw)
        source = variables if binding.source == "variable" else inputs
        key = binding.variableId if binding.source == "variable" else binding.inputName
        if key not in source:
            raise ValueError(f"missing device {binding.source} source: {key}")
        alias = str(index)
        sourceValues[alias] = deepcopy(source[key])
        variableBindings.append({"parameterPath": list(parameterPath(path)), "variableId": alias})
    return resolveParams(resolved, variableBindings, sourceValues)
