"""One nonblocking writer; cancellation stops before, never after, durable commit."""
from copy import deepcopy
from threading import Event, RLock, Thread
from uuid import uuid4

from emo_master.core.project.configuration import digest, selectedCandidate, projectChanges
from .store import ConfigurationError

REQUEST_WINDOW = 2048


class ConfigurationCoordinator:
    def __init__(self, store, runtimeInstanceId, *, lock=None, prepare, publish, beforeCommit=None, discard=None,
                 transition=None, cycleIdentity=None):
        self.store, self.runtimeInstanceId = store, runtimeInstanceId
        self.lock = lock if lock is not None else RLock()
        self.prepare, self.publish = prepare, publish
        self.beforeCommit = beforeCommit
        self.discard = discard
        self.transition = transition
        self.cycleIdentity = cycleIdentity
        self.drainingOwners = set()
        self.parkedOwners = set()
        self.current = store.snapshot()
        self.requests = {}
        self.active = None
        self.thread = None
        self.closed = False
        self.cycles = {}
        self.childCycles = {}
        self.cycleLock = RLock()
        self.publicationError = ""

    def snapshot(self):
        with self.lock:
            if self.current is None:
                raise ConfigurationError("E_CONFIG_NOT_LOADED", "no online project configuration")
            return dict(deepcopy(self.current), runtimeInstanceId=self.runtimeInstanceId)

    def _check(self, request):
        if self.closed:
            raise ConfigurationError("E_RUNTIME_CLOSING", "configuration authority is closing")
        if self.publicationError:
            raise ConfigurationError("E_CONFIG_NOT_READY", "previous configuration publication or cleanup requires recovery")
        if request["expectedRuntimeInstanceId"] != self.runtimeInstanceId:
            raise ConfigurationError("E_CONFIG_INSTANCE", "Runtime restarted; compare again")
        if request.get('operation') == 'initialize':
            if self.current is not None:
                raise ConfigurationError('E_CONFIG_ACTIVE', 'Runtime already has an online project; compare before writing')
            return
        if self.current is None or request["expectedRevision"] != self.current["documentRevision"]:
            raise ConfigurationError("E_CONFIG_CONFLICT", "online revision changed; compare again")

    def _candidate(self, request):
        self._check(request)
        if request.get('operation') == 'loadLocalProject':
            # The local operator explicitly selected a complete on-disk project.
            # Remote writes still require their ordinary field selection.
            if not request.get('sourcePath') or request.get('replacementPolicy') != 'stopAffected':
                raise ConfigurationError('E_CONFIG_PROJECT_REPLACEMENT', 'local loading requires a source and explicit replacement policy')
            from emo_master.core.project.models import ProjectDocument
            return ProjectDocument.model_validate(request['localPayload']).model_dump(mode='json')
        if request.get('sourcePath') and not request['selectedPaths']:
            if request['localPayload']['project']['projectId'] != self.current['payload']['project']['projectId']:
                raise ConfigurationError('E_CONFIG_PROJECT_REPLACEMENT', 'switching projects requires selecting the complete candidate')
            return deepcopy(self.current['payload'])
        candidate = selectedCandidate(self.current["payload"], request["localPayload"], request["selectedPaths"])
        if candidate["project"]["projectId"] != self.current["payload"]["project"]["projectId"]:
            from emo_master.core.project.models import ProjectDocument
            local = ProjectDocument.model_validate(request["localPayload"]).model_dump(mode="json")
            if (projectChanges(ProjectDocument.model_validate(candidate).model_dump(mode="json"), local)
                    or request.get("replacementPolicy", "reject") not in {"stopAffected", "drainAffected"}):
                raise ConfigurationError("E_CONFIG_PROJECT_REPLACEMENT", "project replacement requires the complete candidate and an explicit stop/drain policy")
            candidate = local
        return candidate

    def validate(self, request):
        with self.lock:
            candidate = self._candidate(request)
        source = {'sourcePath': request['sourcePath']} if request.get('sourcePath') else {}
        prepared = self.prepare(candidate, Event(), **source)
        if self.discard is not None:
            self.discard(prepared)
        return candidate

    def start(self, request, *, synchronous=False):
        request = deepcopy(request)
        requestId = request["requestId"]
        if not isinstance(requestId, str) or not requestId or len(requestId) > 160:
            raise ConfigurationError("E_CONFIG_REQUEST_ID", "a bounded unique request ID is required")
        fingerprint = digest(request)
        with self.lock:
            # A lost reply can be recovered even after a Runtime restart.
            saved = self.store.receipt(requestId)
            existing = self.requests.get(requestId)
            if saved or existing:
                if saved:
                    oldFingerprint, status = saved
                else:
                    assert existing is not None
                    oldFingerprint, status = existing["fingerprint"], existing["status"]
                if fingerprint != oldFingerprint:
                    raise ConfigurationError("E_CONFIG_REQUEST_MISMATCH", "request ID content changed")
                return deepcopy(status)
            self._check(request)
            if self.active:
                raise ConfigurationError("E_CONFIG_WRITER_BUSY", "another configuration write is pending")
            self._retireRequests()
            if len(self.requests) >= REQUEST_WINDOW:
                if self.current is None:
                    raise ConfigurationError('E_CONFIG_CAPACITY', 'initial request window is full; no requests were forgotten')
                # Failures at the current revision cannot be forgotten until
                # their original requests have a durable stale-revision fence.
                self.current = self.store.advanceRequestFence(self.current["documentRevision"])
                self._retireRequests()
                raise ConfigurationError("E_CONFIG_CONFLICT", "request window rotated; compare the new document revision before writing")
            candidate = request['localPayload'] if request.get('operation') == 'initialize' else self._candidate(request)
            entry = dict(fingerprint=fingerprint, expectedRevision=request["expectedRevision"], cancelled=Event(), status=dict(
                state="VALIDATING", requestId=requestId, runtimeInstanceId=self.runtimeInstanceId))
            self.requests[requestId] = entry
            self.active = requestId
            if not synchronous:
                self.thread = Thread(target=self._write, args=(request, candidate, entry),
                                     name="project-config-writer", daemon=True)
                self.thread.start()
                return deepcopy(entry["status"])
        # A stopped local directory update already owns the project lock and
        # must learn whether commit occurred before its file rollback can finish.
        self._write(request, candidate, entry)
        return self.status(requestId)

    def _retireRequests(self):
        if self.current is None:
            return
        for key, entry in list(self.requests.items()):
            if len(self.requests) < REQUEST_WINDOW:
                break
            if key != self.active and entry["expectedRevision"] < self.current["documentRevision"]:
                del self.requests[key]

    def startRecovery(self, request, admission):
        request = dict(request, operation='resourceRecovery')
        requestId = request['requestId']
        if not requestId or len(requestId) > 160:
            raise ConfigurationError('E_CONFIG_REQUEST_ID', 'a bounded unique request ID is required')
        fingerprint = digest(request)
        with self.lock:
            existing = self.requests.get(requestId)
            if existing:
                if existing['fingerprint'] != fingerprint:
                    raise ConfigurationError('E_CONFIG_REQUEST_MISMATCH', 'request ID content changed')
                return deepcopy(existing['status'])
            if self.store.receipt(requestId):
                raise ConfigurationError('E_CONFIG_REQUEST_MISMATCH', 'request ID belongs to a configuration write')
            self._check(request)
            if self.active:
                raise ConfigurationError('E_CONFIG_WRITER_BUSY', 'another configuration operation is pending')
            admission()
            self._retireRequests()
            if len(self.requests) >= REQUEST_WINDOW:
                raise ConfigurationError('E_CONFIG_CAPACITY', 'recovery request window is full; no revision was changed')
            entry = dict(fingerprint=fingerprint, expectedRevision=request['expectedRevision'], cancelled=Event(),
                         status=dict(state='VALIDATING', requestId=requestId, runtimeInstanceId=self.runtimeInstanceId))
            self.requests[requestId] = entry
            self.active = requestId
            self.thread = Thread(target=self._recover, args=(request, entry, admission), name='resource-recovery', daemon=True)
            self.thread.start()
            return deepcopy(entry['status'])

    def _recover(self, request, entry, admission):
        prepared = None
        installed = False
        try:
            prepared = self.prepare(deepcopy(self.current['payload']), entry['cancelled'])
            with self.lock:
                self._check(request)
                admission()
                if entry['cancelled'].is_set():
                    raise ConfigurationError('E_CONFIG_CANCELLED', 'resource recovery cancelled')
                installed = True
                self.publish(prepared, self.current)
            self.discard(prepared)
            prepared = None
            entry['status'].update(state='RECOVERED', documentRevision=self.current['documentRevision'],
                                  executionRevision=self.current['executionRevision'], message='Runtime resources ready; no jobs or connections started')
        except Exception as error:
            if installed:
                self.publicationError = str(error)
            entry['status'].update(state='CANCELLED' if entry['cancelled'].is_set() else 'FAILED',
                                  code=getattr(error, 'code', 'E_CONFIG_RECOVERY'), message=str(error))
        finally:
            try:
                if prepared is not None:
                    self.discard(prepared)
            except Exception as error:
                self.publicationError = str(error)
                entry['status'].update(state='FAILED', cleanupError=str(error))
            with self.lock:
                self.active = None

    def _write(self, request, candidate, entry):
        prepared = None
        transition = None
        initial = request.get('operation') == 'initialize'
        try:
            source = {'sourcePath': request['sourcePath']} if request.get('sourcePath') else {}
            if self.transition is not None and request.get("replacementPolicy", "reject") != "reject":
                entry["status"]["state"] = "WAITING"
                transition = self.transition(candidate, entry["cancelled"], request["replacementPolicy"], **source)
            prepared = self.prepare(candidate, entry['cancelled'], **source)
            with self.lock:
                if entry["cancelled"].is_set():
                    entry["status"].update(state="CANCELLED", code="E_CONFIG_CANCELLED")
                    return
                self._check(request)
                with self.cycleLock:
                    leases = [*self.cycles.values(), *(lease for children in self.childCycles.values() for lease in children.values())]
                    if len({value["snapshot"]["executionRevision"] for value in leases}) >= 16:
                        raise ConfigurationError("E_CONFIG_PLAN_CAPACITY", "in-flight plan capacity reached")
                if self.beforeCommit is not None:
                    self.beforeCommit(candidate, **source)
                sourcePath = getattr(prepared, "sourcePath", "") or self.current["sourcePath"]
                if initial:
                    receipt = dict(state='COMMITTED', requestId=request['requestId'], runtimeInstanceId=self.runtimeInstanceId,
                                   documentRevision=1, executionRevision=1, message='首次写入成功；未启动任务')
                    self.current = self.store.initialize(candidate, sourcePath,
                        receipt=receipt, fingerprint=entry['fingerprint'])
                else:
                    receipt = self.store.commit(request["expectedRevision"], candidate, request["requestId"],
                                                entry["fingerprint"], self.runtimeInstanceId, sourcePath=sourcePath)
                    self.current = dict(payload=candidate, sourcePath=sourcePath,
                        documentRevision=receipt["documentRevision"], executionRevision=receipt["executionRevision"])
                entry["status"] = receipt
                # Publication must only install prepared software state. An unexpected
                # publication failure cannot erase the SQLite commit or invite a retry.
                try:
                    self.publish(prepared, receipt)
                except Exception as error:
                    self.publicationError = str(error)
                    entry["status"] = dict(receipt, publicationError=str(error))
                    self.store.recordTransition(entry['status'])
        except Exception as error:
            with self.lock:
                entry["status"].update(state="CANCELLED" if entry["cancelled"].is_set() else "FAILED",
                    code=getattr(error, "code", "E_CONFIG_VALIDATION"), message=str(error))
                from dataclasses import asdict
                from emo_master.core.workflow.errors import WorkflowCompileError
                if isinstance(error, WorkflowCompileError):
                    entry['status']['issues'] = [asdict(issue) for issue in error.issues]
        finally:
            if prepared is not None and self.discard is not None:
                try:
                    self.discard(prepared)
                except Exception as error:
                    with self.lock:
                        self.publicationError = str(error)
                        entry["status"]["cleanupError"] = str(error)
            with self.lock:
                try:
                    if transition is not None:
                        transition.finish(entry["status"])
                except Exception as error:
                    entry["status"]["connectionError"] = str(error)
                finally:
                    self.active = None

    def status(self, requestId):
        with self.lock:
            if requestId in self.requests:
                return deepcopy(self.requests[requestId]["status"])
            saved = self.store.receipt(requestId)
            if saved:
                status = saved[1]
                if status["runtimeInstanceId"] != self.runtimeInstanceId:
                    status["connections"] = {key: "UNKNOWN_AFTER_RESTART" if value == "PENDING" else value
                        for key, value in status.get("connections", {}).items()}
                return status
            raise ConfigurationError("E_CONFIG_REQUEST_UNKNOWN", "request not found; read online revision before any new write")

    def beginCycle(self, owner, knownRevision, cancelled=lambda: False, *, parentCycleId=None):
        import time
        while True:
            if self.closed or cancelled():
                raise ConfigurationError("E_CONFIG_NOT_READY", "cycle admission cancelled")
            with self.cycleLock:
                blocked = self._parkForDrain(owner, parentCycleId)
            if blocked:
                time.sleep(.02)
                continue
            # Shutdown can hold the project lock while waiting for this bridge.
            if not self.lock.acquire(timeout=.02):
                continue
            try:
                if self.closed or cancelled() or self.current is None or self.publicationError:
                    raise ConfigurationError("E_CONFIG_NOT_READY", "online plan is unavailable")
                with self.cycleLock:
                    if self._parkForDrain(owner, parentCycleId):
                        continue
                    if parentCycleId is not None:
                        self.requireCycle(owner, parentCycleId)
                        parent = self._cycleLease(owner, parentCycleId)
                        if not parent["allowChildRefresh"]:
                            return self._cycleResult(parent, knownRevision, ownsCycle=False)
                        if len(self.childCycles.get(owner, {})) >= 32:
                            raise ConfigurationError("E_CYCLE_DEPTH", "nested cycle capacity reached")
                    elif owner in self.cycles:
                        raise ConfigurationError("E_CYCLE_ACTIVE", "previous root cycle has not retired")
                    cycleId = uuid4().hex
                    snapshot = self.current
                    lease = dict(cycleId=cycleId, snapshot=snapshot, parentCycleId=parentCycleId,
                                 allowChildRefresh=parentCycleId is None)
                    if self.cycleIdentity is not None:
                        # Capture the Runtime backends once, alongside the plan.
                        # Retained consumers may use devices the source does not.
                        lease.update(self.cycleIdentity())
                    if parentCycleId is None:
                        self.cycles[owner] = lease
                    else:
                        self.childCycles.setdefault(owner, {})[cycleId] = lease
                    return self._cycleResult(lease, knownRevision, ownsCycle=True)
            finally:
                self.lock.release()

    def _parkForDrain(self, owner, parentCycleId):
        # A long-lived root may contain explicit iteration cycles. Only park
        # between outer iterations; nested cycles must finish the active one.
        if parentCycleId is not None:
            self.requireCycle(owner, parentCycleId)
        createsDetection = parentCycleId is None or self._cycleLease(owner, parentCycleId)["allowChildRefresh"]
        blocked = owner in self.drainingOwners and createsDetection
        if blocked:
            self.parkedOwners.add(owner)
        else:
            self.parkedOwners.discard(owner)
        return blocked

    def _cycleLease(self, owner, cycleId):
        root = self.cycles.get(owner)
        return root if root and root["cycleId"] == cycleId else self.childCycles[owner][cycleId]

    @staticmethod
    def _cycleResult(lease, knownRevision, *, ownsCycle):
        snapshot = lease["snapshot"]
        result = dict(cycleId=lease["cycleId"], planRevision=snapshot["executionRevision"],
                      documentRevision=snapshot["documentRevision"], ownsCycle=ownsCycle)
        for key in ('deviceScope', 'deviceIdentities'):
            if key in lease:
                result[key] = deepcopy(lease[key])
        if knownRevision != snapshot["executionRevision"]:
            result["payload"] = deepcopy(snapshot["payload"])
        return result

    def requireCycle(self, owner, cycleId):
        with self.cycleLock:
            cycle = self.cycles.get(owner)
            if cycle is None or cycle["cycleId"] != cycleId:
                cycle = self.childCycles.get(owner, {}).get(cycleId)
            if cycle is None:
                raise ConfigurationError("E_CYCLE_STALE", "device request has no matching root cycle lease")
            return cycle["snapshot"]["executionRevision"]

    def cyclePayload(self, owner, cycleId):
        with self.cycleLock:
            self.requireCycle(owner, cycleId)
            return deepcopy(self._cycleLease(owner, cycleId)['snapshot']['payload'])

    def retainCycle(self, sourceOwner, cycleId, owner):
        """Queued consumers pin the source plan without holding the project lock."""
        with self.cycleLock:
            self.requireCycle(sourceOwner, cycleId)
            source = self.cycles.get(sourceOwner)
            if source is None or source["cycleId"] != cycleId:
                source = self.childCycles[sourceOwner][cycleId]
            if owner in self.cycles:
                raise ConfigurationError("E_CYCLE_ACTIVE", "consumer already owns a cycle")
            self.cycles[owner] = dict(source, parentCycleId=None, allowChildRefresh=False)
            return self._cycleResult(source, None, ownsCycle=False)

    def endCycle(self, owner, cycleId):
        with self.cycleLock:
            self.requireCycle(owner, cycleId)
            children = self.childCycles.get(owner, {})
            if any(lease["parentCycleId"] == cycleId for lease in children.values()):
                raise ConfigurationError("E_CYCLE_ACTIVE", "child cycles must retire before their parent")
            if cycleId in children:
                del children[cycleId]
                if not children:
                    self.childCycles.pop(owner, None)
            else:
                del self.cycles[owner]

    def retireOwner(self, owner):
        with self.cycleLock:
            self.parkedOwners.discard(owner)
            self.cycles.pop(owner, None)
            self.childCycles.pop(owner, None)

    def cancel(self, requestId):
        with self.lock:
            entry = self.requests.get(requestId)
            if entry and entry["status"]["state"] in {"VALIDATING", "WAITING"}:
                entry["cancelled"].set()
            return self.status(requestId)

    def close(self):
        with self.lock:
            self.closed = True
            if self.active:
                self.requests[self.active]["cancelled"].set()
            thread = self.thread
        if thread:
            thread.join(5)
            if thread.is_alive():
                raise ConfigurationError("E_CONFIG_RELEASE_PENDING", "configuration preparation has not retired")
