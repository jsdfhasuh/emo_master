"""Development host measurements, never Qt visibility or IPC throughput claims."""
import ctypes
from ctypes import wintypes
import os
import hashlib
import json

import cv2
import numpy as np

from emo_master.apps.runtime.grpc_server.service import RuntimeService


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


def benchmark(root, count=96, rounds=3, warmup=8):
    from .continuous import window
    image_path = root / "1080p.png"
    image = np.random.default_rng(20260926).integers(0, 256, (1080, 1920, 3), np.uint8)
    assert cv2.imwrite(str(image_path), image)
    digest = hashlib.sha256(image).hexdigest()
    definitions = [("original", False, 0), ("new-no-presentation", False, 0),
                   ("capture-zero-viewers", True, 0), ("one-consumer", True, 1), ("two-consumers", True, 2)]
    groups = []
    service = RuntimeService(dbPath=root / "runtime.db", workspaceRoot=root / "jobs")
    try:
        for repetition in range(rounds):
            offset = repetition * 2 % len(definitions)
            order = definitions[offset:] + definitions[:offset]
            current = []
            for position, (label, enabled, viewers) in enumerate(order):
                measured = window(root / f"round-{repetition}-{label}", service, image_path, digest,
                                  count=count, viewers=viewers, enabled=enabled, warmup=warmup)
                measured.update(group=label, round=repetition+1, position=position+1)
                current.append(measured)
                # Progress/raw windows survive a later outer watchdog failure.
                print("P0_WINDOW " + json.dumps(measured), flush=True)
            baseline = next(g for g in current if g["group"] == "original")["execution_p95_ms"]
            for group in current:
                regression = (group["execution_p95_ms"]/baseline-1)*100
                group["execution_regression_percent"] = regression
                group["performance_status"] = "PASS" if (
                    regression <= 5 and group["achieved_hz"] >= 4.95
                    and group["max_schedule_lateness_ms"] <= 200
                    and (not group["enabled"] or (group["correctness_status"] == "PASS"
                         and group["model_p95_ms"] is not None and group["model_p95_ms"] <= 200))
                    and all(age <= 500 for age in group["max_live_age_ms"])) else "FAIL"
            groups.extend(current)
    finally:
        service.close()
    return dict(load="1920x1080 uint8 BGR seed=20260926 PNG, absolute 5Hz, legacy preview retained",
                clock="perf_counter_ns, real Runner scopeEnd to actual per-client decode/model commit",
                count=count, warmup=warmup, rounds=rounds, groups=groups,
                correctness_status="PASS" if all(g["correctness_status"] == "PASS" for g in groups) else "FAIL",
                performance_status="PASS" if all(g["performance_status"] == "PASS" for g in groups) else "FAIL",
                qt_visibility="NOT_RUN", industrial_pc="NOT_RUN", frozen_windows="NOT_RUN",
                scope="one real in-process WorkflowRunner, two real spawn exporters, persistent loopback network clients")
