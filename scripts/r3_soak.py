"""Bounded, offline normal-Run/Qt resource observation; never field acceptance.

One synthetic 5 Hz Job, two readonly sessions and two shared Qt windows. Fixed
small input, no hardware. Run serially outside CI/performance contention. JSONL
samples stream to disk; counters and test metadata are bounded. A successful
exit proves only this observation window and explicit ownership invariants.
"""
import argparse
from collections import Counter
from copy import deepcopy
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import shutil
import sys
import tempfile
import threading
import time
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT)]

from examples.runtime_pages_p2 import pacedProject, pluginRoots  # noqa: E402
from emo_master.apps.runtime.grpc_server.service import RuntimeService  # noqa: E402
from emo_master.apps.runtime.grpc_server.aio_entry import AioRuntimeServer  # noqa: E402
from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb  # noqa: E402
from emo_master.apps.runtime.presentation.service import PresentationService  # noqa: E402
from emo_master.clients.runtime.display_session import DisplaySession  # noqa: E402
from emo_master.ui.presentation.hub import DisplayHub  # noqa: E402
from emo_master.ui.presentation.renderer import RuntimePages  # noqa: E402
from PySide2.QtCore import QCoreApplication, QEvent  # noqa: E402
from PySide2.QtWidgets import QApplication  # noqa: E402
from shiboken2 import isValid  # noqa: E402
from scripts.r3_resources import resources  # noqa: E402
from scripts.r3_sampling import BoundedSampler  # noqa: E402
from scripts.r3_job_diagnostics import JobDiagnostics, SqliteKeeper  # noqa: E402


class Observations:
    def __init__(self, expected):
        self.lock = threading.Lock()
        self.seen = bytearray(expected + 1)
        self.complete = bytearray(expected + 1)
        self.counts = Counter()
        self.reasons = Counter()

    def receive(self, result):
        with self.lock:
            ordinal = result.identity.resultOrdinal
            self.counts["notifications"] += 1
            if 0 < ordinal < len(self.seen):
                self.counts["duplicates"] += int(bool(self.seen[ordinal]))
                self.seen[ordinal] = 1
                self.complete[ordinal] = int(result.status == "COMPLETE")
            else:
                self.counts["outside_expected_ordinals"] += 1
            self.counts[result.status] += 1
            for source in result.sources:
                if source.state != "AVAILABLE":
                    self.reasons[source.reasonCode or "UNSPECIFIED"] += 1

    def snapshot(self):
        with self.lock:
            return {"expected": len(self.seen) - 1, "unique_received": sum(self.seen), "unique_complete": sum(self.complete),
                    "counts": dict(self.counts), "source_reasons": dict(self.reasons)}


class Evidence:
    def __init__(self, path):
        self.file = path.open("x", encoding="utf-8")
        self.bytes = 0

    def write(self, kind, value):
        row = json.dumps({"kind": kind, "monotonic_ns": time.monotonic_ns(), **value}, ensure_ascii=True) + "\n"
        self.bytes += len(row.encode("utf-8"))
        if self.bytes > 32 * 1024 * 1024:
            raise RuntimeError("measurement log budget exceeded (32 MiB)")
        self.file.write(row)
        self.file.flush()


class SoakSampler:
    """Bounded native history, streamed once, with explicit cache age on GUI ticks."""
    # One immediate request then at most one per second over the longest allowed
    # input period plus its existing 45-second startup/drain allowance.
    MAX_SAMPLES = 1800 + 45 + 1
    MAX_PROCESSES = 4  # owner, one Job, two existing exporter slots

    def __init__(self, sample=resources):
        self.sampler = BoundedSampler(sample, limit=self.MAX_SAMPLES)
        self.logged = 0
        self.requestedProcesses = set()

    def observe(self, processes, evidence):
        if len(processes) > self.MAX_PROCESSES:
            raise RuntimeError("native sampler process batch budget exceeded")
        requested = set(processes.items())
        if len(self.requestedProcesses | requested) > self.MAX_SAMPLES * self.MAX_PROCESSES:
            raise RuntimeError("native sampler process identity budget exceeded")
        self.requestedProcesses.update(requested)
        stamp = time.perf_counter_ns()
        admitted = self.sampler.request(processes, stamp)
        rows = self.flush(evidence)
        observed = time.perf_counter_ns()
        values = {}
        for label, pid in processes.items():
            row = next((row for row in reversed(rows)
                        if row["processes"].get(label, {}).get("pid") == pid), None)
            if row is None:
                values[label] = {"pid": pid, "status": "SAMPLE_PENDING", "observed_ns": observed,
                                 "sample_age_ms": None, "sample_end_ns": None}
                continue
            boundary = row["per_process_timestamps"][label]
            values[label] = {**row["processes"][label], "observed_ns": observed,
                "sample_requested_ns": row["requested_ns"], "sample_start_ns": boundary["start_ns"],
                "sample_end_ns": boundary["end_ns"], "sample_age_ms": (observed-boundary["end_ns"])/1e6,
                "sample_batch_end_ns": row["end_ns"], "sample_batch_ms": row["elapsed_ms"],
                "cached": True}
        return values, {"requested_ns": stamp, "request_admitted": admitted,
                        "completed_samples": len(rows), "observed_ns": observed}

    def flush(self, evidence):
        rows = self.sampler.snapshot()
        for row in rows[self.logged:]:
            evidence.write("resource_sample", row)
            self.logged += 1
        return rows

    def close(self):
        self.sampler.close()

    def report(self):
        report = self.sampler.report()
        rows = report.pop("samples")
        observed = {(label, value["pid"]) for row in rows for label, value in row["processes"].items()
                    if value.get("status") == "OBSERVED"}
        missing = self.requestedProcesses - observed
        return {**report, "completed_samples": len(rows), "logged_samples": self.logged,
            "requested_processes": [{"role": label, "pid": pid} for label, pid in sorted(self.requestedProcesses)],
            "unobserved_processes": [{"role": label, "pid": pid} for label, pid in sorted(missing)],
            "resource_coverage_complete": bool(self.requestedProcesses) and not missing,
            "semantics": "Native reads run on one background thread. Sample rows contain timestamped cached reads; pending is not zero. Each native batch is logged once as resource_sample."}


