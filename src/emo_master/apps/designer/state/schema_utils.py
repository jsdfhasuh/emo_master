from __future__ import annotations

from copy import deepcopy


def mergeParameterTitles(
    schema: dict[str, object], catalogSchema: dict[str, object],
) -> dict[str, object]:
    """Overlay display annotations without changing a saved node's contract."""
    result = deepcopy(schema)

    def merge(target: dict[str, object], source: dict[str, object]) -> None:
        properties = target.get("properties")
        sourceProperties = source.get("properties")
        if isinstance(properties, dict) and isinstance(sourceProperties, dict):
            for name, field in properties.items():
                sourceField = sourceProperties.get(name)
                if not isinstance(field, dict) or not isinstance(sourceField, dict):
                    continue
                title = sourceField.get("title")
                if isinstance(title, str) and title.strip():
                    field["title"] = title.strip()
                merge(field, sourceField)
        items = target.get("items")
        sourceItems = source.get("items")
        if isinstance(items, dict) and isinstance(sourceItems, dict):
            merge(items, sourceItems)
        elif isinstance(items, list) and isinstance(sourceItems, list):
            for item, sourceItem in zip(items, sourceItems):
                if isinstance(item, dict) and isinstance(sourceItem, dict):
                    merge(item, sourceItem)

    merge(result, catalogSchema)
    return result


def applySchemaDefaults(schema: dict[str, object], value: object) -> object:
  schemaType = _getSchemaType(schema)
  if schemaType == "object":
    rawProperties = schema.get("properties", {})
    properties = rawProperties if isinstance(rawProperties, dict) else {}
    rawValue = value if isinstance(value, dict) else {}
    result: dict[str, object] = {}
    required = schema.get("required", [])
    for propertyName, propertySchema in properties.items():
      if not isinstance(propertyName, str) or not isinstance(propertySchema, dict):
        continue
      if (propertySchema.get("xOptionalPresence") and propertyName not in rawValue
          and (not isinstance(required, list) or propertyName not in required)):
        continue
      currentValue = rawValue.get(propertyName)
      result[propertyName] = applySchemaDefaults(propertySchema, currentValue)
    for key, extraValue in rawValue.items():
      if isinstance(key, str) and key not in result:
        result[key] = extraValue
    return result

  if schemaType == "array":
    itemSchemaRaw = schema.get("items", {})
    itemSchema = itemSchemaRaw if isinstance(itemSchemaRaw, dict) else {}
    if isinstance(value, list):
      return [applySchemaDefaults(itemSchema, itemValue) for itemValue in value]
    defaultValue = schema.get("default")
    if isinstance(defaultValue, list):
      return [applySchemaDefaults(itemSchema, itemValue) for itemValue in defaultValue]
    return []

  if value is not None:
    return value

  if "default" in schema:
    return schema.get("default")

  if schemaType == "boolean":
    return False
  if schemaType == "integer":
    return 0
  if schemaType == "number":
    return 0.0
  if schemaType == "string":
    return ""

  return None


def collectSchemaErrors(schema: dict[str, object], value: object, path: str = "params") -> list[str]:
  errors: list[str] = []
  schemaType = _getSchemaType(schema)

  if schemaType == "object":
    if not isinstance(value, dict):
      errors.append(f"{path} must be object")
      return errors

    requiredRaw = schema.get("required", [])
    required = requiredRaw if isinstance(requiredRaw, list) else []
    for requiredName in required:
      if isinstance(requiredName, str) and requiredName not in value:
        errors.append(f"{path}.{requiredName} is required")

    propertiesRaw = schema.get("properties", {})
    properties = propertiesRaw if isinstance(propertiesRaw, dict) else {}
    for key, itemSchema in properties.items():
      if not isinstance(key, str) or not isinstance(itemSchema, dict):
        continue
      if key in value:
        errors.extend(collectSchemaErrors(itemSchema, value[key], f"{path}.{key}"))
    return errors

  if schemaType == "array":
    if not isinstance(value, list):
      errors.append(f"{path} must be array")
      return errors
    itemSchemaRaw = schema.get("items", {})
    itemSchema = itemSchemaRaw if isinstance(itemSchemaRaw, dict) else {}
    for index, itemValue in enumerate(value):
      errors.extend(collectSchemaErrors(itemSchema, itemValue, f"{path}[{index}]"))
    return errors

  if not _matchesType(schemaType, value):
    errors.append(f"{path} must be {schemaType}")
    return errors

  enumRaw = schema.get("enum", [])
  if isinstance(enumRaw, list) and len(enumRaw) > 0 and value not in enumRaw:
    errors.append(f"{path} must be one of {enumRaw}")

  minimum = schema.get("minimum")
  maximum = schema.get("maximum")
  if isinstance(value, (int, float)):
    if isinstance(minimum, (int, float)) and value < minimum:
      errors.append(f"{path} must be >= {minimum}")
    if isinstance(maximum, (int, float)) and value > maximum:
      errors.append(f"{path} must be <= {maximum}")
  return errors


def _getSchemaType(schema: dict[str, object]) -> str:
  rawType = schema.get("type")
  if isinstance(rawType, str) and rawType in {
    "boolean", "integer", "number", "string", "array", "object"
  }:
    return rawType
  return "string"


def _matchesType(schemaType: str, value: object) -> bool:
  if schemaType == "boolean":
    return isinstance(value, bool)
  if schemaType == "integer":
    return isinstance(value, int) and not isinstance(value, bool)
  if schemaType == "number":
    return isinstance(value, (int, float)) and not isinstance(value, bool)
  if schemaType == "string":
    return isinstance(value, str)
  if schemaType == "object":
    return isinstance(value, dict)
  if schemaType == "array":
    return isinstance(value, list)
  return True
