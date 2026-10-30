"""Resolve page-local files only against an explicitly supplied project root."""
from pathlib import Path


def resolveLocalPath(value: str, projectRoot=None, *, purpose='文件') -> Path:
    path = Path(value).expanduser()
    if path.is_absolute():
        return path
    root = Path(projectRoot).expanduser() if projectRoot else None
    if root is None or not root.is_absolute():
        raise ValueError(f'相对{purpose}路径需要已打开工程的完整目录')
    return root / path
