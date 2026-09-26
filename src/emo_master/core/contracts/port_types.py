from __future__ import annotations

from collections.abc import Mapping

from emo_master.core.contracts.communication import (
    COMMUNICATION_PAYLOAD_PORT_TYPES,
    SUPPORTED_COMMUNICATION_PAYLOAD_SCHEMA_VERSIONS,
    matchesCommunicationPortType,
)
from emo_master.core.contracts.geometry2d import (
    CONCRETE_GEOMETRY_PORT_TYPES,
    SUPPORTED_VISION_PAYLOAD_SCHEMA_VERSIONS,
    VISION_PAYLOAD_PORT_TYPES,
    matchesVisionPortType,
)


_ALIASES = {
    "bool": "boolean",
    "int": "integer",
    "float": "number",
    "rotatedbox2d": "rotatedBox2d",
    "blobcollection": "blobCollection",
    "detectioncollection": "detectionCollection",
    "colorstatistics": "colorStatistics",
    "contourcollection": "contourCollection",
    "shapemeasurementcollection": "shapeMeasurementCollection",
    "linecollection": "lineCollection",
    "circlecollection": "circleCollection",
    "templatematchcollection": "templateMatchCollection",
    "plcvaluecollection": "plcValueCollection",
    "plcwritereceipt": "plcWriteReceipt",
    "tcpmessage": "tcpMessage",
}

JSON_PAYLOAD_PORT_TYPES = VISION_PAYLOAD_PORT_TYPES | COMMUNICATION_PAYLOAD_PORT_TYPES
GEOMETRY2D_PORT_TYPES = CONCRETE_GEOMETRY_PORT_TYPES
PORT_DESCRIPTOR_FIELDS = frozenset(
    {"type", "required", "nullable", "schemaVersion"}
)


class PortSpecValidationError(ValueError):
    """Raised when a string or descriptor does not define a valid port."""


def normalizePortType(value: object) -> str:
    """Return the canonical string representation for a port type."""
    if isinstance(value, Mapping):
        rawType = value.get("type", "object")
        if rawType in {"array", "list"}:
            return listPortType(value.get("items", "any"))
        value = rawType
    if not isinstance(value, str) or value.strip() == "":
        return "object"
    typeName = value.strip()
    lowered = typeName.lower()
    if lowered == "list":
        return "list<any>"
    if lowered.startswith("list<") and lowered.endswith(">"):
        inner = typeName[5:-1].strip()
        return f"list<{normalizePortType(inner)}>"
    return _ALIASES.get(lowered, lowered)


def listPortType(elementType: object) -> str:
    return f"list<{normalizePortType(elementType)}>"


def listElementType(typeName: object) -> str | None:
    normalized = normalizePortType(typeName)
    if normalized.startswith("list<") and normalized.endswith(">"):
        return normalized[5:-1]
    return None


