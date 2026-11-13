"""The selected official MCP protocol cancels work, not project drafts."""
import asyncio
import importlib.metadata
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
from threading import Event
import time

import httpx
import pytest

from emo_master.apps.runtime.grpc_server.service import RuntimeService
from emo_master.apps.runtime.main import createRuntimeServer
from emo_master.apps.runtime.project_config import candidate
from emo_master.authoring import catalog
from emo_master.authoring.http import create_app
from tests.authoring.test_transport import HEADERS, TOKEN, running_http
from tests.authoring.test_transport import real_target as real_target
from tests.runtime.test_candidate_project_precheck import imageProject, package


async def _event(event, timeout=10):
    assert await asyncio.to_thread(event.wait, timeout), "Expected real worker boundary was not reached"


@pytest.mark.parametrize("phase", ["prepare", "stream"])
def test_precheck_preparation_and_reader_keep_files_until_workers_exit(
        tmp_path, monkeypatch, real_target, phase):
    """Both local boundaries remain responsive and retain their actual inputs."""
    from mcp import ClientSession, types
    from mcp.client.streamable_http import streamablehttp_client
    from mcp.shared.exceptions import McpError

    target, runtime = real_target
    app = create_app(tmp_path / "authoring", TOKEN, {"isolated": target})
    service = app.state.authoring
    entered, release, exited = Event(), Event(), Event()
    owned_paths, calls = [], []
    original_zip = service.files._write_zip
    original_file_work = catalog._file_work
    original_precheck = service.catalog.precheck

    def hold(path):
        owned_paths.append(Path(path))
        entered.set()
        assert release.wait(20), "Test did not release the candidate worker"

    def blocking_zip(directory, document, destination):
        original_zip(directory, document, destination)
        try:
            hold(destination)
        finally:
            exited.set()

    async def controlled_file_work(function, *args):
        if phase == "stream" and getattr(function, "__name__", "") == "read":
            def blocking_read(size):
                try:
                    hold(function.__self__.name)
                    return function(size)
                finally:
                    exited.set()
            return await original_file_work(blocking_read, *args)
        return await original_file_work(function, *args)

    async def observed_precheck(*args, **kwargs):
        calls.append(args)
        return await original_precheck(*args, **kwargs)

    if phase == "prepare":
        monkeypatch.setattr(service.files, "_write_zip", blocking_zip)
    monkeypatch.setattr(catalog, "_file_work", controlled_file_work)
    monkeypatch.setattr(service.catalog, "precheck", observed_precheck)

    async def scenario(base):
        async with httpx.AsyncClient(base_url=base, headers=HEADERS, timeout=10) as http:
            payload, image = imageProject(runtime)
            imported = await http.post("/api/project-drafts/imports", params={"filename": "image.zip"},
                                       content=package(payload, image))
            assert imported.status_code == 200, imported.text
            state = imported.json()
            other = (await http.post("/api/project-drafts", json={"name": "Independent"})).json()
            other = (await http.get("/api/project-drafts/" + other["draftId"])).json()
            async with streamablehttp_client(base + "/mcp", headers=HEADERS) as (read, write, _):
                async with ClientSession(read, write) as client:
                    initialized = await client.initialize()
                    assert initialized.protocolVersion == "2025-11-25"
                    changed = await client.call_tool("apply_project_command", {
                        "draftId": state["draftId"], "expectedDraftRevision": state["draftRevision"],
                        "command": {"type": "project.rename", "name": "Keep committed edit"}})
                    assert not changed.isError
                    state = changed.structuredContent
                    state = (await http.get("/api/project-drafts/" + state["draftId"])).json()
                    assert state["project"]["project"]["name"] == "Keep committed edit"
                    request_id = client._request_id
                    pending = asyncio.create_task(client.call_tool("precheck_project_draft", {
                        "draftId": state["draftId"], "expectedDraftRevision": state["draftRevision"],
                        "targetId": "isolated"}))
                    try:
                        await _event(entered)
                        # This HTTP response must arrive while the real local
                        # worker is still held; no timing SLA is assumed.
                        independent = await http.get("/api/project-drafts/" + other["draftId"])
                        assert independent.status_code == 200
                        assert independent.json() == other
                        assert not release.is_set() and not exited.is_set()
                        path = owned_paths[0]
                        assert path.is_file()
                        root = path.parent
                        if phase == "prepare":
                            assert calls == []
                        await client.send_notification(types.ClientNotification(types.CancelledNotification(
                            params=types.CancelledNotificationParams(requestId=request_id,
                                                                   reason="Cancel local candidate work"))))
                        with pytest.raises(McpError, match="Request cancelled"):
                            await asyncio.wait_for(pending, 5)
                        assert not exited.is_set()
                        assert path.is_file() and root.is_dir()
                        current = await http.get("/api/project-drafts/" + state["draftId"])
                        assert current.json() == state
                        assert current.json()["canUndo"]
                    finally:
                        if not pending.done():
                            pending.cancel()
                        release.set()
                    await _event(exited)
                    deadline = time.monotonic() + 10
                    while (root.exists() or service._prechecks) and time.monotonic() < deadline:
                        await asyncio.sleep(.01)
                    assert not root.exists()
                    assert not service._prechecks
                    if phase == "prepare":
                        assert calls == [], "A cancelled preparation must not start the target stage"
            # Reconnection reads the same draft and revision without replaying
            # the already committed operation.
            async with streamablehttp_client(base + "/mcp", headers=HEADERS) as (read, write, _):
                async with ClientSession(read, write) as client:
                    await client.initialize()
                    current = await client.call_tool("get_project_draft", {"draftId": state["draftId"]})
                    assert not current.isError
                    assert current.structuredContent == state
            assert runtime.loadedDocument is None and runtime.jobRepository.all() == []

    try:
        with running_http(app) as base:
            asyncio.run(scenario(base))
    finally:
        release.set()


