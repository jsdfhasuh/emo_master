from __future__ import annotations

import multiprocessing
from collections import deque
from contextlib import contextmanager
import threading
import time
import weakref
from dataclasses import replace
from typing import Any, Callable, cast

from emo_master.apps.runtime.events.event_store import EventStore
from emo_master.apps.runtime.jobs.event_bridge import EventBridge
from emo_master.apps.runtime.jobs.heartbeat import HeartbeatCell, monotonicMs
from emo_master.apps.runtime.jobs.models import JobProcessSpec, JobStatus, nowMs
from emo_master.apps.runtime.jobs.repository import JobRepository
from emo_master.apps.runtime.jobs.finalization import AttemptWaiter, RetirementReceipt, StopOutcome, TerminalTicket, errorText, faultSummary
from emo_master.apps.runtime.jobs.worker_main import runJobProcess


class JobSupervisor:
    def __init__(
        self,
        jobRepository: JobRepository,
        eventStore: EventStore,
        maxConcurrentJobs: int = 2,
        gracefulStopTimeoutMs: int = 5000,
        heartbeatTimeoutMs: int = 5000,
        terminalCallback: Callable[[str, str], None] | None = None,
        retiredCallback: Callable[[str], None] | None = None,
    ) -> None:
        self.jobRepository = jobRepository
        self.eventStore = eventStore
        self.maxConcurrentJobs = max(1, maxConcurrentJobs)
        self.gracefulStopTimeoutMs = max(0, gracefulStopTimeoutMs)
        self.heartbeatTimeoutMs = max(100, heartbeatTimeoutMs)
        self._terminalCallback = terminalCallback
        self._callbackEpoch = 0
        self._presentationEpoch: int | None = None
        self.retiredCallback = retiredCallback
        self.presentationCallback: Callable | None = None
        self.presentationErrors: deque[str] = deque(maxlen=64)
        self._context = multiprocessing.get_context("spawn")
        self._handles: dict[str, tuple[Any, Any, Any]] = {}
        self._bridges: dict[str, EventBridge] = {}
        self._heartbeat: dict[str, int] = {}
        self._heartbeatCells: dict[str, HeartbeatCell] = {}
        self._heartbeatMonotonic: dict[str, int] = {}
        self._heartbeatIntervals: dict[str, int] = {}
        self._heartbeatTimeouts: dict[str, int] = {}
        self._heartbeatSeen: set[str] = set()
        self._terminalEvents: set[str] = set()
        self._lock = threading.RLock()
        self._closing = False
        self._finalizations: dict[str, TerminalTicket] = {}
        self._recoveries: dict[str, TerminalTicket] = {}
        self._terminalFIFO: deque[TerminalTicket] = deque()
        self._terminalOrdinal = 0
        self._attemptThreads: weakref.WeakSet = weakref.WeakSet()
        self._receipts: deque[TerminalTicket] = deque(maxlen=64)
        self._retirements: dict[str, RetirementReceipt] = {}
        self._safeRetirement: set[str] = set()
        self._failedStartRetirement: set[str] = set()
        self.retirementRepairFactory: Callable | None = None

    def startJob(self, spec: JobProcessSpec) -> None:
        self.assertMutationAllowed()
        with self._lock:
            active = len(self._ownedJobs())
            if active >= self.maxConcurrentJobs:
                raise RuntimeError("E_MAX_CONCURRENT_JOBS: maximum concurrent jobs reached")
            if self._closing:
                raise RuntimeError("E_RUNTIME_CLOSING")
            if spec.jobId in self._handles:
                raise ValueError(f"duplicate job id: {spec.jobId}")

            cancelEvent = self._context.Event()
            # Continuous diagnostics must backpressure the producer, not accumulate
            # an unbounded feeder backlog that outlives cancellation and retention.
            eventQueue = self._context.Queue(maxsize=64) if spec.continuous else self._context.Queue()
            heartbeatTimeoutMs = max(100, spec.heartbeatTimeoutMs or self.heartbeatTimeoutMs)
            heartbeatIntervalMs = min(
                max(50, spec.heartbeatIntervalMs),
                max(50, heartbeatTimeoutMs // 3),
            )
            if heartbeatIntervalMs != spec.heartbeatIntervalMs:
                spec = replace(spec, heartbeatIntervalMs=heartbeatIntervalMs)
            process = None
            bridge: EventBridge | None = None
            try:
                cell = HeartbeatCell(self._context)
                spec = replace(spec, heartbeatCell=cell)
                process = self._context.Process(
                    target=runJobProcess,
                    args=(spec, cancelEvent, eventQueue),
                    name=f"emo-master-job-{spec.jobId}",
                )
                self._heartbeatCells[spec.jobId] = cell
                self._heartbeatMonotonic[spec.jobId] = monotonicMs()
                process.start()
                self._handles[spec.jobId] = (process, cancelEvent, eventQueue)
                self._heartbeat[spec.jobId] = nowMs()
                # Match the legacy startup grace: process.start() itself is
                # outside the initial heartbeat observation window.
                self._heartbeatMonotonic[spec.jobId] = monotonicMs()
                self._heartbeatIntervals[spec.jobId] = heartbeatIntervalMs
                self._heartbeatTimeouts[spec.jobId] = heartbeatTimeoutMs
                self._heartbeatSeen.discard(spec.jobId)
                self.jobRepository.update(
                    spec.jobId,
                    status=JobStatus.STARTING.value,
                    pid=getattr(process, "pid", None),
                )
                bridge = EventBridge(self, spec.jobId, process, eventQueue, cancelEvent)
                self._bridges[spec.jobId] = bridge
                bridge.start()
            except BaseException:
                self._cleanupFailedStart(
                    spec.jobId,
                    process,
                    cancelEvent,
                    eventQueue,
                    bridge,
                )
                raise

    def consumeWorkerEvent(self, jobId: str, event: dict[str, object]) -> None:
        self.assertMutationAllowed()
        with self._terminalAttempt() as claim:
            with self._lock:
                record = self.jobRepository.get(jobId)
                if record is None or jobId in self._terminalEvents or record.isTerminal:
                    return

                eventType = str(event.get("eventType", "process.event"))
                if eventType.startswith("display.") and self.presentationCallback is not None:
                    try:
                        self.presentationCallback(jobId, event)
                    except Exception as error:
                        # A display consumer failure must not become a detector crash.
                        # Pending source/terminal fences provide unavailable results.
                        self.presentationErrors.append(repr(error))
                    return
                if eventType == "process.heartbeat":
                    self._heartbeat[jobId] = nowMs()
                    self._heartbeatSeen.add(jobId)

                projectId = str(event.get("projectId", "")) or record.projectId
                workflowId = str(event.get("workflowId", "")) or record.workflowId
                contextIteration = event.get("iterationPath", [])
                iterationPath = (
                    tuple(item for item in contextIteration if isinstance(item, int))
                    if isinstance(contextIteration, list)
                    else ()
                )
                stored = self.eventStore.append(
                    jobId=jobId,
                    eventType=eventType,
                    message=str(event.get("message", eventType)),
                    level=str(event.get("level", "INFO")),
                    nodeId=str(event.get("nodeId", "")),
                    code=str(event.get("code", "")),
                    payload=cast(
                        dict[str, object] | None,
                        event.get("payload") if isinstance(event.get("payload"), dict) else {},
                    ),
                    projectId=projectId,
                    workflowId=workflowId,
                    workflowRunId=str(event.get("workflowRunId", "")),
                    parentWorkflowRunId=str(event.get("parentWorkflowRunId", "")),
                    nodeRunId=str(event.get("nodeRunId", "")),
                    iterationPath=iterationPath,
                )
                if eventType == "job.started":
                    self.jobRepository.update(
                        jobId,
                        status=JobStatus.RUNNING.value,
                        startedAtMs=stored.timestampMs,
                        pid=event.get("pid"),
                    )
                elif eventType == "job.completed":
                    self._claimTerminal(claim,
                        jobId,
                        JobStatus.COMPLETED.value,
                        stored.timestampMs,
                        message=stored.message,
                    )
                elif eventType == "job.failed":
                    self._claimTerminal(claim,
                        jobId,
                        JobStatus.FAILED.value,
                        stored.timestampMs,
                        errorCode=stored.code or "E_WORKER_CRASHED",
                        message=stored.message,
                    )
                elif eventType == "job.aborted":
                    self._claimTerminal(claim,
                        jobId,
                        JobStatus.ABORTED.value,
                        stored.timestampMs,
                        errorCode=stored.code,
                        message=stored.message,
                    )

    def processExited(self, jobId: str, exitCode: int | None) -> None:
        self.assertMutationAllowed()
        with self._terminalAttempt(jobId, reapEmpty=True) as claim:
            with self._lock:
                self._safeRetirement.add(jobId)
                record = self.jobRepository.get(jobId)
                if record is not None and not record.isTerminal and jobId not in self._terminalEvents:
                    if record.stopMode in {"force", "graceful"} or record.status == JobStatus.STOPPING.value:
                        status = JobStatus.ABORTED.value
                        code = "E_CANCELLED"
                        message = "job aborted after process exit"
                    else:
                        status = JobStatus.FAILED.value
                        code = (
                            "E_WORKER_CRASHED"
                            if exitCode not in (0, None)
                            else "E_WORKER_NO_TERMINAL"
                        )
                        message = "worker exited without terminal event"
                    eventType = "job.failed" if status == JobStatus.FAILED.value else "job.aborted"
                    stored = self.eventStore.append(
                        jobId,
                        eventType,
                        message,
                        level="ERROR",
                        code=code,
                        projectId=record.projectId,
                        workflowId=record.workflowId,
                    )
                    self._claimTerminal(claim,
                        jobId,
                        status,
                        stored.timestampMs,
                        errorCode=code,
                        message=message,
                    )

    def checkHeartbeat(self, jobId: str) -> None:
        self.assertMutationAllowed()
        with self._terminalAttempt(jobId) as claim:
            with self._lock:
                record = self.jobRepository.get(jobId)
                if record is None or record.isTerminal or jobId in self._terminalEvents:
                    return
                cell = self._heartbeatCells.get(jobId)
                if cell is not None:
                    # The producer clock is authoritative from process start. A
                    # queued heartbeat, unavailable cell or corrupt future sample
                    # must never renew this deadline.
                    sample = cell.read()
                    current = monotonicMs()
                    lastHeartbeat = self._heartbeatMonotonic[jobId]
                    if sample is not None and lastHeartbeat < sample <= current:
                        lastHeartbeat = sample
                        self._heartbeatMonotonic[jobId] = sample
                else:
                    # Compatibility for supervisors/worker callers without a cell.
                    legacyHeartbeat = self._heartbeat.get(jobId)
                    if legacyHeartbeat is None:
                        return
                    lastHeartbeat = legacyHeartbeat
                    current = nowMs()
                timeoutMs = self._heartbeatTimeouts.get(jobId, self.heartbeatTimeoutMs)
                if jobId not in self._heartbeatSeen:
                    intervalMs = self._heartbeatIntervals.get(jobId, 500)
                    timeoutMs = max(timeoutMs, intervalMs * 2)
                if current - lastHeartbeat <= timeoutMs:
                    return
                handle = self._handles.get(jobId)
                if handle is None:
                    return
                process, cancelEvent, _queue = handle
                cancelEvent.set()
                bridge = self._bridges.get(jobId)
                if bridge is not None:
                    bridge.requestStop()
                wasAlive = self._terminateProcess(process)
                message = "worker heartbeat timed out"
                if wasAlive:
                    self.eventStore.append(
                        jobId,
                        "process.terminated",
                        "worker process terminated after heartbeat timeout",
                        level="WARN",
                        code="E_HEARTBEAT_TIMEOUT",
                        projectId=record.projectId,
                        workflowId=record.workflowId,
                    )
                stored = self.eventStore.append(
                    jobId,
                    "job.failed",
                    message,
                    level="ERROR",
                    code="E_HEARTBEAT_TIMEOUT",
                    projectId=record.projectId,
                    workflowId=record.workflowId,
                )
                self._claimTerminal(claim,
                    jobId,
                    JobStatus.FAILED.value,
                    stored.timestampMs,
                    errorCode="E_HEARTBEAT_TIMEOUT",
                    message=message,
                    stopMode="force",
                )

    def stopJob(self, jobId: str, mode: str = "graceful") -> str:
        outcome = self.stopJobOutcome(jobId, mode)
        if outcome.error is not None:
            raise outcome.error
        if not outcome.ok:
            raise RuntimeError(outcome.message)
        return outcome.status

    def stopJobOutcome(self, jobId: str, mode: str = "graceful", context=None) -> StopOutcome:
        claim: list[TerminalTicket] = []
        pending = None
        accepted = None
        missing = False
        try:
            if mode not in {"graceful", "force"}:
                return StopOutcome(False, JobStatus.FAILED.value, "invalid stop mode",
                                   ValueError(f"invalid stop mode: {mode}"))
            self.assertMutationAllowed()
            with self._terminalAttempt(jobId if mode == "force" else None, claim):
                with self._lock:
                    record = self.jobRepository.get(jobId)
                    if record is None:
                        missing = True
                        raise KeyError(jobId)
                    pending = self._finalizations.get(jobId)
                    failure = self._stopFailure(jobId)
                    if pending is None and failure:
                        return StopOutcome(False, record.status, failure)
                    if pending is None and record.isTerminal:
                        return StopOutcome(True, record.status, self._terminalMessage(record))
                    if pending is None:
                        handle = self._handles.get(jobId)
                        if handle is None:
                            missing = True
                            raise KeyError(jobId)
                        process, cancelEvent, _queue = handle
                        self.jobRepository.update(jobId, status=JobStatus.STOPPING.value, stopMode=mode)
                        self.eventStore.append(jobId, "job.stopping", f"job stopping ({mode})",
                                               level="WARN", projectId=record.projectId, workflowId=record.workflowId)
                        cancelEvent.set()
                        if mode == "force":
                            bridge = self._bridges.get(jobId)
                            if bridge is not None:
                                bridge.requestStop()
                            if self._terminateProcess(process):
                                self.eventStore.append(jobId, "process.terminated", "worker process terminated",
                                                       level="WARN", code="E_CANCELLED",
                                                       projectId=record.projectId, workflowId=record.workflowId)
                            stored = self.eventStore.append(jobId, "job.aborted", "job force aborted",
                                                            level="WARN", code="E_CANCELLED",
                                                            projectId=record.projectId, workflowId=record.workflowId)
                            self._claimTerminal(claim, jobId, JobStatus.ABORTED.value, stored.timestampMs,
                                                         errorCode="E_CANCELLED", message=stored.message, stopMode=mode)
                        else:
                            watcher = threading.Thread(target=self._enforceGracefulStop, args=(jobId, process),
                                                       name=f"runtime-stop-watch-{jobId}", daemon=True)
                            watcher.start()
                            accepted = StopOutcome(True, JobStatus.STOPPING.value, f"job stopping ({mode})")
            if accepted is not None:
                return accepted
            if pending is not None:
                if not self._waitFirstAttempt(pending, context):
                    return StopOutcome(False, record.status, "E_JOB_FINALIZATION_PENDING")
                with self._lock:
                    failure = self._stopFailure(jobId)
                    return StopOutcome(not bool(failure), record.status, failure or self._terminalMessage(record))
            with self._lock:
                failure = self._stopFailure(jobId)
                return StopOutcome(not bool(failure), record.status, failure or f"job stopping ({mode})")
        except BaseException as error:
            record = self.jobRepository.get(jobId)
            status = record.status if record is not None else JobStatus.FAILED.value
            if claim and claim[0].failurePhase:
                message = "E_JOB_FINALIZATION_FAILED: " + claim[0].failurePhase
            elif claim:
                with self._lock:
                    message = self._stopFailure(jobId) or errorText(error)
            elif missing:
                status = JobStatus.FAILED.value
                message = "job not found"
            else:
                message = errorText(error)
            return StopOutcome(False, status, message, error)

    def _stopFailure(self, jobId: str) -> str:
        ticket = self._recoveries.get(jobId)
        if ticket is not None:
            return "E_JOB_FINALIZATION_FAILED: " + ticket.failurePhase
        retirement = self._retirements.get(jobId)
        if retirement is not None and retirement.faults:
            return "E_JOB_RETIREMENT_INCOMPLETE"
        return ""

    @staticmethod
    def _terminalMessage(record) -> str:
        return ("E_EVENT_PERSISTENCE: " + record.message
                if record.errorCode == "E_EVENT_PERSISTENCE" else "job already terminal")

    def _enforceGracefulStop(self, jobId: str, process) -> None:
        self.assertMutationAllowed()
        process.join(timeout=max(0.0, self.gracefulStopTimeoutMs / 1000.0))
        pending = None
        with self._terminalAttempt(jobId) as claim:
            with self._lock:
                pending = self._finalizations.get(jobId)
                if pending is None:
                    record = self.jobRepository.get(jobId)
                    handle = self._handles.get(jobId)
                    if record is None:
                        return
                    if record.isTerminal or jobId in self._terminalEvents:
                        self._reap(jobId)
                        return
                    if handle is None:
                        return
                    if not process.is_alive():
                        return
                    _process, cancelEvent, _queue = handle
                    cancelEvent.set()
                    bridge = self._bridges.get(jobId)
                    if bridge is not None:
                        bridge.requestStop()
                    self._terminateProcess(process)
                    message = "graceful stop timed out; worker terminated"
                    self.eventStore.append(
                        jobId,
                        "process.terminated",
                        "worker process terminated after graceful stop timeout",
                        level="WARN",
                        code="E_STOP_TIMEOUT",
                        projectId=record.projectId,
                        workflowId=record.workflowId,
                    )
                    stored = self.eventStore.append(
                        jobId,
                        "job.aborted",
                        message,
                        level="WARN",
                        code="E_STOP_TIMEOUT",
                        projectId=record.projectId,
                        workflowId=record.workflowId,
                    )
                    self._claimTerminal(claim,
                        jobId,
                        JobStatus.ABORTED.value,
                        stored.timestampMs,
                        errorCode="E_STOP_TIMEOUT",
                        message=message,
                        stopMode="graceful",
                    )

        if pending is not None:
            # A competing watcher already accepted this terminal outcome. Its
            # first attempt completes outside Supervisor; retirement still uses
            # the normal recovery, live-bridge and native-close ownership gates.
            self._waitFirstAttempt(pending)
            with self._lock:
                self._reap(jobId)

    def getProcess(self, jobId: str):
        with self._lock:
            handle = self._handles.get(jobId)
            return handle[0] if handle else None

    def ownsJobResources(self, jobId: str) -> bool:
        """Execution status never clears pending publication or retirement ownership."""
        with self._lock:
            return jobId in self._ownedJobs()

    def activeCount(self) -> int:
        with self._lock:
            return sum(1 for record in self.jobRepository.all() if not record.isTerminal)

    def shutdown(self, deadline: float | None = None) -> None:
        self.beginClosing()
        if deadline is None:
            deadline = time.monotonic() + 2.0
        with self._terminalAttempt(reapAll=True, finishAcceptedOnError=True) as tickets:
            while True:
                with self._lock:
                    pending = tuple(self._finalizations.values())
                    if not pending:
                        for jobId, (process, cancelEvent, _queue) in list(self._handles.items()):
                            record = self.jobRepository.get(jobId)
                            if record is None or record.isTerminal or jobId in self._terminalEvents:
                                # A finished but failed publication must not leave its
                                # producer running while close repairs that publication.
                                if jobId in self._recoveries:
                                    cancelEvent.set()
                                    bridge = self._bridges.get(jobId)
                                    if bridge is not None:
                                        bridge.requestStop()
                                    self._terminateProcess(process)
                                self._reap(jobId)
                                continue
                            self.jobRepository.update(
                                jobId, status=JobStatus.STOPPING.value, stopMode="force"
                            )
                            self.eventStore.append(
                                jobId,
                                "job.stopping",
                                "runtime shutting down",
                                level="WARN",
                                code="E_RUNTIME_SHUTDOWN",
                                projectId=record.projectId,
                                workflowId=record.workflowId,
                            )
                            cancelEvent.set()
                            bridge = self._bridges.get(jobId)
                            if bridge is not None:
                                bridge.requestStop()
                            wasAlive = self._terminateProcess(process)
                            if wasAlive:
                                self.eventStore.append(
                                    jobId,
                                    "process.terminated",
                                    "worker process terminated during runtime shutdown",
                                    level="WARN",
                                    code="E_RUNTIME_SHUTDOWN",
                                    projectId=record.projectId,
                                    workflowId=record.workflowId,
                                )
                            stored = self.eventStore.append(
                                jobId,
                                "job.aborted",
                                "runtime shutting down",
                                level="WARN",
                                code="E_RUNTIME_SHUTDOWN",
                                projectId=record.projectId,
                                workflowId=record.workflowId,
                            )
                            self._claimTerminal(tickets,
                                jobId,
                                JobStatus.ABORTED.value,
                                stored.timestampMs,
                                errorCode="E_RUNTIME_SHUTDOWN",
                                message=stored.message,
                                stopMode="force",
                            )
                        break
                for ticket in pending:
                    if not self._waitFirstAttempt(ticket, deadline=deadline):
                        raise RuntimeError("E_JOB_FINALIZATION_PENDING")

    def bridgeStopped(self, jobId: str) -> None:
        self.assertMutationAllowed()
        with self._lock:
            self._safeRetirement.add(jobId)
            self._reap(jobId)

    def waitForRetirement(self, timeoutSeconds: float = 2.0, *, deadline: float | None = None) -> None:
        """Join outside the lock so bridge finalizers can finish their reap."""
        with self._lock:
            bridges = list(self._bridges.values())
        if deadline is None:
            deadline = time.monotonic() + timeoutSeconds
        for bridge in bridges:
            if bridge is not threading.current_thread() and getattr(bridge, "ident", None) is not None:
                bridge.join(timeout=max(0, deadline - time.monotonic()))

    def bridgeError(self, jobId: str, error: BaseException) -> None:
        self.assertMutationAllowed()
        with self._terminalAttempt(jobId) as claim:
            with self._lock:
                record = self.jobRepository.get(jobId)
                if record is None or record.isTerminal or jobId in self._terminalEvents:
                    return
                handle = self._handles.get(jobId)
                if handle is not None:
                    process, cancelEvent, _queue = handle
                    cancelEvent.set()
                    bridge = self._bridges.get(jobId)
                    if bridge is not None:
                        bridge.requestStop()
                    if self._terminateProcess(process):
                        try:
                            self.eventStore.append(
                                jobId,
                                "process.terminated",
                                "worker process terminated after event bridge failure",
                                level="WARN",
                                code="E_EVENT_PERSISTENCE",
                                projectId=record.projectId,
                                workflowId=record.workflowId,
                            )
                        except BaseException:
                            pass
                message = "event bridge failed: " + errorText(error)
                try:
                    stored = self.eventStore.append(
                        jobId,
                        "job.failed",
                        message,
                        level="ERROR",
                        code="E_EVENT_PERSISTENCE",
                        projectId=record.projectId,
                        workflowId=record.workflowId,
                    )
                except BaseException:
                    self._claimTerminalWithoutEvent(claim,
                        jobId,
                        JobStatus.FAILED.value,
                        errorCode="E_EVENT_PERSISTENCE",
                        message=message,
                    )
                else:
                    self._claimTerminal(claim,
                        jobId,
                        JobStatus.FAILED.value,
                        stored.timestampMs,
                        errorCode="E_EVENT_PERSISTENCE",
                        message=message,
                    )

    @property
    def terminalCallback(self):
        with self._lock:
            return self._terminalCallback

    @terminalCallback.setter
    def terminalCallback(self, callback):
        self.assertMutationAllowed()
        with self._lock:
            if self._closing:
                raise RuntimeError("E_RUNTIME_CLOSING")
            self._terminalCallback = callback
            self._callbackEpoch += 1

    def assertMutationAllowed(self) -> None:
        with self._lock:
            if threading.current_thread() in self._attemptThreads:
                raise RuntimeError("E_FINALIZATION_REENTRANT")

    def _ownedJobs(self) -> set[str]:
        return (set(self._handles) | set(self._bridges) | set(self._finalizations)
                | set(self._recoveries) | set(self._retirements))

    def beginClosing(self) -> None:
        self.assertMutationAllowed()
        with self._lock:
            self._closing = True

    def ownsCallbackEpoch(self, epoch: int) -> bool:
        with self._lock:
            return any((t.epoch == epoch or t.registrationEpoch == epoch)
                       for t in self._finalizations.values())

    def _markTerminal(self, jobId: str, status: str, endedAtMs: int,
                      errorCode: str = "", message: str = "", stopMode: str | None = None) -> None:
        self.assertMutationAllowed()
        with self._terminalAttempt() as claim:
            with self._lock:
                self._claimTerminal(claim, jobId, status, endedAtMs, errorCode, message, stopMode)

    def _claimTerminal(self, claim: list[TerminalTicket], jobId: str, status: str, endedAtMs: int,
                       errorCode: str = "", message: str = "", stopMode: str | None = None,
                       *, fallback: bool = False) -> TerminalTicket | None:
        if jobId in self._terminalEvents:
            return None
        changes: dict[str, object] = {"status": status, "endedAtMs": endedAtMs}
        if errorCode:
            changes["errorCode"] = errorCode
        if message:
            changes["message"] = message
        if stopMode is not None:
            changes["stopMode"] = stopMode
        # Allocate the complete guard before publishing its ownership.
        self._terminalOrdinal += 1
        ticket = TerminalTicket(jobId, changes, self._terminalOrdinal,
                                self._terminalCallback, self._callbackEpoch,
                                weakref.ref(threading.current_thread()),
                                registrationEpoch=self._presentationEpoch, fallback=fallback)
        claim.append(ticket)
        self._terminalEvents.add(jobId)
        self._finalizations[jobId] = ticket
        self._terminalFIFO.append(ticket)
        if len(self._terminalFIFO) == 1:
            ticket.turn.set()
        return ticket

    def _claimTerminalWithoutEvent(self, claim: list[TerminalTicket], jobId: str, status: str,
                                   errorCode: str = "", message: str = "") -> TerminalTicket | None:
        return self._claimTerminal(claim, jobId, status, nowMs(), errorCode, message, fallback=True)

    def _markTerminalWithoutEvent(self, jobId: str, status: str,
                                  errorCode: str = "", message: str = "") -> None:
        self.assertMutationAllowed()
        with self._terminalAttempt() as claim:
            with self._lock:
                self._claimTerminalWithoutEvent(claim, jobId, status, errorCode, message)

    @contextmanager
    def _terminalAttempt(self, reapJobId=None, claim=None, *, reapAll=False,
                         reapEmpty=False, finishAcceptedOnError=False):
        claims = [] if claim is None else claim
        try:
            yield claims
        except BaseException as error:
            for ticket in claims:
                if not ticket.done.is_set():
                    if finishAcceptedOnError:
                        try:
                            self._finishClaim(ticket, ticket.jobId if reapAll else reapJobId)
                        except BaseException:
                            pass  # The original preparation error remains primary.
                    else:
                        ticket.callbackState = "ABANDONED"
                        ticket.fault("owner", error)
                        self._completeAttempt(ticket)
            raise
        else:
            firstError = None
            for ticket in claims:
                try:
                    self._finishClaim(ticket, ticket.jobId if reapAll else reapJobId)
                except BaseException as error:
                    if firstError is None:
                        firstError = error
            if not claims and reapJobId is not None and reapEmpty:
                with self._lock:
                    self._reap(reapJobId)
            if firstError is not None:
                raise firstError

    def _finishClaim(self, ticket: TerminalTicket | None, reapJobId: str | None = None) -> None:
        try:
            if ticket is not None:
                self._runTerminal(ticket)
        except BaseException:
            if reapJobId is not None:
                try:
                    with self._lock:
                        self._reap(reapJobId)
                except BaseException:
                    pass  # Retirement receipt preserves cleanup failure; primary is unchanged.
            raise
        else:
            if reapJobId is not None:
                with self._lock:
                    self._reap(reapJobId)

    def _publishTerminal(self, ticket: TerminalTicket) -> BaseException | None:
        try:
            if ticket.fallback:
                record = self.jobRepository.get(ticket.jobId)
                if record is None:
                    raise RuntimeError("terminal Job record missing")
                for name, value in ticket.changes.items():
                    setattr(record, name, value)
            elif self.jobRepository.update(ticket.jobId, **ticket.changes) is None:
                raise RuntimeError("terminal Job record missing")
            ticket.publication = "CONFIRMED"
            ticket.faults.pop("publication", None)
        except BaseException as error:
            ticket.publication = "FAILED"
            ticket.fault("publication", error)
            return error
        return None

    def _notifyTerminal(self, ticket: TerminalTicket) -> BaseException | None:
        try:
            self.eventStore.markTerminal(ticket.jobId)
            ticket.notification = "CONFIRMED"
            ticket.faults.pop("notification", None)
        except BaseException as error:
            ticket.notification = "FAILED"
            ticket.fault("notification", error)
            return error
        return None

    def _runTerminal(self, ticket: TerminalTicket) -> None:
        primary = None
        try:
            ticket.turn.wait()
            with self._lock:
                self._attemptThreads.add(threading.current_thread())
            if ticket.fallback:
                # Legacy memory-first execution failure is explicitly not a durability receipt.
                primary = self._publishTerminal(ticket)
                if primary is None:
                    primary = self._notifyTerminal(ticket)
            ticket.callbackState = "STARTED"
            try:
                if ticket.callback is not None:
                    ticket.callback(ticket.jobId, str(ticket.changes["status"]))
                ticket.callbackState = "RETURNED"
            except BaseException as error:
                ticket.callbackState = "RAISED"
                ticket.fault("callback", error)
                if not ticket.fallback:
                    primary = error
            finally:
                ticket.callback = None
            if not ticket.fallback:
                publicationError = self._publishTerminal(ticket)
                if publicationError is not None:
                    primary = publicationError
                else:
                    notificationError = self._notifyTerminal(ticket)
                    if notificationError is not None:
                        primary = notificationError
        except BaseException as error:
            if ticket.callbackState == "NOT_STARTED":
                ticket.callbackState = "ABANDONED"
                ticket.fault("owner", error)
            primary = error
        finally:
            self._completeAttempt(ticket)
        if primary is not None:
            raise primary

    def _completeAttempt(self, ticket: TerminalTicket) -> None:
        with self._lock:
            self._attemptThreads.discard(threading.current_thread())
            ticket.callback = None
            ticket.owner = None
            self._finalizations.pop(ticket.jobId, None)
            if ticket.incomplete:
                self._recoveries[ticket.jobId] = ticket
            else:
                self._receipts.append(ticket)
            if ticket in self._terminalFIFO:
                self._terminalFIFO.remove(ticket)
            if self._terminalFIFO:
                self._terminalFIFO[0].turn.set()
            ticket.done.set()
            for waiter in tuple(ticket.waiters):
                waiter.wake.set()

    def _waitFirstAttempt(self, ticket: TerminalTicket, context=None,
                          deadline: float | None = None) -> bool:
        waiter = AttemptWaiter()
        with self._lock:
            if ticket.done.is_set():
                return True
            ticket.waiters.add(waiter)
        try:
            if context is not None:
                reference = weakref.ref(waiter)
                def cancelled():
                    current = reference()
                    if current is not None:
                        current.cancel()
                register = getattr(context, "add_callback", None)
                if register is not None and not register(cancelled):
                    waiter.cancel()
                remaining = getattr(context, "time_remaining", lambda: None)()
                if remaining is not None:
                    rpcDeadline = time.monotonic() + max(0, remaining)
                    deadline = rpcDeadline if deadline is None else min(deadline, rpcDeadline)
                if not getattr(context, "is_active", lambda: True)():
                    waiter.cancel()
            while True:
                remaining = None if deadline is None else max(0, deadline - time.monotonic())
                # Sync gRPC represents no deadline with a very large float.
                # Split only at the platform wait limit, never shorten the RPC deadline.
                interval = None if remaining is None else min(remaining, threading.TIMEOUT_MAX)
                if waiter.wake.wait(interval):
                    return ticket.done.is_set() and not waiter.cancelled
                if deadline is not None and time.monotonic() >= deadline:
                    return False
        finally:
            waiter.active = False
            with self._lock:
                ticket.waiters.discard(waiter)

    def recoverFinalizations(self) -> list[str]:
        self.assertMutationAllowed()
        errors = []
        with self._lock:
            tickets = tuple(self._recoveries.values())
        for ticket in tickets:
            with self._lock:
                if ticket.recoveryRunning or self._recoveries.get(ticket.jobId) is not ticket:
                    continue
                if ticket.callbackState == "ABANDONED":
                    errors.append(f"{ticket.jobId}: CALLBACK_NOT_RUN/OWNER_ABANDONED")
                    continue
                ticket.recoveryRunning = True
                self._attemptThreads.add(threading.current_thread())
            try:
                error = None
                if ticket.publication != "CONFIRMED":
                    error = self._publishTerminal(ticket)
                if error is None and ticket.notification != "CONFIRMED":
                    error = self._notifyTerminal(ticket)
                if error is not None:
                    errors.append(f"{ticket.jobId}: {ticket.failurePhase}")
            finally:
                with self._lock:
                    ticket.recoveryRunning = False
                    self._attemptThreads.discard(threading.current_thread())
                    if not ticket.incomplete:
                        self._recoveries.pop(ticket.jobId, None)
                        self._receipts.append(ticket)
        return errors

    def _terminateProcess(self, process) -> bool:
        if not process.is_alive():
            process.join(timeout=0.2)
            return False
        process.terminate()
        process.join(timeout=2.0)
        if process.is_alive():
            kill = getattr(process, "kill", None)
            if callable(kill):
                kill()
                process.join(timeout=2.0)
        return True

    def _cleanupFailedStart(
        self,
        jobId: str,
        process,
        cancelEvent,
        eventQueue,
        bridge: EventBridge | None,
    ) -> None:
        self._failedStartRetirement.add(jobId)
        cancelEvent.set()
        if bridge is not None:
            bridge.requestStop()
        if process is not None:
            try:
                self._terminateProcess(process)
            except BaseException:
                pass
            if process.is_alive():
                # An unsuccessful kill is not resource retirement. Keep the
                # actual owner visible to admission, release and later cleanup.
                self._handles[jobId] = (process, cancelEvent, eventQueue)
                if bridge is not None:
                    self._bridges[jobId] = bridge
                return
        if bridge is not None and bridge is not threading.current_thread():
            if getattr(bridge, "is_alive", lambda: False)():
                # startJob holds the lock needed by bridgeStopped. Let that
                # final hook retire the owners after startup failure unwinds.
                if process is not None:
                    self._handles[jobId] = (process, cancelEvent, eventQueue)
                self._bridges[jobId] = bridge
                return
        if process is not None:
            close = getattr(process, "close", None)
            if callable(close):
                try:
                    close()
                except BaseException:
                    pass
        closeQueue = getattr(eventQueue, "close", None)
        if callable(closeQueue):
            try:
                closeQueue()
            except BaseException:
                pass
        self._handles.pop(jobId, None)
        self._bridges.pop(jobId, None)
        self._heartbeat.pop(jobId, None)
        self._heartbeatCells.pop(jobId, None)
        self._heartbeatMonotonic.pop(jobId, None)
        self._heartbeatIntervals.pop(jobId, None)
        self._heartbeatTimeouts.pop(jobId, None)
        self._heartbeatSeen.discard(jobId)
        self._terminalEvents.discard(jobId)
        self._failedStartRetirement.discard(jobId)

    def _reap(self, jobId: str) -> None:
        if jobId in self._finalizations or jobId in self._recoveries:
            return
        record = self.jobRepository.get(jobId)
        if (record is not None and not record.isTerminal
                and jobId not in self._terminalEvents and jobId not in self._failedStartRetirement):
            # A stopped producer is not a finalized Job. In particular, an
            # append failure before terminal acceptance must retain its handle
            # so the next explicit shutdown can finish the missing outcome.
            return
        handle = self._handles.get(jobId)
        bridge = self._bridges.get(jobId)
        receipt = self._retirements.get(jobId)
        if receipt is not None and receipt.faults:
            return  # Unknown native/callback outcomes are never automatically repeated.
        if bridge is not None and bridge is not threading.current_thread():
            bridge.requestStop()
            if getattr(bridge, "is_alive", lambda: False)():
                return  # The real bridge final action remains the retirement boundary.
        if handle is None and bridge is None and receipt is None:
            return
        if receipt is None:
            receipt = RetirementReceipt()
            if self.retirementRepairFactory is not None:
                receipt.repair = self.retirementRepairFactory(jobId, self.retiredCallback)
            self._retirements[jobId] = receipt
        if handle is not None:
            process, _cancelEvent, eventQueue = handle
            if not receipt.processClosed:
                try:
                    if process.is_alive():
                        process.join(timeout=0.2)
                    if process.is_alive():
                        self._terminateProcess(process)
                    if process.is_alive():
                        return
                    close = getattr(process, "close", None)
                    if callable(close):
                        close()
                    receipt.processClosed = True
                except BaseException as error:
                    receipt.fault("process", error)
                    raise
            if not receipt.queueClosed:
                try:
                    closeQueue = getattr(eventQueue, "close", None)
                    if callable(closeQueue):
                        closeQueue()
                    receipt.queueClosed = True
                except BaseException as error:
                    receipt.fault("queue", error)
                    raise
            self._handles.pop(jobId, None)
        self._bridges.pop(jobId, None)
        self._heartbeat.pop(jobId, None)
        self._heartbeatCells.pop(jobId, None)
        self._heartbeatMonotonic.pop(jobId, None)
        self._heartbeatIntervals.pop(jobId, None)
        self._heartbeatTimeouts.pop(jobId, None)
        self._heartbeatSeen.discard(jobId)
        record = self.jobRepository.get(jobId)
        if record is None or record.isTerminal:
            self._terminalEvents.discard(jobId)
        if not receipt.callbackStarted:
            receipt.callbackStarted = True
            self._attemptThreads.add(threading.current_thread())
            try:
                if self.retiredCallback is not None:
                    self.retiredCallback(jobId)
                receipt.callbackDone = True
            except BaseException as error:
                receipt.fault("callback", error)
                raise
            finally:
                self._attemptThreads.discard(threading.current_thread())
        receipt.repair = None
        self._retirements.pop(jobId, None)
        self._safeRetirement.discard(jobId)
        self._failedStartRetirement.discard(jobId)

    def finishRetirements(self) -> list[str]:
        self.assertMutationAllowed()
        errors = []
        with self._lock:
            for jobId in tuple(self._ownedJobs()):
                receipt = self._retirements.get(jobId)
                if receipt is not None and receipt.faults:
                    if set(receipt.faults) == {"callback"} and receipt.repair is not None:
                        self._attemptThreads.add(threading.current_thread())
                        try:
                            receipt.repair()
                            receipt.repair = None
                            self._retirements.pop(jobId, None)
                            self._safeRetirement.discard(jobId)
                            self._failedStartRetirement.discard(jobId)
                        except BaseException as error:
                            receipt.fault("callback", error)
                        finally:
                            self._attemptThreads.discard(threading.current_thread())
                    if jobId in self._retirements:
                        errors.append(jobId + ": E_JOB_RETIREMENT_INCOMPLETE")
                    continue
                try:
                    self._reap(jobId)
                except BaseException as error:
                    errors.append(jobId + ": " + faultSummary(error))
        return errors
