"""Diagnostic only: source appendJobEvent, unchanged per-event transaction/PRAGMAs.

Run arms as separate processes on the same disk, outside CI/Qt workloads:
  python r3_sqlite_event_probe.py --repo REPO --output probe-baseline --mode baseline
  python r3_sqlite_event_probe.py --repo REPO --output probe-keeper --mode keeper
  python r3_sqlite_event_probe.py --repo REPO --output probe-reuse --mode reuse

Default per-arm measurement is <=20 seconds plus at most one source SQLite call
(source busy timeout remains 5 seconds), initialization and final verification.
This measures persistence capacity, not the 5 Hz workflow or a benchmark PASS.
No source edits, durability changes, event dropping, or GC-policy changes.
Keep outputs local; report necessary technical summaries only.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import gc
import hashlib
import json
from pathlib import Path
import platform
import sqlite3
import subprocess
import sys
import time
import weakref


BUCKETS_MS = (0.01, 0.1, 1, 5, 10, 20, 50, 100, 250, 500, 1000, 5000)
STATS = defaultdict(lambda: {"count": 0, "total_ms": 0.0, "max_ms": 0.0,
                             "histogram": [0] * (len(BUCKETS_MS) + 1)})
CONNECTIONS = {"created": 0, "explicit_closed": 0, "finalized": 0,
               "live": 0, "peak_live": 0}
GC_START = {}
NATIVE_CONNECT = sqlite3.connect


def record(stage, start):
    elapsed = (time.perf_counter_ns() - start) / 1e6
    row = STATS[stage]
    row["count"] += 1
    row["total_ms"] += elapsed
    row["max_ms"] = max(row["max_ms"], elapsed)
    bucket = next((i for i, upper in enumerate(BUCKETS_MS) if elapsed <= upper), len(BUCKETS_MS))
    row["histogram"][bucket] += 1


def measured(stage, operation, *args, **kwargs):
    start = time.perf_counter_ns()
    try:
        return operation(*args, **kwargs)
    finally:
        record(stage, start)


def finalized(token):
    CONNECTIONS["finalized"] += 1
    if not token["closed"]:
        CONNECTIONS["live"] -= 1


def gc_event(phase, info):
    generation = info["generation"]
    if phase == "start":
        GC_START[generation] = time.perf_counter_ns()
    elif generation in GC_START:
        record("gc.generation_" + str(generation), GC_START.pop(generation))


class TimedConnection(sqlite3.Connection):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.probe_token = {"closed": False}
        CONNECTIONS["created"] += 1
        CONNECTIONS["live"] += 1
        CONNECTIONS["peak_live"] = max(CONNECTIONS["live"], CONNECTIONS["peak_live"])
        weakref.finalize(self, finalized, self.probe_token)

    def execute(self, sql, parameters=()):
        normalized = " ".join(sql.split()).upper()
        if normalized.startswith("PRAGMA"):
            stage = "sql." + normalized.split("=", 1)[0]
        elif normalized.startswith("SELECT") and "MAX(SEQUENCE)" in normalized:
            stage = "sql.sequence_select"
        else:
            stage = "sql." + normalized.split(" ", 1)[0]
        return measured(stage, super().execute, sql, parameters)

    def commit(self):
        return measured("sql.commit", super().commit)

    def rollback(self):
        return measured("sql.rollback", super().rollback)

    def __exit__(self, *args):
        return measured("sql.context_exit", super().__exit__, *args)

    def close(self):
        result = measured("sql.explicit_close", super().close)
        if not self.probe_token["closed"]:
            self.probe_token["closed"] = True
            CONNECTIONS["explicit_closed"] += 1
            CONNECTIONS["live"] -= 1
        return result


class RetainedTimedConnection(TimedConnection):
    """Diagnostic reuse owner; source close releases a borrow, not ownership.

    Only the reuse arm retains a native connection across source transactions.
    It must physically retire that owner at cleanup, and reports the distinction.
    Production continues to close each independently owned connection.
    """

    def close(self):
        measured("sql.borrow_release", lambda: None)

    def retire(self):
        return super().close()


def sourceIdentity(repo):
    source = repo / "src/emo_master/apps/runtime/context/sqlite_store.py"
    return {"commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo,
                text=True, timeout=5).strip(),
            "sqlite_source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            "probe_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}


def resourceSnapshot(resources):
    try:
        return resources()
    except (OSError, AssertionError) as failure:
        return {"status": "UNAVAILABLE", "error": repr(failure)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--mode", choices=("baseline", "keeper", "reuse"), required=True)
    parser.add_argument("--count", type=int, default=600)
    parser.add_argument("--seconds", type=float, default=20.0)
    args = parser.parse_args()
    if not 1 <= args.count <= 10000 or not 0 < args.seconds <= 60:
        parser.error("count must be 1..10000; seconds must be in (0, 60]")
    args.repo = args.repo.resolve()
    args.output = args.output.resolve()
    if not (args.repo / "src/emo_master/apps/runtime/context/sqlite_store.py").is_file():
        parser.error("repo must contain the target SQLite implementation")
    if any(args.output.is_relative_to(args.repo / name)
           for name in ("src", "tests", "scripts", "proto", "examples", "prototypes")):
        parser.error("diagnostic output must be outside source directories")
    args.output.mkdir(parents=True, exist_ok=False)
    sys.path[:0] = [str(args.repo), str(args.repo / "src")]
    from emo_master.apps.runtime.context.sqlite_store import SqliteStore
    from scripts.p2_resources import resources

    before = sourceIdentity(args.repo)

    db = args.output / "probe.sqlite3"
    store = SqliteStore(db)
    store.initialize()
    gc.collect()  # Identical setup cleanup before the measured interval in all arms.
    check = NATIVE_CONNECT(db, timeout=5.0)
    try:
        pragmas = {name: check.execute("PRAGMA " + name).fetchone()[0]
                   for name in ("journal_mode", "synchronous", "wal_autocheckpoint", "page_size")}
    finally:
        check.close()
    keeper = NATIVE_CONNECT(db, timeout=5.0) if args.mode == "keeper" else None
    if keeper is not None:
        keeper.execute("SELECT count(*) FROM sqlite_master").fetchone()
    reused = None

    def connection(*positional, **keywords):
        nonlocal reused
        if args.mode == "reuse" and reused is not None:
            return reused
        keywords["factory"] = RetainedTimedConnection if args.mode == "reuse" else TimedConnection
        created = measured("sql.connect", NATIVE_CONNECT, *positional, **keywords)
        if args.mode == "reuse":
            reused = created
        return created

    sqlite3.connect = connection
    gc.callbacks.append(gc_event)
    completed = 0
    error = None
    cleanupErrors = []
    ownerBefore = resourceSnapshot(resources)
    cpuStarted = time.process_time()
    started = time.perf_counter()
    try:
        while completed < args.count and time.perf_counter() - started < args.seconds:
            sequence = measured("append.total", store.appendJobEvent,
                jobId="diagnostic", nodeId="count", eventType="node.completed",
                level="INFO", code="", message="diagnostic event",
                payloadJson='{"status":"COMPLETED","outputs":{"count":2},"metrics":{},"diagnostics":{}}',
                projectId="diagnostic", workflowId="detect", workflowRunId="diagnostic-run",
                nodeRunId="diagnostic-node", iterationPathJson="[0]", sequence=None)
            completed += 1
            if sequence != completed:
                raise AssertionError(f"sequence {sequence} != {completed}")
    except Exception as failure:
        error = repr(failure)
    finally:
        elapsed = time.perf_counter() - started
        cpuElapsed = time.process_time() - cpuStarted
        ownerAfterMeasurement = resourceSnapshot(resources)
        measured_stats = json.loads(json.dumps(STATS))
        measured_connections = dict(CONNECTIONS)
        sqlite3.connect = NATIVE_CONNECT
        gc.callbacks.remove(gc_event)
        for owned in (reused, keeper):
            if owned is not None:
                try:
                    if isinstance(owned, RetainedTimedConnection):
                        owned.retire()
                    else:
                        owned.close()
                except Exception as failure:
                    cleanupErrors.append(repr(failure))
        gc.collect()

    check = NATIVE_CONNECT(db, timeout=5.0)
    try:
        rows = check.execute("SELECT count(*), count(DISTINCT sequence), min(sequence), max(sequence) FROM jobEvents WHERE jobId='diagnostic'").fetchone()
        integrity = check.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        check.close()
    after = sourceIdentity(args.repo)
    payload = {"mode": args.mode, "requested": args.count, "completed": completed,
        "stopped_at_time_bound": completed < args.count and error is None,
        "elapsed_seconds": elapsed, "events_per_second": completed / elapsed if elapsed else None,
        "cpu_elapsed_seconds": cpuElapsed,
        "source_before": before, "source_after": after, "source_stable": before == after,
        "owner_before": ownerBefore, "owner_after_measurement": ownerAfterMeasurement,
        "owner_after_cleanup": resourceSnapshot(resources), "keeper_connection": keeper is not None,
        "cleanup_errors": cleanupErrors,
        "python": sys.version, "platform": platform.platform(), "sqlite": sqlite3.sqlite_version,
        "pragmas": pragmas, "error": error, "histogram_upper_bounds_ms": BUCKETS_MS,
        "stages": measured_stats, "connections_during_measurement": measured_connections,
        "connections_after_cleanup": dict(CONNECTIONS), "verified_row_counts": rows,
        "integrity_check": integrity,
        "verified": rows == (completed, completed, 1 if completed else None, completed or None) and integrity == "ok",
        "limitations": ["synthetic small payload; no workflow, Qt, IPC, readers or counter contention",
                        "instrumented sqlite subclass; adjacent uninstrumented confirmation needed",
                        "reuse arm alone borrows a diagnostic-owned connection; source close releases the borrow; cleanup physically closes the owner",
                        "elapsed bound checked between source operations; not a process watchdog",
                        "no change to SQLite sync policy, PRAGMAs or per-event transactions"]}
    (args.output / "summary.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps(payload, indent=2))
    return 0 if error is None and not cleanupErrors and payload["verified"] and before == after else 1


if __name__ == "__main__":
    raise SystemExit(main())