def test_official_mcp_cancel_preserves_committed_draft_and_work_owned_cleanup(tmp_path, monkeypatch):
    from mcp import ClientSession, types
    from mcp.client.streamable_http import streamablehttp_client
    from mcp.shared.exceptions import McpError

    assert importlib.metadata.version("mcp") == "1.30.0"
    entered, release, exited = Event(), Event(), Event()
    owned_paths = []
    original_compile = candidate.WorkflowCompiler.compile

    def blocking_compile(compiler, document, **kwargs):
        # This holds a real compiler invocation, then executes the real method.
        image = Path(next(node.params["imagePath"] for node in document.workflows["main"].nodes
                          if node.nodeId == "load"))
        owned_paths.append(image)
        entered.set()
        assert release.wait(20), "Test did not release the real compiler"
        try:
            return original_compile(compiler, document, **kwargs)
        finally:
            exited.set()

    runtime = RuntimeService(dbPath=tmp_path / "runtime.db", workspaceRoot=tmp_path / "jobs")
    server, port, _ = createRuntimeServer(port=0, runtimeService=runtime)
    server.start()
    app = create_app(tmp_path / "authoring", TOKEN, {"isolated": f"127.0.0.1:{port}"})
    monkeypatch.setattr(candidate.WorkflowCompiler, "compile", blocking_compile)

    async def scenario(base):
        async with httpx.AsyncClient(base_url=base, headers=HEADERS) as http:
            payload, image = imageProject(runtime)
            imported = await http.post("/api/project-drafts/imports", params={"filename": "image.zip"},
                                       headers={"Content-Type": "application/octet-stream"},
                                       content=package(payload, image))
            assert imported.status_code == 200, imported.text
            state = imported.json()
            draft_id = state["draftId"]
            async with streamablehttp_client(base + "/mcp", headers=HEADERS) as (read, write, _):
                async with ClientSession(read, write) as client:
                    initialized = await client.initialize()
                    assert initialized.protocolVersion == "2025-11-25"
                    changed = await client.call_tool("apply_project_command", {
                        "draftId": draft_id, "expectedDraftRevision": state["draftRevision"],
                        "command": {"type": "project.rename", "name": "Committed before cancellation"}})
                    assert not changed.isError
                    state = changed.structuredContent
                    args = {"draftId": draft_id, "expectedDraftRevision": state["draftRevision"],
                            "targetId": "isolated"}
                    # Fixed to the recorded SDK: this is its next outgoing JSON-RPC
                    # id. No other request is sent until that call reaches compile.
                    request_id = client._request_id
                    pending = asyncio.create_task(client.call_tool("precheck_project_draft", args))
                    try:
                        await _event(entered)
                        image_path = owned_paths[0]
                        candidate_root = image_path.parents[2]
                        assert image_path.is_file()
                        assert server.transport.active["bulk"] == 1
                        await client.send_notification(types.ClientNotification(types.CancelledNotification(
                            params=types.CancelledNotificationParams(requestId=request_id,
                                                                   reason="Cancel this candidate check"))))
                        with pytest.raises(McpError, match="Request cancelled"):
                            await asyncio.wait_for(pending, timeout=5)
                        # The RPC/request is cancelled, but a synchronous compiler
                        # still owns its inputs and admission slot until it exits.
                        assert not exited.is_set()
                        assert image_path.is_file() and candidate_root.is_dir()
                        assert server.transport.active["bulk"] == 1
                        current = await client.call_tool("get_project_draft", {"draftId": draft_id})
                        assert not current.isError
                        assert current.structuredContent["draftRevision"] == state["draftRevision"]
                        assert current.structuredContent["project"]["project"]["name"] == "Committed before cancellation"
                    finally:
                        # A test failure must not leave the real worker blocked.
                        if not pending.done():
                            pending.cancel()
                        release.set()
                    await _event(exited)
                    deadline = time.monotonic() + 10
                    while (candidate_root.exists() or server.transport.active["bulk"]) and time.monotonic() < deadline:
                        await asyncio.sleep(.01)
                    assert not candidate_root.exists()
                    assert server.transport.active["bulk"] == 0
            # A new actual MCP session reads the existing draft without replaying
            # the rename or deleting/recreating any of its state.
            async with streamablehttp_client(base + "/mcp", headers=HEADERS) as (read, write, _):
                async with ClientSession(read, write) as reconnected:
                    await reconnected.initialize()
                    current = await reconnected.call_tool("get_project_draft", {"draftId": draft_id})
                    assert not current.isError
                    assert current.structuredContent["draftRevision"] == state["draftRevision"]
                    assert current.structuredContent["canUndo"]
                    checked = await reconnected.call_tool("precheck_project_draft", args)
                    assert not checked.isError, checked.content
                    assert checked.structuredContent["status"] == "PASSED"
                    assert checked.structuredContent["executionStatus"] == "not_run"
                    assert checked.structuredContent["runtimeInstanceId"] == runtime.runtimeInstanceId
            assert runtime.loadedDocument is None and runtime.jobRepository.all() == []

    try:
        with running_http(app) as base:
            asyncio.run(scenario(base))
    finally:
        release.set()
        server.stop(0).wait()
        runtime.close()


