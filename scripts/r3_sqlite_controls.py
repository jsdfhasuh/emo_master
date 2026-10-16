"""Five isolated durable SQLite controls, run only after the diagnostic exits.

Usage: python scripts/r3_sqlite_controls.py --repo REPO --output NEW_DIRECTORY

This supervisor runs five child interpreters sequentially, each with a fresh DB.
It never opens a supplied/failed database. The source SqliteStore schema and
appendJobEvent transaction are used unchanged. Control connections are retained
until their measured cohort finishes and explicitly closed by their owner; this
bounded cleanup lifecycle differs from production's context-manager non-close.
No forced GC, Qt, SHM inspection, mmap, native tracing, or durability changes.
"""
from __future__ import annotations

import argparse
from collections import deque
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import platform
import sqlite3
import subprocess
import sys
import threading
import time


CASES = (
    "sole_writer_below_threshold",
    "competing_begin_immediate_writer",
    "held_reader_passive_checkpoint",
    "held_reader_truncate_checkpoint",
    "threshold_eligible_commit_then_reuse",
)
CASE_TIMEOUT_SECONDS = 30.0
CASE_OUTPUT_CAP = 1024 * 1024
RESULT_CAP = 256 * 1024
LOG_CAP = CASE_OUTPUT_CAP - RESULT_CAP
MAX_PAYLOAD_BYTES = 16 * 1024 * 1024
TIMER_SECONDS = 0.05
HOLD_SECONDS = 0.35
SOURCE_PATH = "src/emo_master/apps/runtime/context/sqlite_store.py"
LIMITATIONS = (
    "COMPLETED means execution, settings, committed rows, and cleanup completed; "
    "it does not validate a contention signature. Release signaling occurs at the "
    "Python call boundary before native entry: descheduling can consume the hold "
    "interval. Inspect raw span/holder timestamps and actual checkpoint tuples; "
    "no duration threshold is a PASS criterion. "
    "Fresh control databases only; separate sequential subprocesses after the original "
    "diagnostic process exits. Controls do not diagnose or alter a failed database. "
    "Source appendJobEvent SQL and durable commit are unchanged. Native handles are "
    "retained until each measured cohort ends, then explicitly closed on their owning "
    "thread; this retention/cleanup is a control difference from production's "
    "context-manager non-close. No forced GC. Physical WAL byte lengths describe "
    "file capacity only, never active frames, checkpoint history, or automatic "
    "checkpoint evidence. Timer progress and wall/thread CPU timings cannot prove "
    "fsync versus thread scheduling. Only explicit checkpoint actions return frame "
    "counts. Threshold eligibility is not observation of an automatic checkpoint."
)


class UnsupportedControl(RuntimeError):
    pass


def source_identity(repo):
    """Hash exactly the implementation/schema loaded, without reading a database."""
    paths = [Path(SOURCE_PATH), Path("src/emo_master/apps/runtime/events/models.py"),
             Path("src/emo_master/apps/runtime/context/global_counters.py")]
    paths.extend(sorted(path.relative_to(repo) for path in
        (repo / "src/emo_master/apps/runtime/context/migrations").glob("*.sql")))
    hashes = {str(path): hashlib.sha256((repo / path).read_bytes()).hexdigest()
              for path in paths}
    hashes["scripts/r3_sqlite_controls.py"] = hashlib.sha256(
        Path(__file__).read_bytes()).hexdigest()
    commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo,
        capture_output=True, text=True, timeout=3, check=False)
    return {"commit": commit.stdout.strip() if commit.returncode == 0 else None,
            "source_sha256": hashes}


def file_sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sqlite_extension_identity(module):
    # Some supported Python builds link _sqlite3 into the executable.
    filename = getattr(module, "__file__", None)
    executable = Path(sys.executable).resolve()
    return {"module_origin": getattr(getattr(module, "__spec__", None), "origin", None),
            "extension_path": filename,
            "extension_sha256": file_sha256(filename) if filename else None,
            "python_executable": str(executable),
            "python_executable_sha256": file_sha256(executable)}


