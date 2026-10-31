"""Bounded immutable packages: official SDK v1 plus explicitly labelled draft fixtures."""
from __future__ import annotations

from contextlib import closing
import hashlib
import io
import json
import math
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import sqlite3
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


# Requirements implemented by the gateway renderer, not arbitrary RPC capabilities.
OFFICIAL_REQUIREMENTS = frozenset({f"component.{kind}@1.0" for kind in TYPES} | {
    "actions.readonly-v1", "history.image-v1", "history.query-v1",
    "layout.ordered-responsive-v1", "layout.grid-v1", "result.closed-v1",
})


def _official_image(data, mime):
    """Validate declared image encoding and bounded dimensions without rendering."""
    from PIL import Image, UnidentifiedImageError
    try:
        with Image.open(io.BytesIO(data)) as image:
            if image.format != {"image/png": "PNG", "image/jpeg": "JPEG", "image/bmp": "BMP"}[mime]:
                raise PackageError("Image encoding differs from declaration")
            if image.width < 1 or image.height < 1 or image.width * image.height > 4_000_000:
                raise PackageError("Static image pixel budget exceeded")
            image.verify()
    except PackageError:
        raise
    except (UnidentifiedImageError, OSError, ValueError, KeyError, Image.DecompressionBombError) as exc:
        raise PackageError("Invalid static image") from exc


def _official_presentation(manifest, doc, files):
    # The SDK is authoritative for the published shape, revisions, sources and
    # scopes. Additional checks here enforce renderer reachability and ZIP data.
    from emo_panel_protocol.published import PanelManifest, PublishedPresentation
    from emo_panel_protocol.models import Component, DataSource, ResultScope
    from emo_panel_protocol.canonical import canonicalJson, revisionOf
    try:
        model = PanelManifest.model_validate(manifest)
        published = PublishedPresentation.model_validate(doc)
    except (ValueError, TypeError) as exc:
        raise PackageError("Invalid Panel Contract v1 package schema") from exc
    if set(model.rendererRequirements) - OFFICIAL_REQUIREMENTS:
        raise PackageError("Unsupported renderer requirement")
    body = {key: value for key, value in manifest.items() if key != "packageRevision"}
    if revisionOf({"manifest": body, "presentation": doc}) != model.packageRevision:
        raise PackageError("Package revision mismatch")
    paths = set()
    total = 0
    for asset in model.assets.values():
        path = _path(asset.path)
        extension = {"image/png": "png", "image/jpeg": "jpg", "image/bmp": "bmp"}[asset.mimeType]
        # Some JPEG exporters use .jpeg; the digest must still name the asset.
        if path not in {f"assets/{asset.sha256}.{extension}", f"assets/{asset.sha256}.jpeg" if extension == "jpg" else ""}:
            raise PackageError("Static resource must use its digest filename")
        data = files.get(path)
        if data is None or len(data) != asset.byteSize or hashlib.sha256(data).hexdigest() != asset.sha256:
            raise PackageError("Asset size or digest mismatch")
        if path not in paths:
            total += len(data)
            _official_image(data, asset.mimeType)
        paths.add(path)
    if total > 64 * 1024 * 1024:
        raise PackageError("Static resource total budget exceeded")
    if set(files) != {"manifest.json", "presentation.json"} | paths:
        raise PackageError("Undeclared package file")
    if (not 1 <= len(published.pages) <= 128 or
            len(set(published.pageOrder)) != len(published.pageOrder) or
            set(published.pageOrder) != set(published.pages) or
            published.defaultPageId not in published.pages):
        raise PackageError("Invalid published page order or default page")
    try:
        capture = model.captureRequirements
        if set(capture) != {"ruleVersion", "sources", "scopes"} or capture["ruleVersion"] != "1.0":
            raise ValueError("capture definition")
        capture_sources = {canonicalJson(DataSource.model_validate(value).model_dump()) for value in capture["sources"]}
        capture_scopes = {key: ResultScope.model_validate(value).model_dump() for key, value in capture["scopes"].items()}
        if not {canonicalJson(value.model_dump()) for value in published.dataSources.values()} <= capture_sources:
            raise ValueError("capture sources")
        if any(capture_scopes.get(key) != value.model_dump() for key, value in published.resultScopes.items()):
            raise ValueError("capture scopes")
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        raise PackageError("Capture requirements disagree with published sources") from exc
    if any(source.resultScopeId not in published.resultScopes for source in published.dataSources.values()):
        raise PackageError("Unknown source result scope")
    seen = {}
    history_links = []
    def component(value, page_ids, depth=0):
        if depth > 8 or len(seen) >= 1024 or value.componentId in seen:
            raise PackageError("Duplicate or excessively nested published component")
        seen[value.componentId] = value.type
        # PublishedComponent is a transport DTO; Component supplies the binding,
        # static-resource and children semantic validators without local paths.
        shape = value.model_dump(exclude={"history", "children"})
        if value.children and value.type != "container":
            raise PackageError("Only containers can have children")
        try:
            Component.model_validate(shape)
        except (ValueError, TypeError) as exc:
            raise PackageError("Invalid published component semantics") from exc
        for resource in (value.props.imageResourceId, value.props.iconResourceId):
            if resource is not None and resource not in model.assets:
                raise PackageError("Unknown static resource")
        for source in value.bindings.values():
            if source not in published.dataSources:
                raise PackageError("Unknown bound source")
        for action in value.actions.values():
            if action.type == "navigate" and action.pageId not in published.pages:
                raise PackageError("Unknown navigation target")
            if action.resultScopeId is not None and action.resultScopeId not in published.resultScopes:
                raise PackageError("Unknown action result scope")
        history = value.history
        if history is not None:
            expected = {"table": "sqlite_table", "aggregate": "sqlite_value", "fixed_image": "image", "selected_row_image": "image"}[history.kind]
            if value.type != expected or history.componentId != value.componentId or value.bindings or value.props.imageResourceId:
                raise PackageError("Invalid published history component")
            if history.kind == "selected_row_image":
                history_links.append((page_ids, history.sourceComponentId))
            elif history.sourceComponentId is not None:
                raise PackageError("Only selected-row history references a table")
        elif value.type in {"sqlite_table", "sqlite_value"}:
            raise PackageError("History component missing Runtime declaration")
        for child in value.children:
            component(child, page_ids, depth + 1)
    page_components = {}
    for pid in published.pageOrder:
        page = published.pages[pid]
        if any(scope not in published.resultScopes for scope in page.resultScopeIds):
            raise PackageError("Unknown page result scope")
        before = set(seen)
        for item in page.components:
            component(item, pid)
        page_components[pid] = set(seen) - before
    for pid, cid in history_links:
        if cid not in page_components[pid] or seen.get(cid) != "sqlite_table":
            raise PackageError("Selected-row image requires a table on the same page")
    return _normalize_official(manifest, doc)


