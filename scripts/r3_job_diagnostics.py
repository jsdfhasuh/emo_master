"""Opt-in, bounded observations of the real Job path; never a runtime policy.

Snapshots read no event payloads, acquire no production locks, and issue no SQL.
The separate opt-in keeper arm reads schema/PRAGMAs through one idle connection.
No global tracing or queue/durability changes. Shared worker counters are
observations, not an atomic cross-process snapshot or queue depth.
"""
from collections import deque
from functools import partial
import json
import multiprocessing
from pathlib import Path
import sys
import threading
import time


WORKER_FIELDS = (
    "target_enter_ns", "target_return_ns", "target_thread_cpu_ns", "target_error",
    "main_put_entered", "main_put_returned", "heartbeat_put_entered", "heartbeat_put_returned",
    "terminal_put_return_ns", "close_enter_ns", "close_return_ns",
    "join_enter_ns", "join_return_ns", "last_main_put_enter_ns", "last_main_put_return_ns",
    "diagnostic_errors",
)
FIELD = {name: index for index, name in enumerate(WORKER_FIELDS)}


def workerRecord(shared, field, value=None, *, increment=False):
    try:
        if increment:
            shared[FIELD[field]] += 1
        else:
            shared[FIELD[field]] = time.monotonic_ns() if value is None else value
    except BaseException:
        try:
            shared[FIELD["diagnostic_errors"]] += 1
        except BaseException:
            pass


class DiagnosticQueue:
    """Forward precisely the original worker queue calls without extra events."""
    def __init__(self, queue, shared):
        self.queue, self.shared = queue, shared

    def put(self, event, *args, **kwargs):
        heartbeat = isinstance(event, dict) and event.get("eventType") == "process.heartbeat"
        prefix = "heartbeat" if heartbeat else "main"
        workerRecord(self.shared, prefix + "_put_entered", increment=True)
        if not heartbeat:
            workerRecord(self.shared, "last_main_put_enter_ns")
        result = self.queue.put(event, *args, **kwargs)
        workerRecord(self.shared, prefix + "_put_returned", increment=True)
        if not heartbeat:
            workerRecord(self.shared, "last_main_put_return_ns")
        if isinstance(event, dict) and event.get("eventType") in {"job.completed", "job.failed", "job.aborted"}:
            workerRecord(self.shared, "terminal_put_return_ns")
        return result

    def __getattr__(self, name):
        original = getattr(self.queue, name)
        if name not in {"close", "join_thread"} or not callable(original):
            return original
        prefix = "join" if name == "join_thread" else "close"

        def measured(*args, **kwargs):
            workerRecord(self.shared, prefix + "_enter_ns")
            result = original(*args, **kwargs)
            workerRecord(self.shared, prefix + "_return_ns")
            return result
        return measured


def diagnosticWorker(spec, cancelEvent, eventQueue, *, target, shared):
    """Importable spawn target; original target and feeder ownership are unchanged."""
    workerRecord(shared, "target_enter_ns")
    try:
        cpu = time.thread_time_ns()
    except BaseException:
        cpu = None
        workerRecord(shared, "diagnostic_errors", increment=True)
    try:
        result = target(spec, cancelEvent, DiagnosticQueue(eventQueue, shared))
    except BaseException:
        workerRecord(shared, "target_error", 1)
        raise
    else:
        workerRecord(shared, "target_return_ns")
        return result
    finally:
        try:
            if cpu is not None:
                workerRecord(shared, "target_thread_cpu_ns", time.thread_time_ns() - cpu)
        except BaseException:
            workerRecord(shared, "diagnostic_errors", increment=True)


def eventDetails(event):
    """Only bounded structural identifiers; never message, payload or file content."""
    details = {key: str(event.get(key, ""))[:80]
               for key in ("jobId", "eventType", "nodeId", "workflowId")}
    path = event.get("iterationPath", ())
    details["iterationPath"] = [value for value in path[:8] if isinstance(value, int)] if isinstance(path, (list, tuple)) else []
    return details


