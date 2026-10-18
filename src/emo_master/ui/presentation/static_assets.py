"""Bounded raster decode from verified bytes, never from an external URL."""
import hashlib
from pathlib import Path

from PySide2.QtCore import QByteArray, QBuffer, QIODevice, QSize
from PySide2.QtGui import QImageReader

from emo_master.core.presentation.assets import MAX_ASSET_BYTES, MAX_ASSET_PIXELS


def readAsset(asset, remaining, *, icon=False):
    path, digest, size = asset
    if size > MAX_ASSET_BYTES or size < 1:
        raise ValueError("STATIC_ASSET_SIZE")
    with Path(path).open("rb") as stream:
        data = stream.read(size + 1)
    if len(data) != size or hashlib.sha256(data).hexdigest() != digest:
        raise ValueError("STATIC_ASSET_HASH")
    buffer = QBuffer()
    buffer.setData(QByteArray(data))
    buffer.open(QIODevice.ReadOnly)
    reader = QImageReader(buffer)
    if bytes(reader.format()).lower() not in {b"png", b"jpeg", b"bmp"}:
        raise ValueError("STATIC_ASSET_FORMAT: PNG/JPEG/BMP required")
    dimensions = reader.size()
    pixels = dimensions.width() * dimensions.height()
    if dimensions.width() <= 0 or dimensions.height() <= 0 or pixels > MAX_ASSET_PIXELS:
        raise ValueError("STATIC_ASSET_DIMENSIONS")
    # Reserve full decode size even when the displayed icon will be small.
    if pixels * 8 > remaining:
        raise ValueError("STATIC_ASSET_BUDGET")
    image = reader.read()
    if image.isNull():
        raise ValueError("STATIC_ASSET_DECODE")
    if image.sizeInBytes() > remaining:
        raise ValueError("STATIC_ASSET_BUDGET")
    if icon:
        from PySide2.QtCore import Qt
        image = image.scaled(QSize(32, 32), Qt.KeepAspectRatio, Qt.SmoothTransformation)
    return image