def _normalize_official(manifest, doc):
    """Explicit gateway view model; the immutable published document is retained."""
    def component(c):
        props = dict(c.get("props", {}))
        history = c.get("history")
        if props.get("imageResourceId"):
            props["assetId"] = props["imageResourceId"]
        if history:
            props["aggregate"] = history.get("aggregate", "count")
            if history["kind"] in {"table", "aggregate"}:
                props["columns"] = [{"key": key, "title": key} for key in history.get("columns", [])]
            if history["kind"] in {"fixed_image", "selected_row_image"}:
                props["sourceMode"] = "history_fixed" if history["kind"] == "fixed_image" else "history_selection"
                props["sourceComponentId"] = history.get("sourceComponentId")
        action = c.get("actions", {}).get("clicked")
        if action:
            props.update(action=action["type"], targetPageId=action.get("pageId"))
        value = {**c, "id": c["componentId"], "label": props.get("title", ""), "props": props,
                 "children": [component(child) for child in c.get("children", [])]}
        bindings = c.get("bindings", {})
        if bindings:
            source = next(iter(bindings.values()))
            value["binding"] = {"sourceId": source, "scopeId": doc["dataSources"][source]["resultScopeId"]}
        return value
    return {**doc, "projectId": manifest["projectId"],
            "historyDefinitionRevision": manifest["historyDefinitionRevision"],
            "captureRequirements": manifest["captureRequirements"],
            "packageRevision": manifest["packageRevision"],
            "pages": [{**doc["pages"][pid], "id": pid, "title": doc["pages"][pid]["name"],
                       "components": [component(c) for c in doc["pages"][pid]["components"]]}
                      for pid in doc["pageOrder"]]}


