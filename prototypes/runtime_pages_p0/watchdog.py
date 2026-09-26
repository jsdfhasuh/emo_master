"""External deadline, including final process-tree cleanup on Windows."""
import os
import json
from pathlib import Path
import signal
import subprocess
import sys


class ProcessTree:
    """Windows kill-on-close Job Object survives an inner supervisor crash."""
    def __init__(self, pid):
        import ctypes
        from ctypes import wintypes as w
        self.handle = None
        if os.name != "nt":
            return
        class Basic(ctypes.Structure):
            _fields_ = [("process_time", ctypes.c_int64), ("job_time", ctypes.c_int64),
                        ("flags", w.DWORD), ("min_ws", ctypes.c_size_t), ("max_ws", ctypes.c_size_t),
                        ("active", w.DWORD), ("affinity", ctypes.c_size_t), ("priority", w.DWORD), ("scheduling", w.DWORD)]
        class Io(ctypes.Structure):
            _fields_ = [(name, ctypes.c_uint64) for name in ("read_ops", "write_ops", "other_ops", "read", "write", "other")]
        class Extended(ctypes.Structure):
            _fields_ = [("basic", Basic), ("io", Io)] + [(name, ctypes.c_size_t) for name in
                       ("process_memory", "job_memory", "peak_process", "peak_job")]
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, w.LPCWSTR]
        self.kernel.CreateJobObjectW.restype = w.HANDLE
        self.kernel.SetInformationJobObject.argtypes = [w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD]
        self.kernel.AssignProcessToJobObject.argtypes = [w.HANDLE, w.HANDLE]
        self.kernel.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
        self.kernel.OpenProcess.restype = w.HANDLE
        self.kernel.CloseHandle.argtypes = [w.HANDLE]
        self.handle = self.kernel.CreateJobObjectW(None, None)
        info = Extended()
        info.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        process = self.kernel.OpenProcess(0x0100 | 0x0001, False, pid)
        try:
            if not self.handle or not process:
                raise ctypes.WinError(ctypes.get_last_error())
            if not self.kernel.SetInformationJobObject(self.handle, 9, ctypes.byref(info), ctypes.sizeof(info)):
                raise ctypes.WinError(ctypes.get_last_error())
            if not self.kernel.AssignProcessToJobObject(self.handle, process):
                raise ctypes.WinError(ctypes.get_last_error())
        except BaseException:
            self.close()
            raise
        finally:
            if process:
                self.kernel.CloseHandle(process)

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None


def supervised(name, timeout=90, parameters=None):
    root = Path(__file__).resolve().parents[2]
    env = dict(os.environ, PYTHONPATH=os.pathsep.join((str(root), str(root / "src"))),
               HUARAY_CAMERA_SMOKE="0", QT_QPA_PLATFORM="offscreen")
    process = subprocess.Popen([sys.executable, "-m", "prototypes.runtime_pages_p0.scenarios", name, json.dumps(parameters or {})],
                               cwd=root, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               stdin=subprocess.PIPE, text=True, start_new_session=os.name != "nt")
    tree = None
    try:
        tree = ProcessTree(process.pid)
        # Child waits for this line, so no descendant can escape assignment.
        stdout, stderr = process.communicate(input="GO\n", timeout=timeout)
    except BaseException as error:
        if tree:
            tree.close()
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                           capture_output=True, timeout=10)
        else:
            os.killpg(process.pid, signal.SIGKILL)
        process.kill()
        stdout, stderr = process.communicate(timeout=10)
        if isinstance(error, subprocess.TimeoutExpired):
            raise AssertionError(f"{name}: outer watchdog {timeout}s\n{stdout}\n{stderr}") from error
        raise
    finally:
        if tree:
            tree.close()
    if process.returncode:
        raise AssertionError(f"{name}: exit={process.returncode}\n{stdout}\n{stderr}")
    return stdout, stderr
