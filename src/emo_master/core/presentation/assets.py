"""Declared page assets; callers never infer paths from component text."""
from pathlib import Path


MAX_ASSET_BYTES = 8 * 1024 * 1024
MAX_ASSET_PIXELS = 4 * 1024 * 1024


def pageAssets(document, root=None):
    result = {}
    if document.resources is None:
        return result
    base = Path(root).resolve() if root is not None else None
    if base is not None and base.is_file():
        base = base.parent
    for key, item in document.resources.items.items():
        if item.purpose not in {"page_image", "icon"}:
            continue
        path = document._pageResourcePaths.get(key)
        if path is None and base is not None:
            candidate = (base / item.path).resolve()
            if candidate.is_relative_to(base):
                path = str(candidate)
        if path is not None:
            result[key] = (str(path), item.sha256, item.size)
    return result
