from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
import hashlib
import re
import threading

from emo_master.core.plugin.icon_resources import (
    ICON_MIMES, MAX_ICON_BYTES, MAX_ICON_TOTAL_BYTES, IconValidationError, validateIconContent,
)
from emo_master.core.plugin.models import PluginIconAsset


@dataclass(frozen=True)
class IconRequest:
    scope: str
    generation: int
    operatorId: str
    version: str
    mimeType: str
    sha256: str
    byteSize: int

    @property
    def resourceKey(self) -> tuple[str, str, str, str]:
        return self.scope, self.operatorId, self.version, self.sha256

    @classmethod
    def fromDefinition(cls, scope: str, generation: int, definition: dict[str, object]):
        raw = definition.get("icon", {})
        if not isinstance(raw, dict):
            raise IconValidationError("E_ICON_CONTENT_INVALID", "invalid icon metadata")
        status = raw.get("status", "none")
        if isinstance(status, str) and status in {"none", "invalid", ""}:
            return None
        mime, digest, size = raw.get("mimeType"), raw.get("sha256"), raw.get("byteSize")
        if (status != "ready" or mime not in ICON_MIMES.values()
                or not isinstance(digest, str) or re.fullmatch(r"[a-fA-F0-9]{64}", digest) is None
                or type(size) is not int or not 1 <= size <= MAX_ICON_BYTES):
            raise IconValidationError("E_ICON_CONTENT_INVALID", "inconsistent ready icon metadata")
        return cls(scope, generation, str(definition.get("operatorId", "")),
                   str(definition.get("version", "")), str(mime), digest.lower(), size)


def validateIconReply(request: IconRequest, reply) -> PluginIconAsset:
    if not bool(getattr(reply, "ok", False)):
        raise IconValidationError(str(getattr(reply, "code", "E_ICON_ASSET_UNAVAILABLE")),
                                  str(getattr(reply, "message", "icon unavailable")))
    content = getattr(reply, "content", b"")
    if not isinstance(content, bytes) or len(content) != request.byteSize:
        raise IconValidationError("E_ICON_CONTENT_INVALID", "icon response length mismatch")
    if getattr(reply, "version", "") != request.version:
        raise IconValidationError("E_ICON_VERSION_MISMATCH", "icon response version mismatch")
    digest = getattr(reply, "sha256", "")
    if (not isinstance(digest, str) or digest.lower() != request.sha256
            or hashlib.sha256(content).hexdigest() != request.sha256):
        raise IconValidationError("E_ICON_DIGEST_MISMATCH", "icon response digest mismatch")
    if getattr(reply, "mime_type", "") != request.mimeType:
        raise IconValidationError("E_ICON_CONTENT_INVALID", "icon response MIME mismatch")
    validateIconContent(content, request.mimeType)
    return PluginIconAsset(content, request.mimeType, request.sha256)


class OperatorIconCache:
    def __init__(self, maxBytes: int = MAX_ICON_TOTAL_BYTES) -> None:
        self.maxBytes = maxBytes
        self.byteSize = 0
        self._items: OrderedDict[tuple[str, str, str], PluginIconAsset] = OrderedDict()
        self._lock = threading.RLock()

    def get(self, request: IconRequest) -> PluginIconAsset | None:
        key = request.scope, request.mimeType, request.sha256
        with self._lock:
            asset = self._items.get(key)
            if asset is not None and len(asset.content) == request.byteSize:
                self._items.move_to_end(key)
                return asset
        return None

    def put(self, scope: str, asset: PluginIconAsset) -> None:
        if len(asset.content) > self.maxBytes:
            return
        key = scope, asset.mimeType, asset.sha256
        with self._lock:
            previous = self._items.pop(key, None)
            if previous:
                self.byteSize -= len(previous.content)
            self._items[key] = asset
            self.byteSize += len(asset.content)
            while self.byteSize > self.maxBytes:
                _, removed = self._items.popitem(last=False)
                self.byteSize -= len(removed.content)

    def clear(self) -> None:
        with self._lock:
            self._items.clear()
            self.byteSize = 0
