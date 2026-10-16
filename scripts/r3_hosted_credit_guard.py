"""Current-process Windows guard for one fixed synthetic credit control run.

The private outer Job Object applies a 4 GiB aggregate committed-memory limit
to this controller and inherited descendants. It deliberately has no
KILL_ON_JOB_CLOSE: this controller belongs to it. The unchanged per-trial
ProcessTree retains its separate kill-on-close lifetime and 90-second guards.
No arbitrary PID is opened or modified. The current process alone is set to
BelowNormal and read back before importing/running the control.
"""
import argparse
import ctypes
import hashlib
import json
import os
from pathlib import Path
import runpy
import sys
import traceback


MEMORY_BYTES = 4 * 1024 ** 3
JOB_MEMORY = 0x200
BELOW_NORMAL = 0x4000
EXTENDED_LIMIT_INFORMATION = 9
SOURCE_FILES = ("scripts/r3_hosted_credit_guard.py", "scripts/r3_export_credit_report.py",
                ".github/workflows/runtime-credit-diagnostics.yml")
DWORD = ctypes.c_uint32
BOOL = ctypes.c_int32
HANDLE = ctypes.c_void_p
SIZE_T = ctypes.c_size_t


class BasicLimits(ctypes.Structure):
    _fields_ = [("process_time", ctypes.c_int64), ("job_time", ctypes.c_int64),
                ("flags", DWORD), ("min_ws", SIZE_T), ("max_ws", SIZE_T),
                ("active", DWORD), ("affinity", SIZE_T), ("priority", DWORD), ("scheduling", DWORD)]


class IoCounters(ctypes.Structure):
    _fields_ = [(name, ctypes.c_uint64) for name in
                ("read_ops", "write_ops", "other_ops", "read_bytes", "write_bytes", "other_bytes")]


class ExtendedLimits(ctypes.Structure):
    _fields_ = [("basic", BasicLimits), ("io", IoCounters), ("process_memory", SIZE_T),
                ("job_memory", SIZE_T), ("peak_process_memory", SIZE_T), ("peak_job_memory", SIZE_T)]


class GuardError(RuntimeError):
    pass


class NativeCallError(GuardError):
    def __init__(self, call, winerror):
        self.call, self.winerror = call, winerror
        super().__init__(call + "_failed")


def error_record(error):
    if isinstance(error, NativeCallError):
        return {"call": error.call, "winerror": error.winerror}
    return str(error) if isinstance(error, GuardError) else type(error).__name__


def control_stack(error):
    # Names and line numbers only: no exception text, local values, source
    # lines or full paths. Explicit overflow preserves the bounded contract.
    frames = traceback.extract_tb(error.__traceback__)
    def name(value):
        return value if len(value) <= 160 else "NAME_EXCEEDS_160_CHARS"
    return {"omitted_outer_frames": max(0, len(frames) - 32),
            "frames": [{"file": name(Path(frame.filename).name), "function": name(frame.name), "line": frame.lineno}
                       for frame in frames[-32:]]}


class NativeApi:
    def __init__(self, kernel=None, last_error=None):
        if kernel is None:
            if os.name != "nt" or ctypes.sizeof(SIZE_T) != 8:
                raise GuardError("requires_64_bit_windows")
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel = kernel
        self.last_error = last_error if last_error is not None else getattr(ctypes, "get_last_error", lambda: 0)
        signatures = {
            "CreateJobObjectW": ([ctypes.c_void_p, ctypes.c_wchar_p], HANDLE),
            "SetInformationJobObject": ([HANDLE, ctypes.c_int32, ctypes.c_void_p, DWORD], BOOL),
            "QueryInformationJobObject": ([HANDLE, ctypes.c_int32, ctypes.c_void_p, DWORD, ctypes.POINTER(DWORD)], BOOL),
            "GetCurrentProcess": ([], HANDLE),
            "AssignProcessToJobObject": ([HANDLE, HANDLE], BOOL),
            "IsProcessInJob": ([HANDLE, HANDLE, ctypes.POINTER(BOOL)], BOOL),
            "SetPriorityClass": ([HANDLE, DWORD], BOOL),
            "GetPriorityClass": ([HANDLE], DWORD),
            "CloseHandle": ([HANDLE], BOOL),
        }
        for name, (arguments, result) in signatures.items():
            function = getattr(kernel, name)
            function.argtypes, function.restype = arguments, result

    def call(self, name, *arguments):
        result = getattr(self.kernel, name)(*arguments)
        if not result:
            raise NativeCallError(name, int(self.last_error()))
        return result