def stackSnapshot():
    frames = sys._current_frames()
    threads = sorted(threading.enumerate(), key=lambda thread: (
        not (thread.name == "MainThread" or thread.name.startswith("runtime-event-bridge-")), thread.name))
    rows = []
    try:
        for thread in threads[:16]:
            frame = frames.get(thread.ident)
            stack = []
            while frame is not None and len(stack) < 16:
                stack.append({"file": Path(frame.f_code.co_filename).name[:80],
                              "function": frame.f_code.co_name[:80], "line": frame.f_lineno})
                frame = frame.f_back
            rows.append({"thread": thread.name[:100], "ident": thread.ident,
                         "native_id": thread.native_id, "frames": stack,
                         "frames_truncated": frame is not None})
        return {"threads": rows, "threads_truncated": len(threads) > 16}
    finally:
        del frames


class SqliteKeeper:
    """Diagnostic arm: one ordinary idle connection, no durability changes."""
    def __init__(self, store):
        self.connection = None
        self.state = {"arm": "diagnostic_idle_keeper", "opened_ns": time.monotonic_ns(),
                      "closed_ns": None, "close_wall_ns": None, "closed": False}
        connection = store._connect()
        try:
            cursor = connection.execute("SELECT count(*) FROM sqlite_master")
            try:
                cursor.fetchone()
            finally:
                cursor.close()
            pragmas = {}
            for name in ("journal_mode", "synchronous", "wal_autocheckpoint", "page_size"):
                cursor = connection.execute("PRAGMA " + name)
                try:
                    pragmas[name] = cursor.fetchone()[0]
                finally:
                    cursor.close()
            self.state.update(pragmas=pragmas, in_transaction=connection.in_transaction)
        except BaseException:
            connection.close()
            raise
        self.connection = connection

    def close(self):
        if self.connection is None:
            return
        start = time.monotonic_ns()
        self.connection.close()
        self.connection = None
        self.state.update(closed=True, closed_ns=time.monotonic_ns(),
                          close_wall_ns=time.monotonic_ns() - start)

    def report(self):
        return dict(self.state)


