"""Read-only Windows start-module evidence, collected only after A18 fails.

The helper may query ONLY its immediate parent. The caller has already taken
the failing snapshot; no native calls, helper processes or files precede it.
At most 16 thread IDs and 1024 loaded modules are examined. No addresses,
paths, stack locals, payloads or exception messages leave the helper.

Native calls run in a disposable helper with a 5-second subprocess timeout.
Process creation/termination itself is not a hard wall-clock guarantee; the
diagnostic GitHub job retains its independent 30-minute guard. A timeout kills
the helper, not any observed thread. Normal completion closes every handle;
on helper termination Windows releases its handles.

ABI sources (the NT query is dynamically resolved and may be unavailable):
https://learn.microsoft.com/en-us/windows/win32/api/winternl/nf-winternl-ntqueryinformationthread
https://learn.microsoft.com/en-us/windows/win32/api/tlhelp32/ns-tlhelp32-moduleentry32w
https://learn.microsoft.com/en-us/windows/win32/api/tlhelp32/nf-tlhelp32-createtoolhelp32snapshot
https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-openthread
https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-getprocessidofthread
https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-getthreadtimes
"""
import ctypes
import json
import ntpath
import os
from pathlib import Path
import subprocess
import sys
import time


MAX_THREADS = 16
MAX_MODULES = 1024
MAX_BYTES = 32 * 1024
HELPER_SECONDS = 5
LIMITATIONS = (
    "Thread IDs can retire or be reused between the failed snapshot and query, "
    "including reuse inside the same process. Creation time is query-time evidence, "
    "not a snapshot-time identity proof. Modules can unload or change. A start "
    "module is not the thread creator, owner, call stack or cause of retention. "
    "Evidence is post-failure only; missing evidence does not change the assertion."
)

# Windows DWORD/LONG/WCHAR widths are fixed even when unit-tested on Linux.
DWORD = ctypes.c_uint32
HANDLE = ctypes.c_void_p
BOOL = ctypes.c_int32


class ModuleEntry(ctypes.Structure):
    _fields_ = [(name, DWORD) for name in (
        "dwSize", "th32ModuleID", "th32ProcessID", "GlblcntUsage", "ProccntUsage"
    )] + [("modBaseAddr", ctypes.c_void_p), ("modBaseSize", DWORD),
         ("hModule", HANDLE), ("szModule", ctypes.c_uint16 * 256),
         ("szExePath", ctypes.c_uint16 * 260)]


class FileTime(ctypes.Structure):
    _fields_ = [("low", DWORD), ("high", DWORD)]


def moduleBasename(value):
    """Bound the only string taken from a native module record."""
    return "".join(char if char.isprintable() else "?"
                   for char in ntpath.basename(value))[:255]


