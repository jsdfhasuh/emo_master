"""Fixed, isolated append lifetime/JSONL controls; no production policy changes.

python scripts/r3_sqlite_append_controls.py --repo . --output NEW_DIRECTORY

Seven sequential children use fresh data under the unmodified default temp root.
Only cohort boundary clocks are measured. Eligibility validates inputs, settings,
source and ownership; it is never a latency acceptance test or root-cause finding.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import weakref

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts import r3_sqlite_controls as child_tools

CASES = ("N0-raw-1", "N0-hook-1", "N0-hook-2", "N0-raw-2", "R0", "R1", "N1")
EVENT_COUNT = 16
CHILD_SECONDS = 30.0
LOG_CAP = 32 * 1024
RESULT_CAP = 64 * 1024
SUMMARY_CAP = 64 * 1024
TOTAL_CAP = 1024 * 1024
SOURCE_PATHS = (
    "scripts/r3_sqlite_append_controls.py", "scripts/r3_sqlite_controls.py",
    "src/emo_master/apps/runtime/context/sqlite_store.py",
    "src/emo_master/apps/runtime/context/runtime_lock.py",
    "src/emo_master/apps/runtime/events/event_store.py",
    "src/emo_master/apps/runtime/events/models.py",
    "src/emo_master/apps/runtime/events/jsonl_writer.py",
    "src/emo_master/apps/runtime/jobs/models.py",
    "src/emo_master/apps/runtime/jobs/repository.py",
    "src/emo_master/apps/runtime/jobs/supervisor.py",
    "src/emo_master/apps/runtime/grpc_server/service.py",
)
LIMITATIONS = (
    "Execution/cleanup eligibility is not a performance test or hosted root cause. "
    "Wall/current-thread CPU endpoints cover all 16 EventStore appends, not SQLite "
    "commit/fsync time. ABBA reports raw total differences, without subtraction or "
    "statistical attribution; single remaining cells are exploratory. Natural native "
    "finalization is unobserved. Only setup/keeper settings are queried; FULL on each "
    "measured connection is inferred from unchanged factory/build defaults, not "
    "directly measured. JSONL is the only defined controlled background, not full "
    "Runtime or OS activity. First terminal-flush endpoints indicate cohort overlap "
    "only and perturb the enabled arm. Same temp root is not physical-storage or "
    "failure-VM identity. Linux timings cannot substitute for Windows behavior."
)


def fault(error):
    # Never retain exceptions/tracebacks, which can keep natural native handles alive.
    return {"type": type(error).__name__, "message": str(error)[:400]}


def source_identity(repo):
    paths = list(SOURCE_PATHS)
    paths.extend(str(path.relative_to(repo)) for path in sorted(
        (repo / "src/emo_master/apps/runtime/context/migrations").glob("*.sql")))
    hashes = {path: child_tools.file_sha256(repo / path) for path in paths}
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo,
                          capture_output=True, text=True, timeout=3, check=True)
    dirty = subprocess.run(["git", "status", "--porcelain"], cwd=repo,
                           capture_output=True, text=True, timeout=3, check=True)
    return {"commit": head.stdout.strip(), "dirty": dirty.stdout[:8192],
            "source_sha256": hashes}


def event_manifest():
    types = (("job.started", ""), ("workflow.started", ""),
             ("node.started", "input"), ("node.completed", "input"),
             ("node.started", "output"), ("node.completed", "output"),
             ("workflow.completed", ""), ("job.completed", ""))
    return [dict(jobId=job, eventType=kind, message="fixed append control event",
                 level="INFO", nodeId=node, code="", payload={"fixed": "x" * 256},
                 projectId="append-project", workflowId="main",
                 workflowRunId=f"run-{job}", nodeRunId=f"{job}-{node}" if node else "",
                 timestampMs=1790841600000 + index)
            for job in ("append-A", "append-B") for index, (kind, node) in enumerate(types)]


def manifest_hash(manifest):
    return hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()


def expected_event_rows(manifest):
    return [[item["jobId"], index % 8 + 1, item["eventType"], "INFO", item["timestampMs"]]
            for index, item in enumerate(manifest)]


def cohort_timing_valid(interval):
    if not isinstance(interval, dict):
        return False
    keys = {"wall_start_ns", "wall_end_ns", "cpu_start_ns", "cpu_end_ns"}
    return (set(interval) == keys and all(type(value) is int for value in interval.values())
            and interval["wall_end_ns"] >= interval["wall_start_ns"]
            and interval["cpu_end_ns"] >= interval["cpu_start_ns"])


def flush_timing_valid(records):
    return all(type(item.get("entry_ns")) is int and type(item.get("exit_ns")) is int
               and item["exit_ns"] >= item["entry_ns"] for item in records)


def read_json_bounded(path, cap):
    with path.open("rb") as stream:
        encoded = stream.read(cap + 1)
    if len(encoded) > cap:
        raise ValueError(f"oversized evidence: {path.name}")
    return json.loads(encoded)


def canonical_temp_paths(data_root):
    """Resolve existing directory aliases without changing the default temp root."""
    temp_root_raw = tempfile.gettempdir()
    temp_root = Path(temp_root_raw).resolve(strict=True)
    owned_root = Path(data_root).resolve(strict=True)
    if not owned_root.is_dir() or str(owned_root.parent) != str(temp_root):
        raise ValueError("owned data directory is outside the canonical default temp root")
    return {"data_root": str(owned_root), "temp_root": str(temp_root),
            "temp_root_raw": temp_root_raw}


class ConnectCohort:
    """Count successful returns; optionally retain exactly the created native handles."""
    def __init__(self, store, *, hooked, retained):
        self.store = store
        self.hooked = hooked
        self.retained = retained
        self.owner = weakref.ref(threading.current_thread())
        self.owner_ident = threading.get_ident()
        self.return_count = 0
        self.unexpected_returns = 0
        self.overflow = 0
        self.slots = [None] * EVENT_COUNT if retained else []
        self.close_results = []
        self.restored = not hooked

    def __enter__(self):
        if not self.hooked:
            return self
        self.cls = type(self.store)
        self.original = self.cls.__dict__["_connect"]

        def connect(store):
            connection = self.original(store)
            if store is self.store and threading.current_thread() is self.owner():
                index = self.return_count
                self.return_count += 1
                if self.retained:
                    if index < EVENT_COUNT:
                        self.slots[index] = connection
                    else:
                        self.overflow += 1
            else:
                self.unexpected_returns += 1
            return connection

        self.cls._connect = connect
        return self

    def __exit__(self, *_):
        if self.hooked:
            self.cls._connect = self.original
        self.restored = True

    def close_retained(self):
        if threading.current_thread() is not self.owner():
            raise RuntimeError("retained close must run on its actual creating thread")
        if not self.restored:
            raise RuntimeError("restore the cohort hook before closing retained handles")
        for index in range(len(self.slots)):
            connection = self.slots[index]
            if connection is None:
                continue
            result = {"index": index, "owner_ident": threading.get_ident()}
            try:
                connection.close()
                result["closed"] = True
            except BaseException as error:
                result.update(closed=False, error=fault(error))
            finally:
                # A failed native close is not retried; child reap ends OS ownership.
                self.slots[index] = None
                self.close_results.append(result)

    def report(self):
        return {"connection_count": self.return_count if self.hooked else "UNOBSERVED",
                "count_means": "successful observed _connect returns, not native-live count",
                "unexpected_returns": self.unexpected_returns if self.hooked else "UNOBSERVED",
                "retained": self.retained, "overflow": self.overflow,
                "hook_restored": self.restored, "creator_ident": self.owner_ident,
                "close_results": list(self.close_results),
                "natural_finalization": "UNOBSERVED"}


class WriterObservation:
    def __init__(self):
        self.first = {}
        self.duplicate_terminal_flushes = 0
        self.cleanup_duplicate_terminal_flushes = 0
        self.cleanup_started = False
        self.other_sync_flushes = 0
        self.callback_count = 0
        self.callback_samples = []

    def flush(self, original, writer, *, sync, sourceEvent=None):
        key = getattr(sourceEvent, "jobId", None)
        terminal = (sync and key in {"append-A", "append-B"}
                    and getattr(sourceEvent, "eventType", None) == "job.completed")
        if terminal and key not in self.first:
            record = {"job": key, "entry_ns": time.perf_counter_ns()}
            self.first[key] = record
            try:
                value = original(writer, sync=sync, sourceEvent=sourceEvent)
                record["returned"] = value
                return value
            finally:
                record["exit_ns"] = time.perf_counter_ns()
        if terminal:
            self.duplicate_terminal_flushes += 1
            if self.cleanup_started:
                self.cleanup_duplicate_terminal_flushes += 1
        elif sync:
            self.other_sync_flushes += 1
        return original(writer, sync=sync, sourceEvent=sourceEvent)

    def failure(self, original, runtime, sourceEvent, message):
        self.callback_count += 1
        if len(self.callback_samples) < 4:
            self.callback_samples.append(str(message)[:200])
        return original(runtime, sourceEvent, message)

    def report(self, interval, enabled):
        records = list(self.first.values())
        valid = cohort_timing_valid(interval) and flush_timing_valid(records)
        overlap = valid and any(
            item.get("exit_ns", item["entry_ns"]) > interval["wall_start_ns"]
            and item["entry_ns"] < interval["wall_end_ns"] for item in records)
        return {"first_terminal_flushes": records,
                "timing_valid": valid,
                "duplicate_terminal_flushes": self.duplicate_terminal_flushes,
                "cleanup_duplicate_terminal_flushes": self.cleanup_duplicate_terminal_flushes,
                "other_sync_flushes": self.other_sync_flushes,
                "callback_count": self.callback_count,
                "callback_samples": list(self.callback_samples),
                "overlap": ("TIMING_INVALID" if not valid else
                            "OBSERVED" if overlap else "BACKGROUND_OVERLAP_NOT_OBSERVED")
                           if enabled else "CONTROLLED_JSONL_DISABLED"}


def read_settings(connection):
    result = {}
    for name in ("journal_mode", "synchronous", "busy_timeout", "wal_autocheckpoint", "page_size"):
        cursor = connection.execute(f"PRAGMA {name}")
        try:
            result[name] = cursor.fetchone()[0]
        finally:
            cursor.close()
    return result


def settings_on_setup(store):
    connection = store._connect()
    try:
        return read_settings(connection)
    finally:
        connection.close()


def policy_matches(settings, default):
    return (settings.get("journal_mode") == "wal" and settings.get("synchronous") == 2
            and settings.get("busy_timeout") == 5000
            and settings.get("wal_autocheckpoint") == default
            and settings.get("page_size", 0) > 0)


def inactive_owners(runtime):
    return {"process_handles": len(runtime.jobSupervisor._handles),
            "bridges": len(runtime.jobSupervisor._bridges),
            "preview_sessions": len(runtime.livePreviewManager._sessions),
            "preview_workers_started": len(runtime.previewExecutor._executor._threads),
            "synthetic_job_resources": any(runtime.jobSupervisor.ownsJobResources(job)
                                           for job in ("append-A", "append-B"))}


def verify_events(store, manifest):
    connection = store._connect()
    try:
        cursor = connection.execute(
            "SELECT jobId, sequence, eventType, level, timestampMs FROM jobEvents ORDER BY eventId")
        try:
            rows = [list(row) for row in cursor.fetchall()]
        finally:
            cursor.close()
    finally:
        connection.close()
    expected = expected_event_rows(manifest)
    return {"whole_table_count": len(rows), "exact_manifest_rows": rows == expected,
            "rows": rows, "error_rows": sum(row[3] == "ERROR" for row in rows)}


def thread_snapshot():
    return [{"name": item.name, "ident": item.ident, "daemon": item.daemon}
            for item in threading.enumerate()]


def timed(action, phases, name):
    start = time.perf_counter_ns()
    try:
        return action()
    finally:
        phases[name] = time.perf_counter_ns() - start


def run_worker(repo, data_root, case):
    paths = canonical_temp_paths(data_root)
    data_root = Path(paths["data_root"])
    sys.path.insert(0, str(repo / "src"))
    import _sqlite3
    from emo_master.apps.runtime.grpc_server.service import RuntimeService
    from emo_master.apps.runtime.events.jsonl_writer import RuntimeJsonlLogWriter
    from emo_master.apps.runtime.jobs.models import JobRecord

    before = source_identity(repo)
    manifest = event_manifest()
    background = case in {"N1", "R1"}
    retained = case in {"R0", "R1"}
    report = {"schema_version": 1, "case": case, "status": "INVALID", "eligible": False,
              "source_before": before, "manifest_sha256": manifest_hash(manifest),
              "fixed_foreground_append_calls": EVENT_COUNT, "append_calls_returned": 0,
              "setup_job_inserts_returned": 0,
              "settings_scope": "setup connection before/after and existing keeper; never measured append connections",
              "synthetic_job_scope": "event persistence only; no process, bridge, or supervisor terminal callback",
              "controlled_jsonl_enabled": background, "retained": retained,
              **paths,
              "db_path": str(data_root / "runtime.sqlite3"),
              "log_path": str(data_root / "logs"), "workspace_path": str(data_root / "jobs"),
              "path_anchor": data_root.anchor, "st_dev": data_root.stat().st_dev,
              "threads_before": thread_snapshot(), "errors": [], "cleanup_errors": [],
              "phases_ns": {}, "cohort": None, "limitations": LIMITATIONS}
    report["build"] = {"python": sys.version, "platform": platform.platform(),
        "sqlite_version": sqlite3.sqlite_version, "cpu_count": os.cpu_count(),
        "available_memory": "UNOBSERVED",
        **child_tools.sqlite_extension_identity(_sqlite3),
        "perf_counter": vars(time.get_clock_info("perf_counter")),
        "thread_time": vars(time.get_clock_info("thread_time"))}
    setup = sqlite3.connect(":memory:")
    try:
        report["build"]["source_id"] = setup.execute("SELECT sqlite_source_id()").fetchone()[0]
        report["build"]["compile_options"] = [row[0] for row in setup.execute("PRAGMA compile_options")]
        report["build"]["fresh_native_autocheckpoint_default"] = setup.execute(
            "PRAGMA wal_autocheckpoint").fetchone()[0]
    finally:
        setup.close()
    observation = WriterObservation()
    original_flush = RuntimeJsonlLogWriter.__dict__["_flush"]
    original_failure = RuntimeService.__dict__["_recordLogFileFailure"]
    RuntimeJsonlLogWriter._flush = lambda writer, **kwargs: observation.flush(original_flush, writer, **kwargs)
    RuntimeService._recordLogFileFailure = lambda runtime, event, message: observation.failure(
        original_failure, runtime, event, message)
    runtime = None
    cohort = None
    writer_closed = False
    try:
        # Assign ownership before __init__, so partial construction is visible to cleanup.
        runtime = RuntimeService.__new__(RuntimeService)
        RuntimeService.__init__(runtime, dbPath=data_root / "runtime.sqlite3",
            workspaceRoot=data_root / "jobs", logDirectory=data_root / "logs")
        report["threads_after_setup"] = thread_snapshot()
        runtime._maintenanceStop.set()
        runtime._maintenanceThread.join(timeout=1.0)
        if runtime._maintenanceThread.is_alive():
            raise RuntimeError("maintenance did not retire before cohort")
        if not background:
            runtime.eventStore.removeSink(runtime._operationalLogSink)
            error = runtime.operationalLogWriter.close()
            writer_closed = not runtime.operationalLogWriter._thread.is_alive()
            if error:
                raise RuntimeError(error)
        for job in ("append-A", "append-B"):
            runtime.jobRepository.create(JobRecord(jobId=job, projectId="append-project",
                projectRevision=1, workflowId="main", acceptedAtMs=1790841600000))
            report["setup_job_inserts_returned"] += 1
        report["settings_before"] = settings_on_setup(runtime.sqliteStore)
        report["keeper_settings"] = read_settings(runtime.sqliteStore._idleConnection)
        report["keeper_ready_during_cohort"] = runtime.sqliteStore._idleConnectionReady
        report["inactive_owners_before"] = inactive_owners(runtime)
        report["sink_matches_original"] = (
            runtime._operationalLogSink == runtime.operationalLogWriter.enqueue
            and (runtime._operationalLogSink in runtime.eventStore._sinks) == background)
        default = report["build"]["fresh_native_autocheckpoint_default"]
        if not all(policy_matches(report[key], default)
                   for key in ("settings_before", "keeper_settings")):
            raise RuntimeError("unsupported setup/keeper policy; no measured appends attempted")
        if any(report["inactive_owners_before"].values()) or not report["sink_matches_original"]:
            raise RuntimeError("unexpected Runtime producer/sink before cohort")
        report["threads_at_cohort"] = thread_snapshot()
        cohort = ConnectCohort(runtime.sqliteStore, hooked="raw" not in case, retained=retained)
        interval = {}
        with cohort:
            interval["wall_start_ns"] = time.perf_counter_ns()
            interval["cpu_start_ns"] = time.thread_time_ns()
            try:
                for arguments in manifest:
                    runtime.eventStore.append(**arguments)
                    report["append_calls_returned"] += 1
            finally:
                interval["cpu_end_ns"] = time.thread_time_ns()
                interval["wall_end_ns"] = time.perf_counter_ns()
                report["cohort"] = interval
        # Hook already restored before drain/close/verification, including exceptional exit.
    except BaseException as error:
        report["errors"].append(fault(error))
    finally:
        observation.cleanup_started = True
        if runtime is not None:
            writer = getattr(runtime, "operationalLogWriter", None)
            if writer is not None and not writer_closed:
                try:
                    error = timed(writer.close, report["phases_ns"], "writer_drain_close")
                    writer_closed = not writer._thread.is_alive()
                    if error:
                        raise RuntimeError(error)
                except BaseException as error:
                    report["cleanup_errors"].append({"owner": "writer", **fault(error)})
            if cohort is not None:
                try:
                    timed(cohort.close_retained, report["phases_ns"], "retained_close")
                except BaseException as error:
                    report["cleanup_errors"].append({"owner": "retained", **fault(error)})
                report["connections"] = cohort.report()
            store = getattr(runtime, "sqliteStore", None)
            if store is not None and writer_closed:
                try:
                    report["verification"] = timed(lambda: verify_events(store, manifest),
                        report["phases_ns"], "verify_rows")
                    report["settings_after"] = timed(lambda: settings_on_setup(store),
                        report["phases_ns"], "verify_settings")
                except BaseException as error:
                    report["errors"].append(fault(error))
            if writer is not None and writer_closed:
                report["writer"] = {"retired": True, "enabled": writer.enabled,
                    "enqueue_order": writer._enqueueOrder, "processed": dict(writer._processedSequences),
                    "dropped": dict(writer._dropped), "failure_message": writer.failureMessage,
                    "queues_empty": writer._queuesEmpty(),
                    "handle_reference_cleared": writer._handle is None,
                    "native_handle_close": "UNOBSERVED; source suppresses close OSError; child reap ends OS ownership"}
                try:
                    records = []
                    log_bytes = 0
                    for path in sorted((data_root / "logs").glob("*.jsonl")):
                        # Fixed 16 small records; unexpected output invalidates rather than grows memory.
                        log_bytes += path.stat().st_size
                        if log_bytes > 64 * 1024:
                            raise OverflowError("unexpected JSONL size")
                        with path.open(encoding="utf-8") as stream:
                            records.extend(json.loads(line) for line in stream)
                    report["writer"]["jsonl_records"] = len(records)
                    report["writer"]["jsonl_pairs"] = [[item.get("jobId"), item.get("sequence")] for item in records]
                except BaseException as error:
                    report["errors"].append(fault(error))
            try:
                timed(runtime.close, report["phases_ns"], "runtime_close")
            except BaseException as error:
                report["cleanup_errors"].append({"owner": "runtime", **fault(error)})
                # Runtime.close is fail-fast. Try independent non-native owners, but do not
                # retry ambiguous SQLite closes or release keeper while producers may live.
                for name, action in (
                    ("maintenance", lambda: (runtime._maintenanceStop.set(), runtime._maintenanceThread.join(1))),
                    ("live_preview", lambda: runtime.livePreviewManager.closeAll()),
                    ("preview", lambda: runtime.previewExecutor.close()),
                    ("assets", lambda: runtime.previewAssetStore.close()),
                    ("supervisor", lambda: runtime.jobSupervisor.shutdown()),
                    ("bridges", lambda: runtime.jobSupervisor.waitForRetirement()),
                    ("writer", lambda: runtime.operationalLogWriter.close()),
                ):
                    try:
                        value = action()
                        if value and name in {"live_preview", "writer"}:
                            raise RuntimeError(str(value))
                    except BaseException as error:
                        report["cleanup_errors"].append({"owner": name, **fault(error)})
            report["cleanup"] = {"runtime_closed": getattr(runtime, "_closed", False),
                "keeper_released": store is not None and store._idleConnection is None,
                "keeper_not_ready": store is not None and not store._idleConnectionReady,
                "maintenance_retired": not getattr(runtime, "_maintenanceThread", threading.current_thread()).is_alive(),
                "writer_retired": writer is not None and not writer._thread.is_alive(),
                "data_lock_released": not getattr(getattr(runtime, "_runtimeDataLock", None), "_acquired", True)}
            if report["cleanup"]["runtime_closed"]:
                report["inactive_owners_after"] = inactive_owners(runtime)
        RuntimeJsonlLogWriter._flush = original_flush
        RuntimeService._recordLogFileFailure = original_failure
    report["background"] = observation.report(report["cohort"], background)
    report["threads_after"] = thread_snapshot()
    report["source_after"] = source_identity(repo)
    report["source_stable"] = before == report["source_after"]
    report["eligible"] = comparison_eligible(report)
    report["status"] = "COMPLETED" if report["eligible"] else "INVALID"
    return report


def comparison_eligible(report):
    default = report["build"]["fresh_native_autocheckpoint_default"]
    settings = [report.get(key, {}) for key in ("settings_before", "keeper_settings", "settings_after")]
    policy_ok = all(policy_matches(item, default) for item in settings) and settings[0] == settings[1] == settings[2]
    connection = report.get("connections", {})
    hooked = "raw" not in report["case"]
    count_ok = (connection.get("connection_count") == EVENT_COUNT and
        connection.get("unexpected_returns") == 0) if hooked else (
        connection.get("connection_count") == "UNOBSERVED" and connection.get("unexpected_returns") == "UNOBSERVED")
    closes = connection.get("close_results", [])
    held_ok = (len(closes) == EVENT_COUNT and [item["index"] for item in closes] == list(range(EVENT_COUNT))
        and all(item["closed"] and item["owner_ident"] == connection["creator_ident"] for item in closes)
        ) if report["retained"] else not closes
    writer = report.get("writer", {})
    background = report["background"]
    duplicates = background.get("duplicate_terminal_flushes")
    cleanup_duplicates = background.get("cleanup_duplicate_terminal_flushes")
    duplicates_ok = (type(duplicates) is int and type(cleanup_duplicates) is int
                     and duplicates >= 0 and duplicates == cleanup_duplicates)
    first_flushes = background["first_terminal_flushes"]
    expected_pairs = [[job, index] for job in ("append-A", "append-B") for index in range(1, 9)]
    writer_ok = (writer.get("enqueue_order") == EVENT_COUNT and writer.get("jsonl_records") == EVENT_COUNT
        and writer.get("jsonl_pairs") == expected_pairs
        and writer.get("processed") == {"append-A": 8, "append-B": 8}
        and len(first_flushes) == 2 and {item["job"] for item in first_flushes} == {"append-A", "append-B"}
        and all(item.get("returned") is True for item in first_flushes)) if report["controlled_jsonl_enabled"] else (
        writer.get("enqueue_order") == 0 and writer.get("jsonl_records") == 0)
    return bool(policy_ok and count_ok and held_ok and writer_ok and duplicates_ok and report.get("source_stable")
        and cohort_timing_valid(report.get("cohort"))
        and flush_timing_valid(report["background"]["first_terminal_flushes"])
        and report["background"].get("timing_valid")
        and report.get("keeper_ready_during_cohort") and report["append_calls_returned"] == EVENT_COUNT
        and report.get("setup_job_inserts_returned") == 2
        and report.get("verification", {}).get("exact_manifest_rows")
        and report["verification"].get("whole_table_count") == EVENT_COUNT
        and report["verification"].get("error_rows") == 0
        and report["verification"].get("rows") == expected_event_rows(event_manifest())
        and not report["errors"]
        and not report["cleanup_errors"] and all(report.get("cleanup", {"missing": False}).values())
        and report.get("sink_matches_original") and "inactive_owners_after" in report
        and not any(report.get("inactive_owners_before", {"missing": True}).values())
        and not any(report.get("inactive_owners_after", {"missing": True}).values())
        and report["threads_after"] == report["threads_before"] and connection.get("hook_restored")
        and not connection.get("overflow") and writer.get("queues_empty") and writer.get("handle_reference_cleared")
        and not writer.get("dropped") and not writer.get("failure_message")
        and report["background"]["callback_count"] == 0)


def run_controls(repo, output, *, runner=None):
    repo, output = Path(repo).resolve(), Path(output).resolve()
    child_tools.validate_destination(repo, output)
    before = source_identity(repo)
    output.mkdir(parents=True, exist_ok=False)
    runner = runner or child_tools.run_child
    results = []
    stop = False
    for case in CASES:
        if stop:
            results.append({"case": case, "status": "SKIPPED", "reason": "prior invalid/unfinished control"})
            continue
        directory = output / case
        directory.mkdir()
        data_root_raw = Path(tempfile.mkdtemp(prefix="runtime-append-control-"))
        data_root = data_root_raw
        runner_called = False
        record = {"case": case, "data_root_raw": str(data_root_raw),
                  "data_root": str(data_root_raw), "status": "INVALID", "eligible": False}
        try:
            paths = canonical_temp_paths(data_root_raw)
            data_root = Path(paths["data_root"])
            record.update(data_root=str(data_root), temp_root=paths["temp_root"],
                          parent_temp_root_raw=paths["temp_root_raw"])
            command = [sys.executable, str(Path(__file__).resolve()), "--repo", str(repo),
                "--output", str(directory), "--worker-case", case, "--data-root", str(data_root)]
            # Once the runner is called, a failure may leave a child whose state is unknown.
            runner_called = True
            execution = runner(command, directory, timeout=CHILD_SECONDS, output_cap=LOG_CAP)
            record["execution"] = execution
            if execution["status"] == "EXITED" and execution["returncode"] == 0:
                result_path = directory / "result.json"
                result = read_json_bounded(result_path, RESULT_CAP)
                if (result["case"] != case or result["data_root"] != str(data_root)
                        or result["temp_root"] != paths["temp_root"]):
                    raise ValueError("child identity mismatch")
                record.update(status=result["status"], eligible=result["eligible"],
                    cohort=result["cohort"], background=result["background"],
                    manifest_sha256=result["manifest_sha256"], build=result["build"],
                    temp_root=result["temp_root"], temp_root_raw=result["temp_root_raw"],
                    source_stable=result["source_stable"],
                    result_path=f"{case}/result.json")
                if result["source_before"] != before or result["source_after"] != before:
                    record.update(status="INVALID", eligible=False, source_stable=False)
        except BaseException as error:
            record.update(status="INVALID", eligible=False, error=fault(error))
        finally:
            execution = record.get("execution", {})
            cleanup = execution.get("cleanup", {})
            retired = cleanup.get("process_reaped") and cleanup.get("output_reader_joined")
            record["child_not_started"] = not runner_called
            record["child_retirement_confirmed"] = bool(retired)
            if retired or not runner_called:
                try:
                    shutil.rmtree(data_root)
                    record["temp_data_removed"] = True
                except BaseException as error:
                    record.update(temp_data_removed=False, cleanup_error=fault(error), eligible=False)
            else:
                record["temp_data_removed"] = False
                record["eligible"] = False
            stop = not record["eligible"]
            results.append(record)
    after = source_identity(repo)
    completed = [item for item in results if item.get("eligible")]
    comparable = len(completed) == len(CASES) and all(
        item["build"] == completed[0]["build"] and item["manifest_sha256"] == completed[0]["manifest_sha256"]
        and item["temp_root"] == completed[0]["temp_root"] for item in completed)
    report = {"schema_version": 1, "source_before": before, "source_after": after,
        "source_stable": before == after, "fixed_order": list(CASES), "events_per_cohort": EVENT_COUNT,
        "planned_foreground_transactions": len(CASES) * EVENT_COUNT, "results": results,
        "same_build_manifest_temp_root": comparable, "eligible": comparable and before == after,
        "eligibility_scope": "execution, fixed inputs, source/settings, and declared-owner cleanup; no latency criterion",
        "background_overlap_evidence": {item["case"]: item.get("background", {}).get("overlap", "UNAVAILABLE")
                                        for item in results if item["case"] in {"R1", "N1"}},
        "limits": {"child_seconds": CHILD_SECONDS, "combined_output_bytes": TOTAL_CAP},
        "limitations": LIMITATIONS}
    child_tools.write_json_bounded(output / "summary.json", report, SUMMARY_CAP)
    return report


def validate_output(output, expected_source=None):
    """Read bounded result files and recompute execution eligibility; raise on gaps.

    A returned summary validates this fixed experiment's execution, not latency or
    background overlap. Callers must report each cell's explicit overlap separately.
    No data database is opened and no persisted eligible boolean is trusted alone.
    """
    output = Path(output)

    def require(condition, message):
        if not condition:
            raise ValueError(message)

    def read(path, cap):
        return read_json_bounded(path, cap)

    summary = read(output / "summary.json", SUMMARY_CAP)
    require(summary["schema_version"] == 1 and summary["fixed_order"] == list(CASES), "matrix mismatch")
    require(summary["events_per_cohort"] == EVENT_COUNT
            and summary["planned_foreground_transactions"] == 112, "transaction quota mismatch")
    require(summary["limits"] == {"child_seconds": CHILD_SECONDS, "combined_output_bytes": TOTAL_CAP},
            "bounds mismatch")
    source = summary["source_before"]
    require(source == summary["source_after"] and summary["source_stable"], "source changed")
    require(expected_source is None or source == expected_source, "unexpected source identity")
    require(len(summary["results"]) == len(CASES), "missing cohorts")
    expected_manifest = manifest_hash(event_manifest())
    expected_files = {"summary.json"}
    build = None
    temp_root = None
    for case, entry in zip(CASES, summary["results"]):
        require(entry["case"] == case, "cohort order mismatch")
        require(entry.get("status") == "COMPLETED" and entry.get("eligible")
                and "execution" in entry and "result_path" in entry,
                f"incomplete cohort evidence: {case}")
        execution = entry["execution"]
        require(execution["status"] == "EXITED" and execution["returncode"] == 0, "child execution failed")
        require(execution["hard_work_deadline_seconds"] == CHILD_SECONDS, "child bound changed")
        require(execution["cleanup"]["process_reaped"] and execution["cleanup"]["output_reader_joined"]
                and not execution["cleanup"]["reader_errors"] and not execution["output_truncated"]
                and not execution["capture_error"], "capture/child retirement incomplete")
        require(entry["child_retirement_confirmed"] and entry["temp_data_removed"], "data cleanup incomplete")
        path = f"{case}/result.json"
        require(entry["result_path"] == path, "unexpected result path")
        result = read(output / path, RESULT_CAP)
        require(result["schema_version"] == 1 and result["case"] == case, "result identity mismatch")
        require(result["data_root"] == entry["data_root"]
                and result["db_path"] == str(Path(result["data_root"]) / "runtime.sqlite3"), "data path mismatch")
        require(str(Path(result["data_root"]).parent) == result["temp_root"], "default temp root mismatch")
        require(isinstance(entry.get("data_root_raw"), str)
                and isinstance(entry.get("parent_temp_root_raw"), str)
                and isinstance(result.get("temp_root_raw"), str), "raw temp path evidence missing")
        require(result["source_before"] == source == result["source_after"], "cohort source mismatch")
        require(result["manifest_sha256"] == expected_manifest and result["fixed_foreground_append_calls"] == 16,
                "input mismatch")
        require(result["controlled_jsonl_enabled"] == (case in {"N1", "R1"})
                and result["retained"] == (case in {"R0", "R1"}), "matrix factor mismatch")
        require(comparison_eligible(result) and result["eligible"] and result["status"] == "COMPLETED",
                f"invalid cohort: {case}")
        require(entry["eligible"] and entry["status"] == "COMPLETED", "summary hides invalid execution")
        for field in ("cohort", "background", "manifest_sha256", "build", "temp_root", "temp_root_raw", "source_stable"):
            require(entry[field] == result[field], f"summary/result mismatch: {field}")
        interval = result["cohort"]
        require(set(interval) == {"wall_start_ns", "cpu_start_ns", "cpu_end_ns", "wall_end_ns"}
                and all(type(value) is int for value in interval.values()), "missing raw timing endpoints")
        # Preserve raw data, including negative wall-minus-CPU values. Reverse
        # endpoints of either individual monotonic clock are invalid evidence.
        require(cohort_timing_valid(interval), "invalid monotonic timing endpoints")
        flushes = result["background"]["first_terminal_flushes"]
        if result["controlled_jsonl_enabled"]:
            require({item["job"] for item in flushes} == {"append-A", "append-B"}
                    and all(type(item["entry_ns"]) is int and type(item["exit_ns"]) is int
                            and item["returned"] is True for item in flushes), "terminal flush evidence incomplete")
            overlap = any(item["exit_ns"] > interval["wall_start_ns"]
                          and item["entry_ns"] < interval["wall_end_ns"] for item in flushes)
            require(result["background"]["overlap"] == (
                "OBSERVED" if overlap else "BACKGROUND_OVERLAP_NOT_OBSERVED"), "overlap/endpoints mismatch")
        else:
            require(not flushes and result["background"]["overlap"] == "CONTROLLED_JSONL_DISABLED",
                    "disabled sink produced flush evidence")
        if build is None:
            build, temp_root = result["build"], result["temp_root"]
        require(result["build"] == build and result["temp_root"] == temp_root, "cross-cohort identity gap")
        log = output / case / "worker.log"
        require(log.stat().st_size <= LOG_CAP and log.stat().st_size == execution["captured_log_bytes"],
                "capture size mismatch")
        expected_files.update({path, f"{case}/worker.log"})
    actual_files = {str(path.relative_to(output)).replace(os.sep, "/")
                    for path in output.rglob("*") if path.is_file()}
    require(actual_files == expected_files, "unexpected/missing evidence files")
    require(sum((output / path).stat().st_size for path in expected_files) <= TOTAL_CAP, "total output cap exceeded")
    require(summary["eligible"] and summary["same_build_manifest_temp_root"], "summary not eligible")
    require(summary["background_overlap_evidence"] == {
        item["case"]: item["background"]["overlap"] for item in summary["results"]
        if item["case"] in {"R1", "N1"}}, "background overlap summary mismatch")
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--worker-case", choices=CASES, help=argparse.SUPPRESS)
    parser.add_argument("--data-root", type=Path, help=argparse.SUPPRESS)
    arguments = parser.parse_args()
    if arguments.worker_case:
        if arguments.data_root is None:
            parser.error("worker requires owned data-root")
        paths = canonical_temp_paths(arguments.data_root)
        result = run_worker(arguments.repo.resolve(), Path(paths["data_root"]), arguments.worker_case)
        child_tools.write_json_bounded(arguments.output / "result.json", result, RESULT_CAP)
    else:
        result = run_controls(arguments.repo, arguments.output)
        if result["eligible"]:
            validate_output(arguments.output, expected_source=source_identity(arguments.repo.resolve()))
        print(json.dumps({"eligible": result["eligible"], "summary": str(arguments.output / "summary.json")}))
    return 0 if result["eligible"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
