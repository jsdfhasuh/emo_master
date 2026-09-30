"""Bounded R3 stage attribution of the unchanged P2/P3 offline benchmarks.

Each arm runs serially in an owned process tree. Default workload is the original
fixed-seed 1080p, 5 Hz, 96 inputs / 8 warmup, three alternating pairs. P2 retains
legacy snapshots in capture-off and capture-on; P3 retains capture and two
sessions in UI-off and UI-on. No policy or production instrumentation is enabled.
Stage wrappers add overhead, so these results do not replace historical P2/P3
PASS/FAIL evidence. Native Qt paint events are not physical-screen presentation.
"""
from collections import Counter, defaultdict
from contextlib import contextmanager
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import signal
import subprocess
import statistics
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT), str(ROOT / "scripts")]

from emo_master.clients.runtime.display_session import DisplaySession  # noqa: E402
from emo_master.apps.runtime.presentation.exporter import ExportPool  # noqa: E402
from r3_resources import resources  # noqa: E402

TRACE_LIMIT = 24000
TRACE_BYTES = 24 * 1024 * 1024
LOG_BYTES = 4 * 1024 * 1024
MIN_FREE_BYTES = 512 * 1024 * 1024
OWNER_TRACE = None
RESOURCE_IDENTITIES = {}
ENCODER_TRACES = {}
OBSERVED_RESULTS = {}


class Trace:
    """No per-event I/O, no queues, no unbounded instrumentation history."""
    def __init__(self, role):
        self.role = role
        self.rows = []
        self.dropped = 0
        self.local = threading.local()
        self.lock = threading.Lock()

    def call(self, stage, function, *args, metadata=None, **kwargs):
        previous = getattr(self.local, "metadata", {})
        previousStage = getattr(self.local, "stage", "")
        self.local.metadata = {**previous, **(metadata or {})}
        self.local.stage = stage
        start = time.perf_counter_ns()
        outcome = "OK"
        try:
            return function(*args, **kwargs)
        except BaseException as error:
            outcome = type(error).__name__
            raise
        finally:
            end = time.perf_counter_ns()
            row = {"stage": stage, "parent_stage": previousStage, "start_ns": start,
                   "end_ns": end, "elapsed_ms": (end - start) / 1e6,
                   "outcome": outcome, "thread": threading.current_thread().name,
                   **self.local.metadata}
            with self.lock:
                if len(self.rows) < TRACE_LIMIT:
                    self.rows.append(row)
                else:
                    self.dropped += 1
            self.local.metadata, self.local.stage = previous, previousStage

    def save(self):
        root = Path(os.environ["EMO_R3_TRACE_DIR"])
        payload = {"role": self.role, "pid": os.getpid(), "row_limit": TRACE_LIMIT,
                   "dropped_rows": self.dropped, "rows": self.rows}
        content = json.dumps(payload, separators=(",", ":"))
        if len(content.encode()) > TRACE_BYTES:
            raise RuntimeError("trace file budget exceeded")
        (root / f"trace-{self.role}-{os.getpid()}.json").write_text(content, encoding="utf-8")


@contextmanager
def patches():
    changes = []

    def patch(owner, name, replacement):
        changes.append((owner, name, getattr(owner, name)))
        setattr(owner, name, replacement)
    try:
        yield patch
    finally:
        for owner, name, value in reversed(changes):
            setattr(owner, name, value)


def wrap(trace, patch, owner, name, stage, metadata=None, predicate=None):
    original = getattr(owner, name)

    def measured(*args, **kwargs):
        if predicate is not None and not predicate(*args, **kwargs):
            return original(*args, **kwargs)
        label = stage(*args, **kwargs) if callable(stage) else stage
        details = metadata(*args, **kwargs) if metadata else None
        return trace.call(label, original, *args, metadata=details, **kwargs)
    patch(owner, name, measured)


def contextDetails(context):
    path = list(context.iterationPath)
    return {"workflow": context.workflowId, "invocation": context.workflowRunId,
            "node": context.callerNodeId, "iteration_path": path,
            "ordinal": path[-1] + 1 if path else None}


