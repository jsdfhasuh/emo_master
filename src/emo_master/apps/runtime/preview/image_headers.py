"""Bounded PNG/JPEG/BMP dimensions; never decode a Runtime history image."""
import struct


def imageHeader(path):
    with path.open('rb') as handle:
        prefix = handle.read(32)
        if prefix[:8] == b'\x89PNG\r\n\x1a\n' and prefix[12:16] == b'IHDR':
            width, height = struct.unpack('>II', prefix[16:24])
            return width, height, 'image/png'
        if prefix[:2] == b'BM' and len(prefix) >= 26:
            width, height = struct.unpack('<ii', prefix[18:26])
            return width, abs(height), 'image/bmp'
        if prefix[:2] == b'\xff\xd8':
            handle.seek(2)
            while handle.tell() < 64 * 1024:
                marker = handle.read(2)
                if len(marker) != 2 or marker[0] != 255:
                    break
                code = marker[1]
                if code == 255:
                    handle.seek(-1, 1)
                    continue
                if code in (0xD8, 0xD9):
                    continue
                raw = handle.read(2)
                if len(raw) != 2:
                    break
                size = int.from_bytes(raw, 'big')
                if size < 2:
                    break
                if code in (0xC0, 0xC1, 0xC2):
                    raw = handle.read(5)
                    if len(raw) == 5:
                        height, width = struct.unpack('>HH', raw[1:5])
                        return width, height, 'image/jpeg'
                    break
                handle.seek(size - 2, 1)
    raise ValueError('E_INSPECTION_IMAGE_HEADER: 保存图片头损坏或格式暂不支持（PNG/JPEG/BMP）')
