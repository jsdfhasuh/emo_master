"""Explicit facade over the existing Runtime, JobManager and spawn Supervisor."""
import multiprocessing
import json
from pathlib import Path
import threading
import time
from uuid import uuid4

from emo_master.apps.runtime.jobs.models import JobProcessSpec
from emo_master.apps.runtime.presentation.preparation import prepare
from emo_master.apps.runtime.presentation.store import ResultStore
from emo_master.apps.runtime.presentation.assets import AssetStore
from emo_master.apps.runtime.presentation.exporter import ExportPool
from emo_master.apps.runtime.presentation.collector import unavailable


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
        self.assets = AssetStore(root / "assets")
        self.exporter: ExportPool | None = None
        self.pending: dict = {}
        self.stop = threading.Event()
        self.monitor = threading.Thread(target=self._monitor, name="display-seal-monitor")
        self.monitor.start()
        self.previousTerminal = runtime.jobSupervisor.terminalCallback
        runtime.jobSupervisor.presentationCallback = self.consume
        runtime.jobSupervisor.terminalCallback = self.terminal

    def prepare(self, project, resourceRoot, **kwargs):
        with self.lock:
            if len(self.prepared) >= 8:
                raise ValueError("prepared record quota exceeded")
            record = prepare(project, self.runtime.pluginScanResult.activeOperators, self.root, resourceRoot, **kwargs)
            self.prepared[record.snapshot.snapshotId] = record
            if any(s["expectedType"] == "image" for s in json.loads(record.sourceJson)["sources"].values()) and self.exporter is None:
                self.exporter = ExportPool(self.root / "staging", self._exported)
            return record

    def start(self, preparedId):
        with self.lock:
            if len(self.jobs) >= 2:
                raise ValueError("display Job quota exceeded; explicitly release a terminal Job")
            prepared = self.prepared[preparedId]
            prepared.verify()
            snapshot = prepared.snapshot
            from emo_master.core.project.models import ProjectDocument
            document = ProjectDocument.model_validate_json(prepared.projectPath.read_text(encoding="utf-8"))
            job = self.runtime.jobManager.createJob(snapshot.projectId, document.project.revision, document.entryWorkflowId)
            config = {"plan": prepared.sourceJson, "credits": self.context.BoundedSemaphore(8),
                      "slots": [],
                      "scopeIds": list(json.loads(prepared.sourceJson)["scopes"]),
                      "ordinals": self.context.Array("Q", 16, lock=False),
                      "rejected": self.context.Value("Q", 0), "identity": {
                          "runtimeInstanceId": self.runtimeInstanceId, "jobId": job.jobId,
                          "executionRevision": snapshot.executionRevision,
                          "capturePlanRevision": snapshot.capturePlanRevision, "mode": snapshot.mode}}
            if self.exporter:
                used = {slot["index"] for active in self.jobs.values() for slot in active["slots"]}
                # Static slot ownership makes a worker dying during the copy
                # reclaimable even if it never manages to send a descriptor.
                config["slots"] = [next(s for s in self.exporter.descriptors() if s["index"] not in used)]
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
        with self.store.lock:
            if event["eventType"] == "display.open":
                self.store.begin(event["item"])
                self.pending[event["item"]["identity"]["resultKey"]] = {"job": jobId, "exports": {}}
            elif event["eventType"] == "display.image":
                if self.exporter is not None:
                    self.exporter.submit(event["descriptor"])
            elif event["eventType"] == "display.seal":
                pending = self.pending.get(event["key"])
                if pending is not None and "seal" not in pending:
                    pending["seal"] = event
                    pending["deadline"] = event["sealedAt"] + .5
                    self._finish(event["key"])

    def _exported(self, task, path, outcome):
        with self.store.lock:
            pending = self.pending.get(task["key"])
            if pending is None:
                return  # export owner deletes orphan staging after this callback
            source = unavailable(task["sourceId"], outcome if outcome != "AVAILABLE" else "EXPORT_FAILED")
            if path is not None:
                try:
                    image = self.assets.adopt(path, task["jobId"], task["key"], task["provenance"])
                    source = {"sourceId": task["sourceId"], "state": "AVAILABLE", "image": image}
                except (OSError, ValueError):
                    source = unavailable(task["sourceId"], "EXPORT_FAILED")
            pending["exports"][task["sourceId"]] = source
            self._finish(task["key"])

    def _finish(self, key):
        pending = self.pending.get(key)
        if pending is None or "seal" not in pending:
            return
        seal = pending["seal"]
        sources = []
        for source in seal["sources"]:
            if source.get("pendingImage"):
                exported = pending["exports"].get(source["sourceId"])
                if exported is None:
                    if time.monotonic() < pending["deadline"]:
                        return
                    exported = unavailable(source["sourceId"], "EXPORT_TIMEOUT")
                sources.append(exported)
            else:
                sources.append(source)
        if self.store.close(key, sources, seal["terminal"]):
            self.jobs[pending["job"]]["credits"].release()
        del self.pending[key]
        self._retain()

    def _retain(self):
        self.assets.retain({r.identity.resultKey for r in self.store.history} | set(self.pending))

    def _monitor(self):
        while not self.stop.wait(.02):
            with self.store.lock:
                for job, config in list(self.jobs.items()):
                    for index, scope in enumerate(config["scopeIds"]):
                        ordinal = config["ordinals"][index]
                        address = (job, scope)
                        if ordinal > self.store.high.get(address, 0):
                            self.store.high[address] = ordinal
                            self.store.latest.pop(address, None)
                            self.store._notify()
                for key in list(self.pending):
                    self._finish(key)
                self.assets.stats()  # expire abandoned finite leases without another request

    def terminal(self, jobId, status):
        terminal = {"COMPLETED": "COMPLETED", "ABORTED": "CANCELLED"}.get(status, "FAILED")
        with self.store.lock:
            for key, pending in list(self.pending.items()):
                if pending["job"] == jobId and "seal" not in pending:
                    item = self.store.open[key]
                    code = "EXECUTION_CANCELLED" if terminal == "CANCELLED" else "IPC_ERROR"
                    self.store.close(key, [unavailable(s, code) for s in item["expected"]], terminal)
                    self.jobs[jobId]["credits"].release()
                    del self.pending[key]
            self._retain()
        if self.previousTerminal:
            self.previousTerminal(jobId, status)

    def release(self, jobId):
        with self.lock:
            job = self.runtime.jobRepository.get(jobId)
            if job is not None and not job.isTerminal:
                raise ValueError("cannot release a running Job")
            with self.store.lock:
                for key, pending in list(self.pending.items()):
                    if pending["job"] == jobId:
                        raise ValueError("exports still own this Job; release after result closure")
                config = self.jobs.get(jobId)
                if config and self.exporter:
                    for descriptor in config["slots"]:
                        slot = self.exporter.slots[descriptor["index"]]
                        if slot["busy"]:
                            raise ValueError("export slot still owned")
                        while slot["free"].acquire(False):
                            pass
                        slot["free"].release()
                self.jobs.pop(jobId, None)
                self.store.history = type(self.store.history)(r for r in self.store.history if r.identity.jobId != jobId)
                for table in (self.store.latest, self.store.high):
                    for key in list(table):
                        if key[0] == jobId:
                            del table[key]
                self._retain()

    def close(self):
        # Does not stop the externally owned Runtime or any Job.
        if any(not self.runtime.jobRepository.get(job).isTerminal for job in self.jobs):
            raise ValueError("Runtime owner must stop Jobs before disposing presentation service")
        if self.exporter is not None:
            self.exporter.close()
        self.stop.set()
        self.monitor.join(2)
        with self.store.lock:
            for key, pending in list(self.pending.items()):
                pending["deadline"] = 0
                self._finish(key)
        for job in list(self.jobs):
            self.release(job)
        self.assets.close()

    def resourceStats(self):
        # Conservative fixed reservations, not a substitute for measured RSS.
        # At most 2 Jobs, one 8 MiB raw slot per Job; 6x includes encoder/IPC copies.
        exportReservation = 2 * 6 * 8 * 1024 * 1024 if self.exporter else 0
        sharedCapacity = 2 * 8 * 1024 * 1024 if self.exporter else 0
        metadataReservation = len(self.jobs) * 32 * 1024 * 1024
        readReservation = 2 * 8 * 1024 * 1024
        return dict(self.assets.stats(), open_results=len(self.store.open),
                    history_results=len(self.store.history),
                    history_bytes=sum(len(r.model_dump_json().encode()) for r in self.store.history),
                    rejected=sum(c["rejected"].value for c in self.jobs.values()),
                    shared_capacity=sharedCapacity, export_reserved=exportReservation,
                    metadata_reserved=metadataReservation, read_reserved=readReservation,
                    total_reserved=sharedCapacity + exportReservation + metadataReservation + readReservation,
                    limit=256 * 1024 * 1024)
