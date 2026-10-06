"""Single owner of the Runtime and its production Job. No Qt imports."""
from __future__ import annotations

import json
import os
from pathlib import Path
import time
from uuid import uuid4

from emo_master.apps.runtime.grpc_server.aio_entry import AioRuntimeServer
from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb
from emo_master.apps.runtime.grpc_server.service import RuntimeService
from emo_master.apps.runtime.presentation.service import PresentationService
from emo_master.core.project.models import ProductionSettings
from emo_master.core.project.delivery_store import DirectoryOwner


def defaultDataRoot() -> Path:
    configured = os.environ.get("EMO_RUNTIME_DATA_DIR")
    if configured:
        return Path(configured).expanduser().resolve()
    base = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / ".local/share")))
    return base / "EmoMaster" / "operator-runtime"


class ProductionRuntime:
    def __init__(self, dataRoot: Path | None = None, *, pluginRootPaths=None):
        self.dataRoot = (dataRoot or defaultDataRoot()).resolve()
        self.runtime = RuntimeService(dbPath=self.dataRoot / "runtime.sqlite3",
            workspaceRoot=self.dataRoot / "jobs", logDirectory=self.dataRoot / "logs",
            pluginRootPaths=pluginRootPaths, productionMode=True, maxConcurrentJobs=1)
        self.server = None
        self.closed = False
        self.jobId = ""
        self.document = None
        self.startUncertain = False
        self.projectOwner = None
        try:
            self.presentation = PresentationService(self.runtime, self.dataRoot / "display")
        except BaseException:
            self.runtime.close()
            raise

    @property
    def address(self):
        if self.server is None:
            self.server = AioRuntimeServer(self.runtime, self.presentation)
        return f"127.0.0.1:{self.server.port}"

    @property
    def settings(self):
        if self.document is not None and self.document.production is not None:
            return self.document.production
        return ProductionSettings()

    def load(self, path):
        self._releasePrevious()
        root = Path(path).resolve(strict=True)
        root = root if root.is_dir() else root.parent
        if self.projectOwner is None or self.projectOwner.root != root:
            nextOwner = DirectoryOwner(root).acquire()
            if self.projectOwner is not None:
                self.projectOwner.close()
            self.projectOwner = nextOwner
        try:
            reply = self.runtime.LoadProject(pb.LoadProjectRequest(project_path=str(Path(path).resolve())), None)
            if not reply.ok:
                raise ValueError(reply.message)
        except BaseException:
            self.document = None
            self.projectOwner.close()
            self.projectOwner = None
            raise
        self.document = self.runtime.loadedDocument
        # The operator program owns at most one production session.
        self.runtime.jobSupervisor.maxConcurrentJobs = 1
        return self.document

    def installPackage(self, package):
        from emo_master.core.project.runtime_package import installRuntimePackage
        self._releasePrevious()
        if self.projectOwner is None:
            raise ValueError("load the destination project before updating")
        root = self.projectOwner.root
        installRuntimePackage(package, root, owner=self.projectOwner, registry=self.runtime.pluginScanResult.activeOperators)
        return self.load(root)

    def start(self):
        if self.document is None:
            raise ValueError("project not loaded")
        self._releasePrevious()
        capture = bool(self.document.presentation and self.document.presentation.pageOrder)
        requestId = uuid4().hex
        request = pb.StartJobRequest(project_id=self.document.project.projectId,
            workflow_id=self.document.entryWorkflowId,
            inputs_json=json.dumps(self.settings.inputs, ensure_ascii=True),
            legacy_snapshot_policy="NONE", capture_presentation=capture,
            expected_runtime_instance_id=self.runtime.runtimeInstanceId, start_request_id=requestId)
        self.startUncertain = True
        try:
            reply = self.runtime.StartJob(request, None)
        except Exception:
            # Resolve this exact admission, never issue a second Start.
            reply = self.runtime.GetStartRequest(pb.StartRequestLookup(
                runtime_instance_id=self.runtime.runtimeInstanceId, start_request_id=requestId), None)
            if not reply.job_id and reply.status != "REJECTED":
                raise
        self.startUncertain = reply.status in {"UNKNOWN", "RESET_REQUIRED"}
        self.jobId = reply.job_id
        if not reply.ok:
            raise RuntimeError(reply.message)
        return self.jobId

    def status(self):
        if self.startUncertain:
            return dict(state="FAULT", jobId=self.jobId, message="Start outcome unknown; close Runtime and verify device outputs before restarting",
                        canStart=False, canLoad=False)
        if not self.jobId:
            return dict(state="READY" if self.document else "EMPTY", jobId="", message="",
                        canStart=self.document is not None, canLoad=True)
        record = self.runtime.jobRepository.get(self.jobId)
        owned = self.runtime.jobSupervisor.ownsJobResources(self.jobId)
        idle = record is not None and record.isTerminal and not owned
        if record is None:
            state, message = "FAULT", "Job record missing"
        elif record.status == "FAILED" or record.errorCode == "E_STOP_TIMEOUT":
            state, message = "FAULT", f"{record.errorCode}: {record.message}"
        elif idle:
            state, message = "READY", record.message
        else:
            state, message = ("STOPPING" if record.status == "STOPPING" or record.isTerminal else "RUNNING"), ""
        return dict(state=state, jobId=self.jobId, message=message,
                    canStart=idle and self.document is not None, canLoad=idle)

    def stop(self):
        if not self.jobId:
            return
        reply = self.runtime.StopJob(pb.StopJobRequest(job_id=self.jobId, mode="graceful"), None)
        if not reply.ok:
            raise RuntimeError(reply.message)
        grace = self.document.runtime.gracefulStopTimeoutMs / 1000 if self.document else 5
        self._waitRetired(time.monotonic() + grace + 5)

    def _waitRetired(self, deadline):
        while self.runtime.jobSupervisor.ownsJobResources(self.jobId):
            if time.monotonic() >= deadline:
                raise TimeoutError("Worker still owns Runtime resources")
            time.sleep(.02)

    def _releasePrevious(self):
        if self.startUncertain:
            raise RuntimeError("Start outcome unknown; do not issue another Start")
        if not self.jobId:
            return
        record = self.runtime.jobRepository.get(self.jobId)
        if record is None or not record.isTerminal:
            raise ValueError("stop the current Job before loading or starting")
        deadline = time.monotonic() + 5
        self._waitRetired(deadline)
        while True:
            try:
                self.presentation.release(self.jobId)
                break
            except ValueError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(.02)
        self.jobId = ""

    def close(self):
        if self.closed:
            return
        if self.server is not None:
            self.server.close()
            self.server = None
        self.stop()
        self.runtime.close()
        if self.projectOwner is not None:
            self.projectOwner.close()
            self.projectOwner = None
        self.closed = True