def measuredJob(spec, cancelEvent, eventQueue):
    """Spawn entrypoint used only by this script; calls the original worker."""
    import cv2
    import numpy as np
    from emo_master.apps.runtime.jobs.worker_main import runJobProcess
    from emo_master.apps.runtime.workflow.runner import WorkflowRunner
    from emo_master.apps.runtime.preview import store
    from emo_master.apps.runtime.presentation.collector import ResultCollector

    trace = Trace("job")
    try:
        with patches() as patch:
            wrap(trace, patch, WorkflowRunner, "_runNode",
                 lambda _self, node, *_a: "input.operator_total" if node.nodeId == "load" else
                 "scheduler.wait" if node.nodeId == "pace" else "algorithm.operator_total",
                 lambda _self, node, _inputs, _supplied, context, _cancel:
                 {**contextDetails(context), "operator": node.operatorId},
                 lambda _self, node, *_a: node.kind == "operator")
            wrap(trace, patch, cv2, "imread", "input.file_read_decode")
            wrap(trace, patch, np, "fromfile", "input.file_read")
            wrap(trace, patch, cv2, "imdecode", "input.decode")
            wrap(trace, patch, store.PreviewSnapshotWriter, "capture", "legacy.capture_total",
                 lambda _self, node, outputs, context: contextDetails(context))
            wrap(trace, patch, cv2, "imencode", "legacy.png_encode")
            wrap(trace, patch, store, "_atomicBytes", "legacy.atomic_write_fsync",
                 lambda path, data: {"asset_kind": path.suffix, "bytes": len(data)})
            wrap(trace, patch, os, "fsync", "legacy.fsync",
                 predicate=lambda *_a: getattr(trace.local, "stage", "").startswith("legacy."))
            wrap(trace, patch, ResultCollector, "observe", "capture.provenance")
            wrap(trace, patch, ResultCollector, "output", "capture.freeze_total",
                 lambda _self, context, _outputs, **_kw: contextDetails(context))
            wrap(trace, patch, ResultCollector, "image", "capture.image_copy_descriptor",
                 lambda _self, key, value, item: {"source": key,
                     "result_key": item["identity"]["resultKey"], "raw_bytes": int(value.nbytes)
                     if hasattr(value, "nbytes") else None})
            trace.call("job.worker_total", runJobProcess, spec, cancelEvent, eventQueue)
    finally:
        trace.save()


class MeasuredConnection:
    def __init__(self, connection, trace):
        self.connection, self.trace = connection, trace

    def recv(self):
        task = self.connection.recv()
        if isinstance(task, dict):
            self.trace.local.metadata = {"result_key": task.get("key"),
                "source": task.get("sourceId"), "lane": task.get("lane", 0),
                "frozen_to_export_dispatch_ms": (time.monotonic() - task["frozenAt"]) * 1000}
        return task

    def send(self, value):
        if isinstance(value, dict):
            # ExportPool terminates even idle workers on normal close. Carry
            # tiny completed-stage batches in its existing reply instead of
            # depending on a child finally block or changing retirement.
            value = {**value, "r3_trace": {"pid": os.getpid(), "rows": self.trace.rows,
                                         "dropped_rows": self.trace.dropped}}
            self.trace.rows = []
        return self.connection.send(value)

    def close(self):
        return self.connection.close()


def measuredEncoder(connection, memoryName):
    import cv2
    from emo_master.apps.runtime.presentation.exporter import encodeWorker
    trace = Trace("exporter")
    with patches() as patch:
        wrap(trace, patch, cv2, "imencode", "export.png_encode")
        wrap(trace, patch, Path, "write_bytes", "export.file_write",
             lambda _path, value: {"bytes": len(value)})
        encodeWorker(MeasuredConnection(connection, trace), memoryName)


class TraceReceiver:
    def __init__(self, connection):
        self.connection = connection

    def recv(self):
        value = self.connection.recv()
        if isinstance(value, dict) and "r3_trace" in value:
            batch = value.pop("r3_trace")
            record = ENCODER_TRACES[batch["pid"]]
            remaining = max(0, TRACE_LIMIT - len(record["rows"]))
            record["rows"].extend(batch["rows"][:remaining])
            record["dropped_rows"] += max(0, len(batch["rows"]) - remaining) + batch["dropped_rows"]
        return value

    def send(self, value):
        return self.connection.send(value)

    def poll(self, timeout=0):
        return self.connection.poll(timeout)

    def close(self):
        return self.connection.close()


