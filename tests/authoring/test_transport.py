"""New public entrypoints: actual HTTP sockets and official MCP client negotiation."""
import asyncio
from contextlib import contextmanager
import hashlib
import io
import socket
from threading import Thread
import time
import zipfile

from fastapi.testclient import TestClient
import httpx
from PIL import Image
import pytest
import uvicorn

from emo_master.authoring.http import create_app
from emo_master.apps.runtime.grpc_server.service import RuntimeService
from emo_master.apps.runtime.main import createRuntimeServer


TOKEN = "only-an-isolated-test-credential"
HEADERS = {"Authorization": "Bearer " + TOKEN}


@contextmanager
def running_http(app):
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(20)
    port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="error", access_log=False))
    thread = Thread(target=lambda: server.run(sockets=[listener]), daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 15
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(.01)
        assert server.started
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=15)
        listener.close()
        assert not thread.is_alive(), "HTTP/MCP lifecycle did not exit"


@pytest.fixture
def real_target(tmp_path):
    runtime = RuntimeService(dbPath=tmp_path / "runtime.db", workspaceRoot=tmp_path / "jobs")
    server, port, _ = createRuntimeServer(port=0, runtimeService=runtime)
    server.start()
    try:
        yield f"127.0.0.1:{port}", runtime
    finally:
        server.stop(0).wait()
        runtime.close()


def test_http_without_optional_mcp_auth_origin_and_input_contract(tmp_path):
    with TestClient(create_app(tmp_path, TOKEN, enable_mcp=False)) as client:
        assert client.get("/api/authoring-capabilities").status_code == 401
        assert client.get("/mcp", headers=HEADERS).status_code == 404
        assert client.get("/api/authoring-capabilities", headers={**HEADERS, "Origin": "https://untrusted.invalid"}).status_code == 403
        assert client.get("/api/authoring-capabilities", headers={**HEADERS, "Origin": "http://localhost:9000"}).status_code == 200
        caps = client.get("/api/authoring-capabilities", headers=HEADERS).json()
        assert caps["fileTransfer"]["resourceUpload"]["purposeValues"] == ["input_image", "model", "page_image", "icon"]
        state = client.post("/api/project-drafts", headers=HEADERS, json={"name": "HTTP"}).json()
        assert state["project"]["schemaVersion"] == "2.9"
        path = "/api/project-drafts/" + state["draftId"]
        state = client.get(path, headers=HEADERS).json()
        bad = client.post(path + "/commands", headers=HEADERS, json={
            "expectedDraftRevision": state["draftRevision"], "command": {"type": "node.create"}})
        assert bad.status_code == 400 and bad.json()["code"] == "E_INPUT"
        assert client.get(path, headers=HEADERS).json() == state
        upload = client.post(path + "/resources?name=a.png&purpose=input_image", content=b"abc",
                             headers={**HEADERS, "Content-Type": "application/octet-stream",
                                      "X-Expected-Draft-Revision": state["draftRevision"],
                                      "X-Content-SHA256": "0" * 64})
        assert upload.status_code == 422
        assert client.get(path, headers=HEADERS).json() == state


