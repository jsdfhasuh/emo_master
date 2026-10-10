"""Windows process metrics for measurement scripts only."""
import ctypes
from ctypes import wintypes
import os


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