def postCleanupResources(sampler):
    """A real native read after the measurement thread has also relinquished ownership."""
    report = sampler.report()
    if not report["retired"]:
        raise RuntimeError("resource sampler must retire before postcleanup resource claims")
    start = time.perf_counter_ns()
    value = resources()
    end = time.perf_counter_ns()
    return {**value, "sample_start_ns": start, "sample_end_ns": end,
            "sample_age_ms": 0, "cached": False, "sampler_retired": True}


class ObservedPages(RuntimePages):
    """Count commits/native image paint observations with bounded ordinal bitmaps."""
    def __init__(self, presentation, expected, **kwargs):
        self.commits = Observations(expected)
        self.paints = Observations(expected)
        super().__init__(presentation, **kwargs)

    def submit(self, view):
        super().submit(view)
        for scope in self.displayed.values():
            self.commits.receive(scope.result)

    def _painted(self, key, stamp):
        super()._painted(key, stamp)
        for scope in self.displayed.values():
            if scope.result.identity.resultKey == key:
                self.paints.receive(scope.result)

    def observations(self):
        return {"commits": self.commits.snapshot(), "native_image_paints": self.paints.snapshot()}


def sourceIdentity():
    digest = hashlib.sha256()
    for parent in (ROOT / "src", ROOT / "examples", ROOT / "scripts"):
        for path in sorted(parent.rglob("*.py")):
            digest.update(path.relative_to(ROOT).as_posix().encode())
            digest.update(path.read_bytes())
    return {"head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
            "dirty": subprocess.check_output(["git", "status", "--short"], cwd=ROOT, text=True),
            "python_sources_sha256": digest.hexdigest()}


def pumpUntil(predicate, seconds):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        QApplication.processEvents()
        if predicate():
            return
        time.sleep(.005)
    raise TimeoutError("bounded observation deadline")


def disposeGuiHub(hub):
    """Retire the script-owned QObject and timer, not just their Python refs."""
    if hub is None or not isValid(hub):
        return
    from emo_master.ui.presentation.images import assertGuiThread
    assertGuiThread()
    assert not hub.windows and not hub.timer.isActive(), "observer windows must detach first"
    timer = hub.timer
    hub.deleteLater()
    QCoreApplication.sendPostedEvents(hub, QEvent.DeferredDelete)
    assert not isValid(hub) and not isValid(timer), "native observer owner did not retire"


def retireOwned(windows, clients, runtime, server, job, hub=None, sampler=None, diagnostics=None, keeper=None):
    """Try every independent owner, without pretending any failed close worked."""
    errors = []

    def attempt(label, operation):
        try:
            operation()
        except Exception as error:
            errors.append({"owner": label, "error": repr(error)})

    for index, window in enumerate(windows):
        attempt(f"window-{index}", window.close)
    for index, client in enumerate(clients):
        attempt(f"client-{index}", client.close)
    if hub is not None:
        attempt("gui-hub", lambda: disposeGuiHub(hub))
    if runtime and job:
        attempt("job-stop", lambda: runtime.StopJob(pb.StopJobRequest(job_id=job, mode="force"), None))
    if server:
        attempt("server", server.close)
    if runtime:
        attempt("runtime", runtime.close)
    if keeper is not None:
        attempt("sqlite-keeper", keeper.close)
    if diagnostics is not None:
        diagnostics.capture("after_cleanup")
        attempt("job-diagnostics", diagnostics.close)
    if sampler is not None:
        attempt("resource-sampler", sampler.close)
    return errors


