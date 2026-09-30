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
from scripts.r3_resources import resources  # noqa: E402


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


def retireOwned(windows, clients, runtime, server, job):
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
    if runtime and job:
        attempt("job-stop", lambda: runtime.StopJob(pb.StopJobRequest(job_id=job, mode="force"), None))
    if server:
        attempt("server", server.close)
    if runtime:
        attempt("runtime", runtime.close)
    return errors


def run(args, evidence):
    before = sourceIdentity()
    expected = math.ceil(args.seconds * 5)
    evidence.write("configuration", {"source": before, "seconds": args.seconds,
        "expected_inputs": expected, "scheduled_hz": 5, "sample_seconds": args.sample_seconds,
        "synthetic": True, "input_shape": [120, 160, 3], "legacy_snapshots": "all (compatibility default)",
        "windows": 2, "sessions": 2, "image_sources": 1, "result_scopes": 1,
        "safety": "temporary data; fixed offline image; no device operators",
        "owner_before_runtime": resources(),
        "not_proven": ["actual project", "field SLA", "unlimited steady state", "real devices", "frozen package"]})
    clients, windows = [], []
    runtime = display = server = hub = None
    job = ""
    root = Path(tempfile.mkdtemp(prefix="emo-r3-soak-"))
    try:
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
                raise TimeoutError("Job exceeded planned duration plus bounded startup/drain allowance")
            if now >= nextSample:
                nextSample = now + args.sample_seconds
                processes = {"owner": resources()}
                record = runtime.jobRepository.get(job)
                for label, pid in [("job", record.pid), *[(f"exporter-{slot['index']}", slot["process"].pid)
                                  for slot in display.exporter.slots if slot["process"] is not None]]:
                    if pid:
                        try:
                            processes[label] = resources(pid)
                        except (OSError, KeyError):
                            processes[label] = {"pid": pid, "status": "EXITED_DURING_SAMPLE"}
                stats = display.resourceStats()
                assert stats["total_reserved"] <= stats["limit"]
                assert stats["open_results"] <= 8 and stats["history_results"] <= 32
                evidence.write("sample", {"elapsed_seconds": now - start, "processes": processes,
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
        QApplication.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        evidence.write("cleanup", {"owner": resources(), "all_job_display_owners_retired": True})
    finally:
        errors = retireOwned(windows, clients, runtime, server, job)
        if errors:
            evidence.write("cleanup_failure", {"errors": errors, "preserved_temporary_root": str(root)})
            raise RuntimeError(f"measurement owners did not all close: {errors}")
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
    args = parser.parse_args()
    if not 5 <= args.seconds <= 1800 or not 1 <= args.sample_seconds <= 30:
        parser.error("seconds must be 5..1800; sampling interval 1..30")
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
