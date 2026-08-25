from __future__ import annotations


WILDCARD_PORT_TYPES = frozenset({"object", "any"})


def arePortTypesCompatible(sourceType: str, targetType: str) -> bool:
    """Return whether a source output can feed a target input."""
    return (
        sourceType == targetType
        or sourceType in WILDCARD_PORT_TYPES
        or targetType in WILDCARD_PORT_TYPES
    )