class MeasuredPool(ExportPool):
    def __init__(self, root, callback):
        super().__init__(root, callback, worker=measuredEncoder)

    def _spawn(self, slot):
        super()._spawn(slot)
        if len(ENCODER_TRACES) >= 128:
            raise RuntimeError("exporter trace process budget exceeded")
        pid = slot["process"].pid
        ENCODER_TRACES[pid] = {"role": "exporter", "pid": pid, "row_limit": TRACE_LIMIT,
                               "dropped_rows": 0, "rows": []}
        slot["connection"] = TraceReceiver(slot["connection"])


class MeasuredSession(DisplaySession):
    def _decode(self):
        original = self.stub.ReadAsset

        def read(*args, **kwargs):
            identity = RESOURCE_IDENTITIES.get(args[0].resource_id, {})
            OWNER_TRACE.local.metadata = {"consumer": id(self), **identity}
            return OWNER_TRACE.call("client.asset_rpc", original, *args, **kwargs)
        self.stub.ReadAsset = read
        OWNER_TRACE.local.metadata = {"consumer": id(self)}
        return super()._decode()


def summaryStages(traces, warmup):
    stages = defaultdict(list)
    for trace in traces:
        for row in trace["rows"]:
            ordinal = row.get("ordinal")
            phase = "warmup" if ordinal is not None and ordinal <= warmup else (
                "measurement" if ordinal is not None else "unattributed")
            stages[(trace["role"], row["stage"], phase)].append(row["elapsed_ms"])
    return [{"role": role, "stage": stage, "phase": phase, "count": len(values),
             "sum_ms": sum(values), "mean_ms": statistics.mean(values), "p95_ms": percentile(values)}
            for (role, stage, phase), values in sorted(stages.items())]


def guiDetails(window, view):
    scopes = list(view.scopes.values())
    details = {"window": id(window)}
    if len(scopes) == 1:
        result = scopes[0].result
        details.update(ordinal=result.identity.resultOrdinal, result_key=result.identity.resultKey)
    return details


def percentile(values):
    return sorted(values)[min(len(values) - 1, int(len(values) * .95))] if values else None


def addDenominators(result, count, warmup):
    expected = count - warmup
    result["denominators"] = {"planned_inputs": count, "warmup_inputs": warmup,
        "measured_inputs": expected, "executed_total": len(result["raw_timings"]),
        "missing_execution_count": max(0, count - len(result["raw_timings"])),
        "execution_ordinal_gap_localization": "not available in existing display.timing protocol"}
    for consumer in result["consumers"]:
        observed = {row["ordinal"] for row in consumer["rows"]}
        consumer["missing_measured_ordinals"] = sorted(set(range(warmup + 1, count + 1)) - observed)
        consumer["received_not_applied"] = sum(not row["applied_to_live"] for row in consumer["rows"])
    keys = {row["key"]: row["ordinal"] for consumer in result["consumers"] for row in consumer["rows"]}
    for window in result.get("ui", []):
        committed = {keys.get(row["key"]) for row in window["raw_rows"]}
        painted = {keys.get(row["key"]) for row in window["raw_rows"] if row.get("paint_ns")}
        window["missing_committed_ordinals"] = sorted(set(range(warmup + 1, count + 1)) - committed)
        window["missing_painted_ordinals"] = sorted(set(range(warmup + 1, count + 1)) - painted)
    result["record_retention"] = {"client_capacity": 128, "qt_capacity": 256,
        "capacity_reached": any(len(c["rows"]) >= 128 for c in result["consumers"]) or
                            any(len(w["raw_rows"]) >= 256 for w in result.get("ui", []))}
    result["phases"] = {"warmup_execution_rows": result["raw_timings"][:warmup],
        "measurement_execution_rows": result["raw_timings"][warmup:],
        "terminal_drain_seconds": .5, "execution_observation_deadline_seconds": 45,
        "export_deadline_seconds": .5, "asset_rpc_deadline_seconds": .5}