class PackageStore:
    MAX_ARCHIVE = 66 * 1024 * 1024
    MAX_EXPANDED = 66 * 1024 * 1024
    MAX_FILE = 8 * 1024 * 1024
    MAX_ENTRIES = 256
    MAX_RATIO = 100

    def __init__(self, root: Path, database: Path | None = None):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.database = Path(database) if database is not None else self.root / "web.sqlite"
        with closing(sqlite3.connect(self.database)) as db, db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS package_targets (
                    package_id TEXT, runtime_id TEXT, PRIMARY KEY(package_id,runtime_id));
                CREATE TABLE IF NOT EXISTS package_activations (
                    runtime_id TEXT NOT NULL, project_id TEXT NOT NULL, package_id TEXT NOT NULL,
                    PRIMARY KEY(runtime_id,project_id));
                CREATE TABLE IF NOT EXISTS package_migrations (name TEXT PRIMARY KEY);
            """)
            if not db.execute("SELECT 1 FROM package_migrations WHERE name='context-active-v1'").fetchone():
                # Old files remain intact. Only explicit old associations authorize
                # migration; an unassociated global active is not broadcast.
                old = self.root / 'active'
                if old.is_file():
                    pid = old.read_text(encoding='ascii').strip()
                    if HASH.fullmatch(pid) and (self.root / pid / 'metadata.json').is_file():
                        item = self.get(pid)
                        known = {row[0] for row in db.execute('SELECT id FROM runtimes')} if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='runtimes'").fetchone() else set()
                        for (rid,) in db.execute('SELECT runtime_id FROM package_targets WHERE package_id=?', (pid,)).fetchall():
                            if rid not in known:
                                continue
                            db.execute('INSERT OR IGNORE INTO package_activations VALUES (?,?,?)',
                                       (rid, item['manifest']['projectId'], pid))
                db.execute("INSERT INTO package_migrations VALUES ('context-active-v1')")

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
        official = isinstance(manifest, dict) and manifest.get("panelContractVersion") == "1.0"
        if official:
            normalized = _official_presentation(manifest, presentation, files)
        else:
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
                    "validationStatus": "panel-contract-1.0" if official else "draft-schema-only", "byteSize": len(raw)}
        if official:
            metadata["officialPresentation"] = presentation
            metadata["presentation"] = normalized
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

    def activate(self, package_id, runtime_id, project_id=None) -> dict:
        """Explicit single-target convenience, also used for isolated previews."""
        item = self.get(package_id)
        return self.activate_many(package_id, [runtime_id], project_id or item['manifest']['projectId'])

    def activate_many(self, package_id, runtime_ids, project_id) -> dict:
        with self._lock:
            item = self.get(package_id)
            if item['manifest']['projectId'] != project_id:
                raise PackageError('Package and target project differ')
            if not runtime_ids or any(not isinstance(rid, str) or not rid for rid in runtime_ids):
                raise PackageError('Explicit Runtime context required')
            with closing(sqlite3.connect(self.database)) as db, db:
                for rid in runtime_ids:
                    db.execute('INSERT INTO package_activations VALUES (?,?,?) '
                               'ON CONFLICT(runtime_id,project_id) DO UPDATE SET package_id=excluded.package_id',
                               (rid, project_id, package_id))
                    # Retain old versions and all other target associations for
                    # pinned views; activation is not an ACL replacement operation.
                    db.execute('INSERT OR IGNORE INTO package_targets VALUES (?,?)', (package_id, rid))
            return item

    def activations(self, package_id=None) -> list:
        with closing(sqlite3.connect(self.database)) as db, db:
            rows = db.execute('SELECT runtime_id,project_id,package_id FROM package_activations' +
                              (' WHERE package_id=?' if package_id else '') + ' ORDER BY runtime_id,project_id',
                              (package_id,) if package_id else ()).fetchall()
        return [{'runtimeId': rid, 'projectId': project, 'packageId': pid} for rid, project, pid in rows]

    def active(self, runtime_id, project_id=None) -> dict | None:
        with self._lock, closing(sqlite3.connect(self.database)) as db, db:
            rows = db.execute('SELECT package_id FROM package_activations WHERE runtime_id=?' +
                              (' AND project_id=?' if project_id is not None else ''),
                              (runtime_id, project_id) if project_id is not None else (runtime_id,)).fetchall()
            # No arbitrary project selection if a Runtime has hosted several.
            return self.get(rows[0][0]) if len(rows) == 1 else None

    def asset(self, package_id, asset_id) -> tuple[bytes, str]:
        with self._lock:
            package = self.get(package_id)
            assets = package["manifest"].get("assets", [])
            if isinstance(assets, dict):
                assets = [{"id": key, **value} for key, value in assets.items()]
            for asset in assets:
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
