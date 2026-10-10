from __future__ import annotations

from collections import OrderedDict, deque
import base64
from copy import deepcopy
from dataclasses import dataclass, field
import queue
import threading
import time
from uuid import uuid4

from emo_master.apps.runtime.jobs.heartbeat import monotonicMs
from emo_master.apps.runtime.operator_debug.contracts import (
    DebugError, MAX_INLINE_BYTES, MAX_REQUEST_BYTES, digest, draft, encode, fail,
    identifier, executionParams, parse,
)
from emo_master.apps.runtime.operator_debug.assets import CACHE_BYTES, CHUNK_BYTES, DebugAssets
from emo_master.apps.runtime.operator_debug.data import prepareInputs, retainedAssetIds
from emo_master.apps.runtime.operator_debug.state import DebugVariables
from emo_master.apps.runtime.operator_debug.worker import DebugWorker


@dataclass
class Session:
    sessionId: str
    spec: dict
    generation: int = 1
    state: str = "STARTING"
    worker: object = None
    assets: object = None
    variables: dict = field(default_factory=dict)
    expiresAt: float = 0
    deadline: float = 0
    retiringAt: float = 0
    cancellationAt: float = 0
    active: str = ""
    cancelCode: str = ""
    code: str = ""
    message: str = ""
    resetting: bool = False
    resetStarting: bool = False
    forced: bool = False
    closedAck: bool = False
    sequence: int = 0
    executions: OrderedDict = field(default_factory=OrderedDict)
    prepared: OrderedDict = field(default_factory=OrderedDict)
    requests: dict = field(default_factory=dict)
    events: deque = field(default_factory=lambda: deque(maxlen=1000))
    flow: dict = field(default_factory=dict)
    checkpoints: OrderedDict = field(default_factory=OrderedDict)