def executeTrial(args):
    global OWNER_TRACE
    os.environ["QT_QPA_PLATFORM"] = args.qt_platform
    os.environ["EMO_R3_TRACE_DIR"] = str(args.output)
    import p2_measure
    import p3_measure
    from emo_master.apps.runtime.jobs import supervisor
    from emo_master.apps.runtime.presentation import service
    from emo_master.clients.runtime import display_session
    from emo_master.ui.presentation.renderer import RuntimePages
    from emo_master.apps.runtime.grpc_server.service import RuntimeService
    from PySide2.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    OWNER_TRACE = Trace("owner")
    ownerBefore = resources()
    result = None
    started = time.perf_counter_ns()
    try:
        with patches() as patch:
            patch(supervisor, "runJobProcess", measuredJob)
            patch(service, "ExportPool", MeasuredPool)
            for module in (p2_measure, p3_measure):
                patch(module, "processResources", resources)
                patch(module, "DisplaySession", MeasuredSession)
            originalDecode = display_session.decodeResult

            def decodeMetadata(wire):
                result = originalDecode(wire)
                if len(OBSERVED_RESULTS) < 128:
                    OBSERVED_RESULTS[result.identity.resultKey] = {
                        "ordinal": result.identity.resultOrdinal, "status": result.status,
                        "source_outcomes": {source.sourceId: {"state": source.state,
                            "reason": source.reasonCode} for source in result.sources}}
                for source in result.sources:
                    if source.image and len(RESOURCE_IDENTITIES) < 256:
                        RESOURCE_IDENTITIES[source.image.resourceId] = {
                            "ordinal": result.identity.resultOrdinal,
                            "result_key": result.identity.resultKey, "source": source.sourceId}
                return result

            patch(display_session, "decodeResult", decodeMetadata)
            wrap(OWNER_TRACE, patch, display_session, "decodePng", "client.png_decode")
            wrap(OWNER_TRACE, patch, RuntimePages, "submit", "gui.apply",
                 guiDetails)
            wrap(OWNER_TRACE, patch, RuntimeService, "close", "cleanup.runtime_close")
            wrap(OWNER_TRACE, patch, DisplaySession, "close", "cleanup.client_close")
            wrap(OWNER_TRACE, patch, service.PresentationService, "prepare", "setup.prepare_exporters")
            wrap(OWNER_TRACE, patch, service.PresentationService, "start", "setup.start_job")
            function = p2_measure.trial if args.family == "capture" else p3_measure.trial
            # No child-side deletion: an original trial's finally can itself
            # fail. The outer supervisor retains this directory until all
            # explicit close calls returned and its process tree is retired.
            workload = args.output / "work-trial"
            workload.mkdir()
            result = function(workload / "workload", bool(args.enabled), args.count, args.warmup)
            (args.output / "owners-closed.json").write_text(
                json.dumps({"all_original_trial_close_calls_returned": True}), encoding="utf-8")
        addDenominators(result, args.count, args.warmup)
        result["observed_result_outcomes"] = OBSERVED_RESULTS
        result.update(family=args.family, owner_resources_before=ownerBefore,
                      owner_resources_after_cleanup=resources(),
                      trial_elapsed_ms=(time.perf_counter_ns() - started) / 1e6,
                      qt_platform=app.platformName(), instrumentation="measurement-only inclusive wrappers")
    finally:
        OWNER_TRACE.save()
        for pid, trace in ENCODER_TRACES.items():
            content = json.dumps(trace, separators=(",", ":"))
            if len(content.encode()) > TRACE_BYTES:
                raise RuntimeError("exporter trace file budget exceeded")
            (args.output / f"trace-exporter-{pid}.json").write_text(content, encoding="utf-8")
        # Explicit child-process ownership: no live trial's objects cross arms.
        app.processEvents()
    if result is not None:
        content = json.dumps(result, ensure_ascii=True)
        if len(content.encode()) > 24 * 1024 * 1024:
            raise RuntimeError("raw trial evidence exceeds 24 MiB")
        (args.output / "trial.json").write_text(content, encoding="utf-8")


