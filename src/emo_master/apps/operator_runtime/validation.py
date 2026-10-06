"""Opt-in synthetic acceptance for source and frozen operator executables."""
from __future__ import annotations

import argparse
from collections import deque
import hashlib
import json
import math
from pathlib import Path
import sys
import time
import traceback

import cv2
import numpy as np
from PySide2.QtCore import QEvent
from PySide2.QtWidgets import QApplication

from emo_master import __version__
from emo_master.apps.operator_runtime.main import OperatorWindow
from emo_master.apps.package_selftest import _checkBuiltins, _checkMigrations, _checkOnnxRuntime
from emo_master.core.project.delivery_store import atomicJson
from emo_master.core.project.models import ProjectDocument
from emo_master.core.project.runtime_package import buildRuntimePackage, installRuntimePackage


def validationProject(root: Path) -> ProjectDocument:
    """Two fixed blobs, no cameras, external writes or private model assets."""
    root.mkdir(parents=True, exist_ok=True)
    pixels = np.zeros((120, 160, 3), np.uint8)
    pixels[20:40, 20:40] = 255
    pixels[60:90, 80:100] = 255
    image = cv2.imencode(".png", pixels)[1].tobytes()
    (root / "input.png").write_bytes(image)
    source = dict(kind="node_output", resultScopeId="root", workflowId="main")
    document = ProjectDocument.model_validate({
        "schemaVersion": "2.3",
        "project": {"projectId": "operator-validation", "name": "Runtime Validation",
                    "createdAt": "2026-10-06T00:00:00Z", "updatedAt": "2026-10-06T00:00:00Z"},
        "entryWorkflowId": "main", "workflowOrder": ["main"],
        "runtime": {"eventRetentionPerJob": 500},
        "production": {"mode": "continuous", "autoStart": True, "cycleIntervalMs": 50},
        "workflows": {"main": {"name": "Synthetic image", "nodes": [
            {"nodeId": "input", "kind": "workflow_input"},
            {"nodeId": "load", "operatorId": "vision.io.image_loader"},
            {"nodeId": "blob", "operatorId": "vision.analysis.blob", "params": {"drawOverlay": True}},
            {"nodeId": "count", "operatorId": "vision.collection.count"},
            {"nodeId": "save", "operatorId": "vision.io.image_saver",
             "params": {"outputPath": "outputs/result.png", "overwrite": True}},
            {"nodeId": "output", "kind": "workflow_output"}], "edges": [
                {"fromNode": "load", "fromPort": "image", "toNode": "blob", "toPort": "image"},
                {"fromNode": "load", "fromPort": "frame", "toNode": "blob", "toPort": "frame"},
                {"fromNode": "blob", "fromPort": "blobs", "toNode": "count", "toPort": "blobs"},
                {"fromNode": "load", "fromPort": "image", "toNode": "save", "toPort": "image"}]}},
        "presentation": {"defaultPageId": "main", "pageOrder": ["main"],
            "pages": {"main": {"name": "Inspection", "components": [
                {"componentId": "count", "type": "number", "bindings": {"value": "count"}},
                {"componentId": "image", "type": "image", "bindings": {"image": "image"}, "layout": {"row": 1}}]}},
            "resultScopes": {"root": {"entryWorkflowId": "main", "scopeWorkflowId": "main"}},
            "dataSources": {"count": dict(source, nodeId="count", port="count", expectedType="integer"),
                            "image": dict(source, nodeId="blob", port="overlay", expectedType="image")}},
        "resources": {"items": {"input": {"path": "input.png", "size": len(image),
            "sha256": hashlib.sha256(image).hexdigest(), "purpose": "input_image"}},
            "parameterBindings": [{"resourceId": "input", "target": {
                "workflowId": "main", "nodeId": "load", "parameterPath": ["imagePath"]}}]},
    })
    atomicJson(root / "project.json", document.model_dump(mode="json"))
    return document


def _wait(app, predicate, seconds=30):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        app.processEvents()
        app.sendPostedEvents(None, QEvent.DeferredDelete)
        if predicate():
            return
        time.sleep(.01)
    raise TimeoutError("operator acceptance deadline")


