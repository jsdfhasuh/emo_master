import dataclasses
import json
from types import SimpleNamespace

import pytest

from emo_master.apps.designer.controllers.operator_catalog_controller import OperatorCatalogController
from emo_master.apps.designer.services.operator_icon_cache import IconRequest, OperatorIconCache, validateIconReply
from emo_master.core.plugin.icon_resources import IconValidationError
from tests.icon_fixtures import SVG, iconDefinition, iconReply, png


@pytest.mark.parametrize("metadata", [None, [], 7, {"status": []}, {"status": "future"},
    {"status": "ready", "mimeType": "image/png", "sha256": "x" * 64, "byteSize": 2}])
def testMalformedMetadataDoesNotRejectTheOperator(metadata):
    controller = OperatorCatalogController(None, lambda *_: None)
    catalog = controller.applyOperators([SimpleNamespace(operatorId="x", displayName="X", icon=metadata)], lambda _: "Other")
    assert controller.hasCatalog and len(catalog) == 1
    json.dumps(catalog)
    with pytest.raises(IconValidationError):
        IconRequest.fromDefinition("scope", 1, catalog[0])


@pytest.mark.parametrize("metadata", [{}, {"status": "none"}, {"status": "invalid"}])
def testNoAssetMetadataDoesNotRequest(metadata):
    assert IconRequest.fromDefinition("scope", 1, {"icon": metadata}) is None


@pytest.mark.parametrize("fields,code", [({"content": b"bad"}, "E_ICON_CONTENT_INVALID"),
    ({"version": "2"}, "E_ICON_VERSION_MISMATCH"), ({"sha256": None}, "E_ICON_DIGEST_MISMATCH"),
    ({"sha256": "0" * 64}, "E_ICON_DIGEST_MISMATCH"), ({"mime_type": "image/png"}, "E_ICON_CONTENT_INVALID"),
    ({"ok": False, "code": "UNIMPLEMENTED"}, "UNIMPLEMENTED")])
def testReplyIsCheckedBeforeDecode(fields, code):
    request = IconRequest.fromDefinition("s", 1, iconDefinition())
    with pytest.raises(IconValidationError) as caught:
        validateIconReply(request, iconReply(**fields))
    assert caught.value.code == code


def testCacheSharesBytesAcrossOperatorsAndGenerationsButNotSessions():
    request = IconRequest.fromDefinition("s", 1, iconDefinition())
    asset = validateIconReply(request, iconReply())
    cache = OperatorIconCache()
    cache.put("s", asset)
    assert cache.get(dataclasses.replace(request, operatorId="another", generation=2)) is asset
    assert cache.get(dataclasses.replace(request, scope="new")) is None
    assert cache.get(dataclasses.replace(request, sha256="0" * 64)) is None
    cache.put("s", asset)
    assert cache.byteSize == len(SVG)
    cache.clear()
    assert cache.byteSize == 0 and cache.get(request) is None


def testCacheEvictsLeastRecentlyUsedWithinByteBudget():
    contents = [png(width=w) for w in (1, 2, 3)]
    requests = [IconRequest.fromDefinition("s", 1, iconDefinition(c, mimeType="image/png")) for c in contents]
    assets = [validateIconReply(r, iconReply(c, mimeType="image/png")) for r, c in zip(requests, contents)]
    cache = OperatorIconCache(maxBytes=len(contents[0]) + len(contents[1]))
    cache.put("s", assets[0])
    cache.put("s", assets[1])
    assert cache.get(requests[0]) is assets[0]
    cache.put("s", assets[2])
    assert cache.get(requests[1]) is None
    assert cache.byteSize <= cache.maxBytes
    tiny = OperatorIconCache(maxBytes=1)
    tiny.put("s", assets[0])
    assert tiny.byteSize == 0


def testSameDigestDoesNotBypassContentPolicy():
    unsafe = SVG.replace(b"<rect", b'<rect onclick="alert(1)"')
    request = IconRequest.fromDefinition("s", 1, iconDefinition(unsafe))
    with pytest.raises(IconValidationError):
        validateIconReply(request, iconReply(unsafe))