def pairAssessment(baseline, display, native):
    required = display["expected"]
    base = baseline["p95_execution_ms"]
    regression = (display["p95_execution_ms"] / base - 1) * 100 if base and display["p95_execution_ms"] else None
    complete = len(display["consumers"]) == 2 and all(
        c["unique_received"] == c["unique_decoded"] == c["unique_applied"] == required
        for c in display["consumers"])
    latency = len(display["consumers"]) == 2 and all(
        c["p95_model_ms"] is not None and c["p95_model_ms"] <= 200 for c in display["consumers"])
    ages = [value for row in display["age_samples_ms"] for value in row if value is not None]
    age = max(ages) if ages else None
    result = {"regression_percent": regression, "consumer_complete": complete,
              "p95_model_pass": latency, "max_sampled_age_ms": age}
    good = (baseline["job_status"] == display["job_status"] == "COMPLETED" and
            baseline["executed"] == display["executed"] == required and complete and latency and
            age is not None and age <= 500 and regression is not None and regression <= 5 and
            display["achieved_hz"] >= 4.95 and display["max_schedule_lateness_ms"] <= 200)
    if native:
        ui = display["ui"]
        uiComplete = len(ui) == 2 and all(c["unique_committed"] == c["unique_painted"] == required for c in ui)
        uiLatency = len(ui) == 2 and all(c["p95_scope_to_gui_ms"] is not None and c["p95_scope_to_gui_ms"] <= 200 for c in ui)
        uiAges = [value for row in display["ui_age_samples_ms"] for value in row if value is not None]
        uiAge = max(uiAges) if uiAges else None
        result.update(ui_complete=uiComplete, ui_latency_pass=uiLatency, max_sampled_ui_age_ms=uiAge,
                      native_platform=display["qt_platform"] not in {"offscreen", "minimal"})
        good = good and uiComplete and uiLatency and uiAge is not None and uiAge <= 500 and result["native_platform"]
    result["pass"] = bool(good)
    return result


def supervisedTrial(command, directory):
    """Bound wall time, stdout bytes and the complete process tree per arm."""
    from prototypes.runtime_pages_p0.watchdog import ProcessTree

    environment = dict(os.environ, PYTHONPATH=os.pathsep.join((str(ROOT / "src"), str(ROOT))),
                       HUARAY_CAMERA_SMOKE="0", PYTHONUNBUFFERED="1")
    gate = "import sys,json,subprocess; sys.stdin.readline(); sys.exit(subprocess.call(json.loads(sys.argv[1])))"
    started = time.monotonic()
    process = subprocess.Popen([sys.executable, "-c", gate, json.dumps(command)], cwd=ROOT,
        env=environment, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.PIPE,
        start_new_session=os.name != "nt")
    tree = None
    overflow = threading.Event()
    ioErrors = []
    logPath = directory / "process.log"

    def drain():
        size = 0
        try:
            with logPath.open("xb") as log:
                while True:
                    block = process.stdout.read1(65536)
                    if not block:
                        break
                    remaining = max(0, LOG_BYTES - size)
                    log.write(block[:remaining])
                    size += len(block)
                    if size > LOG_BYTES:
                        overflow.set()
        except BaseException as error:
            ioErrors.append(repr(error))
            overflow.set()

    reader = threading.Thread(target=drain, name="r3-bounded-log")
    timedOut = False
    try:
        tree = ProcessTree(process.pid)
        reader.start()
        process.stdin.write(b"GO\n")
        process.stdin.close()
        while process.poll() is None and not overflow.is_set():
            if time.monotonic() - started >= 90:
                timedOut = True
                break
            time.sleep(.05)
    finally:
        if tree:
            tree.close()
        if os.name != "nt":
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        if process.poll() is None:
            process.kill()
        process.wait(timeout=10)
        if reader.ident is not None:
            reader.join(timeout=10)
            if reader.is_alive():
                raise RuntimeError("log reader has not retired")
        process.stdout.close()
    cleanup = cleanupTrialDirectory(directory, process.returncode == 0 and
                                    not timedOut and not overflow.is_set())
    return {"command": command, "status": "FAIL" if timedOut or overflow.is_set() or process.returncode else "PASS",
            "exit_code": process.returncode, "timeout": timedOut, "watchdog_seconds": 90,
            "log_overflow": overflow.is_set(), "log_limit_bytes": LOG_BYTES,
            "log_bytes": logPath.stat().st_size, "log_errors": ioErrors,
            "duration_seconds": time.monotonic() - started, "log": logPath.name,
            "log_sha256": hashlib.sha256(logPath.read_bytes()).hexdigest(), **cleanup}


