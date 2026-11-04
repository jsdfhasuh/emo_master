"""Fixed business HTTP routes for independent Web authoring."""
import asyncio
from fastapi import HTTPException, Request
from fastapi.responses import Response

from .editor import EditorStore, DraftConflict


def install_editor_routes(app, config, packages, identity, client_for, json_object, views, registry, target_lock):
    store = EditorStore(config.data_dir / "editor-assets", packages)
    app.state.editor = store

    @app.exception_handler(DraftConflict)
    async def draft_conflict(req, exc):
        from fastapi.responses import JSONResponse
        return JSONResponse({"detail": str(exc), "code": "EDITOR_CONFLICT"}, status_code=409)

    async def target(req, rid, project=None, mutating=False):
        who = await identity(req, "admin", mutating)
        client = await client_for(who, rid, "control")
        info = await client.info()
        if not info.get("projectId"):
            raise HTTPException(409, "Runtime 未加载工程")
        if project is not None and project != info["projectId"]:
            raise HTTPException(409, "Runtime 工程已切换，请刷新目标")
        return who, client, info, info["projectId"]

    @app.get("/api/editor/{rid}/context")
    async def context(rid: str, req: Request):
        who, client, info, project = await target(req, rid)
        catalog, requests = await asyncio.gather(client.output_catalog(), client.output_requests())
        expected = (info["runtimeInstanceId"], project, info.get("documentRevision", info.get("configurationRevision")))
        if any((state.get("runtimeInstanceId"), state.get("projectId"), state.get("documentRevision")) != expected
               for state in (catalog, requests)):
            raise HTTPException(409, "Runtime 工程在读取期间变化，请刷新目录")
        return {"runtime": info, "catalog": catalog, "requests": requests,
                "draft": await asyncio.to_thread(store.draft, rid, project),
                "versions": await asyncio.to_thread(store.versions, rid, project)}

    @app.put("/api/editor/{rid}/draft")
    async def save(rid: str, req: Request):
        body = await json_object(req)
        who, client, info, project = await target(req, rid, body.get("projectId"), True)
        return await asyncio.to_thread(store.save, rid, project, body.get("presentation"), body.get("expectedDraftRevision"))

    @app.post("/api/editor/{rid}/assets")
    async def upload(rid: str, req: Request, projectId: str, name: str = ""):
        await target(req, rid, projectId, True)
        data = bytearray()
        async for chunk in req.stream():
            data.extend(chunk)
            if len(data) > 8 * 1024 * 1024:
                raise HTTPException(413, "静态资源超过 8 MiB")
        return await asyncio.to_thread(store.upload, rid, projectId, bytes(data), req.headers.get("content-type", "").split(";")[0], name)

    @app.get("/api/editor/{rid}/assets/{resource}")
    async def asset(rid: str, resource: str, req: Request, projectId: str):
        await target(req, rid, projectId)
        data, mime = await asyncio.to_thread(store.resource, rid, projectId, resource)
        return Response(data, media_type=mime)

    @app.post("/api/editor/{rid}/preview")
    async def preview(rid: str, req: Request):
        body = await json_object(req)
        who, client, info, project = await target(req, rid, body.get("projectId"), True)
        if body.get("mode") not in {"fixtures", "runtime"}:
            raise HTTPException(422, "选择示例预览或已有任务实时预览")
        package, panel, output = await asyncio.to_thread(store.prepare, rid, project, body.get("presentation"))
        response = {"mode": body["mode"], "panel": panel, "presentation": panel,
                    "source": body["presentation"], "sourceProblems": {},
                    "label": "示例数据 · 不执行检测" if body["mode"] == "fixtures" else "已有任务 · 只读实时预览"}
        if body["mode"] == "runtime":
            run_id = body.get("runId")
            if not run_id:
                raise HTTPException(422, "实时预览必须明确选择已有任务")
            if run_id not in {run.get("id", run.get("runId")) for run in await client.runs()}:
                raise HTTPException(404, "所选任务不存在")
            group = {"runIds": [run_id], "sourceProblems": {}}
            if hasattr(client, "validate_group"):
                group = await client.validate_group(run_id, panel["officialPresentation"])
            elif hasattr(client, "source_problems"):
                group["sourceProblems"] = await client.source_problems(run_id, panel["officialPresentation"])
            if hasattr(client, "job_metadata"):
                response["jobMetadata"] = await client.job_metadata(run_id)
            view = views.create(who["userId"], panel["id"], run_id, panel["packageRevision"], runtime_id=rid, runtime=client)
            view.update(presentation=panel, instanceId=info["runtimeInstanceId"], projectId=project,
                        historyContext={"runtimeInstanceId": info["runtimeInstanceId"], "projectId": project,
                                        "historyDefinitionRevision": panel["historyDefinitionRevision"]}, **group)
            response.update(viewId=view["id"], runIds=group["runIds"], sourceProblems=group["sourceProblems"])
        return response

    @app.post("/api/editor/{rid}/publish")
    async def publish(rid: str, req: Request):
        body = await json_object(req)
        # Share connection coordination with target edit/delete and production
        # control so this release cannot finish against a replaced client.
        async with target_lock(rid):
            who, client, info, project = await target(req, rid, body.get("projectId"), True)
            record = await store.publish(rid, project, client, expected=body.get("expectedDraftRevision"), request_id=body.get("requestId"))
            notify(record)
            return record

    def notify(record):
        if record["state"] == "PUBLISHED":
            for view in views.views.values():
                if view["runtimeId"] == record["runtimeId"] and view.get("projectId") == record["projectId"]:
                    views.publish_notice(view, {"type": "package_updated", "packageId": record["packageId"],
                                               "packageRevision": record["packageRevision"], "explicitRefreshRequired": True})

    @app.get("/api/editor/{rid}/versions")
    async def versions(rid: str, req: Request, projectId: str):
        await target(req, rid, projectId)
        return await asyncio.to_thread(store.versions, rid, projectId)

    @app.get("/api/editor/{rid}/versions/{version_id}")
    async def version(rid: str, version_id: str, req: Request, projectId: str):
        await target(req, rid, projectId)
        return await asyncio.to_thread(store.version, rid, projectId, version_id)

    @app.get("/api/editor/{rid}/publications/{version_id}")
    async def publication(rid: str, version_id: str, req: Request, projectId: str):
        async with target_lock(rid):
            who, client, info, project = await target(req, rid, projectId)
            record = await store.reconcile(await asyncio.to_thread(store.version, rid, project, version_id), client, activate=False)
            notify(record)
            return record

    @app.post("/api/editor/{rid}/publications/{version_id}/complete")
    async def complete(rid: str, version_id: str, req: Request):
        body = await json_object(req)
        async with target_lock(rid):
            who, client, info, project = await target(req, rid, body.get("projectId"), True)
            record = await store.reconcile(await asyncio.to_thread(store.version, rid, project, version_id), client)
            notify(record)
            return record

    @app.post("/api/editor/{rid}/rollback")
    async def rollback(rid: str, req: Request):
        body = await json_object(req)
        async with target_lock(rid):
            who, client, info, project = await target(req, rid, body.get("projectId"), True)
            old = await asyncio.to_thread(store.version, rid, project, body.get("versionId"))
            record = await store.publish(rid, project, client, request_id=body.get("requestId"), source=old["source"])
            notify(record)
            return record

    @app.post("/api/editor/{rid}/publications/{version_id}/abandon")
    async def abandon(rid: str, version_id: str, req: Request):
        body = await json_object(req)
        async with target_lock(rid):
            who, client, info, project = await target(req, rid, body.get("projectId"), True)
            return await store.abandon(await asyncio.to_thread(store.version, rid, project, version_id), client)

    @app.get("/api/editor/{rid}/versions/{version_id}/package")
    async def download(rid: str, version_id: str, req: Request, projectId: str):
        await target(req, rid, projectId)
        record = await asyncio.to_thread(store.version, rid, projectId, version_id)
        data = (packages.root / record["packageId"] / "release.emoweb").read_bytes()
        return Response(data, media_type="application/zip", headers={"Content-Disposition": f'attachment; filename="{version_id}.emoweb"'})

    return store