def test_live_mcp_http_candidate_and_file_roundtrip(tmp_path, real_target):
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client
    target, runtime = real_target
    app = create_app(tmp_path / "authoring", TOKEN, {"local": target})

    async def scenario(base):
        async with httpx.AsyncClient(base_url=base, headers=HEADERS) as http:
            async with streamablehttp_client(base + "/mcp", headers=HEADERS) as (read, write, _):
                async with ClientSession(read, write) as client:
                    initialized = await client.initialize()
                    assert initialized.protocolVersion == "2025-11-25"
                    tools = await client.list_tools()
                    commands = next(tool for tool in tools.tools if tool.name == "apply_project_command")
                    assert commands.inputSchema["properties"]["command"]["oneOf"]

                    async def call(name, args=None):
                        result = await client.call_tool(name, args or {})
                        assert not result.isError, result.content
                        return result.structuredContent

                    listing = await call("list_operators", {"targetId": "local", "query": "threshold"})
                    assert listing["runtimeInstanceId"] == runtime.runtimeInstanceId
                    threshold = await call("get_operator", {"targetId": "local", "operatorId": "vision.preprocess.threshold"})
                    loader = await call("get_operator", {"targetId": "local", "operatorId": "vision.io.image_loader"})
                    assert threshold["paramSchema"]["properties"]["blockSize"]["xOdd"]
                    assert threshold["outputPorts"]["threshold"]["nullable"]
                    assert threshold["nextCycleParameterPaths"] == list(runtime.pluginScanResult.activeOperators[threshold["operatorId"]].manifest.nextCycleParameterPaths)
                    state = await call("create_project_draft", {"name": "Public image pipeline"})
                    draft_id = state["draftId"]

                    async def edit(command):
                        nonlocal state
                        state = await call("apply_project_command", {"draftId": draft_id,
                                           "expectedDraftRevision": state["draftRevision"], "command": command})
                        return state

                    await edit({"type": "node.create", "workflowId": "main", "targetId": "local",
                                "operatorId": loader["operatorId"], "nodeId": "loader"})
                    params = {name: spec["default"] for name, spec in threshold["paramSchema"].get("properties", {}).items()
                              if "default" in spec}
                    await edit({"type": "node.create", "workflowId": "main", "targetId": "local",
                                "operatorId": threshold["operatorId"], "nodeId": "threshold", "params": params})
                    await edit({"type": "edge.connect", "workflowId": "main", "fromNode": "loader",
                                "fromPort": "image", "toNode": "threshold", "toPort": "image"})
                    args = {"draftId": draft_id, "expectedDraftRevision": state["draftRevision"], "targetId": "local"}
                    missing_result = await client.call_tool("precheck_project_draft", args)
                    assert not missing_result.isError
                    assert missing_result.structuredContent["status"] == "INCOMPLETE"
                    assert missing_result.structuredContent["executionStatus"] == "not_run"
                    pixels = io.BytesIO()
                    Image.new("RGB", (12, 10), (120, 120, 120)).save(pixels, format="PNG")
                    content = pixels.getvalue()
                    uploaded = await http.post(f"/api/project-drafts/{draft_id}/resources", params={"name": "input.png", "purpose": "input_image"},
                                               headers={"Content-Type": "application/octet-stream", "X-Expected-Draft-Revision": state["draftRevision"],
                                                        "X-Content-SHA256": hashlib.sha256(content).hexdigest()}, content=content)
                    assert uploaded.status_code == 200, uploaded.text
                    received = uploaded.json()
                    state["draftRevision"] = received["draftRevision"]
                    await edit({"type": "resource.bind", "resourceId": received["resourceId"],
                                "target": {"workflowId": "main", "nodeId": "loader", "parameterPath": ["imagePath"]}})
                    choices = await call("get_output_choices", {"draftId": draft_id})
                    assert "mask" in str(choices)
                    local = await call("validate_project_draft", {"draftId": draft_id, "expectedDraftRevision": state["draftRevision"]})
                    assert local["scope"] == "local" and local["compileStatus"] == "not_run"
                    report = await call("precheck_project_draft", {"draftId": draft_id, "expectedDraftRevision": state["draftRevision"], "targetId": "local"})
                    assert report["status"] == "PASSED", report
                    assert report["compileStatus"] == "passed"
                    assert report["runtimeInstanceId"] == runtime.runtimeInstanceId
                    assert report["draftRevision"] != missing_result.structuredContent["draftRevision"]
                    invalid = await client.call_tool("apply_project_command", {"draftId": draft_id, "expectedDraftRevision": "stale",
                        "command": {"type": "project.rename", "name": "must not happen"}})
                    assert invalid.isError and invalid.structuredContent["draftRevision"] == state["draftRevision"]
                    state = await call("save_project_draft", {"draftId": draft_id, "expectedDraftRevision": state["draftRevision"]})
                    exported = await call("export_project_draft", {"draftId": draft_id, "expectedDraftRevision": state["draftRevision"], "format": "project-zip"})
                    download = await http.get(exported["download"]["path"])
                    assert download.status_code == 200
                    assert len(download.content) == exported["sizeBytes"]
                    assert hashlib.sha256(download.content).hexdigest() == exported["sha256"]
                    assert TOKEN.encode() not in download.content
                    with zipfile.ZipFile(io.BytesIO(download.content)) as package:
                        assert any(name.endswith(".emoproj") for name in package.namelist())
                    anonymous = await http.get(exported["download"]["path"], headers={"Authorization": "Bearer wrong"})
                    assert anonymous.status_code == 401
            # Connection closure does not own or remove the saved draft.
            current = (await http.get(f"/api/project-drafts/{draft_id}")).json()
            assert current["draftRevision"] == state["draftRevision"]
            assert not runtime.jobRepository.all()

    with running_http(app) as base:
        asyncio.run(scenario(base))
