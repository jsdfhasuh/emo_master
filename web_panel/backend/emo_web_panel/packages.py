"""Bounded .emoweb storage, using a deliberately provisional local schema.

This validator is NOT Panel Contract v1 conformance. Replace the draft validation
boundary with the versioned protocol SDK when supplied; never silently upgrade a
package or mutate Runtime configuration when installing/activating it.
"""
from __future__ import annotations

import hashlib
import io
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import tempfile
import threading
import zipfile
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class PackageError(ValueError):
    """Untrusted package rejected; messages never expose host paths."""


TYPES = frozenset({"image", "text", "number", "indicator", "runtime_status", "table",
                   "navigation_button", "container", "page_title", "page_navigation",
                   "clock", "sqlite_table", "sqlite_value"})
MIMES = {"image/png", "image/jpeg", "image/webp", "image/gif"}
ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
HASH = re.compile(r"^[0-9a-f]{64}$")
# Provisional local renderer matrix, not official Panel Contract schema.
COMMON_PROPS = {"label", "style", "row", "column", "rowSpan", "columnSpan", "grid"}
TYPE_PROPS = {
    "image": {"assetId", "sourceMode", "sourceComponentId"},
    "text": {"text", "value"},
    "number": {"text", "value", "unit", "decimals"},
    "indicator": {"text", "value", "mapping"},
    "runtime_status": set(),
    "table": {"value", "columns"},
    "navigation_button": {"text", "action", "targetPageId", "icon"},
    "container": {"title"},  # Preserved draft fixture metadata; not HTML.
    "page_title": set(),
    "page_navigation": set(),
    "clock": {"format", "timezone"},
    "sqlite_table": {"columns"},
    "sqlite_value": {"aggregation", "aggregate", "aggregateField", "followsFilter"},
}
STYLE = {"fontSize", "color", "backgroundColor", "fontWeight", "textAlign"}


def _object_pairs(pairs):
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise PackageError("Duplicate JSON key")
        obj[key] = value
    return obj


def _json(raw: bytes):
    if len(raw) > 1024 * 1024:
        raise PackageError("JSON document too large")
    try:
        return json.loads(raw, object_pairs_hook=_object_pairs,
                          parse_constant=lambda _: (_ for _ in ()).throw(PackageError("Non-finite JSON number")))
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise PackageError("Invalid JSON document") from exc


def _identifier(value, what="identifier"):
    if not isinstance(value, str) or not ID.fullmatch(value):
        raise PackageError(f"Invalid {what}")
    return value


def _path(name: str) -> str:
    if not isinstance(name, str) or not name or len(name) > 240:
        raise PackageError("Invalid archive path")
    if "\\" in name or ":" in name or "\x00" in name or name.startswith("/"):
        raise PackageError("Unsafe archive path")
    if any(part in {"", ".", ".."} for part in name.rstrip("/").split("/")):
        raise PackageError("Unsafe archive path")
    for part in name.rstrip("/").split("/"):
        stem = part.split(".", 1)[0].upper()
        if part.endswith((".", " ")) or stem in {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}:
            raise PackageError("Nonportable archive path")
    if any(ord(c) < 32 for c in name):
        raise PackageError("Unsafe archive path")
    return str(PurePosixPath(name))


def _plain(value, depth=0):
    if depth > 12:
        raise PackageError("Configuration nesting too deep")
    if value is None or isinstance(value, bool):
        return
    if isinstance(value, (float, int)):
        if abs(value) > 1e100 or not math.isfinite(value):
            raise PackageError("Invalid number")
        return
    if isinstance(value, str):
        if len(value) > 8192:
            raise PackageError("Text too long")
        return
    if isinstance(value, list) and len(value) <= 256:
        for item in value:
            _plain(item, depth + 1)
        return
    if isinstance(value, dict) and len(value) <= 64:
        for key, item in value.items():
            if not isinstance(key, str) or key in {"__proto__", "constructor", "prototype"}:
                raise PackageError("Unsafe configuration key")
            _plain(item, depth + 1)
        return
    raise PackageError("Invalid configuration value")


