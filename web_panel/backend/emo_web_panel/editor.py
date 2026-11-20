"""Independent Web authoring state and explicit publish coordination.

Only the small Panel SDK is imported here. Runtime owns execution and capture;
this store owns complete editable Presentation documents and immutable releases.
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
from io import BytesIO
import hashlib
import json
from pathlib import Path
import secrets
import sqlite3
import time
import zipfile
from urllib.parse import quote

from emo_panel_protocol.canonical import canonicalJson, revisionOf
from emo_panel_protocol.models import Presentation, SqliteSource, walkComponents
from emo_panel_protocol.published import HistoryReference, PublishedPresentation, PanelManifest, compositionRequirements

from .packages import PackageError, PackageStore, _official_image


class DraftConflict(ValueError):
    pass


def blank_presentation():
    page = "page-" + secrets.token_hex(8)
    return {"schemaVersion": "1.3", "defaultPageId": page, "pageOrder": [page],
            "navigation": {"mode": "tabs"}, "resultScopes": {}, "dataSources": {},
            "pages": {page: {"name": "检测页", "targets": ["web"],
                              "layout": {"type": "responsive", "direction": "vertical"},
                              "components": [], "resultScopeIds": []}}}


def validate_source(raw, resources):
    model = Presentation.model_validate(raw)
    if model.schemaVersion != "1.3" or not model.pages:
        raise ValueError("Web 页面源必须使用 Presentation 1.3，且至少保留一页")
    if model.shell and model.shell.logoResourceId and model.shell.logoResourceId not in resources:
        raise ValueError("页头图片资源不存在")
    for pid, page in model.pages.items():
        if page.targets != ["web"]:
            raise ValueError(f"{pid}: 独立 Web 页面 targets 必须为 [web]")
        for scope in page.resultScopeIds:
            if scope not in model.resultScopes:
                raise ValueError(f"{pid}: 未定义结果作用域 {scope}")
        for item in walkComponents(page.components):
            path = f"{pid}/{item.componentId}"
            if item.props.imagePath is not None:
                raise ValueError(f"{path}: 请上传静态资源，不能使用浏览器本机路径")
            if item.props.sqlite != SqliteSource() or (
                    item.type in {"sqlite_table", "sqlite_value"} and item.props.historySource is None):
                raise ValueError(f"{path}: 请从组件区选择已有的 Runtime 历史来源")
            for resource in (item.props.imageResourceId, item.props.iconResourceId):
                if resource and resource not in resources:
                    raise ValueError(f"{path}: 静态资源不存在 {resource}")
            for source_id in item.bindings.values():
                source = model.dataSources.get(source_id)
                if source is None:
                    raise ValueError(f"{path}: 来源不存在 {source_id}")
                if source.resultScopeId not in model.resultScopes:
                    raise ValueError(f"{path}: 来源结果作用域不存在")
                allowed = {"image": {"image"}, "number": {"integer", "number"},
                           "indicator": {"integer", "boolean", "string"}, "table": {"collection"},
                           "text": {"integer", "number", "boolean", "string", "json", "geometry"}}
                if item.type == "runtime_status":
                    raise ValueError(f"{path}: 任务状态使用原生任务状态，不绑定业务采集输出")
                if source.expectedType not in allowed.get(item.type, set()):
                    raise ValueError(f"{path}: {source.expectedType} 与 {item.type} 不兼容")
            for action in item.actions.values():
                if action.type == "navigate" and action.pageId not in model.pages:
                    raise ValueError(f"{path}: 导航页面不存在")
                if action.resultScopeId and action.resultScopeId not in model.resultScopes:
                    raise ValueError(f"{path}: 导航作用域不存在")
    return model


def output_config(model):
    used = {sid for page in model.pages.values() for item in walkComponents(page.components)
            for sid in item.bindings.values()}
    scopes = {model.dataSources[sid].resultScopeId for sid in used}
    scopes.update(scope for page in model.pages.values() for scope in page.resultScopeIds)
    return {"schemaVersion": "1.0",
            "sources": {sid: model.dataSources[sid].model_dump(mode="json") for sid in sorted(used)},
            "scopes": {sid: model.resultScopes[sid].model_dump(mode="json") for sid in sorted(scopes)}}


def link_diagnostics(source, diagnostics):
    """Web-owned links from generic Runtime addresses to editable controls."""
    result = deepcopy(diagnostics)
    model = (PublishedPresentation if source['schemaVersion'] == 'published-1' else Presentation).model_validate(source)
    for diagnostic in result:
        source_ids = set(diagnostic.get("sourceIds", []))
        scope_ids = set(diagnostic.get("scopeIds", []))
        if diagnostic.get("sourceId"):
            source_ids.add(diagnostic["sourceId"])
        if diagnostic.get("scopeId"):
            scope_ids.add(diagnostic["scopeId"])
        refs = []
        for page_id, page in model.pages.items():
            for item in walkComponents(page.components):
                if any(sid in source_ids or not source_ids and model.dataSources[sid].resultScopeId in scope_ids
                       for sid in item.bindings.values()):
                    refs.append(dict(pageId=page_id, pageName=page.name, componentId=item.componentId,
                                     componentName=item.props.title or item.type))
        diagnostic["componentRefs"] = refs
    return result


def page_history(page, sources):
    """Project each Web instance from its current Runtime declaration."""
    items = list(walkComponents(page.components))
    expected = {"sqlite_table": {"table"}, "sqlite_value": {"aggregate"},
                "image": {"fixed_image", "selected_row_image"}}
    references = {}
    for item in items:
        source = item.props.historySource
        if source is None:
            continue
        declared = sources.get(source.componentId)
        if declared is None or declared["kind"] not in expected[item.type]:
            raise ValueError(f"{item.componentId}: 当前 Runtime 目录没有这个类型的历史来源")
        row_image = declared["kind"] == "selected_row_image"
        if row_image != bool(source.sourceComponentId):
            raise ValueError(f"{item.componentId}: 选中行图片需要关联同页表格，固定图片独立读取")
        reference = {key: value for key, value in declared.items() if key != "title"}
        reference["sourceComponentId"] = source.sourceComponentId
        references[item.componentId] = HistoryReference.model_validate(reference)
    for item in items:
        reference = references.get(item.componentId)
        if reference and reference.kind == "selected_row_image":
            table = references.get(reference.sourceComponentId)
            if table is None or table.kind != "table" or table.componentId != sources[reference.componentId].get("sourceComponentId"):
                raise ValueError(f"{item.componentId}: 请选择与该图片 Runtime 来源匹配的同页表格")
        if view := item.props.historyView:
            if reference is None or any(field and field not in reference.columns for field in
                    (view.titleField, view.subtitleField, view.trailingField)):
                raise ValueError(f"{item.componentId}: 列表字段必须由历史来源声明")
        if record := item.props.recordValue:
            table = references.get(record.componentId)
            if table is None or table.kind != "table":
                raise ValueError(f"{item.componentId}: 请选择同页历史表格")
            if record.kind == "field" and record.field not in table.columns:
                raise ValueError(f"{item.componentId}: 记录字段必须由历史来源声明")
            if record.kind == "meta" and record.meta == "count" and table.aggregate != "count":
                raise ValueError(f"{item.componentId}: 记录总数要求历史来源使用 count")
        for action in item.actions.values():
            if action.componentId is not None:
                table = references.get(action.componentId)
                if table is None or table.kind != "table":
                    raise ValueError(f"{item.componentId}: 历史动作需要同页历史表格")
    return references


def build_package(project_id, model, resources, read_resource, *, history_sources=None, history_revision=""):
    """One-way authoring -> PublishedPresentation conversion, never saved as source."""
    config = output_config(model)
    payload = model.model_dump(mode="json", exclude_none=True)
    payload["schemaVersion"] = "published-1"
    payload["dataSources"] = config["sources"]
    payload["resultScopes"] = config["scopes"]
    requirements = {"layout.ordered-responsive-v1", "result.closed-v1", "actions.readonly-v1"}
    requirements.update(compositionRequirements(payload))
    referenced = set()
    if logo := payload.get('shell', {}).get('logoResourceId'):
        referenced.add(logo)
    history_sources = history_sources or {}

    def convert(items, references):
        for item in items:
            requirements.add("component." + item["type"] + "@1.0")
            item["props"].pop("sqlite", None)
            item["props"].pop("imagePath", None)
            item["props"].pop("historySource", None)
            if source := references.get(item["componentId"]):
                requirements.add("history.query-v1" if source.kind in {"table", "aggregate"} else "history.image-v1")
                item["history"] = source.model_dump(mode="json", exclude_none=True)
            referenced.update(item["props"][key] for key in ("imageResourceId", "iconResourceId")
                              if item["props"].get(key))
            convert(item["children"], references)
    for page_id, page in payload["pages"].items():
        page.pop("targets", None)
        if page.get("canvasTop"):
            requirements.add("layout.expanded-canvas-v1")
        if page["layout"]["type"] == "grid":
            requirements.add("layout.grid-v1")
        convert(page["components"], page_history(model.pages[page_id], history_sources))
    snapshot = PublishedPresentation.model_validate(payload).model_dump(mode="json", exclude_none=True)
    capture = {"ruleVersion": "1.0", "sources": sorted(
        {canonicalJson(source) for source in config["sources"].values()}) , "scopes": config["scopes"]}
    capture["sources"] = [json.loads(source) for source in capture["sources"]]
    files, assets = {}, {}
    for rid in sorted(referenced):
        asset = resources[rid]
        data, mime = read_resource(rid)
        assets[rid] = {key: asset[key] for key in ("path", "sha256", "byteSize", "mimeType")}
        files[asset["path"]] = data
    if sum(len(data) for data in files.values()) > 64 * 1024 * 1024:
        raise ValueError("页面静态资源合计超过 64 MiB")
    manifest = {"format": "emo-web-panel-1", "projectId": project_id, "panelContractVersion": "1.0",
                "rendererRequirements": sorted(requirements), "presentation": "presentation.json",
                "sourcePresentationVersion": model.schemaVersion,
                "historyDefinitionRevision": history_revision if "history.query-v1" in requirements or "history.image-v1" in requirements else revisionOf({}),
                "assets": assets, "captureRequirements": capture}
    manifest["packageRevision"] = revisionOf({"manifest": manifest, "presentation": snapshot})
    manifest = PanelManifest.model_validate(manifest).model_dump(mode="json")
    files.update({"manifest.json": canonicalJson(manifest).encode(),
                  "presentation.json": canonicalJson(snapshot).encode()})
    def archive_bytes(stored=()):
        buffer = BytesIO()
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, data in sorted(files.items()):
                info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_STORED if name in stored else zipfile.ZIP_DEFLATED
                archive.writestr(info, data)
        return buffer.getvalue()

    raw = archive_bytes()
    with zipfile.ZipFile(BytesIO(raw)) as archive:
        stored = {info.filename for info in archive.infolist()
                  if info.file_size > max(info.compress_size, 1) * PackageStore.MAX_RATIO}
    # Keep existing successful packages byte-identical. Only high-ratio entries
    # (e.g. flat-color BMPs) need storage without compression; import guards stay on.
    if stored:
        raw = archive_bytes(stored)
    return raw, manifest, snapshot, config


class EditorStore:
    def __init__(self, root: Path, packages):
        self.root, self.packages = Path(root), packages
        self.root.mkdir(parents=True, exist_ok=True)
        self.database = packages.database
        self._publish_locks = {}
        with sqlite3.connect(self.database) as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS web_drafts (
                  runtime_id TEXT, project_id TEXT, source TEXT, revision TEXT,
                  PRIMARY KEY(runtime_id,project_id));
                CREATE TABLE IF NOT EXISTS web_editor_assets (
                  runtime_id TEXT, project_id TEXT, resource_id TEXT, metadata TEXT,
                  PRIMARY KEY(runtime_id,project_id,resource_id));
                CREATE TABLE IF NOT EXISTS web_publications (
                  runtime_id TEXT, project_id TEXT, version_id TEXT PRIMARY KEY, body TEXT);
                CREATE TABLE IF NOT EXISTS web_editor_packages (
                  package_id TEXT, runtime_id TEXT, project_id TEXT,
                  PRIMARY KEY(package_id,runtime_id,project_id));
            """)

    def assets(self, rid, project):
        with sqlite3.connect(self.database) as db:
            return {row[0]: json.loads(row[1]) for row in db.execute(
                "SELECT resource_id,metadata FROM web_editor_assets WHERE runtime_id=? AND project_id=?", (rid, project))}

    def draft(self, rid, project):
        with sqlite3.connect(self.database) as db:
            row = db.execute("SELECT source,revision FROM web_drafts WHERE runtime_id=? AND project_id=?", (rid, project)).fetchone()
        return {"presentation": json.loads(row[0]) if row else blank_presentation(),
                "draftRevision": row[1] if row else "", "resources": self.assets(rid, project)}

    def save(self, rid, project, raw, expected=None):
        model = validate_source(raw, self.assets(rid, project))
        source = model.model_dump(mode="json", exclude_none=True)
        revision = revisionOf(source)
        with sqlite3.connect(self.database) as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT revision FROM web_drafts WHERE runtime_id=? AND project_id=?", (rid, project)).fetchone()
            if expected is not None and expected != (row[0] if row else ""):
                raise DraftConflict("草稿已被其他编辑窗口修改，请重新读取后再保存")
            db.execute("INSERT INTO web_drafts VALUES (?,?,?,?) ON CONFLICT(runtime_id,project_id) "
                       "DO UPDATE SET source=excluded.source,revision=excluded.revision",
                       (rid, project, canonicalJson(source), revision))
        return {"presentation": source, "draftRevision": revision, "resources": self.assets(rid, project)}

    def upload(self, rid, project, data, mime, name=""):
        if not data or len(data) > 8 * 1024 * 1024 or mime not in {"image/png", "image/jpeg", "image/bmp"}:
            raise ValueError("静态资源须为 PNG/JPEG/BMP，单个不超过 8 MiB")
        _official_image(data, mime)
        digest = hashlib.sha256(data).hexdigest()
        resource_id = "asset-" + digest[:32]
        suffix = {"image/png": "png", "image/jpeg": "jpg", "image/bmp": "bmp"}[mime]
        metadata = {"resourceId": resource_id, "name": str(name)[:256], "sha256": digest,
                    "mimeType": mime, "byteSize": len(data), "path": f"assets/{digest}.{suffix}",
                    "url": f"/api/editor/{quote(rid, safe='')}/assets/{resource_id}?projectId={quote(project, safe='')}"}
        path = self.root / digest
        if not path.exists():
            path.write_bytes(data)
        with sqlite3.connect(self.database) as db:
            db.execute("INSERT OR IGNORE INTO web_editor_assets VALUES (?,?,?,?)", (rid, project, resource_id, canonicalJson(metadata)))
        return metadata

    def resource(self, rid, project, resource):
        metadata = self.assets(rid, project).get(resource)
        if metadata is None:
            raise KeyError(resource)
        data = (self.root / metadata["sha256"]).read_bytes()
        if len(data) != metadata["byteSize"] or hashlib.sha256(data).hexdigest() != metadata["sha256"]:
            raise PackageError("静态资源内容与声明不匹配")
        return data, metadata["mimeType"]

    def history_sources(self, rid, project, revision):
        """Use the current edition's path-free declarations for this Runtime."""
        active = self.packages.active(rid, project)
        if not active or not revision or active["manifest"]["historyDefinitionRevision"] != revision:
            return {}
        snapshot = active.get("officialPresentation")
        if not snapshot:
            return {}
        published = PublishedPresentation.model_validate(snapshot)
        sources = {}
        for page_id in published.pageOrder:
            items = list(walkComponents(published.pages[page_id].components))
            tables = {item.componentId: item.history.componentId for item in items
                      if item.history is not None and item.history.kind == "table"}
            for item in items:
                if reference := item.history:
                    value = reference.model_dump(mode="json", exclude_none=True)
                    if reference.kind == "selected_row_image":
                        value["sourceComponentId"] = tables[reference.sourceComponentId]
                    sources.setdefault(reference.componentId, {**value, "title": item.props.title})
        return sources

    def prepare(self, rid, project, source, history_revision=""):
        resources = self.assets(rid, project)
        model = validate_source(source, resources)
        history_sources = self.history_sources(rid, project, history_revision)
        raw, manifest, snapshot, config = build_package(project, model, resources, lambda resource: self.resource(rid, project, resource),
                                                        history_sources=history_sources, history_revision=history_revision)
        package = self.packages.install(raw)
        with sqlite3.connect(self.database) as db:
            db.execute("INSERT OR IGNORE INTO web_editor_packages VALUES (?,?,?)", (package["packageId"], rid, project))
        # Retain exportable bytes next to full source records. Packages remain immutable.
        path = self.packages.root / package["packageId"] / "release.emoweb"
        if not path.exists():
            path.write_bytes(raw)
        panel = {**package["presentation"], "id": package["packageId"], "name": "Web 页面",
                 "runtimeId": rid, "officialPresentation": snapshot}
        return package, panel, config

    def owns_package(self, package_id):
        with sqlite3.connect(self.database) as db:
            return db.execute("SELECT 1 FROM web_editor_packages WHERE package_id=? LIMIT 1", (package_id,)).fetchone() is not None

    def versions(self, rid, project):
        with sqlite3.connect(self.database) as db:
            records = [json.loads(row[0]) for row in db.execute("SELECT body FROM web_publications WHERE runtime_id=? AND project_id=? ORDER BY rowid DESC", (rid, project))]
        return [{key: value for key, value in record.items() if key not in {"source", "config", "submitBody"}} for record in records]

    def version(self, rid, project, version_id):
        with sqlite3.connect(self.database) as db:
            row = db.execute("SELECT body FROM web_publications WHERE runtime_id=? AND project_id=? AND version_id=?", (rid, project, version_id)).fetchone()
        if row is None:
            raise KeyError(version_id)
        return json.loads(row[0])

    def update(self, record):
        with sqlite3.connect(self.database) as db:
            db.execute("INSERT INTO web_publications VALUES (?,?,?,?) ON CONFLICT(version_id) DO UPDATE SET body=excluded.body",
                       (record["runtimeId"], record["projectId"], record["versionId"], canonicalJson(record)))
        return deepcopy(record)

    def _publication_lock(self, rid):
        # One target can switch loaded projects; it still has one coordinated
        # publication path. Independent Runtime targets do not share RPC waits.
        return self._publish_locks.setdefault(rid, asyncio.Lock())

    def _new_publication(self, rid, project, info, current, package, source, config, *, request_id=None, installed=False):
        record = {"runtimeId": rid, "projectId": project, "versionId": "web-" + secrets.token_hex(12),
                  "requestId": request_id or "web-publish-" + secrets.token_hex(16),
                  "state": "PREPARED", "message": "候选页面已保存，尚未更新采集请求", "createdAt": time.time(),
                  "source": deepcopy(source), "sourceRevision": revisionOf(source), "config": config,
                  "packageId": package["packageId"], "packageRevision": package["manifest"]["packageRevision"],
                  "runtimeInstanceId": info["runtimeInstanceId"], "installedPackage": installed, "diagnostics": []}
        record["submitBody"] = {"runtimeInstanceId": info["runtimeInstanceId"], "projectId": project,
                    "expectedDocumentRevision": info.get("documentRevision", info.get("configurationRevision", "0")),
                    "expectedConfigRevision": current["configRevision"], "requestId": record["requestId"], "config": config}
        self.update(record)
        return record

    async def publish(self, rid, project, client, *, expected=None, request_id=None, source=None):
        async with self._publication_lock(rid):
            draft = self.draft(rid, project)
            source = source if source is not None else draft["presentation"]
            if request_id is not None and (not isinstance(request_id, str) or not 1 <= len(request_id) <= 160 or any(char.isspace() for char in request_id)):
                raise ValueError("发布 requestId 必须是 1–160 个非空白字符")
            if request_id:
                for old in self.versions(rid, project):
                    if old["requestId"] == request_id:
                        if revisionOf(source) != old["sourceRevision"]:
                            raise DraftConflict("同一发布请求 ID 不能用于不同页面源，请查询原发布或发起新的明确发布")
                        return await self._reconcile(self.version(rid, project, old["versionId"]), client)
            for old in self.versions(rid, project):
                if old["state"] in {"REQUEST_UNKNOWN", "REQUEST_SAVED"}:
                    raise DraftConflict("上次发布尚待确认，请查询并继续完成原发布")
            if expected is not None and expected != draft["draftRevision"]:
                raise DraftConflict("草稿版本已变化，请先保存并重新核对")
            info, current = await client.info(), await client.output_requests()
            if info["projectId"] != project:
                raise DraftConflict("Runtime 工程已切换，请重新选择目标")
            package, _panel, config = self.prepare(rid, project, source, info.get("historyDefinitionRevision", ""))
            record = self._new_publication(rid, project, info, current, package, source, config, request_id=request_id)
            return await self._submit_prepared(record, client, current=current)

    async def synchronize_package(self, rid, project, client, info, package):
        """Register the immutable package's exact capture definition using normal publication receipts."""
        async with self._publication_lock(rid):
            pending = next((item for item in self.versions(rid, project)
                            if item["state"] in {"REQUEST_UNKNOWN", "REQUEST_SAVED"}), None)
            if pending is not None:
                if not pending.get("installedPackage") or pending["packageId"] != package["packageId"]:
                    raise DraftConflict("请先完成或结束当前目标待确认的页面发布")
                record = self.version(rid, project, pending["versionId"])
                record = await self._reconcile(record, client, activate=False)
            else:
                current = await client.output_requests()
                source = package["officialPresentation"]
                config = output_config(PublishedPresentation.model_validate(source))
                record = self._new_publication(rid, project, info, current, package, source, config, installed=True)
                record = await self._submit_prepared(record, client, current=current, activate=False)
            if record["state"] != "REQUEST_SAVED":
                message = record["message"]
                if record["state"] == "REQUEST_UNKNOWN":
                    message = "采集请求结果待确认。再次启用此页面包可查询原请求。"
                raise DraftConflict(message)
            problem, diagnostics = await self._saved_capture_problem(record, client)
            if problem:
                self.update({**record, "message": problem, "diagnostics": diagnostics})
                raise DraftConflict(problem)
            return record

    async def _submit_prepared(self, record, client, *, current=None, activate=True):
        body = record["submitBody"]
        try:
            validation = await client.validate_output_requests(body)
        except Exception:
            record.update(state="PREPARED", message="候选页面已保存，校验连接中断；尚未提交写入，可明确继续发布")
            return self.update(record)
        if not validation.get("ok", True):
            record.update(state="REJECTED", message=validation.get("message", "请求校验失败"),
                          diagnostics=link_diagnostics(record["source"], validation.get("diagnostics", [])))
            return self.update(record)
        if current is None:
            try:
                current = await client.output_requests()
            except Exception:
                record.update(state="PREPARED", message="候选页面已保存，读取采集配置中断；尚未提交写入，可明确继续发布")
                return self.update(record)
        # Pure layout releases still validate the current engineering context.
        if current.get("config") == record["config"]:
            record.update(state="REQUEST_SAVED", configRevision=current["configRevision"], message="采集配置已确认，页面启用待完成")
            self.update(record)
            return await self._complete(record, client) if activate else record
        # Persist the uncertain state *before* sending the one explicit write.
        record.update(state="REQUEST_UNKNOWN", message="正在提交采集请求；结果未知时查询原请求，不自动重放")
        self.update(record)
        try:
            reply = await client.submit_output_requests(body)
        except Exception:
            return self.update(record)
        return await self._accept_reply(record, reply, client, activate=activate)


    async def _accept_reply(self, record, reply, client, *, activate=True):
        if not reply.get("ok", True):
            if reply.get("code") == "NOT_FOUND" or reply.get("state") == "NOT_FOUND":
                record.update(state="REQUEST_UNKNOWN", message="Runtime 尚无可确认的原请求结果；未重放写入")
                try:
                    current = await client.output_requests()
                except Exception:
                    return self.update(record)
                if (current.get("runtimeInstanceId") == record["runtimeInstanceId"] and
                        current.get("projectId") == record["projectId"] and current.get("config") == record["config"]):
                    record.update(state="REQUEST_SAVED", configRevision=current["configRevision"],
                                  message="当前实际采集配置已匹配候选页面；原请求回执未取得，页面启用待完成")
                    self.update(record)
                    return await self._complete(record, client) if activate else record
            else:
                record.update(state="REJECTED", message=reply.get("message", "采集请求被拒绝"),
                              diagnostics=link_diagnostics(record["source"], reply.get("diagnostics", [])))
            return self.update(record)
        if reply.get("state") != "SAVED":
            record.update(state="REQUEST_UNKNOWN", message="采集请求尚未确认保存；未重放写入")
            return self.update(record)
        record.update(state="REQUEST_SAVED", configRevision=reply["configRevision"],
                      message="采集配置已更新，页面启用待完成", diagnostics=reply.get("diagnostics", []))
        self.update(record)
        return await self._complete(record, client) if activate else record

    async def _reconcile(self, record, client, *, activate=True):
        if record["state"] == "PREPARED" and activate:
            return await self._submit_prepared(record, client)
        if record["state"] == "REQUEST_UNKNOWN":
            try:
                reply = await client.output_request_status(record["requestId"], record["submitBody"])
            except Exception:
                return record
            return await self._accept_reply(record, reply, client, activate=activate)
        if record["state"] == "REQUEST_SAVED":
            return await self._complete(record, client) if activate else record
        return record

    async def _saved_capture_problem(self, record, client):
        try:
            current = await client.output_requests()
        except Exception:
            return "采集配置已更新，连接恢复后可继续启用页面", []
        if (current.get("runtimeInstanceId") != record["runtimeInstanceId"] or
                current.get("projectId") != record["projectId"] or
                current.get("configRevision") != record.get("configRevision") or
                current.get("config") != record["config"]):
            return "已保存请求与当前 Runtime 配置不一致，请刷新目标并核对；页面尚未启用", []
        if current.get("diagnostics"):
            return "采集请求已保存，但当前流程来源已失效；页面尚未启用，请修复绑定后重新发布", current["diagnostics"]
        return "", []

    async def _complete(self, record, client):
        problem, diagnostics = await self._saved_capture_problem(record, client)
        if problem:
            return self.update({**record, "state": "REQUEST_SAVED", "message": problem, "diagnostics": diagnostics})
        try:
            self.packages.activate(record["packageId"], record["runtimeId"], record["projectId"])
        except Exception:
            record.update(state="REQUEST_SAVED", message="采集配置已更新，页面启用待完成")
            return self.update(record)
        return self.mark_published(record)

    def mark_published(self, record):
        return self.update({**record, "state": "PUBLISHED", "message": "页面已发布；当前任务保持原采集配置，新来源下次明确启动生效"})

    async def _abandon(self, record, client):
        """End page coordination only; never infer that an uncertain write did not run."""
        if record["state"] == "PUBLISHED":
            raise DraftConflict("正式版本请使用回退，不可结束已完成的发布")
        current = await client.output_requests()
        record.update(state="ABANDONED", message="已结束此页面发布并保留草稿；原请求是否执行以 Runtime 当前配置为准，未撤销或重放采集请求",
                      observedConfigRevision=current["configRevision"], observedRuntimeInstanceId=current.get("runtimeInstanceId"))
        return self.update(record)

    async def reconcile(self, record, client, *, activate=True):
        async with self._publication_lock(record["runtimeId"]):
            current = self.version(record["runtimeId"], record["projectId"], record["versionId"])
            return await self._reconcile(current, client, activate=activate)

    async def complete(self, record, client):
        return await self.reconcile(record, client, activate=True)

    async def abandon(self, record, client):
        async with self._publication_lock(record["runtimeId"]):
            current = self.version(record["runtimeId"], record["projectId"], record["versionId"])
            return await self._abandon(current, client)
