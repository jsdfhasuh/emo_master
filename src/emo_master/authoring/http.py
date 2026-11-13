"""Authenticated HTTP and optional official MCP Streamable HTTP adapters."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import hmac
import json
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from starlette.routing import Route

from .errors import AuthoringError
from .service import AuthoringService


class WorkspaceBoundary:
    def __init__(self, app, *, token, allowed_origins=()):
        self.app, self.token = app, token.encode("utf-8")
        self.allowed_origins = set(allowed_origins)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = dict(scope.get("headers", []))
        authorization = headers.get(b"authorization", b"")
        expected = b"Bearer " + self.token
        if not hmac.compare_digest(authorization, expected):
            return await JSONResponse({"code": "E_AUTH", "message": "Workspace Bearer credential required"},
                                      status_code=401, headers={"WWW-Authenticate": "Bearer"})(scope, receive, send)
        origin = headers.get(b"origin")
        if origin is not None:
            value = origin.decode("latin-1")
            parsed = urlsplit(value)
            local = (parsed.scheme in {"http", "https"} and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
                     and not parsed.username and not parsed.password and parsed.path in {"", "/"}
                     and not parsed.query and not parsed.fragment)
            if value not in self.allowed_origins and not local:
                return await JSONResponse({"code": "E_ORIGIN", "message": "Request origin is not allowed"},
                                          status_code=403)(scope, receive, send)
        return await self.app(scope, receive, send)


class _MCPApplication:
    def __init__(self, manager):
        self.manager = manager

    async def __call__(self, scope, receive, send):
        await self.manager.handle_request(scope, receive, send)


def _mcp_manager(service, allowed_origins, allowed_hosts):
    # Importing this module and running basic HTTP does not require the optional SDK.
    from mcp import types
    from mcp.server.lowlevel import Server
    from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
    from mcp.server.transport_security import TransportSecuritySettings

    server = Server("emo-master-authoring", version="0.1.0",
                    instructions="Edit independent drafts. Read state after uncertain responses; never replay writes automatically.")

    @server.list_tools()
    async def tools():
        return [types.Tool(**definition) for definition in service.tool_definitions()]

    @server.call_tool(validate_input=False)
    async def call(name, arguments):
        try:
            return await service.invoke(name, arguments)
        except AuthoringError as error:
            diagnostic = error.as_dict()
            return types.CallToolResult(isError=True, structuredContent=diagnostic,
                                        content=[types.TextContent(type="text", text=json.dumps(diagnostic, ensure_ascii=False))])

    security = TransportSecuritySettings(
        allowed_hosts=["localhost:*", "127.0.0.1:*", "[::1]:*", *allowed_hosts],
        allowed_origins=["http://localhost:*", "http://127.0.0.1:*", "http://[::1]:*", *allowed_origins])
    return StreamableHTTPSessionManager(server, security_settings=security)


async def _body(request, limit):
    raw_length = request.headers.get("content-length")
    try:
        length = int(raw_length) if raw_length is not None else None
    except ValueError as error:
        raise AuthoringError("E_UPLOAD_LENGTH", "Content-Length must be an integer") from error
    if length is None or length < 0:
        raise AuthoringError("E_UPLOAD_LENGTH", "A known Content-Length is required")
    if length > limit:
        raise AuthoringError("E_UPLOAD_SIZE", "Transfer exceeds the configured limit", status=413)
    chunks, size = [], 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > limit or size > length:
            raise AuthoringError("E_UPLOAD_LENGTH", "Actual bytes exceed the declared length", status=413)
        chunks.append(chunk)
    if size != length:
        raise AuthoringError("E_UPLOAD_LENGTH", "Actual bytes differ from Content-Length")
    return b"".join(chunks), length


def create_app(data_dir: Path, token: str, targets=None, *, enable_mcp=True, allowed_origins=(),
               allowed_hosts=(), service=None, **settings):
    if not isinstance(token, str) or not token:
        raise ValueError("Configure a workspace credential before starting the authoring service")
    business = service or AuthoringService(data_dir, targets, **settings)
    manager = _mcp_manager(business, allowed_origins, allowed_hosts) if enable_mcp else None

    @asynccontextmanager
    async def lifespan(app):
        if manager is None:
            yield
        else:
            async with manager.run():
                yield

    app = FastAPI(title="EMO independent project authoring", lifespan=lifespan)
    app.state.authoring = business
    app.add_middleware(WorkspaceBoundary, token=token, allowed_origins=allowed_origins)

    @app.exception_handler(AuthoringError)
    async def authoring_error(request, error):
        return JSONResponse(error.as_dict(), status_code=error.status)

    @app.exception_handler(json.JSONDecodeError)
    async def invalid_json(request, error):
        return JSONResponse({"code": "E_INPUT", "message": "Expected valid JSON"}, status_code=400)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request, error):
        return JSONResponse({"code": "E_INPUT", "message": "Invalid request fields",
                             "diagnostics": [{"code": "E_INPUT", "message": item["msg"],
                                              "fieldPath": ".".join(map(str, item["loc"]))}
                                             for item in error.errors()]}, status_code=422)

    @app.get("/api/authoring-capabilities")
    async def capabilities():
        return await business.invoke("get_authoring_capabilities", {})

    @app.get("/api/project-catalog/{target_id}/operators")
    async def operators(target_id: str, query: str | None = None, category: str | None = None):
        args = {"targetId": target_id}
        if query is not None:
            args["query"] = query
        if category is not None:
            args["category"] = category
        return await business.invoke("list_operators", args)

    @app.get("/api/project-catalog/{target_id}/operators/{operator_id}")
    async def operator(target_id: str, operator_id: str):
        return await business.invoke("get_operator", {"targetId": target_id, "operatorId": operator_id})

    @app.get("/api/project-catalog/{target_id}/device-types")
    async def devices(target_id: str):
        return await business.invoke("list_device_types", {"targetId": target_id})

    @app.get("/api/project-catalog/{target_id}/device-types/{type_id}")
    async def device(target_id: str, type_id: str):
        return await business.invoke("get_device_type", {"targetId": target_id, "typeId": type_id})

    @app.post("/api/project-drafts")
    async def create(request: Request):
        return await business.invoke("create_project_draft", await request.json())

    @app.get("/api/project-drafts")
    async def drafts():
        return await business.invoke("list_project_drafts", {})

    @app.post("/api/project-drafts/imports")
    async def import_file(request: Request, filename: str):
        content, _ = await _body(request, business.max_transfer_bytes)
        return await asyncio.to_thread(business.files.import_project, content, filename)

    @app.get("/api/project-drafts/{draft_id}")
    async def read(draft_id: str):
        return await business.invoke("get_project_draft", {"draftId": draft_id})

    @app.get("/api/project-drafts/{draft_id}/output-choices")
    async def output_choices(draft_id: str):
        return await business.invoke("get_output_choices", {"draftId": draft_id})

    @app.get("/api/project-drafts/{draft_id}/presentation")
    async def presentation(draft_id: str):
        return await business.invoke("get_project_presentation", {"draftId": draft_id})

    @app.post("/api/project-drafts/{draft_id}/resources")
    async def upload(request: Request, draft_id: str, name: str, purpose: str, resourceId: str | None = None):
        if request.headers.get("content-type", "").split(";")[0].strip() != "application/octet-stream":
            raise AuthoringError("E_UPLOAD_TYPE", "Upload raw bytes as application/octet-stream", status=415)
        revision = request.headers.get("x-expected-draft-revision")
        if not revision:
            raise AuthoringError("E_REVISION", "X-Expected-Draft-Revision is required")
        content, length = await _body(request, business.max_transfer_bytes)
        return await asyncio.to_thread(business.files.upload, draft_id, revision, name, purpose, content,
                                       content_length=length, checksum=request.headers.get("x-content-sha256"),
                                       resource_id=resourceId)

    # These routes are adapters of exactly the same named business operations as MCP.
    def operation(tool_name):
        async def handler(request: Request):
            arguments = await request.json()
            if not isinstance(arguments, dict):
                raise AuthoringError("E_INPUT", "Expected a JSON object")
            arguments = {**arguments, "draftId": request.path_params["draft_id"]}
            return JSONResponse(await business.invoke(tool_name, arguments))
        return handler

    for path, tool in (("commands", "apply_project_command"), ("undo", "undo_project_edit"),
                       ("redo", "redo_project_edit"), ("save", "save_project_draft"),
                       ("validate", "validate_project_draft"), ("precheck", "precheck_project_draft"),
                       ("exports", "export_project_draft")):
        app.add_route("/api/project-drafts/{draft_id}/" + path, operation(tool), methods=["POST"])

    @app.get("/api/project-exports/{export_id}/content")
    async def download(export_id: str):
        path, metadata = await asyncio.to_thread(business.files.export_content, export_id)
        return FileResponse(path, filename=metadata["filename"], media_type=metadata["mediaType"])

    if manager is not None:
        app.router.routes.append(Route("/mcp", _MCPApplication(manager), methods=["POST", "GET", "DELETE"]))
    return app
