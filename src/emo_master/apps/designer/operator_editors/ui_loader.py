from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import cast
from uuid import uuid4


MAX_UI_BYTES = 2 * 1024 * 1024


class UiLoadError(RuntimeError):
    pass


try:
    from PySide2.QtCore import QByteArray, QBuffer, QIODevice
    from PySide2.QtUiTools import QUiLoader
    from PySide2.QtWidgets import QWidget

    QT_UI_AVAILABLE = True
except Exception:  # pragma: no cover
    QT_UI_AVAILABLE = False


def loadUiBytes(content: bytes, parent: object | None = None) -> object:
    if not QT_UI_AVAILABLE:
        raise UiLoadError("PySide2 QUiLoader is unavailable")
    if not content or len(content) > MAX_UI_BYTES:
        raise UiLoadError("editor UI must contain 1 to 2097152 bytes")
    buffer = QBuffer()  # type: ignore[name-defined]
    buffer.setData(QByteArray(content))  # type: ignore[name-defined]
    if not buffer.open(QIODevice.ReadOnly):  # type: ignore[name-defined]
        raise UiLoadError("failed to open editor UI buffer")
    try:
        widget = QUiLoader().load(  # type: ignore[name-defined]
            buffer, cast(QWidget | None, parent)
        )
    finally:
        buffer.close()
    if widget is None:
        raise UiLoadError("QUiLoader failed to create the editor UI")
    return widget


class EditorAssetCache:
    def __init__(self, root: Path | None = None) -> None:
        configured = os.environ.get("EMO_DESIGNER_CACHE_DIR")
        base = Path(configured).expanduser() if configured else Path.home() / ".emo_master" / "designer"
        self.root = root or (base / "editor-ui-cache")

    def store(self, content: bytes, sha256: str) -> Path:
        actual = hashlib.sha256(content).hexdigest()
        if actual != sha256:
            raise UiLoadError("editor UI SHA-256 mismatch")
        self.root.mkdir(parents=True, exist_ok=True)
        destination = self.root / f"{sha256}.ui"
        if destination.exists():
            try:
                if hashlib.sha256(destination.read_bytes()).hexdigest() == sha256:
                    return destination
            except OSError:
                pass
        temp = destination.with_name(f".{destination.name}.{uuid4().hex}.tmp")
        try:
            temp.write_bytes(content)
            temp.replace(destination)
        finally:
            try:
                temp.unlink(missing_ok=True)
            except OSError:
                pass
        return destination
