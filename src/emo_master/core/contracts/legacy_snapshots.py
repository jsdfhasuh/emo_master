"""Explicit per-Run legacy preview capture policy; independent of page capture."""


def normalizeLegacySnapshotPolicy(value: object = "") -> str:
    if not isinstance(value, str) or value not in {"", "ALL", "NONE"}:
        raise ValueError("legacy snapshot policy must be ALL or NONE")
    return value or "ALL"