def run(args, evidence):
    before = sourceIdentity()
    expected = math.ceil(args.seconds * 5)
    diagnosticEnabled = bool(getattr(args, "job_diagnostics", False))
    keeperEnabled = bool(getattr(args, "sqlite_keeper", False))
    evidence.write("configuration", {"source": before, "seconds": args.seconds,
        "expected_inputs": expected, "scheduled_hz": 5, "sample_seconds": args.sample_seconds,
        "synthetic": True, "input_shape": [120, 160, 3], "legacy_snapshots": "all (compatibility default)",
        "windows": 2, "sessions": 2, "image_sources": 1, "result_scopes": 1,
        "safety": "temporary data; fixed offline image; no device operators",
        "owner_before_runtime": resources(),
        "sampler_mode": "background", "resource_sample_capacity": SoakSampler.MAX_SAMPLES,
        "sampling_clock": "perf_counter_ns; per-process native start/end and cache age",
        "sampler_note": "Separate observer-effect run; prior synchronous evidence remains unchanged",
        "job_diagnostics": diagnosticEnabled,
        "sqlite_connection_arm": "diagnostic_idle_keeper" if keeperEnabled else "unchanged",
        "not_proven": ["actual project", "field SLA", "unlimited steady state", "real devices", "frozen package"]})
    clients, windows = [], []
    runtime = display = server = hub = sampler = None
    diagnostics = keeper = None
    job = ""
    root = Path(tempfile.mkdtemp(prefix="emo-r3-soak-"))
    try:
        if diagnosticEnabled:
            diagnosticPath = args.output.with_name(args.output.name + ".job-diagnostics.json")
            diagnostics = JobDiagnostics(diagnosticPath, source=before, plannedSeconds=args.seconds)
            evidence.write("diagnostic_configuration", {"path": str(diagnosticPath),
                "maximum_bytes": diagnostics.MAX_BYTES, "maximum_snapshots": diagnostics.MAX_SNAPSHOTS,
                "observer_effect": "Explicit instrumented run; default workflow, thresholds and deadlines unchanged"})
        sampler = SoakSampler()
        document = pacedProject(root, count=expected)
        document.workflows["detect"].nodes[1].params["imagePath"] = str(root / "input.png")
        second = deepcopy(document.presentation.pages["main"])
        second.name = "Second observer page"
        for component in second.components:
            component.componentId += "-second"
        document.presentation.pages["second"] = second
        document.presentation.pageOrder.append("second")
        (root / "project.json").write_text(document.model_dump_json(), encoding="utf-8")
        runtime = RuntimeService(dbPath=root / "runtime.sqlite3", workspaceRoot=root / "jobs",
                                 pluginRootPaths=pluginRoots(root))
        display = PresentationService(runtime, root / "display")
        if diagnostics is not None:
            diagnostics.install(runtime)
        if keeperEnabled:
            keeper = SqliteKeeper(runtime.sqliteStore)
            evidence.write("sqlite_keeper", keeper.report())
        server = AioRuntimeServer(runtime, display)
        assert runtime.LoadProject(pb.LoadProjectRequest(project_path=str(root)), None).ok
        reply = runtime.StartJob(pb.StartJobRequest(project_id=document.project.projectId,
            capture_presentation=True, start_request_id=uuid4().hex,
            expected_runtime_instance_id=runtime.runtimeInstanceId), None)
        assert reply.ok, reply.message
        job = reply.job_id
        observations = [Observations(expected), Observations(expected)]
        for observation in observations:
            client = DisplaySession(f"127.0.0.1:{server.port}", job)
            client.observe(observation.receive)
            clients.append(client)
        hub = DisplayHub(clients[0])
        for index in range(2):
            window = ObservedPages(document.presentation, expected, hub=hub, label="离线稳定性观测 · 合成输入")
            window.resize(640, 480)
            window.show()
            if index:
                window.navigate("second")
            windows.append(window)
        start = time.monotonic()
        nextSample = start
        while not runtime.jobRepository.get(job).isTerminal:
            QApplication.processEvents()
            now = time.monotonic()
            if now - start > args.seconds + 45:
                if diagnostics is not None:
                    diagnostics.capture("observation_timeout")
                raise TimeoutError("Job exceeded planned duration plus bounded startup/drain allowance")
            if now >= nextSample:
                nextSample = now + args.sample_seconds
                processIds = {"owner": os.getpid()}
                record = runtime.jobRepository.get(job)
                for label, pid in [("job", record.pid), *[(f"exporter-{slot['index']}", slot["process"].pid)
                                  for slot in display.exporter.slots if slot["process"] is not None]]:
                    if pid:
                        processIds[label] = pid
                processes, sampling = sampler.observe(processIds, evidence)
                stats = display.resourceStats()
                assert stats["total_reserved"] <= stats["limit"]
                assert stats["open_results"] <= 8 and stats["history_results"] <= 32
                evidence.write("sample", {"elapsed_seconds": now - start, "processes": processes, "sampling": sampling,
                    "display": stats, "qt": hub.stats(), "bulk_admission": dict(server.active),
                    "clients": [dict(c.stats) for c in clients],
                    "window_observations": [w.observations() for w in windows],
                    "observations": [o.snapshot() for o in observations]})
            time.sleep(.005)
        # Drain does not reset the export/read deadline or substitute for the input period.
        end = time.monotonic() + 1
        while time.monotonic() < end:
            QApplication.processEvents()
            time.sleep(.005)
        evidence.write("terminal", {"job_status": runtime.jobRepository.get(job).status,
            "elapsed_seconds": time.monotonic() - start, "observations": [o.snapshot() for o in observations],
            "client_stats": [dict(c.stats) for c in clients], "qt": hub.stats(),
            "window_observations": [w.observations() for w in windows],
            "supervisor_errors": list(runtime.jobSupervisor.presentationErrors),
            "export_errors": list(display.exporter.errors)})
        assert runtime.jobRepository.get(job).status == "COMPLETED"
        for observation in observations:
            row = observation.snapshot()
            assert row["unique_received"] == row["unique_complete"] == expected, row
        assert all(c.stats["read_failed"] == c.stats["dropped"] == 0 for c in clients)
        for window in windows:
            window.close()
            window.deleteLater()
        windows.clear()
        for client in clients:
            client.close()
        clients.clear()
        disposeGuiHub(hub)
        hub = None
        pumpUntil(lambda: not runtime.jobSupervisor.ownsJobResources(job), 10)
        # Resource retirement, not a terminal label alone, permits release.
        deadline = time.monotonic() + 5
        while True:
            try:
                display.release(job)
                break
            except ValueError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(.02)
        assert not display.jobs and not display.pending and not display.readers
        server.close()
        server = None
        runtime.close()
        runtime = None
        display = None
        if keeper is not None:
            keeper.close()
            evidence.write("sqlite_keeper", keeper.report())
        if diagnostics is not None:
            diagnostics.capture("owners_closed")
            diagnostics.close()
        QApplication.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        sampler.close()
        sampler.flush(evidence)
        sampling = sampler.report()
        evidence.write("cleanup", {"owner": postCleanupResources(sampler),
            "all_job_display_owners_retired": True, "resource_sampler": sampling})
        assert sampling["resource_coverage_complete"], sampling
        assert sampling["retired"] and not sampling["overflow"] and not sampling["errors"], sampling
    except BaseException as error:
        if diagnostics is not None:
            diagnostics.capture("before_cleanup:" + type(error).__name__)
        raise
    finally:
        errors = retireOwned(windows, clients, runtime, server, job, hub, sampler, diagnostics, keeper)
        if keeper is not None:
            evidence.write("sqlite_keeper_final", keeper.report())
        if errors:
            evidence.write("cleanup_failure", {"errors": errors, "preserved_temporary_root": str(root)})
            raise RuntimeError(f"measurement owners did not all close: {errors}")
        if sampler is not None:
            sampler.flush(evidence)
            evidence.write("resource_sampler", sampler.report())
        # Delete only after successful real owner shutdown. Failed shutdown keeps evidence/data.
        shutil.rmtree(root)
    after = sourceIdentity()
    assert before == after, "source changed during measurement"
    evidence.write("summary", {"observation_status": "PASS", "source_stable": True,
        "duration_seconds": args.seconds, "performance_status": "NOT_ASSESSED",
        "steady_state_claim": "bounded window only; inspect raw process trends, not a universal RSS proof"})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=int, default=900)
    parser.add_argument("--sample-seconds", type=float, default=5)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--job-diagnostics", action="store_true", help="opt-in bounded Job stages/frontiers/stacks")
    parser.add_argument("--sqlite-keeper", action="store_true", help="diagnostic idle SQLite connection; requires --job-diagnostics")
    args = parser.parse_args()
    if not 5 <= args.seconds <= 1800 or not 1 <= args.sample_seconds <= 30:
        parser.error("seconds must be 5..1800; sampling interval 1..30")
    if args.sqlite_keeper and not args.job_diagnostics:
        parser.error("--sqlite-keeper requires --job-diagnostics; this is an explicit diagnostic arm")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    evidence = Evidence(args.output)
    try:
        run(args, evidence)
    except Exception as error:
        evidence.write("failure", {"observation_status": "FAIL", "error": repr(error)})
        raise
    finally:
        evidence.file.close()


if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()
    main()