def write_json_bounded(path, value, cap=RESULT_CAP):
    encoded = (json.dumps(value, sort_keys=True, indent=2) + "\n").encode("utf-8")
    if len(encoded) > cap:
        raise OverflowError("control report exceeds its output budget")
    with path.open("xb") as stream:
        stream.write(encoded)


def run_child(command, directory, timeout=CASE_TIMEOUT_SECONDS, output_cap=LOG_CAP):
    """Bound combined stdout/stderr in memory AND on disk; kill at the deadline.

    Only this worker process is launched by a control. A reader drains its single
    merged pipe without ever retaining more than output_cap bytes. No shell,
    communicate(), unbounded log file, or platform-specific select on pipes.
    """
    if not 0 < timeout <= CASE_TIMEOUT_SECONDS or not 0 < output_cap <= LOG_CAP:
        raise ValueError("invalid subprocess bounds")
    started = time.monotonic()
    process = subprocess.Popen(command, cwd=directory, stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    data = bytearray()
    overflow = threading.Event()
    reader_errors = []

    def drain():
        try:
            while True:
                chunk = os.read(process.stdout.fileno(), 8192)
                if not chunk:
                    return
                room = max(0, output_cap - len(data))
                data.extend(chunk[:room])
                if len(chunk) > room:
                    overflow.set()
        except Exception as error:
            reader_errors.append(type(error).__name__)

    reader = None
    reader_started = False
    capture_error = None
    status = "EXITED"
    try:
        reader = threading.Thread(target=drain, name="control-output", daemon=True)
        reader.start()
        reader_started = True
        while process.poll() is None:
            if reader_errors:
                status = "CAPTURE_FAILED"
                break
            if overflow.is_set():
                status = "OUTPUT_LIMIT"
                break
            remaining = timeout - (time.monotonic() - started)
            if remaining <= 0:
                status = "TIMEOUT"
                break
            overflow.wait(min(0.01, remaining))
    except Exception as error:
        capture_error = {"error_type": type(error).__name__, "message": str(error)[:500]}
        status = "CAPTURE_FAILED"
    finally:
        if process.poll() is None:
            process.kill()
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            status = "CLEANUP_INCOMPLETE"
        # The owned cleanup boundary includes Thread construction and start. A
        # startup failure must not leak the already-created process or its pipe.
        if reader is not None and (reader_started or reader.ident is not None):
            reader.join(timeout=1)
        reader_joined = reader is None or not reader.is_alive()
        # After worker exit the writer end is closed; close on the reader's owner
        # only once it has retired. A stuck reader is reported, never waited forever.
        if reader_joined:
            process.stdout.close()
        if overflow.is_set() and status == "EXITED":
            status = "OUTPUT_LIMIT"
        if reader_errors and status == "EXITED":
            status = "CAPTURE_FAILED"
        if not reader_joined:
            status = "CLEANUP_INCOMPLETE"
    log = bytes(data[:output_cap])
    with (directory / "worker.log").open("xb") as stream:
        stream.write(log)
    return {"status": status, "pid": process.pid, "returncode": process.returncode,
            "elapsed_seconds": time.monotonic() - started,
            "hard_work_deadline_seconds": timeout, "captured_log_bytes": len(log),
            "output_truncated": overflow.is_set(),
            "capture_error": capture_error,
            "cleanup": {"process_reaped": process.returncode is not None,
                        "output_reader_joined": reader_joined,
                        "reader_errors": reader_errors}}


def validate_destination(repo, output):
    if not (repo / SOURCE_PATH).is_file():
        raise ValueError("repo must contain SqliteStore")
    if any(output == repo / name or (repo / name) in output.parents
           for name in ("src", "tests", "scripts", "proto", "examples", "prototypes", ".git")):
        raise ValueError("output must be outside source directories")


def run_controls(repo, output, *, timeout=CASE_TIMEOUT_SECONDS, runner=None):
    if not 0 < timeout <= CASE_TIMEOUT_SECONDS:
        raise ValueError("case timeout must be in (0, 30]")
    repo, output = Path(repo).resolve(), Path(output).resolve()
    validate_destination(repo, output)
    before = source_identity(repo)
    output.mkdir(parents=True, exist_ok=False)
    results = []
    runner = runner or run_child
    stop = False
    for case in CASES:
        directory = output / case
        directory.mkdir()
        if stop:
            results.append({"case": case, "status": "SKIPPED",
                "reason": "previous worker cleanup incomplete; no overlapping controls"})
            continue
        command = [sys.executable, str(Path(__file__).resolve()), "--repo", str(repo),
                   "--output", str(directory), "--worker-case", case]
        try:
            execution = runner(command, directory, timeout=timeout)
        except Exception as error:
            results.append({"case": case, "status": "FAILED",
                            "error_type": type(error).__name__})
            # A failed supervisor call cannot establish whether its child retired.
            stop = True
            continue
        record = {"case": case, "execution": execution, "status": execution["status"]}
        result_path = directory / "result.json"
        if execution["status"] == "EXITED":
            try:
                if result_path.stat().st_size > RESULT_CAP:
                    raise OverflowError("result exceeds cap")
                result = json.loads(result_path.read_text(encoding="utf-8"))
                if result.get("case") != case or result.get("db_path") != str(directory / "control.sqlite3"):
                    raise ValueError("result isolation mismatch")
                record["result"] = result
                record["status"] = result["status"]
                if execution["returncode"] != 0 and result["status"] == "COMPLETED":
                    record["status"] = "FAILED"
            except (OSError, ValueError, KeyError, OverflowError) as error:
                record.update(status="FAILED", error_type=type(error).__name__)
        else:
            record["connection_cleanup"] = "not_confirmed_after_forced_worker_exit"
        results.append(record)
        cleanup = execution["cleanup"]
        stop = not (cleanup["process_reaped"] and cleanup["output_reader_joined"])
    after = source_identity(repo)
    identities = [item["result"]["sqlite_build"] for item in results
                  if item.get("result", {}).get("sqlite_build")]
    report = {"schema_version": 1, "source_before": before, "source_after": after,
        "source_stable": before == after, "cases": results,
        "same_sqlite_build_across_completed_controls": bool(identities) and all(
            identity == identities[0] for identity in identities),
        "limits": {"case_hard_seconds": timeout, "case_output_bytes": CASE_OUTPUT_CAP,
            "case_log_bytes": LOG_CAP, "case_result_bytes": RESULT_CAP,
            "max_payload_bytes": MAX_PAYLOAD_BYTES, "timer_seconds": TIMER_SECONDS},
        "limitations": LIMITATIONS}
    report["status"] = "COMPLETED" if (report["source_stable"] and
        report["same_sqlite_build_across_completed_controls"] and
        all(item["status"] == "COMPLETED" for item in results)) else "INCOMPLETE"
    write_json_bounded(output / "summary.json", report, CASE_OUTPUT_CAP)
    return report


class Recorder:
    def __init__(self):
        self.local = threading.local()
        self.lock = threading.Lock()
        self.spans = deque(maxlen=256)
        self.samples = deque(maxlen=240)
        self.span_count = 0
        self.sample_count = 0
        self.active = {}
        self.connections = []
        self.next_id = 0
        self.build = None
        self.timer_stop = threading.Event()
        self.writer_begin = threading.Event()
        self.checkpoint_started = threading.Event()
        self.timer = None

    @contextmanager
    def scope(self, label):
        previous = getattr(self.local, "label", "setup")
        self.local.label = label
        try:
            yield
        finally:
            self.local.label = previous

    def call(self, phase, operation, *args, **kwargs):
        label = getattr(self.local, "label", "setup")
        if phase == "begin_immediate" and label == "contended_event":
            self.writer_begin.set()
        if phase.startswith("explicit_checkpoint"):
            self.checkpoint_started.set()
        thread_id = threading.get_ident()
        row = {"phase": phase, "label": label, "thread_id": thread_id,
            "native_thread_id": threading.get_native_id(),
            "perf_counter_start_ns": time.perf_counter_ns(),
            "monotonic_start_ns": time.monotonic_ns(),
            "thread_cpu_start_ns": time.thread_time_ns()}
        with self.lock:
            previous = self.active.get(thread_id)
            self.active[thread_id] = {key: row[key] for key in ("phase", "label", "thread_id")}
        try:
            return operation(*args, **kwargs)
        except BaseException as error:
            row["error_type"] = type(error).__name__
            raise
        finally:
            row["perf_counter_end_ns"] = time.perf_counter_ns()
            row["monotonic_end_ns"] = time.monotonic_ns()
            row["perf_counter_elapsed_ns"] = row["perf_counter_end_ns"] - row["perf_counter_start_ns"]
            row["monotonic_elapsed_ns"] = row["monotonic_end_ns"] - row["monotonic_start_ns"]
            row["thread_cpu_elapsed_ns"] = time.thread_time_ns() - row["thread_cpu_start_ns"]
            with self.lock:
                self.span_count += 1
                self.spans.append(row)
                if previous is None:
                    self.active.pop(thread_id, None)
                else:
                    self.active[thread_id] = previous

    def start_timer(self):
        def tick():
            while not self.timer_stop.wait(TIMER_SECONDS):
                with self.lock:
                    self.sample_count += 1
                    self.samples.append({"monotonic_ns": time.monotonic_ns(),
                        "perf_counter_ns": time.perf_counter_ns(),
                        "thread_cpu_ns": time.thread_time_ns(),
                        "active": list(self.active.values())})
        self.timer = threading.Thread(target=tick, name="control-timer", daemon=True)
        self.timer.start()

    def stop_timer(self):
        self.timer_stop.set()
        if self.timer is not None and self.timer.ident is not None:
            self.timer.join(timeout=1)
        return self.timer is None or not self.timer.is_alive()

    def close_owned(self):
        errors = []
        for connection in self.connections:
            if connection.owner_thread == threading.get_ident() and not connection.retired:
                try:
                    connection.close()
                except Exception as error:
                    errors.append(type(error).__name__)
        return errors


def sql_phase(sql):
    normalized = " ".join(sql[:256].split()).upper()
    if normalized == "BEGIN IMMEDIATE":
        return "begin_immediate"
    if normalized.startswith("SELECT") and "MAX(SEQUENCE)" in normalized:
        return "sequence_select"
    if normalized.startswith("INSERT INTO JOBEVENTS"):
        return "event_insert"
    if normalized.startswith("PRAGMA WAL_CHECKPOINT("):
        return "explicit_checkpoint_" + normalized.split("(")[1].rstrip(")").lower()
    return "sql_" + normalized.split(" ", 1)[0].lower()


class ControlConnection(sqlite3.Connection):
    def __init__(self, *args, recorder, **kwargs):
        super().__init__(*args, **kwargs)
        self.recorder = recorder
        self.owner_thread = threading.get_ident()
        self.owner_native_thread = threading.get_native_id()
        self.retired = False
        self.settings = None
        self.role = getattr(recorder.local, "label", "setup")
        with recorder.lock:
            recorder.next_id += 1
            self.control_id = recorder.next_id
            recorder.connections.append(self)

    def execute(self, sql, parameters=()):
        return self.recorder.call(sql_phase(sql), super().execute, sql, parameters)

    def executescript(self, script):
        return self.recorder.call("executescript", super().executescript, script)

    def commit(self):
        return self.recorder.call("commit", super().commit)

    def rollback(self):
        return self.recorder.call("rollback", super().rollback)

    def __exit__(self, *args):
        return self.recorder.call("context_exit", super().__exit__, *args)

    def close(self):
        if threading.get_ident() != self.owner_thread:
            raise RuntimeError("control connection must close on its owner thread")
        if not self.retired:
            self.recorder.call("close", super().close)
            self.retired = True

    def verify(self):
        # Deliberately read settings only on our fresh control DB, never original DB.
        settings = {name: super(ControlConnection, self).execute("PRAGMA " + name).fetchone()[0]
                    for name in ("journal_mode", "synchronous", "busy_timeout",
                                 "wal_autocheckpoint", "page_size")}
        build_row = super().execute("SELECT sqlite_version(), sqlite_source_id()").fetchone()
        build = {"sqlite_version": build_row[0], "sqlite_source_id": build_row[1],
            "compile_options": sorted(row[0] for row in super().execute("PRAGMA compile_options"))}
        self.settings = settings
        self.sqlite_build = build
        if (str(settings["journal_mode"]).lower() != "wal" or
            settings["synchronous"] != 2 or settings["busy_timeout"] != 5000 or
            settings["wal_autocheckpoint"] <= 0 or settings["page_size"] <= 0):
            raise UnsupportedControl("actual WAL/FULL=2/busy5000/positive default threshold not available")
        with self.recorder.lock:
            if self.recorder.build is not None and self.recorder.build != build:
                raise UnsupportedControl("SQLite build identity differs across control connections")
            self.recorder.build = build
        return settings


def wal_capacity(db):
    path = Path(str(db) + "-wal")
    try:
        size = path.stat().st_size
    except FileNotFoundError:
        size = 0
    return {"physical_wal_bytes": size, "meaning": "file capacity only; not active frames or checkpoint history"}


class HeldConnection:
    """The holding thread creates, uses, rolls back and closes its own connection."""
    def __init__(self, store, recorder, kind, *, release_after=None):
        self.store, self.recorder, self.kind = store, recorder, kind
        self.release_after = release_after
        self.ready, self.release = threading.Event(), threading.Event()
        self.errors = []
        self.started_ns = self.released_ns = None
        self.action_signal_observed_ns = self.release_call_started_ns = None
        self.thread = threading.Thread(target=self._run, name="held-" + kind, daemon=True)

    def _run(self):
        try:
            with self.recorder.scope("held_" + self.kind):
                connection = self.store._connect()
                connection.execute("BEGIN IMMEDIATE" if self.kind == "writer" else "BEGIN")
                if self.kind == "reader":
                    connection.execute("SELECT count(*) FROM jobEvents").fetchone()
                self.started_ns = time.monotonic_ns()
                self.ready.set()
                if self.release_after is not None:
                    deadline = time.monotonic() + 3
                    while not self.release.is_set() and not self.release_after.wait(0.01):
                        if time.monotonic() >= deadline:
                            raise TimeoutError("expected control action did not start")
                    if self.release_after.is_set():
                        self.action_signal_observed_ns = time.monotonic_ns()
                    self.release.wait(HOLD_SECONDS)
                else:
                    if not self.release.wait(5):
                        raise TimeoutError("reader release was not signaled")
                self.release_call_started_ns = time.monotonic_ns()
                connection.rollback()
                self.released_ns = time.monotonic_ns()
        except BaseException as error:
            self.errors.append(type(error).__name__)
            self.ready.set()
        finally:
            self.errors.extend(self.recorder.close_owned())

    def start(self):
        self.thread.start()
        if not self.ready.wait(5) or self.errors:
            raise RuntimeError("holder failed to acquire transaction: " + ",".join(self.errors))
        return self

    def close(self):
        self.release.set()
        if self.thread.ident is not None:
            self.thread.join(timeout=1)
        return {"thread_joined": not self.thread.is_alive(), "errors": self.errors,
                "transaction_started_ns": self.started_ns,
                "action_signal_observed_ns": self.action_signal_observed_ns,
                "release_call_started_ns": self.release_call_started_ns,
                "transaction_released_ns": self.released_ns}


def run_worker(repo, directory, case):
    """Child-only function: the supervisor is the hard time/output boundary."""
    db = directory / "control.sqlite3"
    if db.exists() or any(directory.glob("control.sqlite3-*")):
        raise FileExistsError("worker refuses an existing control database")
    before = source_identity(repo)
    report = {"case": case, "db_path": str(db), "pid": os.getpid(),
        "status": "FAILED", "source_before": before, "limitations": LIMITATIONS,
        "clock_info": {name: vars(time.get_clock_info(name)) for name in
                       ("perf_counter", "monotonic", "thread_time")},
        "host": {"platform": platform.platform(), "python": sys.version,
                 "python_executable": sys.executable}, "actions": []}
    recorder = Recorder()
    native_connect = sqlite3.connect
    holders = []
    cleanup_errors = []
    store = None
    try:
        sys.path[:0] = [str(repo), str(repo / "src")]
        from emo_master.apps.runtime.context.sqlite_store import SqliteStore, _configureConnection
        import _sqlite3
        report["python_sqlite_extension"] = sqlite_extension_identity(_sqlite3)

        def connect(*args, **kwargs):
            if not args or Path(args[0]).resolve() != db.resolve():
                raise RuntimeError("control attempted another database")
            kwargs["factory"] = lambda *a, **k: ControlConnection(*a, recorder=recorder, **k)
            return recorder.call("connect", native_connect, *args, **kwargs)

        sqlite3.connect = connect
        store = SqliteStore(db)
        source_connect = store._connect
        reused = None

        def checked_connect():
            nonlocal reused
            label = getattr(recorder.local, "label", "setup")
            if case == CASES[4] and label in ("large_event", "reused_event"):
                if reused is None:
                    reused = source_connect()
                else:
                    _configureConnection(reused)
                connection = reused
            else:
                connection = source_connect()
            connection.verify()
            return connection

        store._connect = checked_connect
        store.initialize()
        report["post_initialization_wal"] = wal_capacity(db)
        with recorder.scope("idle_keeper"):
            store.retainIdleConnection()
            keeper = store._idleConnection
            settings = keeper.verify()
        report["settings"] = settings
        report["sqlite_build"] = recorder.build
        recorder.start_timer()
        completed = 0

        def append(label, payload="{}"):
            nonlocal completed
            with recorder.scope(label):
                sequence = recorder.call("append_event", store.appendJobEvent,
                    "control-job", "node", "node.completed", "INFO", "", "control", payload)
            completed += 1
            if sequence != completed:
                raise AssertionError("event sequence is not contiguous")
            return sequence

        def small_events(label, count=3):
            for _ in range(count):
                append(label)

        threshold = int(settings["wal_autocheckpoint"])
        if case == CASES[0]:
            # Tiny fresh-DB cohort under normal default threshold. The physical
            # capacity bound is ONLY a sufficient bound here, never an estimate of
            # active frames; do not derive history or automatic checkpoint results.
            small_events("sole_writer_event")
            capacity = wal_capacity(db)
            boundary_bytes = 32 + threshold * (int(settings["page_size"]) + 24)
            report["below_threshold_control"] = {"event_count": completed,
                "default_threshold": threshold, "capacity_bound_bytes": boundary_bytes,
                "physical_capacity_below_threshold_bound": capacity["physical_wal_bytes"] < boundary_bytes,
                **capacity}
            if capacity["physical_wal_bytes"] >= boundary_bytes:
                raise UnsupportedControl("small cohort not demonstrably below default threshold capacity bound")
        elif case == CASES[1]:
            holder = HeldConnection(store, recorder, "writer", release_after=recorder.writer_begin)
            holders.append(holder)
            holder.start()
            append("contended_event")
            report["expected_signature"] = "deliberate competing writer releases after BEGIN IMMEDIATE entry; inspect begin_immediate span"
        elif case in (CASES[2], CASES[3]):
            append("seed_event")
            holder = HeldConnection(store, recorder, "reader", release_after=(
                recorder.checkpoint_started if case == CASES[3] else None))
            holders.append(holder)
            holder.start()
            small_events("reader_held_event")
            mode = "PASSIVE" if case == CASES[2] else "TRUNCATE"
            with recorder.scope("checkpoint_action"):
                checkpoint = store._connect()
                returned = checkpoint.execute("PRAGMA wal_checkpoint(" + mode + ")").fetchone()
            report["actions"].append({"action": "explicit_checkpoint", "mode": mode,
                "result_tuple": list(returned), "busy": returned[0],
                "log_frames": returned[1], "checkpointed_frames": returned[2],
                "reader_release": "after_action_returns" if mode == "PASSIVE" else "bounded_release_after_action_enters",
                "interpretation": "PASSIVE does not wait for readers and may checkpoint only part of WAL" if mode == "PASSIVE" else "TRUNCATE can wait for reader; reader release deliberately bounded"})
            holder.release.set()
        elif case == CASES[4]:
            payload_bytes = (threshold + 64) * int(settings["page_size"])
            if payload_bytes > MAX_PAYLOAD_BYTES:
                raise UnsupportedControl("default threshold requires payload above bounded workload")
            before_pages = keeper.execute("PRAGMA page_count").fetchone()[0]
            append("large_event", json.dumps({"data": "x" * payload_bytes}))
            after_pages = keeper.execute("PRAGMA page_count").fetchone()[0]
            report["large_commit"] = {"payload_bytes": payload_bytes,
                "default_threshold": threshold, "page_count_before": before_pages,
                "page_count_after": after_pages,
                "new_database_pages": after_pages - before_pages,
                "threshold_eligible": after_pages - before_pages >= threshold,
                "automatic_checkpoint_observation": "not_instrumented; eligibility only",
                "writer_connection_id": reused.control_id, "wal_after_commit": wal_capacity(db)}
            small_events("reused_event")
            report["large_commit"]["wal_after_reuse"] = wal_capacity(db)
            report["large_commit"]["reuse_connection_id"] = reused.control_id
            if not report["large_commit"]["threshold_eligible"]:
                raise UnsupportedControl("bounded large commit did not establish threshold eligibility")
        else:
            raise ValueError("unknown case")
        with recorder.scope("verification"):
            rows = keeper.execute("SELECT count(*), count(DISTINCT sequence), min(sequence), max(sequence) FROM jobEvents WHERE jobId = ?", ("control-job",)).fetchone()
        report["verified_rows"] = list(rows)
        report["completed_events"] = completed
        if list(rows) != [completed, completed, 1, completed]:
            raise AssertionError("committed events failed verification")
        report["status"] = "COMPLETED"
    except UnsupportedControl as error:
        report.update(status="UNSUPPORTED", reason=str(error))
    except ImportError as error:
        report.update(status="UNSUPPORTED", reason="source import unavailable", error_type=type(error).__name__)
    except Exception as error:
        report.update(status="FAILED", error_type=type(error).__name__, reason=str(error)[:1000])
    finally:
        holder_cleanup = [holder.close() for holder in holders]
        timer_joined = recorder.stop_timer()
        if store is not None:
            try:
                store.releaseIdleConnection()
            except Exception as error:
                cleanup_errors.append(type(error).__name__)
        cleanup_errors.extend(recorder.close_owned())
        sqlite3.connect = native_connect
        report["cleanup"] = {"holders": holder_cleanup, "timer_joined": timer_joined,
            "errors": cleanup_errors, "connections_created": len(recorder.connections),
            "connections_closed": sum(connection.retired for connection in recorder.connections),
            "connections": [{"id": connection.control_id, "role": connection.role,
                "owner_thread_id": connection.owner_thread,
                "owner_native_thread_id": connection.owner_native_thread,
                "closed_on_owner_thread": connection.retired,
                "settings": connection.settings,
                "sqlite_build_verified": getattr(connection, "sqlite_build", None) == recorder.build}
                for connection in recorder.connections]}
        report["spans"] = list(recorder.spans)
        report["spans_evicted"] = recorder.span_count - len(recorder.spans)
        report["timer"] = {"cadence_seconds": TIMER_SECONDS, "samples": list(recorder.samples),
            "samples_evicted": recorder.sample_count - len(recorder.samples),
            "interpretation": "progress cannot distinguish fsync from thread scheduling"}
        report["source_after"] = source_identity(repo)
        report["source_stable"] = report["source_after"] == before
        if (not report["source_stable"] or cleanup_errors or not timer_joined or
            any(not item["thread_joined"] or item["errors"] for item in holder_cleanup) or
            any(not connection.retired for connection in recorder.connections)):
            report["status"] = "FAILED"
        write_json_bounded(directory / "result.json", report)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--case-timeout", type=float, default=CASE_TIMEOUT_SECONDS)
    parser.add_argument("--worker-case", choices=CASES, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if not 0 < args.case_timeout <= CASE_TIMEOUT_SECONDS:
        parser.error("case-timeout must be in (0, 30]")
    repo, output = args.repo.resolve(), args.output.resolve()
    try:
        validate_destination(repo, output)
        report = (run_worker(repo, output, args.worker_case) if args.worker_case else
                  run_controls(repo, output, timeout=args.case_timeout))
    except (ValueError, FileExistsError) as error:
        parser.error(str(error))
    print(json.dumps({"status": report["status"], "output": str(output)}))
    return 0 if report["status"] == "COMPLETED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