class OperatorDebugManager:
    def __init__(self, runtimeInstanceId, admitted, *, resourceBusy=lambda: False,
                 leaseSeconds=60.0, graceSeconds=3.0, workerFactory=DebugWorker,
                 maxRequests=4096, maxResults=32, maxOpens=128, sourceProvider=None, variableProvider=None):
        self.runtimeInstanceId = runtimeInstanceId
        self.admitted = admitted
        self.resourceBusy = resourceBusy
        self.sourceProvider, self.variableProvider = sourceProvider, variableProvider
        self.leaseSeconds = leaseSeconds
        self.graceSeconds = graceSeconds
        self.workerFactory = workerFactory
        self.maxRequests, self.maxResults, self.maxOpens = maxRequests, maxResults, maxOpens
        self.lock = threading.RLock()
        self.sessions: dict[str, Session] = {}
        self.opens: dict[str, tuple[str, str]] = {}
        self.closing = False
        self.stop = threading.Event()
        self.monitor = threading.Thread(target=self._monitor, name="operator-debug-monitor", daemon=True)
        self.monitor.start()

    def ownsResources(self):
        with self.lock:
            return any(s.worker is not None for s in self.sessions.values())

    def capabilities(self, registered):
        return dict(protocolVersion=1, runtimeInstanceId=self.runtimeInstanceId,
                    limits=dict(maxSessions=1, leaseMs=int(self.leaseSeconds*1000), renewMs=20000,
                                graceMs=int(self.graceSeconds*1000), defaultTimeoutMs=30000,
                                maxTimeoutMs=30000, maxRequests=self.maxRequests, maxResults=self.maxResults,
                                maxOpenRequests=self.maxOpens, inlineBytes=MAX_INLINE_BYTES,
                                requestBytes=MAX_REQUEST_BYTES, assetTransport=True,
                                cacheBytes=CACHE_BYTES, chunkBytes=CHUNK_BYTES),
                    operators=[dict(operatorId=key, supported=key in self.admitted,
                                    reason="" if key in self.admitted else "No reviewed debug resource or side-effect adapter",
                                    **self.admitted.get(key, {})) for key in sorted(registered)])

    def _runtime(self, request):
        if request.get("runtimeInstanceId") != self.runtimeInstanceId:
            fail("E_DEBUG_STALE_SESSION", "Runtime changed; do not replay an old command")

    def _session(self, request, *, generation=True, active=False):
        self._runtime(request)
        sessionId = request.get("sessionId")
        if not sessionId and request.get("openRequestId") in self.opens:
            sessionId = self.opens[request["openRequestId"]][1]
        session = self.sessions.get(sessionId)
        if session is None:
            fail("E_DEBUG_SESSION_EXPIRED", "unknown debug session")
        if request.get("debugKind", "operator") != session.spec.get("kind", "operator"):
            fail("E_DEBUG_CONTEXT_INVALID", "debug session kind differs from the RPC namespace")
        if generation and request.get("generation") != session.generation:
            fail("E_DEBUG_STALE_SESSION", "debug generation changed")
        if active:
            if self.closing or session.expiresAt <= time.monotonic() or session.state in {"CLOSED", "FAULTED", "CLOSING"}:
                fail("E_DEBUG_SESSION_EXPIRED", "debug session is closing or expired")
        return session

    def _event(self, session, kind, **payload):
        session.sequence += 1
        session.events.append(dict(sequence=session.sequence, type=kind, generation=session.generation, **payload))

    def _view(self, session):
        return dict(sessionId=session.sessionId, generation=session.generation, state=session.state,
                    resourcesHeld=session.worker is not None, activeExecutionId=session.active,
                    code=session.code, message=session.message, lastSequence=session.sequence,
                    draftDigest=session.spec["draftDigest"], operatorId=session.spec["operatorId"],
                    variables=deepcopy(session.variables), cacheBytes=session.assets.used if session.assets else 0,
                    debugKind=session.spec.get("kind", "operator"), flow=deepcopy(session.flow),
                    snapshots=[dict(snapshotId=key, phase=row.get("phase", ""), nodeId=row.get("nodeId", ""),
                                    identity=row.get("identity", {})) for key, row in session.checkpoints.items()],
                    ttlMs=max(0, int((session.expiresAt-time.monotonic())*1000)))

    def _dedup(self, session, action, request):
        requestId = identifier(request.get("requestId"))
        fingerprint = digest([action, request])
        old = session.requests.get(requestId)
        if old is not None:
            if old[0] != fingerprint:
                fail("E_DEBUG_REQUEST_CONFLICT", "request ID was already used for different content")
            return old[1]
        reserve = 1 if action == "cancel" else 2 if action == "close" else 0
        if len(session.requests) >= self.maxRequests + reserve:
            fail("E_DEBUG_LIMIT", "request ledger is full; close this session and create a new one")
        return None

    def _remember(self, session, action, request, response):
        session.requests[request["requestId"]] = (digest([action, request]), response)
        return response

    def call(self, action, request):
        with self.lock:
            try:
                return self._call(action, request)
            except DebugError as error:
                if error.code == "E_RESOURCE_CLEANUP_FAILED":
                    session = self.sessions.get(request.get("sessionId"))
                    if session is not None:
                        session.code, session.message = error.code, str(error)
                        session.resetting = False
                        self._retire(session)
                raise

    def _call(self, action, request):
        with self.lock:
            self._runtime(request)
            if action == "open":
                return self._open(request)
            if action == "get":
                return self._view(self._session(request, generation=False))
            # Reset retries retain the original acceptance even after generation changes.
            if action in {"reset", "close"}:
                session = self._session(request, generation=False)
                duplicate = self._dedup(session, action, request)
                if duplicate is not None:
                    return duplicate
            session = self._session(request, active=action in {"prepare", "execute", "reset", "renew", "upload", "import", "copyVariables", "flow_start", "flow_control"})
            if session.spec.get("kind") == "workflow" and action in {"execute", "reset", "copyVariables", "import", "sources", "cancel"}:
                fail("E_DEBUG_UNSUPPORTED", "operation is unavailable for workflow sessions")
            if action == "flow_command":
                entry = session.requests.get(request.get("requestId"))
                if entry is None:
                    fail("E_DEBUG_RESULT_EXPIRED", "unknown control request; do not replay with a new ID")
                return deepcopy(entry[1])
            if action == "flow_snapshot":
                row = session.checkpoints.get(request.get("executionId"))
                if row is None:
                    fail("E_DEBUG_RESULT_EXPIRED", "workflow snapshot expired")
                return deepcopy(row)
            if action == "asset":
                if session.assets is None:
                    fail("E_DEBUG_RESULT_EXPIRED", "asset owner has retired")
                content, metadata = session.assets.read(request.get("assetId"), request.get("offset", 0))
                return dict(asset=metadata, content=content)
            if action == "sources":
                if self.sourceProvider is None:
                    return dict(sources=[], nextOffset=0)
                return self.sourceProvider(session.spec, request, False)
            if action == "execution":
                executionId = request.get("executionId")
                if not executionId:
                    entry = session.requests.get(request.get("requestId"))
                    executionId = entry[1].get("executionId") if entry else None
                result = session.executions.get(executionId)
                if result is None:
                    fail("E_DEBUG_RESULT_EXPIRED", "execution result unavailable; never re-execute this request ID")
                return dict(result)
            if action == "events":
                after = request.get("afterSequence", 0)
                limit = min(100, max(1, request.get("limit", 100)))
                selected = []
                size = 2
                # Budget one event at a time; a valid large log must advance the cursor.
                for event in session.events:
                    if event["sequence"] <= after:
                        continue
                    eventSize = len(encode(event, MAX_REQUEST_BYTES)) + bool(selected)
                    if size + eventSize > MAX_REQUEST_BYTES // 2:
                        break
                    selected.append(event)
                    size += eventSize
                    if len(selected) == limit:
                        break
                return dict(events=selected, nextSequence=selected[-1]["sequence"] if selected else after,
                            gap=bool(session.events and after < session.events[0]["sequence"]-1))
            if action == "renew":
                session.expiresAt = time.monotonic() + self.leaseSeconds
                return self._view(session)
            duplicate = self._dedup(session, action, request)
            if duplicate is not None:
                return duplicate
            if action in {"flow_start", "flow_control"}:
                from emo_master.apps.runtime.workflow_debug.manager import control
                response = control(self, session, action, request)
            elif action in {"import", "copyVariables"}:
                if session.state != "READY":
                    fail("E_RESOURCE_BUSY", "snapshot copy requires an idle session")
                if action == "import":
                    if self.sourceProvider is None:
                        fail("E_DEBUG_UNSUPPORTED", "historical source provider unavailable")
                    value, provenance = self.sourceProvider(session.spec, request, True)
                    response = dict(asset=session.assets.put(value, provenance))
                else:
                    if self.variableProvider is None:
                        fail("E_DEBUG_UNSUPPORTED", "production variable snapshot unavailable")
                    copied = self.variableProvider(session.spec)
                    session.variables = DebugVariables(session.spec.get("variableDefinitions", {}),
                        dict(session.variables, **copied)).values
                    response = dict(variables=deepcopy(session.variables), copied=list(copied))
            elif action == "upload":
                if session.state != "READY":
                    fail("E_RESOURCE_BUSY", "upload requires an idle session")
                response = session.assets.upload(assetId=request.get("assetId", ""), offset=request.get("offset", 0),
                    total=request.get("totalBytes", 0), data=base64.b64decode(request["content"], validate=True),
                    mime=request.get("mimeType", ""), sha256=request.get("sha256", ""),
                    provenance=parse(request.get("provenanceJson", "{}")))
            elif action == "prepare":
                if session.state != "READY":
                    fail("E_RESOURCE_BUSY", "session is not idle")
                _, ports, _ = executionParams(session.spec, request.get("paramsJson", "{}"), session.variables)
                values, sources = prepareInputs(request.get("inputs", {}), ports, session.assets, session.executions)
                inputId = uuid4().hex
                session.prepared[inputId] = dict(values=values, ports=ports, sources=sources)
                while len(session.prepared) > self.maxResults:
                    session.prepared.popitem(last=False)
                session.assets.collectOutputs(retainedAssetIds(session))
                response = dict(inputSetId=inputId, digest=digest(values), sources=sources)
            elif action == "execute":
                if session.state != "READY":
                    fail("E_RESOURCE_BUSY", "one execution at a time; wait for READY")
                inputId = request.get("inputSetId")
                if inputId not in session.prepared:
                    fail("E_DEBUG_RESULT_EXPIRED", "input set expired")
                timeout = request.get("timeoutMs", 0) or 30000
                if not 1 <= timeout <= 30000:
                    fail("E_DEBUG_LIMIT", "timeout must be within 1..30000 ms")
                params, inputPorts, outputPorts = executionParams(session.spec, request.get("paramsJson", "{}"), session.variables)
                prepared = session.prepared[inputId]
                if prepared["ports"] != inputPorts:
                    fail("E_INPUT_TYPE", "parameter changes changed input ports; prepare inputs again")
                executionId = uuid4().hex
                command = dict(executionId=executionId, inputs=prepared["values"], params=params,
                    inputPorts=inputPorts, outputPorts=outputPorts, variables=session.variables,
                    outputBudget=CACHE_BYTES-session.assets.used)
                encode(command)
                session.worker.execute(command)
                session.active = executionId
                session.deadline = time.monotonic() + timeout/1000
                session.cancelCode = ""
                session.cancellationAt = 0
                session.state = "RUNNING"
                session.executions[executionId] = dict(executionId=executionId, status="RUNNING",
                    runtimeInstanceId=self.runtimeInstanceId, sessionId=session.sessionId, requestId=request["requestId"],
                    inputSetId=inputId, rawParams=parse(request.get("paramsJson", "{}")), effectiveParams=params,
                    inputSources=prepared["sources"], bindingSources={binding["variableId"]: session.variables[binding["variableId"]]
                        for binding in session.spec.get("variableBindings", [])},
                    sourceIdentity=dict(projectId=session.spec["projectId"], workflowId=session.spec["workflowId"],
                                        nodeId=session.spec["nodeId"], draftDigest=session.spec["draftDigest"],
                                        operatorVersion=session.spec["version"], generation=session.generation))
                while len(session.executions) > self.maxResults:
                    session.executions.popitem(last=False)
                session.assets.collectOutputs(retainedAssetIds(session))
                self._event(session, "execution.started", executionId=executionId)
                response = dict(executionId=executionId, status="ACCEPTED")
            elif action == "cancel":
                target = request.get("executionId")
                if target not in session.executions:
                    fail("E_DEBUG_RESULT_EXPIRED", "execution unavailable")
                if target == session.active:
                    self._cancel(session, "E_CANCELLED")
                response = dict(executionId=target, state=session.state)
            elif action == "reset":
                if session.state != "READY":
                    fail("E_RESOURCE_BUSY", "reset requires an idle session")
                session.resetting = True
                self._retire(session)
                session.state = "RESETTING"
                response = self._view(session)
            elif action == "close":
                session.resetting = False
                self._retire(session)
                response = self._view(session)
            else:
                fail("E_DEBUG_UNSUPPORTED", "unknown debug operation")
            return self._remember(session, action, request, response)

    def _open(self, request):
        requestId = identifier(request.get("openRequestId"))
        fingerprint = digest(request)
        old = self.opens.get(requestId)
        if old:
            if old[0] != fingerprint:
                fail("E_DEBUG_REQUEST_CONFLICT", "open request ID conflict")
            return self._view(self.sessions[old[1]])
        if self.closing:
            fail("E_RUNTIME_CLOSING", "Runtime is closing")
        if len(self.opens) >= self.maxOpens:
            fail("E_DEBUG_LIMIT", "Runtime open-request ledger is full")
        if self.ownsResources() or self.resourceBusy():
            fail("E_RESOURCE_BUSY", "another owner still holds Runtime resources")
        if request.get("debugKind") == "workflow":
            from emo_master.apps.runtime.workflow_debug.draft import workflowDraft
            spec = workflowDraft(request, self.admitted)
        else:
            spec = draft(request["projectJson"], request["projectId"], request["workflowId"], request["nodeId"],
                         request["operatorId"], self.admitted)
        if request.get("resourceRoot"):
            fail("E_DEBUG_UNSUPPORTED", "resource roots require the A3 resource adapter")
        encode(spec, MAX_REQUEST_BYTES if spec.get("kind") == "workflow" else MAX_INLINE_BYTES)
        session = Session(uuid4().hex, spec, expiresAt=time.monotonic()+self.leaseSeconds,
                          variables=DebugVariables(spec.get("variableDefinitions", {})).values)
        # Record acceptance before starting a process. A failed start must not replay.
        self.sessions[session.sessionId] = session
        self.opens[requestId] = (fingerprint, session.sessionId)
        try:
            session.worker = self.workerFactory(spec)
            session.assets = DebugAssets(session.worker.workspace.name)
            session.worker.start()
        except Exception as error:
            session.state, session.code = "FAULTED", "E_DEBUG_WORKER_START"
            session.message = str(error)[:512]
            self._retire(session)
        self._event(session, "session.opened")
        return self._view(session)

    def _cancel(self, session, code):
        if not session.cancellationAt:
            session.cancelCode = code
            session.cancellationAt = time.monotonic()
        session.worker.cancel.set()
        session.state = "CANCELLING"

    def _retire(self, session):
        if session.worker is None:
            return
        if session.active:
            self._cancel(session, "E_CANCELLED")
        if not session.retiringAt:
            session.retiringAt = time.monotonic()
        session.state = "CLOSING"
        session.worker.requestStop()

    def _consume(self, session, event):
        kind = event.get("kind")
        if kind.startswith("flow_"):
            from emo_master.apps.runtime.workflow_debug.manager import consume
            consume(self, session, event)
        elif kind == "ready" and session.state == "STARTING":
            if session.resetStarting:
                session.generation += 1
                session.resetStarting = False
            session.state = "READY"
            self._event(session, "session.ready")
        elif kind == "result" and event.get("executionId") == session.active:
            record = session.executions[session.active]
            for entry in event.get("outputAssets", {}).values():
                session.assets.adopt(entry)
            if "variables" in event:
                session.variables = DebugVariables(session.spec.get("variableDefinitions", {}), event["variables"]).values
            record.update({k: v for k, v in event.items() if k != "kind"})
            if event.get("finishedAtMs", 0) > session.deadline * 1000 and not session.cancelCode:
                session.cancelCode = "E_DEBUG_TIMEOUT"
            if session.cancelCode and record["status"] in {"SUCCEEDED", "CANCELLED"}:
                record.update(status="TIMED_OUT" if session.cancelCode == "E_DEBUG_TIMEOUT" else "CANCELLED",
                              code=session.cancelCode)
            self._event(session, "execution.finished", executionId=session.active, status=record["status"])
            session.active = ""
            session.cancellationAt = 0
            if record.get("code") == "E_RESOURCE_CLEANUP_FAILED":
                session.code = "E_RESOURCE_CLEANUP_FAILED"
                session.resetting = False
                self._retire(session)
            elif not session.retiringAt:
                session.state = "READY"
        elif kind == "closed":
            session.closedAck = True
        elif kind == "fault":
            session.code = event.get("code", "E_DEBUG_WORKER_FAILED")
            session.message = event.get("message", "worker failed")
            session.resetting = False
            self._retire(session)
        elif kind == "log":
            self._event(session, "node.log", executionId=event.get("executionId", ""), event=event.get("event", {}))

    def _tick(self, session):
        worker = session.worker
        if worker is None:
            return
        for _ in range(128):
            try:
                self._consume(session, worker.inbound.get_nowait())
            except queue.Empty:
                break
        if worker.droppedLogs:
            dropped = worker.droppedLogs
            worker.droppedLogs -= dropped
            self._event(session, "logs.dropped", count=dropped)
        now = time.monotonic()
        if any(session.flow.get(key, 0) and now >= session.flow[key] for key in ("nodeDeadline", "trialDeadline")):
            session.flow.update(nodeDeadline=0, trialDeadline=0)
            session.code = "E_DEBUG_TIMEOUT"
            self._retire(session)
        if now >= session.expiresAt:
            session.code = session.code or "E_DEBUG_SESSION_EXPIRED"
            session.resetting = False
            self._retire(session)
        if session.active and now >= session.deadline and not session.cancellationAt:
            self._cancel(session, "E_DEBUG_TIMEOUT")
        sample = worker.heartbeat.read()
        worker.lastHeartbeatMs = max(getattr(worker, "lastHeartbeatMs", worker.started), sample or -1)
        if not session.retiringAt and monotonicMs() - worker.lastHeartbeatMs > 30000:
            session.code = "E_DEBUG_HEARTBEAT_TIMEOUT"
            self._retire(session)
        if worker.fault and not session.closedAck and not session.forced:
            session.code, session.message = session.code or "E_DEBUG_IPC", worker.fault
            session.resetting = False
            self._retire(session)
        due = session.retiringAt or session.cancellationAt
        if due and now - due >= self.graceSeconds:
            worker.requestStop()
            if worker.terminate():
                session.forced = True
                session.resetting = False
        if not worker.retire():
            return
        session.worker = None
        session.assets = None
        if session.active:
            session.executions[session.active].update(status="UNKNOWN", code=session.cancelCode or "E_DEBUG_WORKER_EXIT",
                                                     message="worker exited without a confirmed execution result")
            self._event(session, "execution.finished", executionId=session.active, status="UNKNOWN")
            session.active = ""
        if not session.closedAck:
            session.code = session.code or "E_DEBUG_WORKER_EXIT"
        if session.resetting and not session.code and session.closedAck and not session.forced:
            session.prepared.clear()
            session.executions.clear()
            session.variables = DebugVariables(session.spec.get("variableDefinitions", {})).values
            session.resetting, session.resetStarting = False, True
            session.retiringAt, session.cancellationAt = 0, 0
            session.closedAck = False
            session.state = "STARTING"
            try:
                session.worker = self.workerFactory(session.spec)
                session.assets = DebugAssets(session.worker.workspace.name)
                session.worker.start()
            except Exception as error:
                session.code, session.message = "E_DEBUG_WORKER_START", str(error)[:512]
                session.state = "FAULTED"
                self._retire(session)
        else:
            session.state = "FAULTED" if session.code or session.forced else "CLOSED"
            session.prepared.clear()
            session.checkpoints.clear()
            self._event(session, "session.retired", code=session.code, forced=session.forced)
            # Closed records are bounded tombstones, not an accumulating data store.
            while len(session.executions) > 1:
                session.executions.popitem(last=False)
            session.events = deque(list(session.events)[-16:], maxlen=1000)

    def _monitor(self):
        while not self.stop.wait(.02):
            with self.lock:
                for session in self.sessions.values():
                    try:
                        self._tick(session)
                    except Exception as error:
                        # Failed retirement stays owned and is retried; never free its slot.
                        session.code = "E_RESOURCE_CLEANUP_FAILED"
                        session.message = str(error)[:512]
                        session.resetting = False
                        if session.worker is not None:
                            try:
                                self._retire(session)
                            except Exception:
                                pass

    def close(self):
        with self.lock:
            self.closing = True
            for session in self.sessions.values():
                session.resetting = False
                self._retire(session)
        deadline = time.monotonic() + self.graceSeconds + 2
        while self.ownsResources() and time.monotonic() < deadline:
            time.sleep(.02)
        if self.ownsResources():
            raise DebugError("E_RESOURCE_CLEANUP_FAILED", "debug process or IPC is still held")
        self.stop.set()
        self.monitor.join(1)
        if self.monitor.is_alive():
            raise DebugError("E_RESOURCE_CLEANUP_FAILED", "debug monitor is still held")
