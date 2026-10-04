"""Small read-only port descriptions; never copy images or traverse collections."""
from __future__ import annotations

import json
import math
from collections.abc import Sized
from typing import Any, cast

from .geometry2d import (BlobCollection, CircleCollection, ContourCollection,
                        DetectionCollection, LineCollection,
                        ShapeMeasurementCollection, TemplateMatchCollection)

MAX_IO_BYTES = 4096
MAX_PORTS = 12
_SIDE_BYTES = 1792
_SECRET_PARTS = ('password', 'secret', 'token', 'authorization', 'credential', 'apikey', 'privatekey')
_COLLECTION_TYPES = (BlobCollection, CircleCollection, ContourCollection, DetectionCollection,
                     LineCollection, ShapeMeasurementCollection, TemplateMatchCollection)
_COLLECTION_NAMES = {name[0].lower() + name[1:] for name in (kind.__name__ for kind in _COLLECTION_TYPES)}


def clipText(value: str, maximumBytes: int = 240) -> str:
    return value[:maximumBytes].encode('utf-8', errors='replace')[:maximumBytes].decode('utf-8', errors='ignore')


def encodedBytes(value: object) -> int:
    return len(json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode('utf-8'))


def describeValue(value: object) -> dict:
    kind = type(value)
    if value is None or kind is bool:
        return {'kind': 'scalar', 'value': value}
    if kind is int:
        bits = cast(int, value).bit_length()
        return {'kind': 'scalar', 'value': value} if bits <= 128 else {'kind': 'integer', 'bits': bits}
    if kind is float:
        return {'kind': 'scalar', 'value': value} if math.isfinite(cast(float, value)) else {'kind': 'unavailable', 'reason': '非有限数值'}
    if kind is str:
        text = clipText(cast(str, value))
        return {'kind': 'scalar', 'value': text, 'truncated': text != value}
    if kind in (list, tuple, set, frozenset):
        return {'kind': 'collection', 'count': len(cast(Sized, value))}
    if kind is dict:
        payload = cast(dict, value)
        # Known collection DTOs need only a top-level length; never expand items.
        name, items = payload.get('type'), payload.get('items')
        if type(name) is str and name in _COLLECTION_NAMES and type(items) in (list, tuple):
            return {'kind': 'collection', 'count': len(cast(Sized, items))}
        return {'kind': 'object', 'count': len(payload)}
    if kind in _COLLECTION_TYPES:
        return {'kind': 'collection', 'count': len(cast(Any, value).items)}
    if kind.__module__ == 'numpy' and kind.__name__ == 'ndarray':
        return {'kind': 'image' if value.ndim in (2, 3) else 'array',  # type: ignore[attr-defined]
                'shape': list(value.shape[:4]), 'dtype': clipText(str(value.dtype), 32)}  # type: ignore[attr-defined]
    return {'kind': 'unavailable', 'reason': clipText(kind.__name__, 48)}


def _secret(name: str) -> bool:
    normalized = name.lower().replace('_', '').replace('-', '')
    return any(part in normalized for part in _SECRET_PARTS)


def summarizePorts(values: object) -> dict:
    values = cast(dict, values) if type(values) is dict else {}
    items: list[dict] = []
    for index, (name, value) in enumerate(values.items()):
        if index >= MAX_PORTS:
            break
        if not isinstance(name, str):
            continue
        row = {'port': clipText(name, 64), 'value': {'kind': 'redacted'} if _secret(name) else describeValue(value)}
        if encodedBytes({'items': [*items, row], 'portCount': len(values), 'omitted': len(values)}) > _SIDE_BYTES:
            break
        items.append(row)
    return {'items': items, 'portCount': len(values), 'omitted': len(values) - len(items)}


def startInspection(inputs: dict) -> dict:
    return {'version': 1, 'inputs': summarizePorts(inputs), 'outputs': None}


def finishInspection(start: dict, outputs: dict) -> dict:
    return {'version': 1, 'inputs': start['inputs'], 'outputs': summarizePorts(outputs)}


def normalizeInspection(raw: object, legacyOutputs: object = None) -> dict:
    """Validate remote descriptions as well as locally generated DTOs."""
    result: dict = {'version': 1, 'inputs': None, 'outputs': None, 'legacy': True}
    if not isinstance(raw, dict) or raw.get('version') != 1:
        if type(legacyOutputs) is dict:
            result['outputs'] = summarizePorts(legacyOutputs)
        return result
    result['legacy'] = False
    for side in ('inputs', 'outputs'):
        batch = raw.get(side)
        if not isinstance(batch, dict) or not isinstance(batch.get('items'), list):
            continue
        items: list[dict] = []
        for row in batch['items'][:MAX_PORTS]:
            if not isinstance(row, dict) or not isinstance(row.get('port'), str):
                continue
            name = clipText(row['port'], 64)
            description = row.get('value')
            value = _normalizeDescription(description)
            item = {'port': name, 'value': {'kind': 'redacted'} if _secret(row['port']) else value}
            if encodedBytes({'items': [*items, item], 'portCount': 1_000_000, 'omitted': 1_000_000}) > _SIDE_BYTES:
                break
            items.append(item)
        total = batch.get('portCount', len(batch['items']))
        total = min(1_000_000, max(len(items), total)) if type(total) is int else len(items)
        result[side] = {'items': items, 'portCount': total, 'omitted': total - len(items)}
    return result


def _normalizeDescription(value: object) -> dict:
    if not isinstance(value, dict):
        return {'kind': 'unavailable', 'reason': '摘要无效'}
    kind = value.get('kind')
    if not isinstance(kind, str):
        return {'kind': 'unavailable', 'reason': '摘要类型无效'}
    if kind == 'scalar':
        result = describeValue(value.get('value'))
        if value.get('truncated'):
            result['truncated'] = True
        return result
    if kind in {'object', 'collection', 'integer'}:
        field = 'bits' if kind == 'integer' else 'count'
        count = value.get(field)
        if type(count) is int and 0 <= count <= 1_000_000_000:
            return {'kind': kind, field: count}
    if kind in {'image', 'array'}:
        shape = value.get('shape')
        if isinstance(shape, list) and len(shape) <= 4 and all(type(n) is int and 0 <= n <= 1_000_000 for n in shape):
            dtype = value.get('dtype', '')
            return {'kind': kind, 'shape': list(shape), 'dtype': clipText(dtype, 32) if isinstance(dtype, str) else ''}
    if kind == 'redacted':
        return {'kind': 'redacted'}
    reason = value.get('reason', '仅提供摘要')
    return {'kind': 'unavailable', 'reason': clipText(reason, 48) if isinstance(reason, str) else '仅提供摘要'}
