"""Read-only native process counters for bounded offline R3 measurements."""
import os
from pathlib import Path
import threading


def resources(pid=None, *, includeThreadIds=False):
    """Sample counters, optionally including the native thread ID inventory."""
    pid = pid or os.getpid()
    if os.name == "nt":
        from scripts.p2_resources import resources as windowsResources
        result = windowsResources(pid)
        import ctypes
        from ctypes import wintypes

        class ThreadEntry(ctypes.Structure):
            _fields_ = [("size", wintypes.DWORD), ("usage", wintypes.DWORD),
                        ("thread", wintypes.DWORD), ("owner", wintypes.DWORD),
                        ("base", wintypes.LONG), ("delta", wintypes.LONG),
                        ("flags", wintypes.DWORD)]

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
        kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        kernel.Thread32First.argtypes = [wintypes.HANDLE, ctypes.POINTER(ThreadEntry)]
        kernel.Thread32Next.argtypes = [wintypes.HANDLE, ctypes.POINTER(ThreadEntry)]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        snapshot = kernel.CreateToolhelp32Snapshot(4, 0)
        if snapshot == ctypes.c_void_p(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            entry = ThreadEntry()
            entry.size = ctypes.sizeof(entry)
            count = 0
            if includeThreadIds:
                threadIds = []
            available = kernel.Thread32First(snapshot, ctypes.byref(entry))
            while available:
                count += int(entry.owner == pid)
                if includeThreadIds and entry.owner == pid:
                    threadIds.append(entry.thread)
                available = kernel.Thread32Next(snapshot, ctypes.byref(entry))
            if includeThreadIds:
                # Thread32First/Next report ERROR_NO_MORE_FILES at a normal end.
                error = ctypes.get_last_error()
                if error != 18:
                    raise ctypes.WinError(error)
            result["native_threads"] = count
        finally:
            kernel.CloseHandle(snapshot)
    elif Path("/proc").is_dir():
        root = Path("/proc") / str(pid)
        status = dict(line.split(":", 1) for line in (root / "status").read_text().splitlines() if ":" in line)
        fields = (root / "stat").read_text().rsplit(")", 1)[1].split()
        result = {"rss_bytes": int(status["VmRSS"].split()[0]) * 1024,
                  "peak_rss_bytes": int(status["VmHWM"].split()[0]) * 1024,
                  "handles": len(list((root / "fd").iterdir())),
                  "handle_kind": "file descriptors",
                  "native_threads": int(status["Threads"]),
                  "cpu_seconds": (int(fields[11]) + int(fields[12])) / os.sysconf("SC_CLK_TCK")}
        if includeThreadIds:
            threadIds = [int(task.name) for task in (root / "task").iterdir()]
    else:
        return {"status": "NOT_RUN", "reason": "native sampler supports Windows and Linux only", "pid": pid}
    result.update(pid=pid, status="OBSERVED")
    if includeThreadIds:
        result["native_thread_ids"] = sorted(threadIds)
        result["native_threads"] = len(threadIds)
    if pid == os.getpid():
        result["python_threads"] = threading.active_count()
    return result