class WindowsQuery:
    def __init__(self):
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.nt = ctypes.WinDLL("ntdll", use_last_error=True)
        signatures = {
            "OpenThread": ([DWORD, BOOL, DWORD], HANDLE),
            "GetProcessIdOfThread": ([HANDLE], DWORD),
            "GetThreadTimes": ([HANDLE] + [ctypes.POINTER(FileTime)] * 4, BOOL),
            "CreateToolhelp32Snapshot": ([DWORD, DWORD], HANDLE),
            "Module32FirstW": ([HANDLE, ctypes.POINTER(ModuleEntry)], BOOL),
            "Module32NextW": ([HANDLE, ctypes.POINTER(ModuleEntry)], BOOL),
            "CloseHandle": ([HANDLE], BOOL),
        }
        for name, (arguments, result) in signatures.items():
            function = getattr(self.kernel, name)
            function.argtypes, function.restype = arguments, result
        self.nt.NtQueryInformationThread.argtypes = [
            HANDLE, ctypes.c_int32, ctypes.c_void_p, DWORD, ctypes.POINTER(DWORD)]
        self.nt.NtQueryInformationThread.restype = ctypes.c_int32

    def modules(self, pid):
        # Read-only snapshot of this one process, never a system-wide listing.
        snapshot = self.kernel.CreateToolhelp32Snapshot(0x08 | 0x10, pid)
        if snapshot == ctypes.c_void_p(-1).value:
            return [], "snapshot_unavailable"
        modules = []
        status = "complete"
        try:
            entry = ModuleEntry()
            entry.dwSize = ctypes.sizeof(entry)
            available = self.kernel.Module32FirstW(snapshot, ctypes.byref(entry))
            while available:
                if entry.th32ProcessID != pid:
                    modules.clear()
                    status = "module_owner_mismatch"
                    break
                name = bytes(entry.szModule).decode("utf-16-le", errors="replace").split("\0", 1)[0]
                modules.append((entry.modBaseAddr or 0, entry.modBaseSize, moduleBasename(name)))
                if len(modules) == MAX_MODULES:
                    status = "module_limit"
                    break
                available = self.kernel.Module32NextW(snapshot, ctypes.byref(entry))
            if not available and ctypes.get_last_error() != 18:  # ERROR_NO_MORE_FILES
                status = "enumeration_incomplete"
        finally:
            if not self.kernel.CloseHandle(snapshot):
                status = "snapshot_close_failed"
        return modules, status

    def thread(self, threadId, pid, modules):
        row = {"native_thread_id": threadId, "query_unix_ns": time.time_ns()}
        # THREAD_QUERY_INFORMATION only; no suspend, context, terminate or write.
        handle = self.kernel.OpenThread(0x0040, False, threadId)
        if not handle:
            return dict(row, status="thread_unavailable")
        try:
            # IDs may retire or be reused. Never query a thread in another PID.
            owner = self.kernel.GetProcessIdOfThread(handle)
            if owner != pid:
                row["status"] = "owner_query_failed" if not owner else "owner_mismatch"
                return row
            creation, exited, kernel, user = (FileTime() for _ in range(4))
            if self.kernel.GetThreadTimes(handle, *(ctypes.byref(value) for value in (
                    creation, exited, kernel, user))):
                ticks = (creation.high << 32) | creation.low
                row["creation_unix_ns"] = (ticks - 116444736000000000) * 100
            else:
                row["creation_time_status"] = "unavailable"
            address = ctypes.c_void_p()
            # ThreadQuerySetWin32StartAddress == 9; the buffer holds one PVOID.
            status = self.nt.NtQueryInformationThread(
                handle, 9, ctypes.byref(address), ctypes.sizeof(address), None)
            if status < 0:
                row.update(status="start_query_failed", ntstatus=status & 0xffffffff)
                return row
            row["status"] = "module_not_found"
            if address.value:
                for base, size, name in modules:
                    if base <= address.value < base + size and name:
                        row.update(status="observed", module=name, module_offset=address.value - base)
                        break
            return row
        finally:
            if not self.kernel.CloseHandle(handle):
                row["handle_close_status"] = "failed"


def queryParent(parentPid, threadIds):
    """Helper-only entry: reject arbitrary PIDs before loading any native API."""
    if (type(parentPid) is not int or parentPid <= 0 or parentPid != os.getppid()
            or parentPid == os.getpid()):
        raise ValueError("parent PID required")
    if (not isinstance(threadIds, list) or not 0 < len(threadIds) <= MAX_THREADS
            or any(type(value) is not int or not 0 < value <= 0xffffffff for value in threadIds)
            or len(set(threadIds)) != len(threadIds)):
        raise ValueError("bounded unique DWORD thread IDs required")
    if os.name != "nt":
        return {"status": "unsupported_platform", "threads": []}
    query = WindowsQuery()
    modules, moduleStatus = query.modules(parentPid)
    return {"status": "queried", "module_snapshot_status": moduleStatus,
            "threads": [query.thread(threadId, parentPid, modules) for threadId in threadIds]}


def collect(threadIds):
    """Query our own failed-snapshot IDs via a parent-validated helper."""
    ids = sorted(set(threadIds))[:MAX_THREADS]
    if not ids:
        return {"status": "no_new_native_threads", "threads": []}
    if os.name != "nt":
        return {"status": "unsupported_platform", "threads": []}
    try:
        result = subprocess.run(
            [sys.executable, "-I", "-S", str(Path(__file__).resolve()), str(os.getpid()),
             *map(str, ids)], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, timeout=HELPER_SECONDS, check=False)
        if result.returncode:
            return {"status": "helper_failed", "threads": []}
        if len(result.stdout) > MAX_BYTES:
            return {"status": "helper_output_limit", "threads": []}
        return json.loads(result.stdout)
    except subprocess.TimeoutExpired:
        return {"status": "helper_timeout", "threads": []}
    except Exception:
        return {"status": "helper_unavailable", "threads": []}


def main():
    try:
        record = queryParent(int(sys.argv[1]), [int(value) for value in sys.argv[2:]])
    except Exception:
        # Never emit an exception message, traceback, native address or path.
        record = {"status": "helper_unavailable", "threads": []}
    content = json.dumps(record, ensure_ascii=True)
    if len(content.encode("ascii")) > MAX_BYTES:
        content = '{"status":"helper_output_limit","threads":[]}'
    print(content)


if __name__ == "__main__":
    main()
