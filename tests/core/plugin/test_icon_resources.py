import struct
import sys
import zlib

import pytest

from emo_master.core.plugin.icon_resources import (
    IconValidationError,
    loadIconAsset,
    parseIconResource,
    validateIconContent,
)
from tests.icon_fixtures import SVG, chunk, png


@pytest.mark.parametrize("value", [None, 1, [], {}])
def testInvalidDeclarationType(value):
    resource, issues = parseIconResource({"iconResource": value})
    assert not resource
    assert issues[0].code == "E_ICON_SPEC_INVALID"


@pytest.mark.parametrize(
    "value",
    [
        "/a.svg",
        "C:a.svg",
        "C:/a.svg",
        "\\\\host\\a.svg",
        "a\\b.svg",
        "../a.svg",
        "a/./b.svg",
        "a//b.svg",
        "https://host/a.svg",
        "a\0.svg",
        " a.svg",
        "a /b.svg",
        "NUL.svg",
    ],
)
def testInvalidPaths(value):
    assert (
        parseIconResource({"iconResource": value})[1][0].code == "E_ICON_PATH_INVALID"
    )


def testMissingAndEmptyDeclaration():
    assert parseIconResource({}) == ("", ())
    assert parseIconResource({"iconResource": ""}) == ("", ())


def testBoundedFilesAndImmutableAsset(tmp_path):
    path = tmp_path / "icon.svg"
    path.write_bytes(SVG)
    asset = loadIconAsset(tmp_path, "icon.svg")
    path.unlink()
    assert asset.content == SVG
    with pytest.raises(IconValidationError, match="unavailable"):
        loadIconAsset(tmp_path, "icon.svg")
    path.write_bytes(SVG)
    with pytest.raises(IconValidationError) as error:
        loadIconAsset(tmp_path, "icon.svg", budget=1)
    assert error.value.code == "E_ICON_LIMIT_EXCEEDED"