def _checkWindow(root, duration, restarts, report):
    from PySide2.QtCore import QSettings
    app = QApplication.instance() or QApplication([])
    project = validationProject(root / "engineering")
    package = buildRuntimePackage(root / "engineering", root / "delivery")
    installRuntimePackage(package, root / "project")
    preferences = QSettings(str(root / "preferences.ini"), QSettings.IniFormat)
    window = OperatorWindow(root / "project", dataRoot=root / "data", preferences=preferences)
    window.show()
    sessions = []
    screenshot = root / "operator-runtime.png"
    try:
        for index in range(restarts + 1):
            _wait(app, lambda: window.error or (window.hub is not None and bool(window.pages.displayed)))
            if window.error:
                raise RuntimeError(window.error)
            owner = window.controller
            job = owner.jobId
            _wait(app, lambda: owner.runtime.jobRepository.get(job).status not in {"ACCEPTED", "STARTING"})
            record = owner.runtime.jobRepository.get(job)
            if record.status != "RUNNING":
                raise RuntimeError(f"validation worker did not start: {record.status}: {record.errorCode}")
            pid = record.pid
            if pid <= 0 or window.pages.editing or window.pages.editorHost:
                raise RuntimeError("worker or operator-only page contract failed")
            result = next(iter(window.pages.displayed.values())).result
            if next(s.valueJson for s in result.sources if s.sourceId == "count") != "2":
                raise RuntimeError("synthetic count mismatch")
            if result.identity.jobId != job:
                raise RuntimeError("page contains another Job")
            cursor, completed = 0, 0
            recentIdentities = deque(maxlen=500)
            until = time.monotonic() + (duration if index == 0 else .2)
            while time.monotonic() < until or completed < 4:
                app.processEvents()
                app.sendPostedEvents(None, QEvent.DeferredDelete)
                for event in owner.runtime.eventStore.read(job, afterSequence=cursor):
                    cursor = max(cursor, event.sequence)
                    if event.eventType == "workflow.completed":
                        if event.workflowRunId in recentIdentities:
                            raise RuntimeError("cycle identity repeated")
                        recentIdentities.append(event.workflowRunId)
                        completed += 1
                record = owner.runtime.jobRepository.get(job)
                if record.status != "RUNNING":
                    raise RuntimeError(f"continuous validation unexpectedly stopped: {record.status}: {record.errorCode}")
                report("continuous", workerPid=pid, cycles=completed, jobId=job)
                if time.monotonic() > until + 30:
                    raise TimeoutError("continuous cycles deadline")
                time.sleep(.03)
            workspace = owner.runtime._workspacePaths[job]
            if list(workspace.rglob("*.png")) or not (root / "project/outputs/result.png").is_file():
                raise RuntimeError("output persistence or temporary snapshot contract failed")
            imageView = window.pages.widgets["main"]["image"][1]
            _wait(app, lambda: not imageView.image.isNull())
            if imageView.image.width() != 160 or imageView.image.height() != 120 or imageView.image.pixelColor(30, 30).value() < 100:
                raise RuntimeError("Qt image pixels mismatch")
            if not window.grab().save(str(screenshot)):
                raise RuntimeError("Qt screenshot failed")
            _wait(app, lambda: not window.busy)
            window.stopDetection()
            _wait(app, lambda: window.error or (not window.busy and window.state["canStart"]))
            if window.error or owner.runtime.jobSupervisor.ownsJobResources(job):
                raise RuntimeError(window.error or "worker did not retire")
            terminal = owner.runtime.jobRepository.get(job)
            if terminal.errorCode != "E_CANCELLED":
                raise RuntimeError(f"normal cancellation failed: {terminal.errorCode}")
            retained = len(owner.runtime.eventStore.read(job))
            if retained > project.runtime.eventRetentionPerJob:
                raise RuntimeError("terminal diagnostic retention exceeded")
            sessions.append(dict(jobId=job, pid=pid, cycles=completed, retainedEvents=retained, stopCode=terminal.errorCode))
            if index < restarts:
                window.startDetection()
                _wait(app, lambda: window.error or owner.jobId != job)
        return dict(sessions=sessions, screenshot=str(screenshot), qtPlatform=app.platformName(), packageRoundTrip="PASS")
    finally:
        window.close()
        _wait(app, lambda: window.done)
        if window.controller and not window.controller.closed:
            raise RuntimeError("Runtime did not close")


def runValidationCommand(arguments) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true", required=True)
    parser.add_argument("--result-json", type=Path, required=True)
    parser.add_argument("--duration", type=float, default=1)
    parser.add_argument("--restarts", type=int, default=2)
    args = parser.parse_args(arguments)
    if not math.isfinite(args.duration) or args.duration < 0 or args.restarts < 0:
        parser.error("duration and restarts must be nonnegative")
    args.result_json = args.result_json.resolve()
    args.result_json.parent.mkdir(parents=True, exist_ok=True)
    checks, errors = {}, []
    nextReport = 0.0
    def report(phase, **values):
        nonlocal nextReport
        if phase != "continuous" or time.monotonic() >= nextReport:
            atomicJson(args.result_json, dict(status="running", phase=phase, **values))
            nextReport = time.monotonic() + .5
    for name, check in (("builtins", _checkBuiltins), ("migrations", _checkMigrations),
                        ("onnxruntime", _checkOnnxRuntime)):
        report(name)
        try:
            checks[name] = dict(status="ok", **check())
        except Exception as error:
            checks[name] = dict(status="error", message=str(error))
            errors.append(f"{name}: {error}")
    workspace = args.result_json.parent / (args.result_json.stem + "-workspace")
    try:
        # Keep evidence and project separate from any installed or remembered site project.
        workspace.mkdir(exist_ok=False)
        checks["operatorRuntime"] = dict(status="ok", **_checkWindow(workspace, args.duration, args.restarts, report))
    except Exception as error:
        errors.append(f"operatorRuntime: {error}")
        checks["operatorRuntime"] = dict(status="error", message=str(error), traceback=traceback.format_exc())
    imported = sorted(name for name in sys.modules if name.startswith("emo_master.apps.designer"))
    if imported:
        errors.append("Designer modules imported: " + ", ".join(imported))
    payload = dict(status="error" if errors else "ok", version=__version__,
                   frozen=bool(getattr(sys, "frozen", False)), fixture="synthetic-two-blobs",
                   checks=checks, errors=errors, designerModules=imported,
                   fieldAcceptance="NOT_RUN", durationSeconds=args.duration, restarts=args.restarts)
    atomicJson(args.result_json, payload)
    print(json.dumps(dict(status=payload["status"], result=str(args.result_json))))
    return 1 if errors else 0
