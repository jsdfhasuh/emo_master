"""Existing Python canonical form; revision semantics are preserved for all clients."""
import hashlib
import json


def canonicalJson(value: object) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":"), allow_nan=False)


def revisionOf(value: object) -> str:
    return hashlib.sha256(canonicalJson(value).encode("utf-8")).hexdigest()
