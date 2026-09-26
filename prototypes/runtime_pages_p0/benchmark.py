"""Development host measurements, never Qt visibility or IPC throughput claims."""
import ctypes
from ctypes import wintypes
import os
import time

import cv2
import numpy as np

from emo_master.apps.runtime.preview.store import PreviewSnapshotWriter
from emo_master.apps.runtime.workflow.runner import WorkflowRunner
from emo_master.apps.runtime.workflow.context import RunContext
from emo_master.apps.runtime.workflow.cancellation import CancellationToken
from emo_master.core.project.models import ProjectDocument
from emo_master.core.workflow.compiler import WorkflowCompiler
from emo_master.plugins.builtins.image_loader.operator import ImageLoaderOperator

from .contracts import Consumer
from .exporter import Exporters
from .runner_probe import Capture, Source, Mutator, image_project


def resources(pid=None):
    if os.name != "nt":
        return {"status": "NOT_RUN", "reason": "Windows resource sampler"}
    class Memory(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD)] + [
            (name, ctypes.c_size_t) for name in ["PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage",
            "QuotaPagedPoolUsage", "QuotaPeakNonPagedPoolUsage", "QuotaNonPagedPoolUsage", "PagefileUsage", "PeakPagefileUsage"]]
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.GetProcessHandleCount.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Memory), wintypes.DWORD]
    handle = kernel.OpenProcess(0x1000 | 0x10, False, pid or os.getpid())
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        memory = Memory()
        memory.cb = ctypes.sizeof(memory)
        assert psapi.GetProcessMemoryInfo(handle, ctypes.byref(memory), memory.cb)
        handles = wintypes.DWORD()
        assert kernel.GetProcessHandleCount(handle, ctypes.byref(handles))
        creation, exit_time, system, user = (wintypes.FILETIME() for _ in range(4))
        assert kernel.GetProcessTimes(handle, ctypes.byref(creation), ctypes.byref(exit_time), ctypes.byref(system), ctypes.byref(user))
        def ticks(value):
            return (value.dwHighDateTime << 32) + value.dwLowDateTime
        return {"rss_bytes": memory.WorkingSetSize, "peak_rss_bytes": memory.PeakWorkingSetSize,
                "handles": handles.value, "cpu_seconds": (ticks(system) + ticks(user)) / 1e7}
    finally:
        kernel.CloseHandle(handle)


def benchmark(root):
    image_path = root / "1080p.png"
    # Fixed high-entropy fixture also exercises encoding buffer/disk costs.
    image = np.random.default_rng(20260926).integers(0, 256, (1080, 1920, 3), np.uint8)
    assert cv2.imwrite(str(image_path), image)
    registry = {"vision.io.image_loader": ImageLoaderOperator, "p0.source": Source, "p0.mutate": Mutator}
    compiled = WorkflowCompiler(operatorRegistry=registry).compile(ProjectDocument.model_validate(image_project(image_path, False)))
    groups = []
    with Exporters(root / "exports") as exporter:
        for label, enabled, viewers in [("original", False, 0), ("new-no-presentation", False, 0),
                                        ("capture-zero-viewers", True, 0), ("one-consumer", True, 1),
                                        ("two-consumers", True, 2)]:
            directory = root / label
            directory.mkdir()
            old_preview = PreviewSnapshotWriter(directory)
            execution, display, read_decode = [], [], []
            incomplete = 0
            previous_scope_end = None
            max_live_age = 0
            raw_rows = []
            before = resources()
            started = time.perf_counter()
            for index in range(30):
                scheduled = started + index / 5
                remaining = scheduled - time.perf_counter()
                if remaining > 0:
                    time.sleep(remaining)
                capture = Capture(compiled, job="measurement") if enabled else None
                class Tee:
                    def capture(self, node, outputs, context):
                        old_preview.capture(node, outputs, context)
                        if capture:
                            capture.capture(node, outputs, context)
                runner = WorkflowRunner(compiled, registry,
                                        eventPublisher=capture.publish if capture else None,
                                        previewSnapshotStore=Tee())
                begin = time.perf_counter_ns()
                runner.run("main", {}, RunContext.root("measurement", "main"), CancellationToken())
                end = time.perf_counter_ns()
                execution.append((end-begin)/1e6)
                row = {"index": index, "execution_ms": execution[-1], "scheduled_lateness_ms": max(0, (begin/1e9-scheduled)*1000)}
                if capture:
                    snapshot = next(iter(capture.results.history.values()))
                    # Use actual frozen ImageLoader output, not a replacement test image.
                    shape, raw = dict(snapshot.values)["load.image"][1]
                    frozen_image = np.frombuffer(raw, np.uint8).reshape(shape)
                    exported = exporter.execute(frozen_image)
                    read_decode.append(exported.get("read_decode_ms"))
                    age = (time.perf_counter_ns()-snapshot.scope_end_ns)/1e6
                    valid = exported["status"] == "VALID" and snapshot.status == "COMMITTED"
                    incomplete += not valid
                    consumers = [Consumer(capture.session) for _ in range(viewers)]
                    for consumer in consumers:
                        consumer.receive(snapshot)
                    committed = time.perf_counter_ns()
                    if previous_scope_end is not None:
                        max_live_age = max(max_live_age, (committed - previous_scope_end) / 1e6)
                    previous_scope_end = snapshot.scope_end_ns
                    display.append(age)
                    row.update(scope_end_ns=snapshot.scope_end_ns, snapshot_commit_ns=snapshot.commit_ns,
                               consumer_ready_age_ms=age, status="COMPLETE" if valid else "INCOMPLETE")
                    capture.close()
                    assert capture.ledger.used == 0
                raw_rows.append(row)
            after = resources()
            def p95(values):
                return float(np.percentile(values, 95)) if values else None
            groups.append({"group": label, "generated": 30, "sealed": 30 if enabled else None,
                           "final_commits": 30 if enabled else None, "incomplete": incomplete,
                           "consumers": viewers, "complete_per_consumer": [30-incomplete]*viewers,
                           "execution_p95_ms": p95(execution), "consumer_ready_p95_ms": p95(display),
                           "max_ready_age_ms": max(display) if display else None,
                           "read_decode_p95_ms": p95([v for v in read_decode if v is not None]),
                           "achieved_hz": 30 / (time.perf_counter() - started),
                           "max_schedule_lateness_ms": max(row["scheduled_lateness_ms"] for row in raw_rows),
                           "max_live_age_ms": max_live_age if viewers else None,
                           "elapsed_seconds": time.perf_counter()-started, "before": before, "after": after,
                           "exporter_resources": [resources(slot[0].pid) for slot in exporter.slots],
                           "capture_drops": 0, "distribution_drops": 0, "ui_drops": "NOT_RUN",
                           "rows": raw_rows})
        peak = exporter.ledger.peak
    baseline = groups[0]["execution_p95_ms"]
    for group in groups:
        group["execution_regression_percent"] = (group["execution_p95_ms"] / baseline - 1) * 100
    return {"load": "1920x1080 uint8 BGR seeded noise, 5 Hz, 30 results/group; legacy preview retained",
            "clock": "perf_counter_ns; scope end before export wait; export children bracket-verified",
            "groups": groups, "export_peak_reserved_bytes": peak,
            "qt_visibility": "NOT_RUN", "frozen_windows": "NOT_RUN", "industrial_pc": "NOT_RUN",
            "scope": "in-process Runner + spawn encoder; consumers are model probes, no Qt/network images"}
