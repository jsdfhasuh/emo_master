"""Explicit facade over the existing Runtime, JobManager and spawn Supervisor."""
import multiprocessing
from collections import deque
from functools import wraps
import json
import logging
import os
import queue
from pathlib import Path
import threading
import time
from uuid import uuid4

from emo_master.apps.runtime.jobs.models import JobProcessSpec
from emo_master.apps.runtime.jobs.finalization import faultSummary
from emo_master.apps.runtime.presentation.preparation import prepare
from emo_master.apps.runtime.presentation.store import ResultStore
from emo_master.apps.runtime.presentation.assets import AssetStore
from emo_master.apps.runtime.presentation.exporter import ExportPool
from emo_master.apps.runtime.presentation.collector import unavailable
from emo_master.apps.runtime.presentation.mailbox import SharedMailbox
from emo_master.apps.runtime.presentation.diagnostics import closureDiagnostic


def _guardMutation(method):
    @wraps(method)
    def guarded(self, *args, **kwargs):
        self.runtime.jobSupervisor.assertMutationAllowed()
        return method(self, *args, **kwargs)
    return guarded


def _devicePreviewAdmission(method):
    @wraps(method)
    def guarded(self, *args, **kwargs):
        with self.runtime._previewJobLock:
            debug = getattr(self.runtime, "operatorDebugManager", None)
            if debug is not None and debug.ownsResources():
                raise RuntimeError("E_RESOURCE_BUSY: operator debug still holds Runtime resources")
            return method(self, *args, **kwargs)
    return guarded