def testSymlinkCannotEscapeRoot(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    (tmp_path / "outside.svg").write_bytes(SVG)
    try:
        (root / "icon.svg").symlink_to(tmp_path / "outside.svg")
    except OSError:
        pytest.skip("symlinks unavailable in this environment")
    with pytest.raises(IconValidationError) as error:
        loadIconAsset(root, "icon.svg")
    assert error.value.code == "E_ICON_PATH_INVALID"


@pytest.mark.parametrize(
    "color,depth",
    [
        (0, 1),
        (0, 2),
        (0, 4),
        (0, 8),
        (0, 16),
        (2, 8),
        (2, 16),
        (4, 8),
        (4, 16),
        (6, 8),
        (6, 16),
    ],
)
def testPngColorDepths(color, depth):
    assert validateIconContent(png(color=color, depth=depth), "image/png") == (2, 2)


@pytest.mark.parametrize(
    "asset",
    [
        b"bad",
        png()[:-1],
        png() + b"x",
        png(interlace=1),
        png(depth=3),
        png(width=513),
        png(color=3),
        png(extra=chunk(b"IHDR")),
        png(extra=chunk(b"tRNS", b"xx")),
        png(extra=chunk(b"acTL", b"")),
        png(extra=chunk(b"zTXt", b"x\0\0" + zlib.compress(b"x" * 100000))),
        png(extra=chunk(b"iCCP", b"x\0\0" + zlib.compress(b"x" * 100000))),
        png(extra=chunk(b"pHYs", b"\0" * 8 + b"\2")),
        png(extra=chunk(b"gAMA", b"\0" * 4)),
        png(extra=chunk(b"sRGB", b"\4")),
        png(raw=b"\0"),
        png(raw=b"\0" * 100000),
        png(raw=b"\5" + b"\xff" * 8 + b"\0" + b"\xff" * 8),
        png(compressed=zlib.compress(b"\0" + b"\xff" * 17) + zlib.compress(b"x")),
        png(compressed=zlib.compress(b"\0" + b"\xff" * 17)[:-1]),
    ],
)
def testPngRejectsMalformedOrUnsupported(asset):
    with pytest.raises(IconValidationError):
        validateIconContent(asset, "image/png")


def testPngPaletteTransparencyAndCrc():
    valid = png(
        color=3,
        depth=1,
        extra=chunk(b"PLTE", b"\0\0\0\xff\xff\xff") + chunk(b"tRNS", b"\0\xff"),
    )
    validateIconContent(valid, "image/png")
    with pytest.raises(IconValidationError):
        validateIconContent(valid[:-4] + struct.pack(">I", 0), "image/png")


@pytest.mark.parametrize(
    "body",
    [
        '<path d="M1 2l3-1H8v2C1 2 3 4 5 6s1 2 3 4Q1 2 3 4t1 2A3 4 0 0110 12z"/>',
        '<g transform="translate(2,3) scale(.5) rotate(90,12,12) matrix(1,0,0,1,0,0)"><circle cx="12" cy="12" r="5"/></g>',
        '<polygon points="1,2 3,4 5,6"/><polyline points="1 2 3 4"/>',
        '<ellipse rx="4" ry="2"/><line x1="2" y1="4"/><title>A &amp; B</title>',
    ],
)
def testSvgSafeSubset(body):
    validateIconContent(
        (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
            + body
            + "</svg>"
        ).encode(),
        "image/svg+xml",
    )


@pytest.mark.parametrize(
    "body",
    [
        "<script/>",
        '<image href="http://localhost/a"/>',
        "<use/>",
        "<foreignObject/>",
        '<path style="fill:red"/>',
        '<rect onclick="x()"/>',
        '<g xmlns="evil"/>',
        '<g xmlns:xlink="http://www.w3.org/1999/xlink"/>',
        "<svg/>",
        '<path d="L1 2"/>',
        '<path d="M1 2L"/>',
        '<path d="M1,,2"/>',
        '<path d="M1 2Z3 4"/>',
        '<path d="M1 2A1 1 0 2 0 3 4"/>',
        '<path d="M1 2A-1 1 0 0 0 3 4"/>',
        '<path d="M999999 0l2 0"/>',
        '<g transform="scale(1000000) scale(2)"/>',
        '<g transform="skewX(1)"/>',
        '<rect fill="url(#x)"/>',
        '<rect fill="currentColor"/>',
        '<rect width="NaN"/>',
        '<rect width="1e1000"/>',
        '<circle r="-1"/>',
        '<rect opacity="2"/>',
        "<title><g/></title>",
        "<g>text</g>",
        '<polygon points="1 2 3"/>',
        '<path d="M0 0' + "L1 1" * 2049 + '"/>',
        "<g>" * 17 + "</g>" * 17,
    ],
)
def testSvgRejectsUnsafeAndMalformed(body):
    with pytest.raises(IconValidationError):
        validateIconContent(
            (
                '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
                + body
                + "</svg>"
            ).encode(),
            "image/svg+xml",
        )


@pytest.mark.parametrize(
    "asset",
    [
        SVG.replace(b'xmlns="http://www.w3.org/2000/svg"', b""),
        b"<!DOCTYPE svg>" + SVG,
        b'<?xml-stylesheet href="http://host"?>' + SVG,
        b'<!DOCTYPE svg [<!ENTITY x SYSTEM "file:///secret">]>' + SVG,
        b'<?xml version="1.0" encoding="ISO-8859-1"?>' + SVG,
        SVG.replace(b"0 0 24 24", b"0 0 0 24"),
    ],
)
def testSvgXmlPolicy(asset):
    with pytest.raises(IconValidationError):
        validateIconContent(asset, "image/svg+xml")


def testSvgBomPrefixAndUnavailableParser(monkeypatch):
    validateIconContent(b"\xef\xbb\xbf" + SVG, "image/svg+xml")
    validateIconContent(
        SVG.replace(b"<svg xmlns=", b"<s:svg xmlns:s=")
        .replace(b"</svg>", b"</s:svg>")
        .replace(b"<rect", b"<s:rect"),
        "image/svg+xml",
    )
    monkeypatch.setitem(sys.modules, "defusedxml.ElementTree", None)
    with pytest.raises(IconValidationError) as error:
        validateIconContent(SVG, "image/svg+xml")
    assert error.value.code == "E_ICON_VALIDATOR_UNAVAILABLE"
    validateIconContent(png(), "image/png")