def validatePortSpec(value: object, path: str = "port") -> str | dict[str, object]:
    if isinstance(value, str):
        if value == "" or value.strip() != value:
            raise PortSpecValidationError(f"{path} type must be a trimmed non-empty string")
        return value
    if not isinstance(value, Mapping):
        raise PortSpecValidationError(f"{path} must be a type string or descriptor")
    if any(not isinstance(key, str) for key in value):
        raise PortSpecValidationError(f"{path} descriptor keys must be strings")
    unknown = sorted(set(value) - PORT_DESCRIPTOR_FIELDS)
    if unknown:
        raise PortSpecValidationError(
            f"{path} descriptor has unknown fields: {', '.join(unknown)}"
        )
    rawType = value.get("type")
    if not isinstance(rawType, str) or rawType == "" or rawType.strip() != rawType:
        raise PortSpecValidationError(f"{path}.type must be a trimmed non-empty string")
    for fieldName in ("required", "nullable"):
        fieldValue = value.get(fieldName)
        if fieldName in value and not isinstance(fieldValue, bool):
            raise PortSpecValidationError(f"{path}.{fieldName} must be boolean")
    hasSchemaVersion = "schemaVersion" in value
    schemaVersion = value.get("schemaVersion")
    if hasSchemaVersion:
        normalizedType = normalizePortType(rawType)
        itemType = listElementType(normalizedType)
        semanticType = normalizedType if normalizedType in JSON_PAYLOAD_PORT_TYPES else itemType
        if semanticType not in JSON_PAYLOAD_PORT_TYPES:
            raise PortSpecValidationError(
                f"{path}.schemaVersion is only valid for semantic payloads"
            )
        if not _isSupportedSchemaVersionSelector(schemaVersion, semanticType):
            raise PortSpecValidationError(
                f"{path}.schemaVersion must be a supported exact version or '1.x'"
            )
    return {
        "type": rawType,
        **({"required": value["required"]} if "required" in value else {}),
        **({"nullable": value["nullable"]} if "nullable" in value else {}),
        **(
            {"schemaVersion": schemaVersion}
            if hasSchemaVersion
            else {}
        ),
    }


def normalizePortSpec(value: object) -> dict[str, object]:
    validated = validatePortSpec(value)
    if isinstance(validated, str):
        return {"type": normalizePortType(validated)}
    return {**validated, "type": normalizePortType(validated["type"])}


def isPortRequired(value: object, *, default: bool = True) -> bool:
    if isinstance(value, Mapping) and isinstance(value.get("required"), bool):
        return bool(value["required"])
    return default


def isPortNullable(value: object) -> bool | None:
    if isinstance(value, Mapping) and isinstance(value.get("nullable"), bool):
        return bool(value["nullable"])
    return None


def portSchemaVersion(value: object) -> str | None:
    if not isinstance(value, Mapping):
        return None
    schemaVersion = value.get("schemaVersion")
    return schemaVersion if isinstance(schemaVersion, str) else None


def matchesPortSpec(value: object, expectedSpec: object) -> bool:
    nullable = isPortNullable(expectedSpec)
    if value is None and nullable is not None:
        return nullable
    return matchesPortType(value, expectedSpec)


def canonicalPortTypes(ports: Mapping[str, object]) -> dict[str, str]:
    return {name: normalizePortType(spec) for name, spec in ports.items()}


def matchesPortType(value: object, expectedType: object) -> bool:
    """Validate a runtime value against the supported workflow port types."""
    expected = normalizePortType(expectedType)
    if expected in {"any", "object", "json", "image"}:
        return True
    itemType = listElementType(expected)
    if itemType is not None:
        itemSpec: object = itemType
        schemaVersion = portSchemaVersion(expectedType)
        if schemaVersion is not None:
            itemSpec = {"type": itemType, "schemaVersion": schemaVersion}
        return isinstance(value, list) and all(
            matchesPortSpec(item, itemSpec) for item in value
        )
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "string":
        return isinstance(value, str)
    if expected in VISION_PAYLOAD_PORT_TYPES:
        return matchesVisionPortType(
            value,
            expected,
            portSchemaVersion(expectedType),
        )
    if expected in COMMUNICATION_PAYLOAD_PORT_TYPES:
        return matchesCommunicationPortType(
            value,
            expected,
            portSchemaVersion(expectedType),
        )
    return True


def _isSupportedSchemaVersionSelector(value: object, semanticType: object) -> bool:
    if not isinstance(value, str):
        return False
    supportedVersions = (
        SUPPORTED_VISION_PAYLOAD_SCHEMA_VERSIONS
        if semanticType in VISION_PAYLOAD_PORT_TYPES
        else SUPPORTED_COMMUNICATION_PAYLOAD_SCHEMA_VERSIONS
    )
    if value.endswith(".x"):
        major = value[:-2]
        return major.isdigit() and any(
            version.split(".", 1)[0] == major
            for version in supportedVersions
        )
    return value in supportedVersions
