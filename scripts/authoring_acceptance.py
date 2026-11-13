"""External Inspector + HTTP byte-transfer acceptance, using isolated local services."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from copy import deepcopy
import hashlib
import importlib.metadata
import io
import json
from pathlib import Path
import secrets
import socket
import subprocess
import sys
from threading import Thread
import time
import zipfile

import httpx
from PIL import Image
import uvicorn

from emo_master.authoring.http import create_app
from emo_master.apps.runtime.grpc_server.service import RuntimeService
from emo_master.apps.runtime.main import createRuntimeServer
from emo_panel_protocol.generated import runtime_pb2 as pb


@contextmanager
def serve(app):
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(20)
    server = uvicorn.Server(uvicorn.Config(app, log_level="error", access_log=False))
    thread = Thread(target=lambda: server.run(sockets=[listener]), daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 15
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(.01)
        assert server.started, "HTTP lifecycle did not start"
        yield f"http://127.0.0.1:{listener.getsockname()[1]}"
    finally:
        server.should_exit = True
        thread.join(15)
        listener.close()
        assert not thread.is_alive(), "HTTP lifecycle did not stop"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inspector", type=Path, required=True, help="Inspector clients/launcher/build/index.js")
    parser.add_argument("--output", type=Path, required=True, help="New evidence directory")
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists() and any(output.iterdir()):
        parser.error("Use an empty output directory; previous evidence is preserved")
    output.mkdir(parents=True, exist_ok=True)
    token = secrets.token_urlsafe(32)
    runtime = RuntimeService(dbPath=output / "runtime.db", workspaceRoot=output / "jobs")
    grpc_server, port, _ = createRuntimeServer(port=0, runtimeService=runtime)
    grpc_server.start()
    app = create_app(output / "workspace", token, {"isolated": f"127.0.0.1:{port}"})
    report = {"python": sys.version.split()[0], "sdk": importlib.metadata.version("mcp"),
              "client": "MCP Inspector", "fileMode": "Inspector + HTTP helper", "success": False,
              "protocolVersions": [], "steps": [], "designer": "not_run", "hardware": "not_run"}
    package = args.inspector.resolve().parents[3] / "package.json"
    report["clientVersion"] = json.loads(package.read_text())["version"]

    class RecordProtocol:
        async def __call__(self, scope, receive, send):
            if scope["type"] == "http" and scope["path"] == "/mcp":
                version = dict(scope.get("headers", [])).get(b"mcp-protocol-version")
                if version and version.decode() not in report["protocolVersions"]:
                    report["protocolVersions"].append(version.decode())
            return await app(scope, receive, send)

    try:
        with serve(RecordProtocol()) as base, httpx.Client(base_url=base, headers={"Authorization": "Bearer " + token}, timeout=30) as http:
            def external(method, name=None, arguments=None):
                command = ["node", str(args.inspector.resolve()), "--cli", "--server-url", base + "/mcp",
                           "--transport", "http", "--header", "Authorization: Bearer " + token,
                           "--format", "json", "--quiet", "--method", method]
                if name:
                    command += ["--tool-name", name, "--tool-args-json", json.dumps(arguments or {}, ensure_ascii=False)]
                result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", timeout=45)
                if result.returncode:
                    raise RuntimeError(f"Inspector {name or method}: " + result.stderr.replace(token, "[redacted]"))
                envelope = json.loads(result.stdout)
                payload = envelope.get("result", envelope)
                assert not payload.get("isError"), payload.get("structuredContent")
                report["steps"].append(name or method)
                print(name or method, flush=True)
                return payload.get("structuredContent", payload)

            tools = external("tools/list")
            assert any(tool["name"] == "apply_project_command" for tool in tools["tools"])
            caps = external("tools/call", "get_authoring_capabilities")
            listing = external("tools/call", "list_operators", {"targetId": "isolated", "query": "threshold"})
            assert listing["runtimeInstanceId"] == runtime.runtimeInstanceId
            declarations = {operator_id: external("tools/call", "get_operator", {"targetId": "isolated", "operatorId": operator_id})
                            for operator_id in ("vision.io.image_loader", "vision.preprocess.threshold", "vision.preprocess.roi")}
            threshold = declarations["vision.preprocess.threshold"]
            assert threshold["paramSchema"]["properties"]["blockSize"]["xOdd"]
            assert threshold["outputPorts"]["threshold"]["nullable"]
            assert "input_image" in caps["fileTransfer"]["resourceUpload"]["purposeValues"]
            state = external("tools/call", "create_project_draft", {"name": "Inspector Image Project"})
            draft_id = state["draftId"]

            def tool(name, **fields):
                return external("tools/call", name, {"draftId": draft_id, "expectedDraftRevision": state["draftRevision"], **fields})

            def edit(command_type, **fields):
                nonlocal state
                state = tool("apply_project_command", command={"type": command_type, **fields})
                return state

            for operator_id, node_id in (("vision.io.image_loader", "loader"), ("vision.preprocess.threshold", "threshold")):
                properties = declarations[operator_id]["paramSchema"].get("properties", {})
                params = {key: value["default"] for key, value in properties.items() if "default" in value}
                edit("node.create", workflowId="main", targetId="isolated", operatorId=operator_id, nodeId=node_id, params=params)
            edit("edge.connect", workflowId="main", fromNode="loader", fromPort="image", toNode="threshold", toPort="image")
            report["beforeRepair"] = tool("precheck_project_draft", targetId="isolated")
            assert report["beforeRepair"]["status"] == "INCOMPLETE" and report["beforeRepair"]["missingInputs"]
            image = io.BytesIO()
            Image.new("RGB", (32, 24), (170, 170, 170)).save(image, format="PNG")
            content = image.getvalue()
            uploaded = http.post(f"/api/project-drafts/{draft_id}/resources", params={"name": "input.png", "purpose": "input_image"},
                headers={"Content-Type": "application/octet-stream", "X-Expected-Draft-Revision": state["draftRevision"],
                         "X-Content-SHA256": hashlib.sha256(content).hexdigest()}, content=content)
            uploaded.raise_for_status()
            resource = uploaded.json()
            assert resource["sha256"] == hashlib.sha256(content).hexdigest()
            state["draftRevision"] = resource["draftRevision"]
            report["steps"].append("HTTP raw-byte upload and SHA256")
            edit("resource.bind", resourceId=resource["resourceId"], target={"workflowId": "main", "nodeId": "loader", "parameterPath": ["imagePath"]})
            outputs = external("tools/call", "get_output_choices", {"draftId": draft_id})
            assert any(value["nodeId"] == "threshold" and value["port"] == "mask" for value in outputs["outputs"])
            edit("page.create", name="Threshold mask")
            page_id = state["createdIds"]["pageId"]
            presentation = deepcopy(state["project"]["presentation"])
            presentation["resultScopes"]["main-result"] = {"entryWorkflowId": "main", "scopeWorkflowId": "main"}
            presentation["pages"][page_id]["resultScopeIds"] = ["main-result"]
            edit("presentation.configure", presentation=presentation)
            edit("component.put", pageId=page_id, component={"componentId": "mask-view", "type": "image", "props": {"title": "Mask"}})
            edit("component.bind", pageId=page_id, componentId="mask-view", property="image", source={
                "kind": "node_output", "resultScopeId": "main-result", "workflowId": "main", "nodeId": "threshold", "port": "mask", "expectedType": "image"})
            edit("node.move", workflowId="main", nodeId="threshold", position={"x": 420., "y": 120.})
            moved = deepcopy(state["project"])
            state = tool("undo_project_edit")
            assert state["project"] != moved
            state = tool("redo_project_edit")
            assert state["project"] == moved
            report["localValidation"] = tool("validate_project_draft")
            assert report["localValidation"]["status"] == "PASSED", report["localValidation"]
            state = tool("save_project_draft")
            state = tool("adopt_project_declarations", targetId="isolated")
            report["declarationAdoption"] = {"draftRevision": state["draftRevision"],
                                              "context": state["declarationContext"]}
            report["localValidationAfterAdoption"] = tool("validate_project_draft")
            assert report["localValidationAfterAdoption"]["status"] == "PASSED"
            report["afterRepair"] = tool("precheck_project_draft", targetId="isolated")
            assert report["afterRepair"]["status"] == "PASSED", report["afterRepair"]
            assert report["afterRepair"]["draftRevision"] != report["beforeRepair"]["draftRevision"]
            exported = tool("export_project_draft", format="project-zip")
            assert exported["draftRevision"] == report["afterRepair"]["draftRevision"]
            assert "precheck" not in exported
            assert exported["checkReport"] == {"status": "not_attached", "draftRevision": exported["draftRevision"],
                                               "performedByExport": False}
            downloaded = http.get(exported["download"]["path"])
            downloaded.raise_for_status()
            assert len(downloaded.content) == exported["sizeBytes"] and hashlib.sha256(downloaded.content).hexdigest() == exported["sha256"]
            assert http.get(exported["download"]["path"], headers={"Authorization": ""}).status_code == 401
            assert token.encode() not in downloaded.content
            (output / "download.zip").write_bytes(downloaded.content)
            with zipfile.ZipFile(io.BytesIO(downloaded.content)) as package_zip:
                package_zip.extractall(output / "download")  # our own authenticated, validated service export
            project_file = next((output / "download").glob("*.emoproj"))
            report["download"] = exported
            report["projectFile"] = str(project_file)
            report["steps"].append("HTTP authenticated download, SHA256, anonymous denial")
            assert not runtime.jobRepository.all(), "Authoring must not create a Job"
            loaded = runtime.LoadProject(pb.LoadProjectRequest(project_path=str(project_file)), None)
            assert loaded.ok, loaded.message
            started = runtime.StartJob(pb.StartJobRequest(project_id=state["project"]["project"]["projectId"], workflow_id="main"), None)
            assert started.ok, started.message
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                status = runtime.GetJobStatus(pb.GetJobStatusRequest(job_id=started.job_id), None)
                if status.status in {"COMPLETED", "FAILED", "STOPPED"}:
                    break
                time.sleep(.02)
            assert status.status == "COMPLETED", status
            report["isolatedSoftwareRun"] = {"jobId": started.job_id, "status": status.status}
            report["success"] = True
    finally:
        grpc_server.stop(0).wait()
        runtime.close()
        (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("Evidence:", output / "report.json")


if __name__ == "__main__":
    main()