def _style(value):
    if not isinstance(value, dict) or set(value) - STYLE:
        raise PackageError("Unsupported style property")
    for key, item in value.items():
        if key in {"color", "backgroundColor"}:
            if not isinstance(item, str) or not re.fullmatch(r"#[0-9A-Fa-f]{3,8}|[A-Za-z]{1,20}", item):
                raise PackageError("Unsafe color")
        elif key == "fontSize":
            if type(item) not in {int, float} or not 8 <= item <= 128:
                raise PackageError("Invalid font size")
        elif key == "fontWeight":
            if type(item) not in {str, int} or item not in {"normal", "bold", "400", "500", "600", "700", 400, 500, 600, 700}:
                raise PackageError("Invalid font weight")
        elif key == "textAlign" and (not isinstance(item, str) or item not in {"left", "right", "center"}):
            raise PackageError("Invalid text alignment")


def _grid(value):
    if not isinstance(value, dict) or set(value) - {"row", "column", "rowSpan", "columnSpan", "columns", "gap"}:
        raise PackageError("Invalid grid")
    for key, item in value.items():
        if type(item) is not int or not (0 if key == "gap" else 1) <= item <= 128:
            raise PackageError("Invalid grid value")


def _validate_presentation(doc, asset_ids):
    if not isinstance(doc, dict) or set(doc) - {"pages", "defaultPageId", "schemaVersion", "title", "grid"}:
        raise PackageError("Invalid draft presentation")
    pages = doc.get("pages")
    if not isinstance(pages, list) or not 1 <= len(pages) <= 32:
        raise PackageError("Invalid page count")
    page_ids, component_ids, links = set(), set(), []
    component_types, history_links = {}, []
    count = 0

    def component(c, depth=0):
        nonlocal count
        count += 1
        if count > 512 or depth > 8:
            raise PackageError("Too many or deeply nested components")
        if not isinstance(c, dict) or set(c) - {"id", "type", "props", "children", "binding", "grid", "label"}:
            raise PackageError("Invalid component")
        cid = _identifier(c.get("id"), "component ID")
        if cid in component_ids:
            raise PackageError("Duplicate component ID")
        component_ids.add(cid)
        if not isinstance(c.get("type"), str) or c["type"] not in TYPES:
            raise PackageError("Unsupported component type")
        props = c.get("props", {})
        if not isinstance(props, dict) or set(props) - (COMMON_PROPS | TYPE_PROPS[c["type"]]):
            raise PackageError("Unsupported component property")
        component_types[cid] = c["type"]
        _plain(props)
        for key in ("text", "label", "title", "unit", "icon"):
            if key in props and not isinstance(props[key], str):
                raise PackageError("Invalid text property")
        if "decimals" in props and (type(props["decimals"]) is not int or not 0 <= props["decimals"] <= 8):
            raise PackageError("Invalid decimal precision")
        if "mapping" in props:
            if not isinstance(props["mapping"], dict) or any(type(v) not in (str, bool, int, float) for v in props["mapping"].values()):
                raise PackageError("Invalid indicator mapping")
        if "columns" in props:
            columns = props["columns"]
            if not isinstance(columns, list) or len(columns) > 64:
                raise PackageError("Invalid table columns")
            keys = set()
            for column in columns:
                if not isinstance(column, dict) or set(column) - {"key", "title"}:
                    raise PackageError("Invalid table column")
                key = _identifier(column.get("key"), "table column key")
                if key in keys or ("title" in column and not isinstance(column["title"], str)):
                    raise PackageError("Duplicate or invalid table column")
                keys.add(key)
        if "format" in props and props["format"] not in ("date", "time", "datetime"):
            raise PackageError("Unsupported clock format")
        if "timezone" in props:
            zone = props["timezone"]
            if not isinstance(zone, str) or len(zone) > 100 or not re.fullmatch(r"[A-Za-z0-9_+/-]+", zone):
                raise PackageError("Invalid clock timezone")
            if zone not in ("browser", "UTC"):
                try:
                    ZoneInfo(zone)
                except (ValueError, ZoneInfoNotFoundError) as exc:
                    raise PackageError("Unknown timezone or timezone database unavailable") from exc
        if c["type"] == "image":
            mode = props.get("sourceMode")
            if mode is not None and mode not in ("history_fixed", "history_selection"):
                raise PackageError("Invalid history image mode")
            if mode is not None and ("assetId" in props or "binding" in c):
                raise PackageError("History image cannot also use static or live source")
            if mode == "history_selection":
                history_links.append(_identifier(props.get("sourceComponentId"), "history table reference"))
            elif "sourceComponentId" in props:
                raise PackageError("Table reference requires history_selection mode")
        if "icon" in props and (not isinstance(props['icon'],str) or len(props['icon'])>32):raise PackageError('Invalid text icon')
        if 'sourceMode' in props and props['sourceMode'] not in ('history_fixed','history_selection'):raise PackageError('Invalid history image mode')
        if 'sourceComponentId' in props:_identifier(props['sourceComponentId'],'history table reference')
        if "followsFilter" in props and type(props["followsFilter"]) is not bool:raise PackageError("Invalid aggregate filter policy")
        if "aggregateField" in props:_identifier(props["aggregateField"], "aggregate field")
        if "style" in props:
            _style(props["style"])
        # Local draft renderer calls this aggregate; the published SDK will
        # eventually determine the canonical field name.
        for key in ("aggregation", "aggregate"):
            if key in props and (not isinstance(props[key], str) or props[key] not in {"count", "sum", "avg", "min", "max"}):
                raise PackageError("Unsupported aggregate")
        if "aggregation" in props and "aggregate" in props and props["aggregation"] != props["aggregate"]:
            raise PackageError("Conflicting aggregate declarations")
        if "assetId" in props and (not isinstance(props["assetId"], str) or props["assetId"] not in asset_ids):
            raise PackageError("Unknown static asset")
        if "action" in props and (not isinstance(props["action"], str) or props["action"] not in {"navigate", "freeze", "resume_live"}):
            raise PackageError("Unsupported page action")
        if "targetPageId" in props:
            links.append(props["targetPageId"])
        if "binding" in c:
            b = c["binding"]
            if isinstance(b, str):
                _identifier(b, "source ID")
            elif isinstance(b, dict) and not set(b) - {"sourceId", "field", "scopeId", "componentId", "historyDefinitionRevision"}:
                _plain(b)
                if "sourceId" in b:
                    _identifier(b["sourceId"], "source ID")
            else:
                raise PackageError("Invalid binding")
        if "grid" in c:
            _grid(c["grid"])
        if "grid" in props:
            _grid(props["grid"])
        for key in ("row", "column", "rowSpan", "columnSpan"):
            if key in props:
                _grid({key: props[key]})
        if "label" in c and (not isinstance(c["label"], str) or len(c["label"]) > 256):
            raise PackageError("Invalid component label")
        children = c.get("children", [])
        if not isinstance(children, list) or (children and c["type"] != "container"):
            raise PackageError("Invalid component children")
        for child in children:
            component(child, depth + 1)

    for page in pages:
        if not isinstance(page, dict) or set(page) - {"id", "title", "components", "grid", "mobile"}:
            raise PackageError("Invalid page")
        pid = _identifier(page.get("id"), "page ID")
        if pid in page_ids:
            raise PackageError("Duplicate page ID")
        page_ids.add(pid)
        if not isinstance(page.get("title", ""), str) or len(page.get("title", "")) > 256:
            raise PackageError("Invalid page title")
        if "grid" in page:
            _grid(page["grid"])
        if "mobile" in page:
            # The formal mobile-override schema is not supplied yet.
            raise PackageError("Mobile overrides require the formal Panel SDK")
        components = page.get("components", [])
        if not isinstance(components, list):
            raise PackageError("Invalid components")
        for c in components:
            component(c)
    if not isinstance(doc.get("defaultPageId", pages[0]["id"]), str) or doc.get("defaultPageId", pages[0]["id"]) not in page_ids or any(not isinstance(x, str) or x not in page_ids for x in links):
        raise PackageError("Unknown navigation target")
    if any(component_types.get(ref) != "sqlite_table" for ref in history_links):
        raise PackageError("History selection must reference an existing sqlite_table")
    if "grid" in doc:
        _grid(doc["grid"])
    _plain({k: v for k, v in doc.items() if k != "pages"})


