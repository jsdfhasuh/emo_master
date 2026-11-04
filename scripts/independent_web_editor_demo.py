"""Explicit local software Runtime for the independent Web editor acceptance.

Creates a current-format project containing two white rectangles and installed
image/blob/count/compare operators. No camera, PLC or custom operator is used.
The project has no Web page: those are created and published in the Web editor.
Run in the repository's Python 3.10 Runtime environment with PYTHONPATH=src.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
from pathlib import Path
import sys
import time


PROJECT_ID = "independent-web-editor-software-demo"


def create_demo_project(directory: Path, registry, *, continuous: bool = True):
    from PIL import Image, ImageDraw
    from emo_master.apps.designer.state.workflow_store import WorkflowStore
    from emo_master.apps.designer.state.project_store import saveProject, loadProjectDocument
    from emo_master.core.project.models import ProjectDocument
    from emo_master.core.workflow.compiler import WorkflowCompiler

    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "web-editor-demo.emoproj"
    if path.exists():
        document = loadProjectDocument(path)
        if document.project.projectId != PROJECT_ID or document.devices.definitions:
            raise ValueError("The selected directory is not this software demo")
        WorkflowCompiler(operatorRegistry=registry).compile(document)
        return path, document
    if any(directory.glob("*.emoproj")) or (directory / "project.json").exists():
        raise ValueError("Choose a new demo directory, separate from existing projects")
    image = Image.new("RGB", (320, 240), "black")
    draw = ImageDraw.Draw(image)
    draw.rectangle((40, 40, 79, 79), fill="white")
    draw.rectangle((160, 120, 199, 179), fill="white")
    image.save(directory / "input.png")
    data = (directory / "input.png").read_bytes()
    payload = WorkflowStore().toPayload("独立 Web 编辑器 · 本机模拟")
    payload["project"]["projectId"] = PROJECT_ID
    workflow = payload["workflows"]["main"]
    workflow["name"] = "模拟检测"
    workflow["nodes"][1:1] = [
        dict(nodeId="load", operatorId="vision.io.image_loader", displayName="测试图片",
             params={"imagePath": "input.png"}),
        dict(nodeId="blob", operatorId="vision.analysis.blob", displayName="目标检测",
             params={"drawOverlay": True, "includeContour": False}),
        dict(nodeId="count", operatorId="vision.collection.count", displayName="数量",
             params={"collectionType": "blobs"}),
        dict(nodeId="judge", operatorId="vision.compare.number", displayName="判定",
             params={"operator": "eq", "rightValue": 2.0}),
    ]
    workflow["edges"] = [
        dict(fromNode="load", fromPort="image", toNode="blob", toPort="image"),
        dict(fromNode="load", fromPort="frame", toNode="blob", toPort="frame"),
        dict(fromNode="blob", fromPort="blobs", toNode="count", toPort="blobs"),
        dict(fromNode="count", fromPort="count", toNode="judge", toPort="left"),
    ]
    payload["dependencies"]["operators"] = [
        dict(operatorId=key, version=registry[key].manifest.version)
        for key in sorted({node["operatorId"] for node in workflow["nodes"] if node.get("operatorId")})]
    payload["resources"]["items"]["input"] = dict(
        path="input.png", sha256=hashlib.sha256(data).hexdigest(), size=len(data), purpose="input_image")
    payload["resources"]["parameterBindings"] = [dict(
        target=dict(workflowId="main", nodeId="load", parameterPath=["imagePath"]), resourceId="input")]
    payload["production"].update(autoStart=False, mode="continuous" if continuous else "single",
                                 cycleIntervalMs=250)
    document = ProjectDocument.model_validate(payload)
    assert document.schemaVersion == "2.9" and document.presentation.schemaVersion == "1.2"
    saveProject(path, document.model_dump(mode="json"))
    reloaded = loadProjectDocument(path)
    assert reloaded == document
    WorkflowCompiler(operatorRegistry=registry).compile(reloaded)
    return path, reloaded


def serve(directory: Path, *, port: int = 50061, single: bool = False):
    from emo_master.apps.runtime.grpc_server.service import RuntimeService
    from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb
    from emo_master.apps.runtime.main import createRuntimeServer

    directory = directory.resolve()
    directory.mkdir(parents=True, exist_ok=True)
    with contextlib.redirect_stdout(sys.stderr):
        runtime = RuntimeService(dbPath=directory / "runtime.sqlite3", workspaceRoot=directory / "jobs",
                                 productionMode=not single)
        try:
            path, document = create_demo_project(directory / "project", runtime.pluginScanResult.activeOperators,
                                                  continuous=not single)
            loaded = runtime.LoadProject(pb.LoadProjectRequest(project_path=str(path)), None)
            if not loaded.ok:
                raise RuntimeError(loaded.message)
            server, actual_port, _ = createRuntimeServer(port=port, runtimeService=runtime)
            server.start()
        except BaseException:
            runtime.close()
            raise
    print(json.dumps(dict(event="ready", address=f"127.0.0.1:{actual_port}", projectId=document.project.projectId,
                          projectFile=str(path), syntheticOnly=True), ensure_ascii=False), flush=True)
    try:
        while not (directory / "stop-requested").exists():
            time.sleep(.1)
    except KeyboardInterrupt:
        pass
    finally:
        with contextlib.redirect_stdout(sys.stderr):
            server.stop(0).wait()
            runtime.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True, help="New isolated demo state directory")
    parser.add_argument("--port", type=int, default=50061)
    parser.add_argument("--single", action="store_true", help="One detection per explicit Start")
    arguments = parser.parse_args()
    serve(arguments.directory, port=arguments.port, single=arguments.single)
