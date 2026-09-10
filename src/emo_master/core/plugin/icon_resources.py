"""Bounded, GUI-independent validation of the v1 operator icon format."""

from __future__ import annotations

import hashlib
import io
import math
from pathlib import Path
import re
import stat
import struct
import unicodedata
import zlib
from xml.etree.ElementTree import ParseError

from emo_master.core.plugin.models import PluginIconAsset, ValidationIssue


MAX_ICON_BYTES = 262144
MAX_ICON_TOTAL_BYTES = 16 * 1024 * 1024
ICON_MIMES = {".png": "image/png", ".svg": "image/svg+xml"}
_SVG_NS = "http://www.w3.org/2000/svg"
_NUMBER = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?")
_IDENTITY = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
_PUBLIC = {
    "fill",
    "stroke",
    "stroke-width",
    "stroke-linecap",
    "stroke-linejoin",
    "stroke-miterlimit",
    "fill-rule",
    "opacity",
    "fill-opacity",
    "stroke-opacity",
}
_ATTRS = {
    "svg": {"viewBox", "width", "height", "preserveAspectRatio"},
    "g": {"transform"},
    "path": {"d", "transform"},
    "rect": {"x", "y", "width", "height", "rx", "ry", "transform"},
    "circle": {"cx", "cy", "r", "transform"},
    "ellipse": {"cx", "cy", "rx", "ry", "transform"},
    "line": {"x1", "y1", "x2", "y2", "transform"},
    "polyline": {"points", "transform"},
    "polygon": {"points", "transform"},
    "title": set(),
    "desc": set(),
}


class IconValidationError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)

    def issue(self) -> ValidationIssue:
        return ValidationIssue("ICON_RESOURCE", self.code, str(self))


def _reject(message: str, code: str = "E_ICON_CONTENT_INVALID") -> None:
    raise IconValidationError(code, message)


def _limit(condition: bool, message: str) -> None:
    if condition:
        _reject(message, "E_ICON_LIMIT_EXCEEDED")


def parseIconResource(
    data: dict[str, object],
) -> tuple[str, tuple[ValidationIssue, ...]]:
    value = data.get("iconResource", "")
    if not isinstance(value, str):
        return "", (
            ValidationIssue(
                "ICON_RESOURCE",
                "E_ICON_SPEC_INVALID",
                "iconResource must be a relative path string",
            ),
        )
    if not value:
        return "", ()
    try:
        parts = value.split("/")
        if (
            ":" in value
            or "\\" in value
            or any(unicodedata.category(c) == "Cc" for c in value)
            or any(
                p in {"", ".", ".."} or p.strip() != p or p.endswith(".") for p in parts
            )
            or any(
                re.fullmatch(r"(?i)(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?", p)
                for p in parts
            )
        ):
            _reject(
                "iconResource must be a canonical in-plugin POSIX path",
                "E_ICON_PATH_INVALID",
            )
    except IconValidationError as err:
        return "", (err.issue(),)
    return value, ()


def loadIconAsset(
    root: Path, resource: str, budget: int = MAX_ICON_TOTAL_BYTES
) -> PluginIconAsset:
    normalized, issues = parseIconResource({"iconResource": resource})
    if issues:
        raise IconValidationError(issues[0].code, issues[0].message)
    if not normalized:
        _reject("no icon resource", "E_ICON_ASSET_NOT_FOUND")
    try:
        resolvedRoot = root.resolve(strict=True)
        path = root.joinpath(*resource.split("/")).resolve(strict=True)
        try:
            path.relative_to(resolvedRoot)
        except ValueError:
            _reject("icon resource escapes plugin root", "E_ICON_PATH_INVALID")
        if not stat.S_ISREG(path.stat().st_mode):
            _reject("icon resource must be a regular file", "E_ICON_ASSET_NOT_FOUND")
        with path.open("rb") as stream:
            content = stream.read(min(MAX_ICON_BYTES, max(0, budget)) + 1)
    except (OSError, RuntimeError) as err:
        # Do not propagate absolute paths in OS exception messages over the RPC.
        raise IconValidationError(
            "E_ICON_ASSET_NOT_FOUND",
            f"icon resource unavailable ({type(err).__name__})",
        ) from err
    _limit(len(content) > budget, "registry icon byte budget exceeded")
    mime = ICON_MIMES.get(Path(resource).suffix.lower(), "")
    validateIconContent(content, mime)
    return PluginIconAsset(content, mime, hashlib.sha256(content).hexdigest())


