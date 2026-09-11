import hashlib
import struct
from types import SimpleNamespace
import zlib


SVG = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><rect x="3" y="3" width="18" height="18" fill="#e23456"/></svg>'


def iconDefinition(content=SVG, *, operatorId="test.icon", version="1.0.0", mimeType="image/svg+xml"):
    return {"operatorId": operatorId, "displayName": operatorId, "version": version, "iconKey": "edge",
            "icon": {"status": "ready", "mimeType": mimeType, "sha256": hashlib.sha256(content).hexdigest(),
                     "byteSize": len(content)}, "inputPorts": {}, "outputPorts": {}}


def iconReply(content=SVG, *, version="1.0.0", mimeType="image/svg+xml", **fields):
    reply = {"ok": True, "content": content, "version": version, "mime_type": mimeType,
             "sha256": hashlib.sha256(content).hexdigest()}
    reply.update(fields)
    return SimpleNamespace(**reply)


def chunk(kind: bytes, data: bytes = b"") -> bytes:
    return (
        struct.pack(">I", len(data))
        + kind
        + data
        + struct.pack(">I", zlib.crc32(kind + data))
    )


def png(
    *,
    width=2,
    height=2,
    depth=8,
    color=6,
    interlace=0,
    raw=None,
    extra=b"",
    compressed=None,
):
    if raw is None:
        channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}.get(color, 4)
        raw = (b"\0" + b"\xff" * ((width * channels * depth + 7) // 8)) * height
    if compressed is None:
        compressed = zlib.compress(raw)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(
            b"IHDR",
            struct.pack(">IIBBBBB", width, height, depth, color, 0, 0, interlace),
        )
        + extra
        + chunk(b"IDAT", compressed)
        + chunk(b"IEND")
    )