def _image_matches(data, mime):
    return ((mime == "image/png" and data.startswith(b"\x89PNG\r\n\x1a\n")) or
            (mime == "image/jpeg" and data.startswith(b"\xff\xd8\xff")) or
            (mime == "image/gif" and data[:6] in {b"GIF87a", b"GIF89a"}) or
            (mime == "image/webp" and data[:4] == b"RIFF" and data[8:12] == b"WEBP"))


class PackageStore:
    MAX_ARCHIVE = 16 * 1024 * 1024
    MAX_EXPANDED = 32 * 1024 * 1024
    MAX_FILE = 8 * 1024 * 1024
    MAX_ENTRIES = 256
    MAX_RATIO = 100

    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def _directory(self, package_id):
        if not isinstance(package_id, str) or not HASH.fullmatch(package_id):
            raise PackageError("Invalid package ID")
        path = self.root / package_id
        if path.is_symlink() or not path.is_dir():
            raise PackageError("Unknown package")
        return path

    def install(self, raw: bytes) -> dict:
        if not isinstance(raw, bytes) or len(raw) > self.MAX_ARCHIVE:
            raise PackageError("Archive too large")
        files = {}
        try:
            with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                infos = archive.infolist()
                if len(infos) > self.MAX_ENTRIES:
                    raise PackageError("Too many archive entries")
                seen, total = set(), 0
                for info in infos:
                    name = _path(info.filename)
                    if name.casefold() in seen:
                        raise PackageError("Duplicate archive path")
                    seen.add(name.casefold())
                    mode = info.external_attr >> 16
                    if stat.S_ISLNK(mode) or (stat.S_IFMT(mode) not in {0, stat.S_IFREG, stat.S_IFDIR}):
                        raise PackageError("Unsupported archive entry")
                    if info.flag_bits & 1:
                        raise PackageError("Encrypted archives unsupported")
                    if info.is_dir():
                        continue
                    total += info.file_size
                    if info.file_size > self.MAX_FILE or total > self.MAX_EXPANDED:
                        raise PackageError("Expanded archive too large")
                    if info.file_size > max(info.compress_size, 1) * self.MAX_RATIO:
                        raise PackageError("Unsafe compression ratio")
                    with archive.open(info) as stream:
                        data = stream.read(self.MAX_FILE + 1)
                    if len(data) != info.file_size:
                        raise PackageError("Invalid archive entry size")
                    files[name] = data
        except PackageError:
            raise
        except (zipfile.BadZipFile, OSError, RuntimeError, NotImplementedError, ValueError) as exc:
            raise PackageError("Invalid ZIP archive") from exc
        if not {"manifest.json", "presentation.json"} <= files.keys():
            raise PackageError("Missing package documents")
        manifest, presentation = _json(files["manifest.json"]), _json(files["presentation.json"])
        allowed = {"format", "projectId", "packageRevision", "panelContractVersion", "rendererRequirements", "presentationFile", "assets"}
        if not isinstance(manifest, dict) or set(manifest) - allowed or manifest.get("format") != "emo-web-panel-1":
            raise PackageError("Unsupported package manifest")
        if manifest.get("panelContractVersion") != "draft-unverified":
            raise PackageError("Unsupported panel contract; official SDK integration required")
        for name in ("projectId", "packageRevision", "panelContractVersion"):
            _identifier(manifest.get(name), name)
        if manifest.get("presentationFile", "presentation.json") != "presentation.json":
            raise PackageError("Unsupported presentation document")
        requirements = manifest.get("rendererRequirements", [])
        if not isinstance(requirements, list) or any(not isinstance(x, str) or x not in TYPES for x in requirements):
            raise PackageError("Unsupported renderer requirement")
        assets = manifest.get("assets", [])
        if not isinstance(assets, list) or len(assets) > 128:
            raise PackageError("Invalid assets")
        ids, paths = set(), set()
        for asset in assets:
            if not isinstance(asset, dict) or set(asset) != {"id", "path", "mimeType", "sha256"}:
                raise PackageError("Invalid asset declaration")
            aid, path = _identifier(asset["id"], "asset ID"), _path(asset["path"])
            if aid in ids or path in paths or not path.startswith("assets/"):
                raise PackageError("Duplicate or unsafe asset")
            ids.add(aid); paths.add(path)
            data = files.get(path)
            if data is None or not isinstance(asset["mimeType"], str) or asset["mimeType"] not in MIMES or not _image_matches(data, asset["mimeType"]):
                raise PackageError("Invalid image asset")
            if asset["sha256"] != hashlib.sha256(data).hexdigest():
                raise PackageError("Asset digest mismatch")
        if set(files) != {"manifest.json", "presentation.json"} | paths:
            raise PackageError("Undeclared package file")
        _validate_presentation(presentation, ids)
        package_id = hashlib.sha256(raw).hexdigest()
        metadata = {"packageId": package_id, "manifest": manifest, "presentation": presentation,
                    "validationStatus": "draft-schema-only", "byteSize": len(raw)}
        with self._lock:
            if (self.root / package_id).is_dir():
                return self.get(package_id)
            for existing in self.list():
                other = existing["manifest"]
                if (other["projectId"], other["packageRevision"]) == (manifest["projectId"], manifest["packageRevision"]):
                    raise PackageError("Project revision already installed with different content")
            staging = Path(tempfile.mkdtemp(prefix=".install-", dir=self.root))
            try:
                (staging / "metadata.json").write_text(json.dumps(metadata, ensure_ascii=False), encoding="utf-8")
                for name, data in files.items():
                    path = staging / name
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(data)
                staging.rename(self.root / package_id)
            finally:
                if staging.exists():
                    shutil.rmtree(staging)
        return metadata

    def list(self) -> list:
        with self._lock:
            return [self.get(p.name) for p in sorted(self.root.iterdir())
                    if HASH.fullmatch(p.name) and p.is_dir() and not p.is_symlink()]

    def get(self, package_id) -> dict:
        with self._lock:
            path = self._directory(package_id) / "metadata.json"
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise PackageError("Stored package unavailable") from exc

    def activate(self, package_id) -> dict:
        with self._lock:
            package = self.get(package_id)
            fd, temporary = tempfile.mkstemp(prefix=".active-", dir=self.root)
            try:
                with os.fdopen(fd, "w", encoding="ascii") as file:
                    file.write(package_id)
                    file.flush()
                    os.fsync(file.fileno())
                os.replace(temporary, self.root / "active")
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
            return package

    def active(self) -> dict | None:
        with self._lock:
            try:
                package_id = (self.root / "active").read_text(encoding="ascii")
            except FileNotFoundError:
                return None
            return self.get(package_id)

    def asset(self, package_id, asset_id) -> tuple[bytes, str]:
        with self._lock:
            package = self.get(package_id)
            for asset in package["manifest"].get("assets", []):
                if asset["id"] == asset_id:
                    root = self._directory(package_id).resolve()
                    path = root / asset["path"]
                    if not path.resolve().is_relative_to(root) or path.is_symlink():
                        raise PackageError("Stored resource unavailable")
                    data = path.read_bytes()
                    if hashlib.sha256(data).hexdigest() != asset["sha256"]:
                        raise PackageError("Stored resource digest mismatch")
                    return data, asset["mimeType"]
            raise PackageError("Unknown asset")