def validateIconContent(content: bytes, mimeType: str) -> tuple[int, int] | None:
    _limit(not 1 <= len(content) <= MAX_ICON_BYTES, "icon size must be 1..262144 bytes")
    if mimeType == "image/png":
        return _validatePng(content)
    if mimeType == "image/svg+xml":
        _validateSvg(content)
        return None
    _reject(
        "only static PNG and restricted SVG are supported", "E_ICON_FORMAT_UNSUPPORTED"
    )
    return None


def _validatePng(content: bytes) -> tuple[int, int]:
    if not content.startswith(b"\x89PNG\r\n\x1a\n"):
        _reject("PNG signature does not match its MIME", "E_ICON_FORMAT_UNSUPPORTED")
    chunks: list[tuple[bytes, bytes]] = []
    offset = 8
    while offset < len(content):
        if len(content) - offset < 12:
            _reject("truncated PNG chunk")
        size = struct.unpack_from(">I", content, offset)[0]
        end = offset + 12 + size
        if end > len(content):
            _reject("PNG chunk exceeds file boundary")
        kind = content[offset + 4 : offset + 8]
        data = content[offset + 8 : end - 4]
        crc = struct.unpack_from(">I", content, end - 4)[0]
        if zlib.crc32(kind + data) != crc:
            _reject("invalid PNG CRC")
        if kind not in {
            b"IHDR",
            b"PLTE",
            b"IDAT",
            b"IEND",
            b"tRNS",
            b"sRGB",
            b"gAMA",
            b"cHRM",
            b"pHYs",
        }:
            _reject(
                "PNG chunk is outside the static v1 subset", "E_ICON_FORMAT_UNSUPPORTED"
            )
        chunks.append((kind, data))
        _limit(len(chunks) > 256, "too many PNG chunks")
        offset = end
        if kind == b"IEND" and offset != len(content):
            _reject("trailing bytes after IEND")
    names = [kind for kind, _ in chunks]
    if not names or names[0] != b"IHDR" or names[-1] != b"IEND" or b"IDAT" not in names:
        _reject("missing or misplaced PNG critical chunk")
    for name in set(names) - {b"IDAT"}:
        if names.count(name) != 1:
            _reject("duplicate PNG chunk")
    header = chunks[0][1]
    if len(header) != 13 or chunks[-1][1]:
        _reject("invalid IHDR/IEND length")
    width, height, depth, color, compression, filtering, interlace = struct.unpack(
        ">IIBBBBB", header
    )
    _limit(not 1 <= width <= 512 or not 1 <= height <= 512, "PNG dimensions exceed 512")
    depths = {0: {1, 2, 4, 8, 16}, 2: {8, 16}, 3: {1, 2, 4, 8}, 4: {8, 16}, 6: {8, 16}}
    if (
        color not in depths
        or depth not in depths[color]
        or compression
        or filtering
        or interlace not in {0, 1}
    ):
        _reject("invalid PNG IHDR fields")
    if interlace:
        _reject("export a non-interlaced PNG", "E_ICON_FORMAT_UNSUPPORTED")
    first = names.index(b"IDAT")
    last = len(names) - 1 - names[::-1].index(b"IDAT")
    if any(name != b"IDAT" for name in names[first : last + 1]):
        _reject("IDAT chunks must be consecutive")
    palette = b""
    for index, (kind, data) in enumerate(chunks[1:-1], 1):
        if kind == b"IDAT":
            continue
        if index > first:
            _reject("PNG ancillary chunk must precede IDAT")
        if kind == b"PLTE":
            palette = data
            if color in {0, 4} or not len(data) or len(data) % 3 or len(data) > 768:
                _reject("invalid PNG palette")
            if color == 3 and len(data) // 3 > 2**depth:
                _reject("palette exceeds indexed bit depth")
            if b"tRNS" in names and index > names.index(b"tRNS"):
                _reject("PLTE must precede tRNS")
        elif kind == b"tRNS":
            if color == 3:
                if not palette or not 1 <= len(data) <= len(palette) // 3:
                    _reject("invalid indexed transparency")
            elif color in {0, 2}:
                if len(data) != (2 if color == 0 else 6):
                    _reject("invalid transparency length")
                if any(
                    v >= 2**depth
                    for v in struct.unpack(">" + "H" * (len(data) // 2), data)
                ):
                    _reject("transparency sample exceeds bit depth")
            else:
                _reject("tRNS not allowed for this color type")
        else:
            if (
                kind in {b"sRGB", b"gAMA", b"cHRM"}
                and b"PLTE" in names
                and index > names.index(b"PLTE")
            ):
                _reject("color chunk must precede PLTE")
            lengths = {b"sRGB": 1, b"gAMA": 4, b"cHRM": 32, b"pHYs": 9}
            if len(data) != lengths[kind]:
                _reject("invalid PNG ancillary chunk length")
            if kind == b"sRGB" and data[0] > 3:
                _reject("invalid sRGB intent")
            if kind == b"gAMA" and struct.unpack(">I", data)[0] == 0:
                _reject("invalid gamma")
            if kind == b"pHYs" and data[8] not in {0, 1}:
                _reject("invalid physical unit")
            if kind == b"cHRM":
                values = struct.unpack(">8I", data)
                if any(values[i] + values[i + 1] > 100000 for i in range(0, 8, 2)):
                    _reject("invalid chromaticities")
    if color == 3 and not palette:
        _reject("indexed PNG requires PLTE")
    rowBytes = (width * {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[color] * depth + 7) // 8
    expected = height * (rowBytes + 1)
    _limit(expected > 3 * 1024 * 1024, "PNG decompression budget exceeded")
    compressed = b"".join(data for kind, data in chunks if kind == b"IDAT")
    inflater = zlib.decompressobj()
    output = bytearray()
    try:
        for start in range(0, len(compressed), 16384):
            output.extend(
                inflater.decompress(
                    compressed[start : start + 16384], expected + 1 - len(output)
                )
            )
            _limit(len(output) > expected, "PNG IDAT expands beyond image dimensions")
            if inflater.unused_data or inflater.unconsumed_tail:
                _reject("trailing or oversized PNG zlib stream")
    except zlib.error as err:
        raise IconValidationError(
            "E_ICON_CONTENT_INVALID", "invalid PNG zlib stream"
        ) from err
    if len(output) != expected or not inflater.eof:
        _reject("truncated PNG image data")
    if any(output[i] > 4 for i in range(0, expected, rowBytes + 1)):
        _reject("invalid PNG row filter")
    return width, height


class _Numbers:
    """A forward-only SVG number scanner, including compact path/arc syntax."""

    def __init__(self, text: str) -> None:
        self.text = text
        self.pos = 0

    def space(self) -> None:
        while self.pos < len(self.text) and self.text[self.pos] in " \t\r\n":
            self.pos += 1

    def atEnd(self) -> bool:
        self.space()
        return self.pos == len(self.text)

    def number(self, *, comma: bool = True, flag: bool = False) -> float:
        self.space()
        if comma and self.pos < len(self.text) and self.text[self.pos] == ",":
            self.pos += 1
            self.space()
        if flag:
            if self.pos >= len(self.text) or self.text[self.pos] not in "01":
                _reject("SVG arc flag must be 0 or 1")
            value = float(self.text[self.pos])
            self.pos += 1
            return value
        match = _NUMBER.match(self.text, self.pos)
        if match is None:
            _reject("invalid SVG number or separator")
            raise AssertionError
        self.pos = match.end()
        value = float(match.group())
        _bounded(value)
        return value


def _bounded(*values: float) -> None:
    _limit(
        any(not math.isfinite(v) or abs(v) > 1000000 for v in values),
        "SVG number/coordinate exceeds bounds",
    )


def _numbers(text: str) -> list[float]:
    scanner = _Numbers(text)
    values: list[float] = []
    while not scanner.atEnd():
        values.append(scanner.number(comma=bool(values)))
    return values


def _scalar(text: str) -> float:
    if _NUMBER.fullmatch(text) is None:
        _reject("invalid SVG scalar")
    value = float(text)
    _bounded(value)
    return value


def _multiply(left: tuple[float, ...], right: tuple[float, ...]) -> tuple[float, ...]:
    a, b, c, d, e, f = left
    g, h, i, j, k, last = right
    result = (
        a * g + c * h,
        b * g + d * h,
        a * i + c * j,
        b * i + d * j,
        a * k + c * last + e,
        b * k + d * last + f,
    )
    _bounded(*result)
    return result


def _transform(text: str) -> tuple[float, ...]:
    scanner = _Numbers(text)
    result: tuple[float, ...] = _IDENTITY
    count = 0
    while not scanner.atEnd():
        if count and scanner.text[scanner.pos] == ",":
            scanner.pos += 1
            scanner.space()
        match = re.compile(r"(translate|scale|rotate|matrix)\s*\(").match(
            text, scanner.pos
        )
        if match is None:
            _reject("unsupported SVG transform")
            raise AssertionError
        end = text.find(")", match.end())
        if end < 0:
            _reject("unclosed SVG transform")
        values = _numbers(text[match.end() : end])
        scanner.pos = end + 1
        name = match.group(1)
        matrix: tuple[float, ...]
        if (
            len(values)
            not in {
                "translate": {1, 2},
                "scale": {1, 2},
                "rotate": {1, 3},
                "matrix": {6},
            }[name]
        ):
            _reject("incorrect SVG transform arity")
        if name == "translate":
            matrix = (
                1.0,
                0.0,
                0.0,
                1.0,
                values[0],
                values[1] if len(values) == 2 else 0.0,
            )
        elif name == "scale":
            matrix = (values[0], 0.0, 0.0, values[-1], 0.0, 0.0)
        elif name == "rotate":
            angle = math.radians(values[0])
            c, s = math.cos(angle), math.sin(angle)
            x, y = values[1:] if len(values) == 3 else (0.0, 0.0)
            matrix = (c, s, -s, c, x - c * x + s * y, y - s * x - c * y)
        else:
            matrix = tuple(values)
        _bounded(*matrix)
        result = _multiply(result, matrix)
        count += 1
        _limit(count > 16, "too many SVG transforms")
    if not count:
        _reject("empty SVG transform")
    return result


def _pathSegments(text: str) -> int:
    scanner = _Numbers(text)
    command = ""
    previous = ""
    x = y = sx = sy = cx = cy = 0.0
    count = 0
    while not scanner.atEnd():
        char = text[scanner.pos]
        explicit = char.isalpha()
        if explicit:
            if char not in "MmLlHhVvCcSsQqTtAaZz":
                _reject("unsupported SVG path command")
            command = char
            scanner.pos += 1
        elif not command or command.upper() == "Z":
            _reject("SVG path needs a command")
        upper = command.upper()
        if not count and upper != "M":
            _reject("SVG path must begin with moveto")
        if upper == "Z":
            x, y = sx, sy
        else:
            arity = {
                "M": 2,
                "L": 2,
                "H": 1,
                "V": 1,
                "C": 6,
                "S": 4,
                "Q": 4,
                "T": 2,
                "A": 7,
            }[upper]
            values = [
                scanner.number(
                    comma=(i > 0 or not explicit), flag=(upper == "A" and i in {3, 4})
                )
                for i in range(arity)
            ]
            relative = command.islower()
            ox, oy = (x, y) if relative else (0.0, 0.0)
            if upper == "H":
                x = values[0] + ox
            elif upper == "V":
                y = values[0] + oy
            else:
                pairs = values[5:] if upper == "A" else values
                for i in range(0, len(pairs), 2):
                    _bounded(pairs[i] + ox, pairs[i + 1] + oy)
                if upper == "A" and (values[0] < 0 or values[1] < 0):
                    _reject("negative SVG arc radius")
                if (upper == "S" and previous in {"C", "S"}) or (
                    upper == "T" and previous in {"Q", "T"}
                ):
                    _bounded(2 * x - cx, 2 * y - cy)
                if upper in {"C", "S", "Q"}:
                    cx, cy = values[-4] + ox, values[-3] + oy
                elif upper == "T":
                    cx, cy = (
                        (2 * x - cx, 2 * y - cy) if previous in {"Q", "T"} else (x, y)
                    )
                x, y = pairs[-2] + ox, pairs[-1] + oy
            if upper == "M":
                sx, sy = x, y
                command = "l" if relative else "L"
        _bounded(x, y)
        previous = upper
        count += 1
        _limit(count > 2048, "too many SVG path segments")
    return count


def _validateSvg(content: bytes) -> None:
    try:
        from defusedxml.ElementTree import iterparse
        from defusedxml.common import DefusedXmlException
    except ImportError as err:
        raise IconValidationError(
            "E_ICON_VALIDATOR_UNAVAILABLE", "defusedxml is required for SVG"
        ) from err
    try:
        text = content.decode("utf-8-sig")
    except UnicodeError as err:
        raise IconValidationError(
            "E_ICON_CONTENT_INVALID", "SVG must be UTF-8"
        ) from err
    declaration = re.match(r"<\?xml\s+[^?]*\?>", text)
    if declaration:
        encoding = re.search(
            r"""encoding\s*=\s*['"]([^'"]+)['"]""", declaration.group()
        )
        if encoding and encoding.group(1).lower() != "utf-8":
            _reject("SVG declaration must specify UTF-8")
    stack: list[tuple[str, tuple[float, ...]]] = []
    count = chars = segments = 0
    try:
        events = iterparse(
            io.BytesIO(content),
            events=("start", "end", "start-ns", "pi"),
            forbid_dtd=True,
            forbid_entities=True,
            forbid_external=True,
        )
        for event, element in events:
            if event == "pi":
                _reject("SVG processing instructions are forbidden")
            if event == "start-ns":
                if element[1] != _SVG_NS or element[0] in {"xml", "xlink"}:
                    _reject("unsupported SVG namespace")
                continue
            if event == "end":
                tag, _ = stack.pop()
                if tag in {"title", "desc"}:
                    _limit(len(element.text or "") > 2048, "SVG text exceeds limit")
                elif (element.text or "").strip():
                    _reject("SVG drawable elements may not contain text")
                if (element.tail or "").strip():
                    _reject("SVG tail text is not supported")
                continue
            prefix = "{" + _SVG_NS + "}"
            if not isinstance(element.tag, str) or not element.tag.startswith(prefix):
                _reject("SVG element must use the SVG namespace")
            tag = element.tag[len(prefix) :]
            if (
                tag not in _ATTRS
                or (not stack and tag != "svg")
                or (stack and tag == "svg")
            ):
                _reject("unsupported SVG element")
            if stack and stack[-1][0] not in {"svg", "g"}:
                _reject("SVG geometry and text must not contain children")
            count += 1
            _limit(count > 512 or len(stack) >= 16, "SVG structure exceeds budget")
            allowed = _ATTRS[tag] | (_PUBLIC if tag not in {"title", "desc"} else set())
            if set(element.attrib) - allowed:
                _reject("unsupported SVG attribute")
            attrs = element.attrib
            matrix = (
                _transform(attrs["transform"]) if "transform" in attrs else _IDENTITY
            )
            matrix = _multiply(stack[-1][1] if stack else _IDENTITY, matrix)
            stack.append((tag, matrix))
            if tag == "svg":
                box = _numbers(attrs.get("viewBox", ""))
                if (
                    len(box) != 4
                    or not 0.000001 <= box[2] <= 1000000
                    or not 0.000001 <= box[3] <= 1000000
                ):
                    _reject("SVG needs a finite positive viewBox")
            for name, value in attrs.items():
                if name in {"viewBox", "transform"}:
                    continue
                if name in {"d", "points"}:
                    chars += len(value)
                    _limit(
                        len(value) > 32768 or chars > 65536,
                        "SVG path text exceeds budget",
                    )
                    if name == "d":
                        segments += _pathSegments(value)
                    else:
                        points = _numbers(value)
                        if len(points) % 2 or len(points) < (
                            6 if tag == "polygon" else 4
                        ):
                            _reject("invalid SVG points")
                        segments += len(points) // 2 - (0 if tag == "polygon" else 1)
                    _limit(segments > 2048, "SVG segment budget exceeded")
                elif name in {"fill", "stroke"}:
                    if (
                        value != "none"
                        and re.fullmatch(r"#(?:[a-fA-F0-9]{3}|[a-fA-F0-9]{6})", value)
                        is None
                    ):
                        _reject("SVG colors must be none, #RGB or #RRGGBB")
                elif name in {
                    "stroke-linecap",
                    "stroke-linejoin",
                    "fill-rule",
                    "preserveAspectRatio",
                }:
                    enums = {
                        "stroke-linecap": {"butt", "round", "square"},
                        "stroke-linejoin": {"miter", "round", "bevel"},
                        "fill-rule": {"nonzero", "evenodd"},
                        "preserveAspectRatio": {"xMidYMid meet", "none"},
                    }
                    if value not in enums[name]:
                        _reject("unsupported SVG presentation value")
                else:
                    number = _scalar(
                        value.removesuffix("px")
                        if tag == "svg" and name in {"width", "height"}
                        else value
                    )
                    if (
                        name in {"opacity", "fill-opacity", "stroke-opacity"}
                        and not 0 <= number <= 1
                    ):
                        _reject("SVG opacity must be 0..1")
                    if (
                        name in {"width", "height", "r", "rx", "ry", "stroke-width"}
                        and number < 0
                    ):
                        _reject("negative SVG length")
                    if (
                        tag == "svg"
                        and name in {"width", "height"}
                        and not 1 <= number <= 512
                    ):
                        _reject("SVG root dimensions must be 1..512")
                    if name == "stroke-width" and number > 1024:
                        _reject("SVG stroke width exceeds 1024")
                    if name == "stroke-miterlimit" and not 1 <= number <= 100:
                        _reject("SVG miterlimit must be 1..100")
    except (ParseError, DefusedXmlException) as err:
        raise IconValidationError(
            "E_ICON_CONTENT_INVALID", "invalid or unsafe SVG XML"
        ) from err