class JobDiagnostics:
    MAX_SNAPSHOTS = 24
    MAX_BYTES = 1024 * 1024
    MAX_ACTIVE_THREADS = 32

    def __init__(self, path, *, source, plannedSeconds):
        self.path = Path(path)
        self.path.open("x", encoding="utf-8").close()
        self.source = source
        self.plannedSeconds = plannedSeconds
        self.runtime = None
        self.jobId = ""
        self.started = time.monotonic()
        self.shared = multiprocessing.get_context("spawn").Array("q", len(WORKER_FIELDS), lock=False)
        self.stages = {}
        self.active = {}
        self.frontiers = {}
        self.rows = deque(maxlen=self.MAX_SNAPSHOTS)
        self.evicted = 0
        self.errors = deque(maxlen=8)
        self.lock = threading.Lock()
        self.writeLock = threading.Lock()
        self.stop = threading.Event()
        self.patches = []
        self.sinkPatch = None
        self.thread = None
        self.closed = False

    def _patch(self, owner, name, replacement):
        original = getattr(owner, name)
        self.patches.append((owner, name, original, replacement))
        setattr(owner, name, replacement)

    def wrap(self, owner, name, stage, *, details=None, returned=None):
        original = getattr(owner, name)
        self.stages[stage] = {"entered": 0, "returned": 0, "failed": 0,
                              "wall_ns": 0, "thread_cpu_ns": 0, "max_wall_ns": 0}

        def measured(*args, **kwargs):
            context = {}
            try:
                context = details(*args, **kwargs) if details else {}
            except BaseException as error:
                self._error(error)
            ident = start = cpu = None
            previous = None
            try:
                ident = threading.get_ident()
                start, cpu = time.monotonic_ns(), time.thread_time_ns()
                with self.lock:
                    self.stages[stage]["entered"] += 1
                    previous = self.active.get(ident)
                    if previous is not None or len(self.active) < self.MAX_ACTIVE_THREADS:
                        self.active[ident] = {"stage": stage, "start_ns": start,
                                              "thread": threading.current_thread().name[:100], **context}
            except BaseException as error:
                self._error(error)
            failed = True
            try:
                result = original(*args, **kwargs)
                failed = False
                try:
                    if returned:
                        returned(result, context)
                except BaseException as error:
                    self._error(error)
                return result
            finally:
                try:
                    if start is not None and cpu is not None:
                        wall, used = time.monotonic_ns() - start, time.thread_time_ns() - cpu
                        with self.lock:
                            row = self.stages[stage]
                            row["failed" if failed else "returned"] += 1
                            row["wall_ns"] += wall
                            row["thread_cpu_ns"] += used
                            row["max_wall_ns"] = max(row["max_wall_ns"], wall)
                            if previous is None:
                                self.active.pop(ident, None)
                            else:
                                self.active[ident] = previous
                except BaseException as error:
                    self._error(error)
        self._patch(owner, name, measured)

    def _error(self, error):
        try:
            self.errors.append(type(error).__name__)
        except BaseException:
            pass

    def _frontier(self, name, value):
        with self.lock:
            self.frontiers[name] = {"observed_ns": time.monotonic_ns(), **value}

    def install(self, runtime):
        from emo_master.apps.runtime.jobs import supervisor as supervisorModule
        self.runtime = runtime
        supervisor = runtime.jobSupervisor

        def startDetails(spec):
            self.jobId = spec.jobId
            return {}

        def consumeDetails(_job, event):
            details = eventDetails(event)
            self._frontier("consume_enter", details)
            return details

        def appendDetails(*args, **kwargs):
            rawPath = kwargs.get("iterationPathJson", "[]")
            try:
                path = json.loads(rawPath) if isinstance(rawPath, str) and len(rawPath) <= 256 else []
            except ValueError:
                path = []
            return eventDetails({"jobId": kwargs.get("jobId", args[0] if args else ""),
                                 "eventType": kwargs.get("eventType", args[2] if len(args) > 2 else ""),
                                 "nodeId": kwargs.get("nodeId", args[1] if len(args) > 1 else ""),
                                 "workflowId": kwargs.get("workflowId", ""), "iterationPath": path})

        self.wrap(supervisor, "startJob", "supervisor.start", details=startDetails)
        self.wrap(supervisor, "consumeWorkerEvent", "formal.consume", details=consumeDetails,
                  returned=lambda _result, context: self._frontier("consume_return", context))
        self.wrap(runtime.eventStore, "append", "formal.event_store")
        self.wrap(runtime.sqliteStore, "appendJobEvent", "formal.sqlite_append", details=appendDetails,
                  returned=lambda sequence, context: self._frontier("persisted_return", {"sequence": sequence, **context}))
        # addSink already captured the bound method. Replace exactly that sink
        # in place before StartJob, retaining order and exactly one dispatch.
        oldSink = runtime._operationalLogSink
        self.wrap(runtime, "_operationalLogSink", "formal.jsonl_enqueue")
        sinks = runtime.eventStore._sinks
        index = sinks.index(oldSink)
        sinks[index] = runtime._operationalLogSink
        self.sinkPatch = (sinks, oldSink, runtime._operationalLogSink)
        self.wrap(supervisor, "checkHeartbeat", "supervisor.heartbeat_check")
        self.wrap(supervisor, "_markTerminal", "terminal.mark")
        self.wrap(supervisor, "terminalCallback", "terminal.callback")
        self.wrap(runtime.previewAssetStore, "promote", "terminal.preview_promote")
        self.wrap(runtime.jobRepository, "update", "job.repository_update")
        self.wrap(runtime.sqliteStore, "updateJobStatus", "job.sqlite_status")
        self.wrap(supervisor, "processExited", "supervisor.process_exited")
        self.wrap(supervisor, "bridgeStopped", "supervisor.bridge_stopped")
        self._patch(supervisorModule, "runJobProcess", partial(diagnosticWorker,
                    target=supervisorModule.runJobProcess, shared=self.shared))
        self.thread = threading.Thread(target=self._observe, name="r3-job-diagnostics", daemon=True)
        self.thread.start()

    def _state(self):
        runtime = self.runtime
        if runtime is None:
            return {"status": "RUNTIME_NOT_BOUND"}
        supervisor = runtime.jobSupervisor
        record = runtime.jobRepository._jobs.get(self.jobId)
        handle = supervisor._handles.get(self.jobId)
        bridge = supervisor._bridges.get(self.jobId)
        state = {"job_status": getattr(record, "status", None),
                 "job_error_code": str(getattr(record, "errorCode", ""))[:80],
                 "job_ended_at_ms": getattr(record, "endedAtMs", None),
                 "owner_has_process": handle is not None, "owner_has_bridge": bridge is not None,
                 "bridge_alive": bridge.is_alive() if bridge is not None else None,
                 "bridge_stop_requested": bridge.stopEvent.is_set() if bridge is not None else None,
                 "event_store_sequence": runtime.eventStore._sequences.get(self.jobId, 0),
                 "event_store_terminal_sequence": runtime.eventStore._terminalSequences.get(self.jobId, 0),
                 "supervisor_terminal_seen": self.jobId in supervisor._terminalEvents,
                 "last_consumed_heartbeat_ms": supervisor._heartbeat.get(self.jobId),
                 "last_observed_cell_ms": supervisor._heartbeatMonotonic.get(self.jobId)}
        cell = supervisor._heartbeatCells.get(self.jobId)
        state["heartbeat_cell_ms"] = cell.read() if cell is not None else None
        if handle is not None:
            try:
                state.update(worker_pid=handle[0].pid, worker_exitcode=handle[0].exitcode,
                             worker_alive=handle[0].is_alive())
            except (OSError, ValueError, AssertionError):
                state["worker_state"] = "RETIRED_OR_UNAVAILABLE"
        return state

    def capture(self, reason, *, stacks=True):
        """Best effort and payload-free; must never hide the original failure."""
        if self.closed:
            return
        try:
            stamp = time.monotonic_ns()
            state = self._state()
            worker = {name: int(self.shared[index]) for index, name in enumerate(WORKER_FIELDS)}
            stack = stackSnapshot() if stacks else None
            with self.lock:
                row = {"reason": reason[:40], "monotonic_ns": stamp,
                       "owner_process_cpu_ns": time.process_time_ns(), "state": state,
                       "worker": worker, "frontiers": dict(self.frontiers),
                       "active": [dict(value, elapsed_ns=stamp-value["start_ns"]) for value in self.active.values()],
                       "stages": {name: dict(value) for name, value in self.stages.items()}, "stacks": stack}
                self.evicted += int(len(self.rows) == self.MAX_SNAPSHOTS)
                self.rows.append(row)
            self._save()
        except BaseException as error:
            self._error(error)

    def _save(self):
        with self.writeLock:
            with self.lock:
                value = {"source": self.source, "job_id": self.jobId,
                         "snapshots": list(self.rows), "evicted_snapshots": self.evicted,
                         "errors": list(self.errors), "maximum_snapshots": self.MAX_SNAPSHOTS,
                         "maximum_bytes": self.MAX_BYTES,
                         "observer_retired": self.closed,
                         "semantics": "Opt-in observer. Inclusive stage wall/thread CPU totals; do not sum nested stages. Active CPU is not sampled. Worker fields are non-atomic observations; put return is not feeder flush. Cached state uses no production locks or database queries. No event payloads or source lines."}
            encoded = json.dumps(value, ensure_ascii=True, separators=(",", ":"))
            while len(encoded.encode("utf-8")) > self.MAX_BYTES and len(value["snapshots"]) > 1:
                value["snapshots"].pop(0)
                value["byte_budget_omitted_snapshots"] = value.get("byte_budget_omitted_snapshots", 0) + 1
                encoded = json.dumps(value, ensure_ascii=True, separators=(",", ":"))
            if len(encoded.encode("utf-8")) > self.MAX_BYTES:
                raise RuntimeError("diagnostic byte budget exceeded")
            temporary = self.path.with_name(self.path.name + ".pending")
            temporary.write_text(encoded, encoding="utf-8")
            temporary.replace(self.path)

    def _observe(self):
        while not self.stop.is_set():
            self.capture("periodic")
            elapsed = time.monotonic() - self.started
            # Eight input-period opportunities, then a two-second terminal tail.
            interval = max(2.0, self.plannedSeconds / 8) if elapsed < self.plannedSeconds else 2.0
            self.stop.wait(interval)

    def close(self):
        if self.closed:
            return
        self.stop.set()
        if self.thread is not None:
            self.thread.join(timeout=2)
            if self.thread.is_alive():
                raise RuntimeError("diagnostic observer still owns its output")
        if self.sinkPatch is not None:
            sinks, original, measured = self.sinkPatch
            if measured in sinks:
                sinks[sinks.index(measured)] = original
            self.sinkPatch = None
        for owner, name, original, replacement in reversed(self.patches):
            # A real owner may already have retired/replaced a callback. Do
            # not resurrect it just because the diagnostic wrapper is closing.
            if getattr(owner, name) is replacement:
                setattr(owner, name, original)
        self.patches.clear()
        self.closed = True
        self._save()
        self.runtime = None
