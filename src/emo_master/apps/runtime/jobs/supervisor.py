from __future__ import annotations

import multiprocessing
import threading
from dataclasses import replace
from typing import Any, Callable, cast

from emo_master.apps.runtime.events.event_store import EventStore
from emo_master.apps.runtime.jobs.event_bridge import EventBridge
from emo_master.apps.runtime.jobs.models import JobProcessSpec, JobStatus, nowMs
from emo_master.apps.runtime.jobs.repository import JobRepository
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
    ) -> None:
        self.jobRepository = jobRepository
        self.eventStore = eventStore
        self.maxConcurrentJobs = max(1, maxConcurrentJobs)
        self.gracefulStopTimeoutMs = max(0, gracefulStopTimeoutMs)
        self.heartbeatTimeoutMs = max(100, heartbeatTimeoutMs)
        self.terminalCallback = terminalCallback
        self._context = multiprocessing.get_context("spawn")
        self._handles: dict[str, tuple[Any, Any, Any]] = {}
        self._bridges: dict[str, EventBridge] = {}
        self._heartbeat: dict[str, int] = {}
        self._heartbeatIntervals: dict[str, int] = {}
        self._heartbeatTimeouts: dict[str, int] = {}
        self._heartbeatSeen: set[str] = set()
        self._terminalEvents: set[str] = set()
        self._lock = threading.RLock()

    def startJob(self, spec: JobProcessSpec) -> None:
        with self._lock:
            active = sum(
                1
                for jobId in self._handles
                if (record := self.jobRepository.get(jobId)) is not None
                and not record.isTerminal
            )
            if active >= self.maxConcurrentJobs:
                raise RuntimeError("E_MAX_CONCURRENT_JOBS: maximum concurrent jobs reached")
            if spec.jobId in self._handles:
                raise ValueError(f"duplicate job id: {spec.jobId}")

            cancelEvent = self._context.Event()
            eventQueue = self._context.Queue()
            heartbeatTimeoutMs = max(100, spec.heartbeatTimeoutMs or self.heartbeatTimeoutMs)
            heartbeatIntervalMs = min(
                max(50, spec.heartbeatIntervalMs),
                max(50, heartbeatTimeoutMs // 3),
            )
            if heartbeatIntervalMs != spec.heartbeatIntervalMs:
                spec = replace(spec, heartbeatIntervalMs=heartbeatIntervalMs)
            process = self._context.Process(
                target=runJobProcess,
                args=(spec, cancelEvent, eventQueue),
                name=f"emo-master-job-{spec.jobId}",
            )
            bridge: EventBridge | None = None
            try:
                process.start()
                self._handles[spec.jobId] = (process, cancelEvent, eventQueue)
                self._heartbeat[spec.jobId] = nowMs()
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
        with self._lock:
            record = self.jobRepository.get(jobId)
            if record is None or jobId in self._terminalEvents or record.isTerminal:
                return

            eventType = str(event.get("eventType", "process.event"))
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
                self._markTerminal(
                    jobId,
                    JobStatus.COMPLETED.value,
                    stored.timestampMs,
                    message=stored.message,
                )
            elif eventType == "job.failed":
                self._markTerminal(
                    jobId,
                    JobStatus.FAILED.value,
                    stored.timestampMs,
                    errorCode=stored.code or "E_WORKER_CRASHED",
                    message=stored.message,
                )
            elif eventType == "job.aborted":
                self._markTerminal(
                    jobId,
                    JobStatus.ABORTED.value,
                    stored.timestampMs,
                    errorCode=stored.code,
                    message=stored.message,
                )

    def processExited(self, jobId: str, exitCode: int | None) -> None:
        with self._lock:
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
                self._markTerminal(
                    jobId,
                    status,
                    stored.timestampMs,
                    errorCode=code,
                    message=message,
                )
            self._reap(jobId)

    def checkHeartbeat(self, jobId: str) -> None:
        with self._lock:
            record = self.jobRepository.get(jobId)
            lastHeartbeat = self._heartbeat.get(jobId)
            if record is None or record.isTerminal or lastHeartbeat is None:
                return
            timeoutMs = self._heartbeatTimeouts.get(jobId, self.heartbeatTimeoutMs)
            if jobId not in self._heartbeatSeen:
                intervalMs = self._heartbeatIntervals.get(jobId, 500)
                timeoutMs = max(timeoutMs, intervalMs * 2)
            if nowMs() - lastHeartbeat <= timeoutMs:
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
            self._markTerminal(
                jobId,
                JobStatus.FAILED.value,
                stored.timestampMs,
                errorCode="E_HEARTBEAT_TIMEOUT",
                message=message,
                stopMode="force",
            )
            self._reap(jobId)

    def stopJob(self, jobId: str, mode: str = "graceful") -> str:
        if mode not in {"graceful", "force"}:
            raise ValueError(f"invalid stop mode: {mode}")
        with self._lock:
            handle = self._handles.get(jobId)
            record = self.jobRepository.get(jobId)
            if handle is None or record is None:
                raise KeyError(jobId)
            process, cancelEvent, _queue = handle
            if record.isTerminal:
                return record.status
            self.jobRepository.update(
                jobId, status=JobStatus.STOPPING.value, stopMode=mode
            )
            self.eventStore.append(
                jobId,
                "job.stopping",
                f"job stopping ({mode})",
                level="WARN",
                projectId=record.projectId,
                workflowId=record.workflowId,
            )
            cancelEvent.set()
            if mode == "force":
                bridge = self._bridges.get(jobId)
                if bridge is not None:
                    bridge.requestStop()
                wasAlive = self._terminateProcess(process)
                if wasAlive:
                    self.eventStore.append(
                        jobId,
                        "process.terminated",
                        "worker process terminated",
                        level="WARN",
                        code="E_CANCELLED",
                        projectId=record.projectId,
                        workflowId=record.workflowId,
                    )
                stored = self.eventStore.append(
                    jobId,
                    "job.aborted",
                    "job force aborted",
                    level="WARN",
                    code="E_CANCELLED",
                    projectId=record.projectId,
                    workflowId=record.workflowId,
                )
                self._markTerminal(
                    jobId,
                    JobStatus.ABORTED.value,
                    stored.timestampMs,
                    errorCode="E_CANCELLED",
                    message=stored.message,
                    stopMode=mode,
                )
                self._reap(jobId)
            else:
                watcher = threading.Thread(
                    target=self._enforceGracefulStop,
                    args=(jobId, process),
                    name=f"runtime-stop-watch-{jobId}",
                    daemon=True,
                )
                watcher.start()
            return (
                JobStatus.ABORTED.value
                if mode == "force"
                else JobStatus.STOPPING.value
            )

    def _enforceGracefulStop(self, jobId: str, process) -> None:
        process.join(timeout=max(0.0, self.gracefulStopTimeoutMs / 1000.0))
        with self._lock:
            record = self.jobRepository.get(jobId)
            handle = self._handles.get(jobId)
            if record is None or record.isTerminal or handle is None:
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
            self._markTerminal(
                jobId,
                JobStatus.ABORTED.value,
                stored.timestampMs,
                errorCode="E_STOP_TIMEOUT",
                message=message,
                stopMode="graceful",
            )
            self._reap(jobId)

    def getProcess(self, jobId: str):
        with self._lock:
            handle = self._handles.get(jobId)
            return handle[0] if handle else None

    def activeCount(self) -> int:
        with self._lock:
            return sum(1 for record in self.jobRepository.all() if not record.isTerminal)

    def shutdown(self) -> None:
        with self._lock:
            for jobId, (process, cancelEvent, _queue) in list(self._handles.items()):
                record = self.jobRepository.get(jobId)
                if record is None or record.isTerminal:
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
                self._markTerminal(
                    jobId,
                    JobStatus.ABORTED.value,
                    stored.timestampMs,
                    errorCode="E_RUNTIME_SHUTDOWN",
                    message=stored.message,
                    stopMode="force",
                )
                self._reap(jobId)

    def bridgeStopped(self, jobId: str) -> None:
        with self._lock:
            self._reap(jobId)

    def bridgeError(self, jobId: str, error: BaseException) -> None:
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
            message = f"event bridge failed: {error}"
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
                self._markTerminalWithoutEvent(
                    jobId,
                    JobStatus.FAILED.value,
                    errorCode="E_EVENT_PERSISTENCE",
                    message=message,
                )
            else:
                self._markTerminal(
                    jobId,
                    JobStatus.FAILED.value,
                    stored.timestampMs,
                    errorCode="E_EVENT_PERSISTENCE",
                    message=message,
                )
            self._reap(jobId)

    def _markTerminal(
        self,
        jobId: str,
        status: str,
        endedAtMs: int,
        errorCode: str = "",
        message: str = "",
        stopMode: str | None = None,
    ) -> None:
        self._terminalEvents.add(jobId)
        self.eventStore.markTerminal(jobId)
        changes: dict[str, object] = {
            "status": status,
            "endedAtMs": endedAtMs,
        }
        if errorCode:
            changes["errorCode"] = errorCode
        if message:
            changes["message"] = message
        if stopMode is not None:
            changes["stopMode"] = stopMode
        self.jobRepository.update(jobId, **changes)
        if self.terminalCallback is not None:
            self.terminalCallback(jobId, status)

    def _markTerminalWithoutEvent(
        self,
        jobId: str,
        status: str,
        errorCode: str = "",
        message: str = "",
    ) -> None:
        """Keep in-memory job state terminal when event persistence is unavailable."""
        self._terminalEvents.add(jobId)
        try:
            self.eventStore.markTerminal(jobId)
        except BaseException:
            pass
        record = self.jobRepository.get(jobId)
        if record is not None:
            record.status = status
            record.endedAtMs = nowMs()
            record.errorCode = errorCode
            record.message = message
        if self.terminalCallback is not None:
            try:
                self.terminalCallback(jobId, status)
            except BaseException:
                pass

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
        cancelEvent.set()
        if bridge is not None:
            bridge.requestStop()
        if process is not None:
            try:
                self._terminateProcess(process)
            except BaseException:
                pass
        if bridge is not None and bridge is not threading.current_thread():
            if getattr(bridge, "ident", None) is not None:
                try:
                    bridge.join(timeout=1.0)
                except BaseException:
                    pass
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
        self._heartbeatIntervals.pop(jobId, None)
        self._heartbeatTimeouts.pop(jobId, None)
        self._heartbeatSeen.discard(jobId)
        self._terminalEvents.discard(jobId)

    def _reap(self, jobId: str) -> None:
        handle = self._handles.get(jobId)
        bridge = self._bridges.get(jobId)
        if bridge is not None and bridge is not threading.current_thread():
            try:
                bridge.requestStop()
                if getattr(bridge, "ident", None) is not None:
                    bridge.join(timeout=1.0)
            except BaseException:
                pass
        if handle is not None:
            process, _cancelEvent, eventQueue = handle
            try:
                if process.is_alive():
                    process.join(timeout=0.2)
                if process.is_alive():
                    self._terminateProcess(process)
            except BaseException:
                pass
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
        self._heartbeatIntervals.pop(jobId, None)
        self._heartbeatTimeouts.pop(jobId, None)
        self._heartbeatSeen.discard(jobId)
        record = self.jobRepository.get(jobId)
        if record is None or record.isTerminal:
            self._terminalEvents.discard(jobId)
