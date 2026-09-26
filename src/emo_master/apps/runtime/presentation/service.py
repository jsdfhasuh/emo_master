"""Explicit facade over the existing Runtime, JobManager and spawn Supervisor."""
import multiprocessing
from collections import deque
import json
import queue
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
from emo_master.apps.runtime.presentation.mailbox import SharedMailbox


class PresentationService:
    def __init__(self, runtime, root: Path):
        if getattr(runtime, "_presentationOwner", None) is not None:
            raise ValueError("Runtime already has a presentation owner")
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
        self.timings: dict = {}
        self.readers: dict = {}
        self.terminalStates: dict = {}
        self.stop = threading.Event()
        self.monitor = threading.Thread(target=self._monitor, name="display-seal-monitor")
        self.monitor.start()
        self.previousTerminal = runtime.jobSupervisor.terminalCallback
        self.previousPresentation = runtime.jobSupervisor.presentationCallback
        runtime.jobSupervisor.presentationCallback = self.consume
        runtime.jobSupervisor.terminalCallback = self.terminal
        runtime._presentationOwner = self
        self.closed = False

    def prepare(self, project, resourceRoot, **kwargs):
        with self.lock:
            if len(self.prepared) >= 8:
                raise ValueError("prepared record quota exceeded")
            record = prepare(project, self.runtime.pluginScanResult.activeOperators, self.root, resourceRoot, **kwargs)
            try:
                if any(s["expectedType"] == "image" for s in json.loads(record.sourceJson)["sources"].values()) and self.exporter is None:
                    self.exporter = ExportPool(self.root / "staging", self._exported)
            except BaseException:
                import shutil
                shutil.rmtree(record.projectPath.parent)
                if record.snapshot.mode == "debug":
                    stateRoot = Path(record.snapshot.runtimeDbPath).parent.resolve()
                    if (stateRoot.is_relative_to(self.root.resolve())
                            and stateRoot.name == record.snapshot.snapshotId and stateRoot.parent.name == "debug"):
                        shutil.rmtree(stateRoot)
                raise
            self.prepared[record.snapshot.snapshotId] = record
            return record

    def start(self, preparedId, *, capture=True, measure=False):
        with self.lock:
            if len(self.jobs) >= 2:
                raise ValueError("display Job quota exceeded; explicitly release a terminal Job")
            prepared = self.prepared[preparedId]
            prepared.verify()
            snapshot = prepared.snapshot
            from emo_master.core.project.models import ProjectDocument
            document = ProjectDocument.model_validate_json(prepared.projectPath.read_text(encoding="utf-8"))
            job = self.runtime.jobManager.createJob(snapshot.projectId, document.project.revision, document.entryWorkflowId)
            config = {"plan": prepared.sourceJson, "preparedId": preparedId, "credits": self.context.BoundedSemaphore(8),
                      "queue": SharedMailbox(self.context),
                      "capture": capture, "measure": measure,
                      "versions": {k: d.manifest.version for k, d in self.runtime.pluginScanResult.activeOperators.items()},
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
            self.timings[job.jobId] = deque(maxlen=128)
            reader = threading.Thread(target=self._read, args=(job.jobId, config), name=f"display-ipc-{job.jobId}")
            self.readers[job.jobId] = reader
            reader.start()
            workspace = self.root / "jobs" / job.jobId
            workspace.mkdir(parents=True)
            try:
                self.runtime.jobManager.start(job, JobProcessSpec(
                    job.jobId, str(prepared.projectPath), document.entryWorkflowId,
                    pluginRootPaths=tuple(self.runtime.pluginRootPaths), jobWorkspacePath=str(workspace),
                    projectId=snapshot.projectId, runtimeDbPath=snapshot.runtimeDbPath, presentation=config))
            except BaseException:
                self.runtime.jobRepository.update(job.jobId, status="FAILED")
                self.terminalStates[job.jobId] = ("FAILED", time.monotonic())
                raise
            return job.jobId

    def _read(self, jobId, config):
        while True:
            try:
                event = config["queue"].get(timeout=.02)
                self.consume(jobId, event)
            except queue.Empty:
                terminal = self.terminalStates.get(jobId)
                if terminal and time.monotonic() >= terminal[1]:
                    self._fence(jobId, terminal[0])
                    return
            except (EOFError, OSError, ValueError) as error:
                self.runtime.jobSupervisor.presentationErrors.append(repr(error))
                self._fence(jobId, "UNKNOWN")
                return
            except Exception as error:
                self.runtime.jobSupervisor.presentationErrors.append(repr(error))

    def consume(self, jobId, event):
        with self.store.lock:
            if event["eventType"] == "display.timing":
                self.timings[jobId].append(event)
            elif event["eventType"] == "display.open":
                self.store.begin(event["item"])
                self.pending[event["item"]["identity"]["resultKey"]] = {"job": jobId, "exports": {}}
            elif event["eventType"] == "display.image":
                if self.exporter is not None:
                    self.exporter.submit(event["descriptor"])
            elif event["eventType"] == "display.seal":
                pending = self.pending.get(event["key"])
                if pending is not None and "seal" not in pending:
                    pending["seal"] = event
                    if "scopeEndedNs" in event:
                        self.store.open[event["key"]]["scopeEndedNs"] = event["scopeEndedNs"]
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
                            self.store._notify()
                for key in list(self.pending):
                    self._finish(key)
                self.assets.stats()  # expire abandoned finite leases without another request

    def terminal(self, jobId, status):
        terminal = {"COMPLETED": "COMPLETED", "ABORTED": "CANCELLED"}.get(status, "FAILED")
        if jobId in self.jobs:
            self.terminalStates[jobId] = (terminal, time.monotonic() + .5)
        if self.previousTerminal:
            self.previousTerminal(jobId, status)

    def _fence(self, jobId, terminal):
        with self.store.lock:
            for key, pending in list(self.pending.items()):
                if pending["job"] == jobId and "seal" not in pending:
                    item = self.store.open[key]
                    code = "EXECUTION_CANCELLED" if terminal == "CANCELLED" else "IPC_ERROR"
                    self.store.close(key, [unavailable(s, code) for s in item["expected"]], terminal)
                    self.jobs[jobId]["credits"].release()
                    del self.pending[key]
            self._retain()

    def release(self, jobId):
        with self.lock:
            job = self.runtime.jobRepository.get(jobId)
            if job is not None and not job.isTerminal:
                raise ValueError("cannot release a running Job")
            if jobId in self.readers and self.readers[jobId].is_alive():
                raise ValueError("display IPC still retiring")
            handle = self.runtime.jobSupervisor._handles.get(jobId)
            if handle is not None and handle[0].is_alive():
                raise ValueError("Worker still owns its workspace")
            with self.store.lock:
                for key, pending in list(self.pending.items()):
                    if pending["job"] == jobId:
                        raise ValueError("exports still own this Job; release after result closure")
                config = self.jobs.get(jobId)
                if config:
                    import shutil
                    from emo_master.apps.runtime.preview.store import _ioPath
                    workspace = (self.root / "jobs" / jobId).resolve()
                    if not workspace.is_relative_to((self.root / "jobs").resolve()):
                        raise ValueError("workspace ownership mismatch")
                    if workspace.exists():
                        # PreviewSnapshotWriter uses extended Win32 paths; a
                        # killed atomic write can leave a >260-character file.
                        shutil.rmtree(_ioPath(workspace))
                if config and self.exporter:
                    for descriptor in config["slots"]:
                        slot = self.exporter.slots[descriptor["index"]]
                        if slot["busy"]:
                            raise ValueError("export slot still owned")
                        while slot["free"].acquire(False):
                            pass
                        slot["free"].release()
                self.jobs.pop(jobId, None)
                self.timings.pop(jobId, None)
                self.readers.pop(jobId, None)
                self.terminalStates.pop(jobId, None)
                if config:
                    config["queue"] = None
                self.store.history = type(self.store.history)(r for r in self.store.history if r.identity.jobId != jobId)
                self.store.events = deque(((offset, result) for offset, result in self.store.events
                                           if result is None or result.identity.jobId != jobId), maxlen=32)
                for table in (self.store.latest, self.store.high, self.store.closedHigh):
                    for key in list(table):
                        if key[0] == jobId:
                            del table[key]
                self._retain()

    def close(self):
        # Does not stop the externally owned Runtime or any Job.
        if self.closed:
            return
        if any(not self.runtime.jobRepository.get(job).isTerminal for job in self.jobs):
            raise ValueError("Runtime owner must stop Jobs before disposing presentation service")
        for reader in self.readers.values():
            reader.join(2)
            if reader.is_alive():
                raise RuntimeError("display IPC owner has not retired")
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
        self.runtime.jobSupervisor.terminalCallback = self.previousTerminal
        self.runtime.jobSupervisor.presentationCallback = self.previousPresentation
        self.runtime._presentationOwner = None
        self.closed = True

    def resourceStats(self):
        with self.store.lock:
            return self._resourceStats()

    def _resourceStats(self):
        # Conservative fixed reservations, not a substitute for measured RSS.
        # At most 2 Jobs, one 8 MiB raw slot per Job; 6x includes encoder/IPC copies.
        exportReservation = 2 * 6 * 8 * 1024 * 1024 if self.exporter else 0
        sharedCapacity = 2 * 8 * 1024 * 1024 if self.exporter else 0
        metadataReservation = len(self.jobs) * 64 * 1024 * 1024
        readReservation = 2 * 8 * 1024 * 1024
        return dict(self.assets.stats(), open_results=len(self.store.open),
                    history_results=len(self.store.history),
                    history_bytes=sum(len(r.model_dump_json().encode()) for r in self.store.history),
                    rejected=sum(c["rejected"].value for c in self.jobs.values()),
                    shared_capacity=sharedCapacity, export_reserved=exportReservation,
                    metadata_reserved=metadataReservation, read_reserved=readReservation,
                    total_reserved=sharedCapacity + exportReservation + metadataReservation + readReservation,
                    limit=256 * 1024 * 1024)

    def discardPrepared(self, preparedId):
        import shutil
        with self.lock:
            record = self.prepared[preparedId]
            if any(config.get("preparedId") == preparedId for config in self.jobs.values()):
                raise ValueError("release Jobs before discarding their prepared snapshot")
            shutil.rmtree(record.projectPath.parent)
            del self.prepared[preparedId]
