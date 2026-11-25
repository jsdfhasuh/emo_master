"""Validate descriptions that sit alongside an operator's execution contracts."""

from collections.abc import Mapping, Sequence


_FIELD_GROUPS = {"duration", "business", "result", "parameter"}
_FIELD_KEYS = {"title", "description", "unit", "group", "fields", "items"}
_SECTIONS = ("inputs", "outputs", "metrics", "diagnostics")


def parameterSchemaAtPath(
    schema: Mapping[str, object], path: Sequence[str],
) -> Mapping[str, object] | None:
    """Find a declared object property, including mode-dependent properties."""
    for part in path:
        properties = schema.get("properties")
        child = properties.get(part) if isinstance(properties, Mapping) else None
        if not isinstance(child, Mapping):
            return None
        schema = child
    return schema


def _descriptions(value: object, path: str) -> dict[str, object]:
    if not isinstance(value, dict) or any(not isinstance(key, str) or not key.strip() for key in value):
        raise ValueError(f"{path} must map field names to descriptions")
    for key, field in value.items():
        _description(field, f"{path}.{key}")
    return value


def _description(value: object, path: str) -> None:
    if not isinstance(value, dict) or value.keys() - _FIELD_KEYS:
        raise ValueError(f"{path} must be a FieldDescription")
    for name in ("title", "description", "unit"):
        if name == "unit" and name not in value:
            continue
        text = value.get(name)
        if not isinstance(text, str) or not text.strip():
            raise ValueError(f"{path}.{name} must be non-empty text")
    if "group" in value:
        group = value["group"]
        if not isinstance(group, str) or group not in _FIELD_GROUPS:
            raise ValueError(f"{path}.group must be duration, business, result or parameter")
    if "fields" in value:
        _descriptions(value["fields"], f"{path}.fields")
    if "items" in value:
        _description(value["items"], f"{path}.items")


def validateResultSchema(
    value: object, inputs: Mapping[str, object], outputs: Mapping[str, object],
    paramSchema: Mapping[str, object],
) -> dict[str, object]:
    if not isinstance(value, dict) or value.keys() - {*_SECTIONS, "captureParams"}:
        raise ValueError("resultSchema must contain only inputs, outputs, metrics, diagnostics or captureParams")
    portGroups = {"inputs": inputs, "outputs": outputs}
    for section in _SECTIONS:
        if section not in value:
            continue
        path = f"resultSchema.{section}"
        descriptions = _descriptions(value[section], path)
        if section in portGroups:
            unknown = descriptions.keys() - portGroups[section].keys()
            if unknown:
                raise ValueError(f"{path} references unknown ports: {', '.join(sorted(unknown))}")

    captures = value.get("captureParams", [])
    if not isinstance(captures, list):
        raise ValueError("resultSchema.captureParams must be a list of parameter paths")
    seen: set[tuple[str, ...]] = set()
    for index, pathParts in enumerate(captures):
        path = f"resultSchema.captureParams[{index}]"
        if (not isinstance(pathParts, list) or not pathParts
                or any(not isinstance(part, str) or not part.strip() for part in pathParts)):
            raise ValueError(f"{path} must be a non-empty list of parameter path segments")
        if parameterSchemaAtPath(paramSchema, pathParts) is None:
            raise ValueError(f"{path} references an unknown parameter: {'.'.join(pathParts)}")
        key = tuple(pathParts)
        if key in seen:
            raise ValueError(f"{path} duplicates parameter: {'.'.join(pathParts)}")
        seen.add(key)
    return value