class Guard:
    def __init__(self, api):
        self.api, self.handle = api, None
        self.record = {"schema_version": 1, "role": "hosted_credit_guard", "status": "INVALID",
                       "configured_job_memory_bytes": MEMORY_BYTES, "configured_limit_flags": JOB_MEMORY,
                       "configured_priority_class": BELOW_NORMAL, "priority_name": "BELOW_NORMAL_PRIORITY_CLASS",
                       "current_process_only": True, "kill_on_job_close": False,
                       "assignment_verified": False, "setup_verified": False, "handle_closed": False,
                       "control_started": False, "original_control_exit_code": None,
                       "errors": [], "memory_limit_violation_status": "NOT_ASSESSED",
                       "interpretation": "Aggregate committed-memory ceiling for this controller and inherited descendants. Peak memory alone does not prove that no limit violation occurred. Priority is verified for the current controller only; child priority is not inferred. Original per-trial ProcessTree kill-on-close and 90-second deadlines remain authoritative for retirement."}

    def query(self):
        value, returned = ExtendedLimits(), DWORD()
        self.api.call("QueryInformationJobObject", self.handle, EXTENDED_LIMIT_INFORMATION,
                      ctypes.byref(value), ctypes.sizeof(value), ctypes.byref(returned))
        if returned.value != ctypes.sizeof(value):
            raise GuardError("job_query_size_mismatch")
        return value

    def setup(self):
        self.handle = self.api.call("CreateJobObjectW", None, None)
        value = ExtendedLimits()
        value.basic.flags, value.job_memory = JOB_MEMORY, MEMORY_BYTES
        self.api.call("SetInformationJobObject", self.handle, EXTENDED_LIMIT_INFORMATION,
                      ctypes.byref(value), ctypes.sizeof(value))
        current = self.api.call("GetCurrentProcess")
        self.api.call("AssignProcessToJobObject", self.handle, current)
        assigned = BOOL()
        self.api.call("IsProcessInJob", current, self.handle, ctypes.byref(assigned))
        self.record["assignment_verified"] = bool(assigned.value)
        if not assigned.value:
            raise GuardError("job_assignment_not_verified")
        readback = self.query()
        self.record.update(readback_limit_flags=readback.basic.flags,
                           readback_job_memory_bytes=readback.job_memory)
        if readback.basic.flags != JOB_MEMORY or readback.job_memory != MEMORY_BYTES:
            raise GuardError("job_limit_readback_mismatch")
        self.api.call("SetPriorityClass", current, BELOW_NORMAL)
        priority = self.api.call("GetPriorityClass", current)
        self.record["readback_priority_class"] = priority
        if priority != BELOW_NORMAL:
            raise GuardError("process_priority_readback_mismatch")
        self.record["setup_verified"] = True

    def finish(self):
        if self.handle is None:
            return
        try:
            if self.record["setup_verified"]:
                value = self.query()
                self.record.update(final_limit_flags=value.basic.flags, final_job_memory_bytes=value.job_memory,
                                   peak_job_memory_bytes=value.peak_job_memory,
                                   peak_process_memory_bytes=value.peak_process_memory)
                if value.basic.flags != JOB_MEMORY or value.job_memory != MEMORY_BYTES:
                    raise GuardError("final_job_limit_readback_mismatch")
        except Exception as error:
            self.record["errors"].append(error_record(error))
        finally:
            try:
                self.api.call("CloseHandle", self.handle)
                self.record["handle_closed"] = True
            except Exception as error:
                self.record["errors"].append(error_record(error))
            self.handle = None


def run_control(output):
    control = Path(__file__).resolve().with_name("r3_export_credit_control.py")
    previous = sys.argv
    try:
        sys.argv = [str(control), "--qt-platform", "windows", "--output", str(output)]
        runpy.run_path(str(control), run_name="__main__")
    finally:
        sys.argv = previous


def source_hashes():
    root = Path(__file__).resolve().parents[1]
    hashes = {}
    for name in SOURCE_FILES:
        path = root / name
        if not path.is_file() or path.is_symlink() or not 0 < path.stat().st_size <= 2 * 1024 ** 2:
            raise GuardError("guard_source_missing_or_oversized")
        hashes[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    return hashes


def execute(output, api=None, control=run_control):
    output = Path(output).resolve()
    guard = None
    record = {"schema_version": 1, "role": "hosted_credit_guard", "status": "INVALID",
              "setup_verified": False, "control_started": False, "errors": [],
              "original_control_exit_code": None, "performance_status": "NOT_ASSESSED"}
    exit_code = 1
    try:
        if output.exists():
            raise GuardError("new_output_directory_required")
        guard = Guard(api if api is not None else NativeApi())
        record = guard.record
        record["source_before"] = source_hashes()
        guard.setup()
        record["control_started"] = True
        try:
            control(output)
            exit_code = 0
        except SystemExit as error:
            exit_code = error.code if type(error.code) is int else 0 if error.code is None else 1
        except BaseException as error:
            record["errors"].append("control_" + type(error).__name__)
            record["control_exception_stack"] = control_stack(error)
            exit_code = 1
        record["original_control_exit_code"] = exit_code
    except Exception as error:
        record["errors"].append(error_record(error))
    finally:
        if guard is not None:
            guard.finish()
            try:
                record["source_after"] = source_hashes()
                record["source_stable"] = bool(record.get("source_before")) and record["source_before"] == record["source_after"]
                if not record["source_stable"]:
                    record["errors"].append("guard_source_changed")
            except Exception as error:
                record["errors"].append(error_record(error))
    if record.get("setup_verified") and record.get("handle_closed") and not record["errors"]:
        record["status"] = "COMPLETE"
    if record["status"] != "COMPLETE" and exit_code == 0:
        exit_code = 1
    record["wrapper_exit_code"] = exit_code
    content = json.dumps(record, separators=(",", ":"), allow_nan=False)
    if len(content.encode("utf-8")) > 16 * 1024:
        raise GuardError("guard_record_budget_exceeded")
    # Only the original control may create the output directory. A setup fault
    # is reported to stdout without fabricating a control manifest or directory.
    if record.get("control_started") and output.is_dir() and not output.is_symlink():
        try:
            with (output / "hosted-guard.json").open("x", encoding="utf-8") as stream:
                stream.write(content)
        except OSError:
            record.update(status="INVALID", guard_record_write_failed=True)
            record["errors"].append("guard_record_write_failed")
            exit_code = exit_code if exit_code else 1
            record["wrapper_exit_code"] = exit_code
            content = json.dumps(record, separators=(",", ":"))
    else:
        record["status"] = "INVALID"
        record["errors"].append("control_output_unavailable")
        exit_code = exit_code if exit_code else 1
        record["wrapper_exit_code"] = exit_code
        content = json.dumps(record, separators=(",", ":"))
    print(content, flush=True)
    return exit_code


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    return execute(parser.parse_args(argv).output)


if __name__ == "__main__":
    raise SystemExit(main())