class PresentationService:
    supportsNormalCapture = True

    def __init__(self, runtime, root: Path):
        runtime.jobSupervisor.assertMutationAllowed()
        with runtime.jobSupervisor._lock:
            if runtime.jobSupervisor._closing or getattr(runtime, "_closing", False):
                raise RuntimeError("E_RUNTIME_CLOSING")
            if getattr(runtime, "_presentationOwner", None) is not None:
                raise ValueError("Runtime already has a presentation owner")
            self.runtime = runtime
            self.root = root
            self.runtimeInstanceId = getattr(runtime, "runtimeInstanceId", str(uuid4()))
            self.prepared: dict = {}
            self.jobs: dict = {}
            self.store = ResultStore()
            self.lock = threading.RLock()
            self.context = multiprocessing.get_context("spawn")
            self.assets = AssetStore(root / "assets")
            self.exporter: ExportPool | None = None
            self.pending: dict = {}
            self.diagnostics: deque[dict] = deque(maxlen=64)
            self.phaseTraces: dict = {}
            self.timings: dict = {}
            self.readers: dict = {}
            self.terminalStates: dict = {}
            self.stop = threading.Event()
            self.monitor = threading.Thread(target=self._monitor, name="display-seal-monitor")
            self.previousTerminal = runtime.jobSupervisor._terminalCallback
            self.previousPresentation = runtime.jobSupervisor.presentationCallback
            self._previousTerminal = self.previousTerminal
            self._previousPresentation = self.previousPresentation
            previous = self._previousTerminal
            def installedTerminal(jobId, status):
                self._terminalFence(jobId, status)
                if previous is not None:
                    previous(jobId, status)
            self._installedTerminal = installedTerminal
            runtime.jobSupervisor._terminalCallback = installedTerminal
            runtime.jobSupervisor._callbackEpoch += 1
            self.callbackEpoch = runtime.jobSupervisor._callbackEpoch
            runtime.jobSupervisor._presentationEpoch = self.callbackEpoch
            runtime.jobSupervisor.presentationCallback = self.consume
            runtime._presentationOwner = self
            self.closed = False
            self.closing = False
            self._closeStages: dict[str, str] = {}
            self.monitor.start()

    @_guardMutation
    def prepare(self, project, resourceRoot, **kwargs):
        with self.lock:
            if self.closing or self.closed or getattr(self.runtime, "_closing", False):
                raise RuntimeError("E_RUNTIME_CLOSING")
            if len(self.prepared) >= 8:
                raise ValueError("prepared record quota exceeded")
            record = prepare(project, self.runtime.pluginScanResult.activeOperators, self.root, resourceRoot,
                             protected=self.runtime.sqliteProtectedPaths(), **kwargs)
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

    @_devicePreviewAdmission
    @_guardMutation
    def start(self, preparedId, *, capture=True, measure=False):
        with self.lock:
            if self.closing or self.closed or getattr(self.runtime, "_closing", False):
                raise RuntimeError("E_RUNTIME_CLOSING")
            limit = self.runtime.jobSupervisor.maxConcurrentJobs
            if limit is not None and len(self.jobs) >= limit:
                raise ValueError("display Job quota exceeded; explicitly release a terminal Job")
            prepared = self.prepared[preparedId]
            prepared.verify()
            snapshot = prepared.snapshot
            from emo_master.core.project.models import ProjectDocument
            document = ProjectDocument.model_validate_json(prepared.projectPath.read_text(encoding="utf-8"))
            errors = self.runtime.livePreviewManager.closeAll(timeoutSeconds=3.0)
            if errors:
                raise RuntimeError("E_PREVIEW_RELEASE_FAILED: " + "; ".join(errors))
            errors = self.runtime.plcDebugManager.closeSessions()
            if errors:
                raise RuntimeError("E_PLC_RELEASE_FAILED: " + "; ".join(errors))
            job = self.runtime.jobManager.createJob(snapshot.projectId, document.project.revision, document.entryWorkflowId)
            job.executionMode = snapshot.mode
            config = self._attach(job.jobId, prepared.sourceJson,
                executionRevision=snapshot.executionRevision,
                capturePlanRevision=snapshot.capturePlanRevision, mode=snapshot.mode,
                preparedId=preparedId, capture=capture, measure=measure,
                captureDefinitionJson=snapshot.capturePlanJson)
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

    @_guardMutation
    def checkNormalAdmission(self):
        with self.lock:
            if self.closing or self.closed or getattr(self.runtime, "_closing", False):
                raise RuntimeError("E_RUNTIME_CLOSING")
            if not self.supportsNormalCapture:
                raise ValueError("this test-release host does not accept normal StartJob capture")
            if self.closed:
                raise ValueError("presentation service is closed")
            limit = self.runtime.jobSupervisor.maxConcurrentJobs
            if limit is not None and len(self.jobs) >= limit:
                # A fresh explicit Start may evict a retired display, not a
                # running Job. Historical captures are not concurrent Jobs.
                # release() still proves reader/export/worker retirement;
                # failure leaves all ownership charged, never steals a slab.
                for jobId in list(self.jobs):
                    record = self.runtime.jobRepository.get(jobId)
                    if record is not None and record.isTerminal:
                        try:
                            self.release(jobId)
                        except ValueError:
                            continue
                        if len(self.jobs) < limit:
                            break
            if limit is not None and len(self.jobs) >= limit:
                raise ValueError("display Job quota exceeded; explicitly release a terminal Job")

    @_guardMutation
    def attachNormal(self, jobId, frozen):
        """Attach to an already-created normal Job; never create a second Job."""
        with self.lock:
            if self.closing or self.closed or getattr(self.runtime, "_closing", False):
                raise RuntimeError("E_RUNTIME_CLOSING")
            self.checkNormalAdmission()
            return self._attach(jobId, frozen.sourceJson,
                executionRevision=frozen.executionRevision,
                capturePlanRevision=frozen.capturePlanRevision, mode="runtime",
                captureDefinitionJson=frozen.captureDefinitionJson, limitsJson=frozen.limitsJson)

    def _attach(self, jobId, sourceJson, *, executionRevision, capturePlanRevision,
                mode, captureDefinitionJson, preparedId="", capture=True, measure=False, limitsJson=""):
        plan = json.loads(sourceJson)
        needsImage = any(source["expectedType"] == "image" for source in plan["sources"].values())
        if needsImage and self.exporter is None:
            self.exporter = ExportPool(self.root / "staging", self._exported)
        config = {"plan": sourceJson, "preparedId": preparedId,
                  "captureDefinitionJson": captureDefinitionJson,
                  "limitsJson": limitsJson,
                  "imageLaneBySource": json.loads(limitsJson).get("imageLaneBySource", {}) if limitsJson else {},
                  "credits": self.context.BoundedSemaphore(8), "queue": SharedMailbox(self.context),
                  "capture": capture, "measure": measure,
                  "versions": {key: definition.manifest.version
                               for key, definition in self.runtime.pluginScanResult.activeOperators.items()},
                  "slots": [], "scopeIds": list(plan["scopes"]),
                  "ordinals": self.context.Array("Q", 16, lock=False),
                  "rejected": self.context.Value("Q", 0), "identity": {
                      "runtimeInstanceId": self.runtimeInstanceId, "jobId": jobId,
                      "executionRevision": executionRevision,
                      "capturePlanRevision": capturePlanRevision, "mode": mode}}
        if needsImage and self.exporter:
            used = {slot["index"] for active in self.jobs.values() for slot in active["slots"]}
            index = next((slot["index"] for slot in self.exporter.slots
                          if slot["index"] not in used and not slot["quarantined"].value), None)
            if index is None:
                index = self.exporter.addSlot()
            laneCount = len(set(config["imageLaneBySource"].values())) or 1
            config["slots"] = self.exporter.configureLanes(index, laneCount)
        if os.environ.get("EMO_PRESENTATION_TRACE") == "1":
            from emo_master.apps.runtime.presentation.phase_trace import LocalPhaseTrace, SharedPhaseTrace
            try:
                producerTrace, parentTrace = SharedPhaseTrace(self.context), LocalPhaseTrace()
                config["phaseTrace"] = producerTrace
                self.phaseTraces[jobId] = parentTrace
            except Exception as error:
                config["phaseTraceUnavailable"] = type(error).__name__
        self.jobs[jobId] = config
        self.timings[jobId] = deque(maxlen=128)
        reader = threading.Thread(target=self._read, args=(jobId, config), name=f"display-ipc-{jobId}")
        self.readers[jobId] = reader
        reader.start()
        return config

    def _read(self, jobId, config):
        while True:
            try:
                event = config["queue"].get(timeout=.02)
                # This already-admitted reader must drain while close owns the
                # callback-registration lock and waits for it to retire.
                self._consume(jobId, event)
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
        # The result assembler can also be used without a Runtime owner.
        # Runtime-bound public calls still reject callback-owner reentry.
        runtime = getattr(self, "runtime", None)
        if runtime is not None:
            runtime.jobSupervisor.assertMutationAllowed()
        self._consume(jobId, event)

    def _consume(self, jobId, event):
        key = event.get("key", event.get("descriptor", {}).get("key"))
        identity = event.get("item", {}).get("identity")
        self._trace(jobId, "reader.received", key, identity=identity, reason=event.get("eventType"))
        waiting = time.monotonic_ns() if getattr(self, "phaseTraces", {}).get(jobId) is not None else None
        with self.store.lock:
            if waiting is not None:
                self._trace(jobId, "reader.lock_acquired", key, identity=identity, waitNs=time.monotonic_ns() - waiting)
            if event["eventType"] == "display.timing":
                self.timings[jobId].append(event)
            elif event["eventType"] == "display.open":
                if self.store.begin(event["item"]):
                    self.pending[event["item"]["identity"]["resultKey"]] = {"job": jobId, "exports": {}}
                    self._trace(jobId, "store.opened", identity=event["item"]["identity"])
            elif event["eventType"] == "display.image":
                if self.exporter is not None:
                    descriptor = event["descriptor"]
                    config = self.jobs[jobId]
                    if (descriptor["jobId"] != jobId
                            or not any(slot["index"] == descriptor["slot"]
                                and slot.get("lane", 0) == descriptor.get("lane", 0)
                                for slot in config["slots"])
                            or (config["imageLaneBySource"]
                                and config["imageLaneBySource"].get(descriptor["sourceId"]) != descriptor.get("lane", 0))):
                        raise ValueError("image descriptor does not belong to this Job/source lane")
                    self._trace(jobId, "export.submitting", key, sourceId=descriptor["sourceId"])
                    self.exporter.submit(descriptor)
                    self._trace(jobId, "export.submitted", key, sourceId=descriptor["sourceId"])
            elif event["eventType"] == "display.seal":
                pending = self.pending.get(event["key"])
                if pending is not None and "seal" not in pending:
                    pending["seal"] = event
                    item = self.store.open.get(event["key"])
                    if item is not None:
                        item["sourceObservations"] = event.get("sourceObservations", {})
                    if "scopeEndedNs" in event and item is not None:
                        item["scopeEndedNs"] = event["scopeEndedNs"]
                    pending["deadline"] = event["sealedAt"] + .5
                    self._trace(jobId, "seal.received", key, terminal=event.get("terminal"))
                    self._finish(event["key"])

    def _exported(self, task, path, outcome):
        self._trace(task.get("jobId"), "export.callback", task.get("key"), sourceId=task.get("sourceId"), reason=outcome)
        with self.store.lock:
            pending = self.pending.get(task["key"])
            if pending is None:
                self._trace(task.get("jobId"), "export.orphan", task.get("key"), sourceId=task.get("sourceId"))
                return  # export owner deletes orphan staging after this callback
            source = unavailable(task["sourceId"], outcome if outcome != "AVAILABLE" else "EXPORT_FAILED")
            diagnostic = dict(task.get("exportDiagnostic", {}))
            if path is not None:
                try:
                    diagnostic["adoptionStartedNs"] = time.monotonic_ns()
                    image = self.assets.adopt(path, task["jobId"], task["key"], task["provenance"])
                    self._trace(task["jobId"], "export.adopted", task["key"], sourceId=task["sourceId"])
                    source = {"sourceId": task["sourceId"], "state": "AVAILABLE", "image": image}
                except (OSError, ValueError) as error:
                    self._trace(task["jobId"], "export.adopt_failed", task["key"], errorType=type(error).__name__)
                    from emo_master.apps.runtime.presentation.diagnostics import errorDetail
                    diagnostic.update(adoptionError=errorDetail(error), stage="asset_adoption")
                    source = unavailable(task["sourceId"], "EXPORT_FAILED",
                        f"asset adoption failed: {type(error).__name__}; see Runtime diagnostics")
                finally:
                    diagnostic["adoptionEndedNs"] = time.monotonic_ns()
            elif diagnostic:
                source["reason"] = f"{outcome} at {diagnostic.get('stage')}; see Runtime diagnostics"
            pending.setdefault("exportDiagnostics", {})[task["sourceId"]] = diagnostic
            pending["exports"][task["sourceId"]] = source
            self._finish(task["key"])

    def _finish(self, key):
        # Readers, exporter callbacks, fences and the monitor share ownership.
        # A repeated/late finish must not return a scope credit twice.
        with self.store.lock:
            self._finishLocked(key)

    def _finishLocked(self, key):
        pending = self.pending.get(key)
        if pending is None:
            return
        if "seal" not in pending:
            self._traceWait(key, pending, "finish.wait_seal")
            return
        seal = pending["seal"]
        item = self.store.open.get(key)
        if item is None:
            # The pending record, not close()'s boolean, owns the scope credit.
            self._retirePending(key, pending)
            return
        sources = []
        terminal = seal.get("terminal", "UNKNOWN")
        try:
            for source in seal["sources"]:
                if source.get("pendingImage"):
                    exported = pending["exports"].get(source.get("imageSourceId", source["sourceId"]))
                    if exported is None:
                        if time.monotonic() < pending["deadline"]:
                            if getattr(self, "phaseTraces", {}).get(pending["job"]) is not None:
                                waiting = [s.get("sourceId") for s in seal["sources"][:16]
                                    if isinstance(s, dict) and s.get("pendingImage")
                                    and s.get("imageSourceId", s.get("sourceId")) not in pending["exports"]]
                                self._traceWait(key, pending, "finish.wait_sources", waiting)
                            return
                        exported = unavailable(source["sourceId"], "EXPORT_TIMEOUT",
                            "assembler waiting for export callback exceeded seal deadline")
                    sources.append(dict(exported, sourceId=source["sourceId"]))
                else:
                    sources.append(source)
            self._trace(pending["job"], "store.closing", key, terminal=terminal)
            self.store.close(key, sources, terminal)
        except (ValueError, TypeError, KeyError, AttributeError) as error:
            self._trace(pending["job"], "store.validation_error", identity=item["identity"], errorType=type(error).__name__)
            self._diagnose(key, item, seal.get("sources", []), terminal, "result_validation", error)
            # No invalid payload is published as COMPLETE. Keep the original
            # identity/expected manifest and a visible failure for every source.
            # Atomic store.close left the open item intact for this closure.
            detail = f"result closure validation failed: {type(error).__name__}; see Runtime diagnostics"
            terminal = terminal if terminal in {"COMPLETED", "FAILED", "CANCELLED", "UNKNOWN"} else "UNKNOWN"
            self.store.close(key, [unavailable(name, "INVALID_VALUE", detail) for name in item["expected"]], terminal)
        else:
            if terminal != "COMPLETED" or any(source["state"] != "AVAILABLE" for source in sources):
                self._diagnose(key, item, sources, terminal, "result_closed")
        self._trace(pending["job"], "store.closed", identity=item["identity"], terminal=terminal)
        self._retirePending(key, pending)

    def _trace(self, jobId, phase, key=None, *, identity=None, **details):
        trace = getattr(self, "phaseTraces", {}).get(jobId)
        if trace is None:
            return
        try:
            if identity is None:
                identity = self.store.open.get(key, {}).get("identity")
            if identity is None:
                identity = dict(self.jobs.get(jobId, {}).get("identity", {}), resultKey=key)
            trace.record(phase, identity, **details)
        except Exception:
            pass  # Optional evidence must never replace the original operation.

    def _traceWait(self, key, pending, phase, sources=None):
        if getattr(self, "phaseTraces", {}).get(pending["job"]) is None:
            return
        state = (phase, tuple(sources or ()))
        if pending.get("traceWait") != state:
            pending["traceWait"] = state
            self._trace(pending["job"], phase, key, waitingSources=sources or [])

    def _diagnose(self, key, item, sources, terminal, stage, error=None):
        record = closureDiagnostic(key, item, sources, terminal, stage, error)
        record["exports"] = list(self.pending.get(key, {}).get("exportDiagnostics", {}).values())[:16]
        if not hasattr(self, "diagnostics"):
            self.diagnostics = deque(maxlen=64)
        self.diagnostics.append(record)
        if error is not None:
            logging.getLogger(__name__).warning("presentation closure failure %s", json.dumps(record))

    def _retirePending(self, key, pending):
        if self.pending.get(key) is not pending:
            return
        del self.pending[key]
        self.jobs[pending["job"]]["credits"].release()
        # Scope credit is independent of image lane credit. Only the real
        # exporter owner may return a lane after producer retirement.
        self._retain()

    def _retain(self):
        self.assets.retain({r.identity.resultKey for r in self.store.history}
                           | {r.identity.resultKey for r in self.store.latest.values()}
                           | {r.identity.resultKey for _, r in self.store.events if r is not None} | set(self.pending))

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

    @_guardMutation
    def terminal(self, jobId, status):
        self._installedTerminal(jobId, status)

    def _terminalFence(self, jobId, status):
        terminal = {"COMPLETED": "COMPLETED", "ABORTED": "CANCELLED"}.get(status, "FAILED")
        if jobId in self.jobs:
            self.terminalStates[jobId] = (terminal, time.monotonic() + .5)

    def _fence(self, jobId, terminal):
        with self.store.lock:
            for key, pending in list(self.pending.items()):
                if pending["job"] == jobId and "seal" not in pending:
                    item = self.store.open[key]
                    code = "EXECUTION_CANCELLED" if terminal == "CANCELLED" else "IPC_ERROR"
                    pending["seal"] = {"sources": [unavailable(s, code) for s in item["expected"]],
                                       "terminal": terminal}
                    pending["deadline"] = 0
                    self._finish(key)
            self._retain()

    @_guardMutation
    def release(self, jobId):
        with self.lock:
            job = self.runtime.jobRepository.get(jobId)
            if job is not None and not job.isTerminal:
                raise ValueError("cannot release a running Job")
            if jobId in self.readers and self.readers[jobId].is_alive():
                raise ValueError("display IPC still retiring")
            if self.runtime.jobSupervisor.ownsJobResources(jobId):
                raise ValueError("Worker still owns its workspace")
            with self.store.lock:
                for key, pending in list(self.pending.items()):
                    if pending["job"] == jobId:
                        raise ValueError("exports still own this Job; release after result closure")
                config = self.jobs.get(jobId)
                if config and config.get("preparedId"):
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
                    for index in {descriptor["index"] for descriptor in config["slots"]}:
                        self.exporter.releaseSlot(index)
                self.jobs.pop(jobId, None)
                getattr(self, "phaseTraces", {}).pop(jobId, None)
                self.timings.pop(jobId, None)
                self.readers.pop(jobId, None)
                self.terminalStates.pop(jobId, None)
                if config:
                    config["queue"] = None
                self.store.history = type(self.store.history)(r for r in self.store.history if r.identity.jobId != jobId)
                self.store.events = deque(((offset, result) for offset, result in self.store.events
                                           if result is None or result.identity.jobId != jobId), maxlen=32)
                for table in (self.store.latest, self.store.high, self.store.closedHigh, self.store.expired):
                    for key in list(table):
                        if key[0] == jobId:
                            del table[key]
                self.store.expiryResets.pop(jobId, None)
                self._retain()

    def beginClosing(self):
        self.runtime.jobSupervisor.assertMutationAllowed()
        with self.lock:
            self.closing = True

    @_guardMutation
    def close(self):
        with self.lock:
            self.closing = True
            supervisor = self.runtime.jobSupervisor
            # Claim and restoration use this same lock. No uncaptured Job can
            # acquire our callback epoch between the check and disposal.
            with supervisor._lock:
                if self.closed:
                    return
                if supervisor.ownsCallbackEpoch(self.callbackEpoch):
                    raise RuntimeError("terminal callback epoch still owns presentation resources")
                if (supervisor._terminalCallback is not self._installedTerminal
                        and not (supervisor._closing and not supervisor._ownedJobs())):
                    raise RuntimeError("presentation callback registration was replaced")
                if any(not self.runtime.jobRepository.get(job).isTerminal for job in self.jobs):
                    raise ValueError("Runtime owner must stop Jobs before disposing presentation service")
                if any(supervisor.ownsJobResources(job) for job in self.jobs):
                    raise ValueError("Worker still owns presentation resources")
                for reader in self.readers.values():
                    reader.join(2)
                    if reader.is_alive():
                        raise RuntimeError("display IPC owner has not retired")
                if self.exporter is not None:
                    self._closeStep("exporter", self.exporter.close)
                self.stop.set()
                self.monitor.join(2)
                if self.monitor.is_alive():
                    raise RuntimeError("display monitor still owns resources")
                with self.store.lock:
                    for key, pending in list(self.pending.items()):
                        pending["deadline"] = 0
                        self._finish(key)
                for job in list(self.jobs):
                    self._closeStep("release:" + job, lambda job=job: self.release(job))
                # AssetStore.close explicitly rejects live readers before mutations.
                with self.assets.lock:
                    if self.assets.readers:
                        raise RuntimeError("cannot close while reads still own resources")
                    self._closeStep("assets", self.assets.close)
                # Runtime shutdown may have an outer wrapper registered. The
                # closing fence and empty owner set above prove that complete
                # chain is quiescent before restoring the frozen registration.
                supervisor._terminalCallback = self._previousTerminal
                supervisor._callbackEpoch += 1
                supervisor.presentationCallback = self._previousPresentation
                supervisor._presentationEpoch = None
                self.runtime._presentationOwner = None
                self.closed = True

    def _closeStep(self, name, action):
        state = self._closeStages.get(name)
        if state == "DONE":
            return
        if state is not None:
            raise RuntimeError("incomplete presentation close step: " + name + ": " + state)
        self._closeStages[name] = "STARTED"
        try:
            action()
        except BaseException as error:
            self._closeStages[name] = faultSummary(error)
            raise
        self._closeStages[name] = "DONE"

    def resourceStats(self):
        with self.store.lock:
            return self._resourceStats()

    def _resourceStats(self):
        # Conservative fixed reservations, not a substitute for measured RSS.
        # At most 2 Jobs, one 8 MiB raw slot per Job (counted separately).
        # The existing 6x encoder reservation includes ONE private input snapshot,
        # encoded ndarray/bytes and codec workspace. Snapshot <= lane capacity;
        # no pixels go over IPC. This reservation is not a measured RSS ceiling.
        exportReservation = 2 * 6 * 8 * 1024 * 1024 if self.exporter else 0
        sharedCapacity = 2 * 8 * 1024 * 1024 if self.exporter else 0
        metadataReservation = len(self.jobs) * 64 * 1024 * 1024
        readReservation = 2 * 8 * 1024 * 1024
        return dict(self.assets.stats(), open_results=len(self.store.open),
                    history_results=len(self.store.history),
                    history_bytes=sum(len(r.model_dump_json().encode()) for r in self.store.history),
                    retained_metadata_bytes=self.store.metadataBytes(),
                    latest_results=len(self.store.latest),
                    rejected=sum(c["rejected"].value for c in self.jobs.values()),
                    shared_capacity=sharedCapacity, export_reserved=exportReservation,
                    metadata_reserved=metadataReservation, read_reserved=readReservation,
                    total_reserved=sharedCapacity + exportReservation + metadataReservation + readReservation,
                    limit=256 * 1024 * 1024)

    @_guardMutation
    def discardPrepared(self, preparedId):
        import shutil
        with self.lock:
            record = self.prepared[preparedId]
            if any(config.get("preparedId") == preparedId for config in self.jobs.values()):
                raise ValueError("release Jobs before discarding their prepared snapshot")
            stateRoot = None
            if record.snapshot.mode == "debug":
                stateRoot = Path(record.snapshot.runtimeDbPath).parent.resolve()
                if not (stateRoot.is_relative_to(self.root.resolve())
                        and stateRoot.name == record.snapshot.snapshotId
                        and stateRoot.parent.name == "debug"):
                    raise ValueError("debug state ownership mismatch")
            if record.projectPath.parent.exists():
                shutil.rmtree(record.projectPath.parent)
            if stateRoot is not None and stateRoot.exists():
                shutil.rmtree(stateRoot)
            del self.prepared[preparedId]
