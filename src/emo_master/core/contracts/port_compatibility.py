from __future__ import annotations

from emo_master.core.contracts.port_types import (
    GEOMETRY2D_PORT_TYPES,
    JSON_PAYLOAD_PORT_TYPES,
    isPortNullable,
    listElementType,
    normalizePortType,
    portSchemaVersion,
)


WILDCARD_PORT_TYPES = frozenset({"object", "any"})


def arePortTypesCompatible(sourceType: object, targetType: object) -> bool:
    """Return whether a source output can feed a target input."""
    source = normalizePortType(sourceType)
    target = normalizePortType(targetType)
    if not _areNullabilitiesAssignable(sourceType, targetType):
        return False
    if not _areSchemaVersionsAssignable(sourceType, targetType):
        return False
    if source == target:
        return True
    if source in WILDCARD_PORT_TYPES or target in WILDCARD_PORT_TYPES:
        return True
    if _isSemanticSubtype(source, target):
        return True
    sourceItem = listElementType(source)
    targetItem = listElementType(target)
    if sourceItem is None or targetItem is None:
        return False
    return arePortTypesCompatible(sourceItem, targetItem)


def isPortTypeAssignable(sourceType: object, targetType: object) -> bool:
    """Return whether every source value can safely satisfy the target type."""
    source = normalizePortType(sourceType)
    target = normalizePortType(targetType)
    if not _areNullabilitiesAssignable(sourceType, targetType):
        return False
    if not _areSchemaVersionsAssignable(sourceType, targetType):
        return False
    if source == target:
        return True
    if target in WILDCARD_PORT_TYPES:
        return True
    if source in WILDCARD_PORT_TYPES:
        return False
    if _isSemanticSubtype(source, target):
        return True
    sourceItem = listElementType(source)
    targetItem = listElementType(target)
    if sourceItem is None or targetItem is None:
        return False
    return isPortTypeAssignable(sourceItem, targetItem)


def _isSemanticSubtype(sourceType: str, targetType: str) -> bool:
    if sourceType == "integer" and targetType == "number":
        return True
    if sourceType in GEOMETRY2D_PORT_TYPES and targetType == "geometry2d":
        return True
    return sourceType in JSON_PAYLOAD_PORT_TYPES and targetType == "json"


def _areSchemaVersionsAssignable(sourceSpec: object, targetSpec: object) -> bool:
    sourceVersion = portSchemaVersion(sourceSpec)
    targetVersion = portSchemaVersion(targetSpec)
    if sourceVersion is None or targetVersion is None:
        return True
    if targetVersion.endswith(".x"):
        return sourceVersion.split(".", 1)[0] == targetVersion[:-2]
    if sourceVersion.endswith(".x"):
        return False
    return sourceVersion == targetVersion


def _areNullabilitiesAssignable(sourceSpec: object, targetSpec: object) -> bool:
    sourceNullable = isPortNullable(sourceSpec)
    targetNullable = isPortNullable(targetSpec)
    return not (sourceNullable is True and targetNullable is False)
