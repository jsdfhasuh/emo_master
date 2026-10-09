"""Typed project declarations and parameter references, without runtime state."""
from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
import math
from typing import Literal, cast

from pydantic import BaseModel, ConfigDict, Field, model_validator


READ_VARIABLE = "vision.state.variable_read"
WRITE_VARIABLE = "vision.state.variable_write"
SCALAR_TYPES = {"boolean", "integer", "number", "string"}


class VariableError(ValueError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


def validateValue(value: object, valueType: str, *, legacy: bool = False) -> object:
    valid = {
        "boolean": lambda: isinstance(value, bool),
        "integer": lambda: type(value) is int and -(1 << 63) <= value < (1 << 63),
        "number": lambda: type(value) in (int, float) and math.isfinite(float(cast(int | float, value))),
        "string": lambda: isinstance(value, str),
    }.get(valueType)
    try:
        accepted = valid is not None and valid()
    except (OverflowError, TypeError):
        accepted = False
    if not accepted or (legacy and (type(value) is not int or value < 0)):
        raise VariableError("E_VARIABLE_TYPE", f"invalid {valueType} value")
    return value


class VariableDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    name: str = Field(min_length=1, max_length=128)
    type: Literal["boolean", "integer", "number", "string"]
    kind: Literal["constant", "variable"] = "variable"
    initialValue: object
    lifetime: Literal["persistent", "job"] = "job"
    legacyCounterName: str | None = None

    @model_validator(mode="after")
    def validateDefinition(self):
        if self.name != self.name.strip():
            raise ValueError("variable name must not have surrounding whitespace")
        validateValue(self.initialValue, self.type, legacy=self.legacyCounterName is not None)
        if self.legacyCounterName is not None and (
            self.name != self.legacyCounterName or self.type != "integer"
            or self.kind != "variable" or self.lifetime != "persistent" or self.initialValue != 0
        ):
            raise ValueError("legacy counters retain their name, integer type, zero initial value and persistence")
        return self


class VariableBinding(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    parameterPath: list[str] = Field(min_length=1, max_length=16)
    variableId: str = Field(min_length=1)


def definitions(values: Mapping[str, object]) -> dict[str, VariableDefinition]:
    return {key: value if isinstance(value, VariableDefinition) else VariableDefinition.model_validate(value)
            for key, value in values.items()}


def scalarFields(schema: Mapping[str, object], prefix=()):
    properties = schema.get("properties", {})
    if not isinstance(properties, Mapping):
        return
    for name, spec in properties.items():
        if not isinstance(spec, Mapping):
            continue
        path = (*prefix, str(name))
        if spec.get("type") in SCALAR_TYPES:
            yield path, spec
        elif spec.get("type") == "object":
            yield from scalarFields(spec, path)


def compatible(variableType: str, parameterType: str) -> bool:
    return variableType == parameterType or (variableType == "integer" and parameterType == "number")


def isFileParameter(spec: Mapping) -> bool:
    """File references need the resource/path pipeline, not scalar substitution."""
    return spec.get("xWidget") == "file" or spec.get("xFileMode") in {"open", "save", "directory"}


def _checkBindableField(spec, path):
    if isFileParameter(spec):
        raise VariableError(
            "E_VARIABLE_FILE_BINDING",
            f"{'.'.join(path)}: file/directory parameters cannot bind global variables; "
            "use fixed values or resource/site bindings",
        )


def resolveParams(params, bindings, values):
    resolved = deepcopy(dict(params))
    for raw in bindings:
        binding = raw if isinstance(raw, VariableBinding) else VariableBinding.model_validate(dict(raw))
        target = resolved
        for part in binding.parameterPath[:-1]:
            child = target.setdefault(part, {})
            if not isinstance(child, dict):
                raise VariableError("E_VARIABLE_BINDING", "parameter parent is not an object")
            target = child
        target[binding.parameterPath[-1]] = values[binding.variableId]
    return resolved


def validateBindings(bindings, variables, schema, *, occupied=()):
    fields = dict(scalarFields(schema))
    seen = set()
    for raw in bindings:
        binding = raw if isinstance(raw, VariableBinding) else VariableBinding.model_validate(dict(raw))
        path = tuple(binding.parameterPath)
        if path in seen or path not in fields:
            raise VariableError("E_VARIABLE_BINDING", f"invalid or duplicate scalar parameter: {'.'.join(path)}")
        seen.add(path)
        _checkBindableField(fields[path], path)
        if any(path[:len(other)] == tuple(other) or tuple(other)[:len(path)] == path for other in occupied):
            raise VariableError("E_VARIABLE_BINDING_CONFLICT", "variable and resource/site parameter bindings overlap")
        definition = variables.get(binding.variableId)
        if definition is None:
            raise VariableError("E_VARIABLE_UNKNOWN", f"unknown variable: {binding.variableId}")
        if not compatible(definition.type, fields[path]["type"]):
            raise VariableError("E_VARIABLE_TYPE", f"variable type does not match {'.'.join(path)}")


def variablePorts(operatorId, params, variables):
    if operatorId not in {READ_VARIABLE, WRITE_VARIABLE}:
        return None
    variableId = params.get("variableId")
    definition = variables.get(variableId)
    if definition is None:
        raise VariableError("E_VARIABLE_UNKNOWN", f"unknown variable: {variableId}")
    if operatorId == WRITE_VARIABLE and definition.kind == "constant":
        raise VariableError("E_VARIABLE_READ_ONLY", "constants cannot be written")
    port = {"type": definition.type, "required": True, "nullable": False}
    return ({"value": dict(port)} if operatorId == WRITE_VARIABLE else {}, {"value": dict(port)})


def validateBoundValues(params, bindings, schema):
    fields = dict(scalarFields(schema))
    for raw in bindings:
        binding = raw if isinstance(raw, VariableBinding) else VariableBinding.model_validate(dict(raw))
        path = tuple(binding.parameterPath)
        spec = fields.get(path)
        if spec is None:
            raise VariableError("E_VARIABLE_BINDING", "parameter schema no longer contains the bound scalar field")
        _checkBindableField(spec, path)
        value = params
        for part in path:
            value = value[part]
        validateValue(value, spec["type"])
        invalid = "enum" in spec and value not in spec["enum"]
        if type(value) in (int, float):
            invalid |= "minimum" in spec and value < spec["minimum"]
            invalid |= "maximum" in spec and value > spec["maximum"]
            invalid |= "exclusiveMinimum" in spec and value <= spec["exclusiveMinimum"]
            invalid |= "exclusiveMaximum" in spec and value >= spec["exclusiveMaximum"]
        if isinstance(value, str):
            import re
            invalid |= len(value) < spec.get("minLength", 0)
            invalid |= "maxLength" in spec and len(value) > spec["maxLength"]
            invalid |= "pattern" in spec and re.search(spec["pattern"], value) is None
        if invalid:
            raise VariableError("E_PARAM_INVALID", f"bound value violates constraints for {'.'.join(path)}")


def validateEffectiveParams(params, bindings, schema):
    """Use the full manifest validator in addition to operator semantics."""
    validateBoundValues(params, bindings, schema)
    from emo_master.core.project.snapshots import _parameters
    return _parameters(params, schema, "params")
