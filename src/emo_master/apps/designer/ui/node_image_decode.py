"""Owned QImage decode with an 8 MiB bitmap and 8 MiB encoded scratch bound."""
from dataclasses import dataclass

from PySide2.QtCore import QBuffer, QIODevice
from PySide2.QtGui import QImage, QImageReader


@dataclass(frozen=True)
class DecodedNodeImage:
    image: QImage
    bytes: int
    format: str
    scratchBytes: int
    peakDecodedBytes: int

    def __iter__(self):
        # Preserve the existing three-value helper interface.
        return iter((self.image, self.bytes, self.format))


def decodeNodeImage(payload, width, height):
    encodedBytes = len(payload)
    if len(payload) > 4 * 1024 * 1024:
        raise ValueError('E_INSPECTION_READ_BUDGET: 编码暂存与 Qt 缓冲合计超过8 MiB')
    buffer = QBuffer()
    buffer.setData(payload)
    buffer.open(QIODevice.ReadOnly)
    reader = QImageReader(buffer)
    dimensions = reader.size()
    if not dimensions.isValid():
        raise ValueError('E_INSPECTION_CORRUPT: 文件损坏或不是可解码图片')
    if (dimensions.width(), dimensions.height()) != (width, height):
        raise ValueError('E_INSPECTION_IDENTITY: 图片实际尺寸与资产元数据不符')
    nativeFormat = reader.imageFormat()
    bytesPerPixel = 8 if nativeFormat in (QImage.Format_RGBX64, QImage.Format_RGBA64,
                                        QImage.Format_RGBA64_Premultiplied) else 4
    if width * height * bytesPerPixel > 8 * 1024 * 1024:
        raise ValueError('E_INSPECTION_DECODE_BUDGET: 当前图片解码超过8 MiB')
    formatName = bytes(reader.format()).decode('ascii', errors='replace').upper()
    image = reader.read()
    buffer.close()
    # Release encoded buffers before any format conversion, so the original
    # decoded bitmap is the only conversion temporary (<=8 MiB).
    buffer.setData(b'')
    if isinstance(payload, bytearray):
        payload.clear()
    if image.isNull():
        raise ValueError('E_INSPECTION_CORRUPT: 图片解码失败：' + reader.errorString())
    originalBytes = image.sizeInBytes()
    if originalBytes > 8 * 1024 * 1024:
        raise ValueError('E_INSPECTION_DECODE_BUDGET: 原始解码缓冲超过8 MiB')
    # QImageReader owns its pixels. Convert only when needed; the temporary
    # original is bounded by 8 MiB and released before delivery to the GUI.
    converting = image.format() not in (QImage.Format_RGB32, QImage.Format_ARGB32, QImage.Format_RGBA8888)
    if converting:
        image = image.convertToFormat(QImage.Format_ARGB32)
    if image.sizeInBytes() > 8 * 1024 * 1024:
        raise ValueError('E_INSPECTION_DECODE_BUDGET: 图片缓冲超过8 MiB')
    return DecodedNodeImage(image, image.sizeInBytes(), formatName,
        max(2 * encodedBytes, originalBytes if converting else 0), max(originalBytes, image.sizeInBytes()))