def cleanupTrialDirectory(directory, treeRetiredSuccessfully):
    marker = directory / "owners-closed.json"
    confirmed = treeRetiredSuccessfully and marker.exists() and json.loads(
        marker.read_text(encoding="utf-8")).get("all_original_trial_close_calls_returned") is True
    roots = list(directory.glob("work-*"))
    if confirmed:
        for root in roots:
            shutil.rmtree(root)
    return {"owner_retirement_verified": bool(confirmed),
            "preserved_workload_roots": [] if confirmed else [str(root) for root in roots]}


def diskPreflight(path):
    usage = shutil.disk_usage(path)
    result = {"path": str(path), "free_bytes": usage.free, "total_bytes": usage.total,
              "minimum_free_bytes": MIN_FREE_BYTES}
    if usage.free < MIN_FREE_BYTES:
        raise RuntimeError("insufficient free disk for bounded synthetic trial: " + json.dumps(result))
    return result


def writeManifest(root, payload):
    content = json.dumps(payload, indent=2)
    if len(content.encode()) > 16 * 1024 * 1024:
        raise RuntimeError("manifest budget exceeded")
    temporary = root / "evidence.partial.json"
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(root / "evidence.json")


def run(args):
    from p2_validate import identity, git

    before = identity()
    originals = {name: hashlib.sha256((ROOT / "scripts" / name).read_bytes()).hexdigest()
                 for name in ("p2_measure.py", "p3_measure.py")}
    manifest = {"started_ns": time.perf_counter_ns(), "head": git("rev-parse", "HEAD"),
        "dirty": git("status", "--short"), "source_before": before, "original_script_hashes": originals,
        "python": sys.version, "os": platform.platform(), "configuration": vars(args) | {"output": str(args.output)},
        "historical_results": "UNCHANGED; instrumented attribution is separate evidence",
        "clock": "same-host perf_counter_ns; nested stages inclusive, never sum overlapping stage totals",
        "instrumentation_cost": "bounded in-memory rows; exporter completed-stage batches in existing replies; final trace I/O after observation; wrapper overhead retained in both arms",
        "incomplete_stage_attempts": "killed/timed-out exporter stages cannot acknowledge timings; report export timeout/failure counters and trace role counts, never fabricate their durations",
        "load": "fixed seed 20260926; 1920x1080x3; scheduled 5 Hz; legacy snapshots ALL in every arm",
        "runtime_route": "original isolated presentation.prepare/start baseline; separate from normal StartJob user-path acceptance",
        "native_claim": "Qt paint events only; offscreen never passes native acceptance",
        "not_proven": ["actual user project", "field SLA", "five pages/fifty controls", "long-run steady state"],
        "disk_preflight": diskPreflight(args.output),
        "bounds": {"trace_rows_per_process": TRACE_LIMIT, "trace_file_bytes": TRACE_BYTES,
                   "raw_trial_bytes": 24 * 1024 * 1024, "log_bytes_per_trial": LOG_BYTES,
                   "maximum_trials": 12, "workload_temporary": True},
        "measurement_status": "RUNNING", "performance_status": "NOT_ASSESSED",
        "trials": [], "pairs": []}
    writeManifest(args.output, manifest)
    families = ("capture", "qt") if args.family == "both" else (args.family,)
    for family in families:
        for pair in range(args.pairs):
            pairRows = {}
            for enabled in ([False, True] if pair % 2 == 0 else [True, False]):
                directory = args.output / f"{family}-{pair}-{int(enabled)}"
                directory.mkdir()
                command = [sys.executable, str(Path(__file__).resolve()), "--child", "--output", str(directory),
                    "--family", family, "--enabled", str(int(enabled)), "--count", str(args.count),
                    "--warmup", str(args.warmup), "--qt-platform", args.qt_platform]
                disk = diskPreflight(directory)
                watch = supervisedTrial(command, directory)
                entry = {"family": family, "pair": pair, "enabled": enabled,
                         "watchdog": watch, "disk_preflight": disk}
                if (directory / "trial.json").exists():
                    payload = json.loads((directory / "trial.json").read_text(encoding="utf-8"))
                    traces = [json.loads(path.read_text(encoding="utf-8")) for path in sorted(directory.glob("trace-*.json"))]
                    ordinals = {event["result_key"]: event["ordinal"] for trace in traces for event in trace["rows"]
                                if event.get("result_key") and event.get("ordinal") is not None}
                    for trace in traces:
                        for event in trace["rows"]:
                            if event.get("ordinal") is None and event.get("result_key") in ordinals:
                                event["ordinal"] = ordinals[event["result_key"]]
                    entry["stage_summary"] = summaryStages(traces, args.warmup)
                    entry["trace_roles"] = dict(Counter(t["role"] for t in traces))
                    entry["trace_dropped_rows"] = sum(t["dropped_rows"] for t in traces)
                    entry["raw_evidence"] = str((directory / "trial.json").relative_to(args.output))
                    entry["trace_complete"] = entry["trace_roles"] == {"exporter": 2, "job": 1, "owner": 1} and not entry["trace_dropped_rows"] and not payload["record_retention"]["capacity_reached"]
                    pairRows[enabled] = payload
                manifest["trials"].append(entry)
                writeManifest(args.output, manifest)
                print(json.dumps({"trial": entry}), flush=True)
            assessment = pairAssessment(pairRows[False], pairRows[True], family == "qt") if len(pairRows) == 2 else {"pass": False, "reason": "missing trial; denominator retained"}
            manifest["pairs"].append({"family": family, "pair": pair, **assessment})
            writeManifest(args.output, manifest)
    manifest["source_after"] = identity()
    manifest["source_stable"] = before == manifest["source_after"]
    formal = args.count == 96 and args.warmup == 8 and args.pairs == 3
    valid = manifest["source_stable"] and all(t["watchdog"]["status"] == "PASS" and t.get("trace_complete") for t in manifest["trials"])
    manifest["measurement_status"] = "VALID" if valid else "INVALID"
    manifest["performance_status"] = ("PASS" if all(p["pass"] for p in manifest["pairs"]) else "FAIL") if formal and valid else "NOT_ASSESSED"
    manifest["finished_ns"] = time.perf_counter_ns()
    writeManifest(args.output, manifest)
    print(json.dumps({"measurement_status": manifest["measurement_status"],
        "performance_status": manifest["performance_status"], "pairs": manifest["pairs"]}), flush=True)
    return int(not valid or (formal and manifest["performance_status"] != "PASS"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--family", choices=("both", "capture", "qt"), default="both")
    parser.add_argument("--count", type=int, default=96)
    parser.add_argument("--warmup", type=int, default=8)
    parser.add_argument("--pairs", type=int, default=3)
    parser.add_argument("--qt-platform", default="windows" if os.name == "nt" else "offscreen")
    parser.add_argument("--child", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--enabled", type=int, choices=(0, 1), default=0, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if not 2 <= args.count <= 96 or not 0 <= args.warmup < args.count or not 1 <= args.pairs <= 3:
        parser.error("count 2..96, warmup 0..count-1, pairs 1..3; reduced runs are diagnostic only")
    args.output = args.output.resolve()
    if any(args.output.is_relative_to(ROOT / name) for name in
           ("src", "tests", "scripts", "proto", "examples", "prototypes")):
        parser.error("evidence must be outside source identity directories")
    if args.child:
        if args.family == "both":
            parser.error("child requires one family")
        executeTrial(args)
        return 0
    args.output.mkdir(parents=True, exist_ok=False)
    try:
        return run(args)
    except BaseException as error:
        (args.output / "failure.json").write_text(json.dumps({"measurement_status": "INVALID",
            "error": repr(error), "at_ns": time.perf_counter_ns()}), encoding="utf-8")
        raise


if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()
    raise SystemExit(main())
