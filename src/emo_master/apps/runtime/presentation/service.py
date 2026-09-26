"""Explicit facade over the existing Runtime, JobManager and spawn Supervisor."""
import multiprocessing
from pathlib import Path
import threading
from uuid import uuid4

from emo_master.apps.runtime.jobs.models import JobProcessSpec
from emo_master.apps.runtime.presentation.preparation import prepare
from emo_master.apps.runtime.presentation.store import ResultStore


class PresentationService:
    def __init__(self, runtime, root: Path):
        self.runtime = runtime
        self.root = root
        self.runtimeInstanceId = str(uuid4())
        self.prepared: dict = {}
        self.jobs: dict = {}
        self.store = ResultStore()
        self.lock = threading.RLock()
        self.context = multiprocessing.get_context("spawn")
        self.previousTerminal = runtime.jobSupervisor.terminalCallback
        runtime.jobSupervisor.presentationCallback = self.consume
        runtime.jobSupervisor.terminalCallback = self.terminal

    def prepare(self, project, resourceRoot, **kwargs):
        with self.lock:
            if len(self.prepared) >= 8:
                raise ValueError("prepared record quota exceeded")
            record = prepare(project, self.runtime.pluginScanResult.activeOperators, self.root, resourceRoot, **kwargs)
            self.prepared[record.snapshot.snapshotId] = record
            return record

    def start(self, preparedId):
        with self.lock:
            if len(self.jobs) >= 2:
                raise ValueError("display Job quota exceeded; explicitly release a terminal Job")
            prepared = self.prepared[preparedId]
            snapshot = prepared.snapshot
            from emo_master.core.project.models import ProjectDocument
            document = ProjectDocument.model_validate_json(prepared.projectPath.read_text(encoding="utf-8"))
            job = self.runtime.jobManager.createJob(snapshot.projectId, document.project.revision, document.entryWorkflowId)
            config = {"plan": prepared.sourceJson, "credits": self.context.BoundedSemaphore(8),
                      "rejected": self.context.Value("Q", 0), "identity": {
                          "runtimeInstanceId": self.runtimeInstanceId, "jobId": job.jobId,
                          "executionRevision": snapshot.executionRevision,
                          "capturePlanRevision": snapshot.capturePlanRevision, "mode": snapshot.mode}}
            self.jobs[job.jobId] = config
            workspace = self.root / "jobs" / job.jobId
            workspace.mkdir(parents=True)
            try:
                self.runtime.jobManager.start(job, JobProcessSpec(
                    job.jobId, str(prepared.projectPath), document.entryWorkflowId,
                    pluginRootPaths=tuple(self.runtime.pluginRootPaths), jobWorkspacePath=str(workspace),
                    projectId=snapshot.projectId, runtimeDbPath=snapshot.runtimeDbPath, presentation=config))
            except BaseException:
                self.runtime.jobRepository.update(job.jobId, status="FAILED")
                self.jobs.pop(job.jobId)
                raise
            return job.jobId

    def consume(self, jobId, event):
        if event["eventType"] == "display.open":
            self.store.begin(event["item"])
        elif event["eventType"] == "display.seal":
            if self.store.close(event["key"], event["sources"], event["terminal"]):
                self.jobs[jobId]["credits"].release()

    def terminal(self, jobId, status):
        terminal = {"COMPLETED": "COMPLETED", "ABORTED": "CANCELLED"}.get(status, "FAILED")
        self.store.fence(jobId, terminal)
        if self.previousTerminal:
            self.previousTerminal(jobId, status)

    def release(self, jobId):
        with self.lock:
            job = self.runtime.jobRepository.get(jobId)
            if job is not None and not job.isTerminal:
                raise ValueError("cannot release a running Job")
            self.jobs.pop(jobId, None)
            with self.store.lock:
                self.store.history = type(self.store.history)(r for r in self.store.history if r.identity.jobId != jobId)
                for table in (self.store.latest, self.store.high):
                    for key in list(table):
                        if key[0] == jobId:
                            del table[key]

    def close(self):
        # Does not stop the externally owned Runtime or any Job.
        if any(not self.runtime.jobRepository.get(job).isTerminal for job in self.jobs):
            raise ValueError("Runtime owner must stop Jobs before disposing presentation service")
        for job in list(self.jobs):
            self.release(job)