def test_cli_no_mcp_runs_without_optional_sdk_or_qt_imports(tmp_path):
    """A fresh process enforces missing optional modules before app bootstrap."""
    hook = tmp_path / "import-hook"
    hook.mkdir()
    report = hook / "attempts.json"
    report.write_text("[]", encoding="utf-8")
    (hook / "sitecustomize.py").write_text(
        "import importlib.abc, json, os, sys\n"
        "from pathlib import Path\n"
        "class MissingOptionalModules(importlib.abc.MetaPathFinder):\n"
        "    def find_spec(self, fullname, path=None, target=None):\n"
        "        if fullname.split('.')[0] in {'mcp', 'PySide2'}:\n"
        "            report = Path(os.environ['AUTHORING_IMPORT_REPORT'])\n"
        "            attempted = json.loads(report.read_text(encoding='utf-8'))\n"
        "            report.write_text(json.dumps(attempted + [fullname]), encoding='utf-8')\n"
        "            raise ModuleNotFoundError('Optional dependency unavailable: ' + fullname)\n"
        "sys.meta_path.insert(0, MissingOptionalModules())\n", encoding="utf-8")
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    env = dict(os.environ)
    repo_src = str(Path(__file__).resolve().parents[2] / "src")
    env["PYTHONPATH"] = os.pathsep.join([str(hook), repo_src])
    env["AUTHORING_IMPORT_REPORT"] = str(report)
    env["EMO_WORKSPACE_TOKEN"] = TOKEN
    process = subprocess.Popen([sys.executable, "-m", "emo_master.authoring", "--no-mcp",
                                "--data-dir", str(tmp_path / "standalone"), "--port", str(port)],
                               env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        with httpx.Client(base_url=f"http://127.0.0.1:{port}", headers=HEADERS, timeout=.5) as client:
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    pytest.fail("--no-mcp process exited: " + process.communicate(timeout=1)[0])
                try:
                    ready = client.get("/api/authoring-capabilities")
                    if ready.status_code == 200:
                        break
                except httpx.TransportError:
                    pass
                time.sleep(.02)
            else:
                pytest.fail("--no-mcp HTTP service did not become ready")
            assert client.get("/mcp").status_code == 404
            created = client.post("/api/project-drafts", json={"name": "No MCP or Qt"})
            assert created.status_code == 200
            state = created.json()
            loaded = client.get("/api/project-drafts/" + state["draftId"])
            assert loaded.status_code == 200
            assert loaded.json()["project"]["project"]["name"] == "No MCP or Qt"
            assert loaded.json()["project"]["schemaVersion"] == "2.9"
            assert json.loads(report.read_text(encoding="utf-8")) == []
    finally:
        process.terminate()
        try:
            process.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate(timeout=5)
