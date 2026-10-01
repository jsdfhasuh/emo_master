"""Fake WinAPI only: never calls Windows, starts a process, or runs a benchmark."""
import contextlib
import ctypes
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "r3_hosted_credit_guard.py"
SPEC = importlib.util.spec_from_file_location("hosted_guard_under_test", SCRIPT)
guard = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(guard)


class Function:
    def __init__(self, function):
        self.function = function

    def __call__(self, *args):
        return self.function(*args)


class FakeKernel:
    def __init__(self, fault=None):
        self.fault, self.calls, self.queries = fault, [], 0
        self.info = guard.ExtendedLimits()
        self.priority = guard.BELOW_NORMAL
        for name in ("CreateJobObjectW", "SetInformationJobObject", "QueryInformationJobObject", "GetCurrentProcess",
                     "AssignProcessToJobObject", "IsProcessInJob", "SetPriorityClass", "GetPriorityClass", "CloseHandle"):
            setattr(self, name, Function(lambda *args, name=name: self.invoke(name, *args)))

    def invoke(self, name, *args):
        self.calls.append((name, args))
        if self.fault == name:
            return 0
        if name == "CreateJobObjectW":
            return 101
        if name == "GetCurrentProcess":
            return -1
        if name == "SetInformationJobObject":
            ctypes.memmove(ctypes.byref(self.info), args[2], ctypes.sizeof(self.info))
        elif name == "QueryInformationJobObject":
            self.queries += 1
            if self.fault == "final_query" and self.queries == 2:
                return 0
            if self.fault == "wrong_limit":
                self.info.job_memory = 1024
            if self.fault == "kill_on_close":
                self.info.basic.flags |= 0x2000
            self.info.peak_job_memory, self.info.peak_process_memory = 2000, 1000
            ctypes.memmove(args[2], ctypes.byref(self.info), ctypes.sizeof(self.info))
            ctypes.cast(args[4], ctypes.POINTER(guard.DWORD))[0] = ctypes.sizeof(self.info)
        elif name == "IsProcessInJob":
            ctypes.cast(args[2], ctypes.POINTER(guard.BOOL))[0] = self.fault != "unassigned"
        elif name == "GetPriorityClass":
            return 0x20 if self.fault == "wrong_priority" else self.priority
        elif name == "SetPriorityClass":
            self.priority = args[1]
        return 1


class GuardTests(unittest.TestCase):
    def run_case(self, fault=None, code=0):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        output = Path(temporary.name) / "new-evidence"
        kernel = FakeKernel(fault)
        calls = []

        def control(destination):
            calls.append(destination)
            destination.mkdir()
            raise SystemExit(code)

        stream = io.StringIO()
        with contextlib.redirect_stdout(stream):
            actual = guard.execute(output, guard.NativeApi(kernel), control)
        return actual, json.loads(stream.getvalue()), kernel, calls, output

    def test_verified_guard_runs_once_and_preserves_success(self):
        code, record, kernel, calls, output = self.run_case()
        self.assertEqual(code, 0)
        self.assertEqual(record["status"], "COMPLETE")
        self.assertEqual(len(calls), 1)
        self.assertEqual(json.loads((output / "hosted-guard.json").read_text()), record)
        self.assertEqual(record["peak_job_memory_bytes"], 2000)
        self.assertEqual(record["memory_limit_violation_status"], "NOT_ASSESSED")
        self.assertEqual(kernel.info.basic.flags, 0x200)
        self.assertEqual(kernel.info.job_memory, 4 * 1024 ** 3)
        self.assertEqual(kernel.info.basic.priority, 0)
        self.assertEqual(kernel.queries, 2)
        self.assertEqual([args for name, args in kernel.calls if name == "AssignProcessToJobObject"], [(101, -1)])
        self.assertEqual([args for name, args in kernel.calls if name == "SetPriorityClass"], [(-1, 0x4000)])
        self.assertEqual(kernel.calls[-1][0], "CloseHandle")
        self.assertEqual(kernel.CreateJobObjectW.restype, ctypes.c_void_p)
        self.assertEqual(ctypes.sizeof(guard.DWORD), 4)
        self.assertEqual(ctypes.sizeof(guard.BOOL), 4)
        self.assertEqual(ctypes.sizeof(guard.ExtendedLimits), 144)

    def test_nonzero_original_exit_is_never_replaced_by_success(self):
        code, record, _, calls, _ = self.run_case(code=7)
        self.assertEqual(code, 7)
        self.assertEqual(record["original_control_exit_code"], 7)
        self.assertEqual(record["wrapper_exit_code"], 7)
        self.assertEqual(record["status"], "COMPLETE")
        self.assertEqual(len(calls), 1)

    def test_setup_or_readback_failures_abort_before_control(self):
        faults = ("CreateJobObjectW", "SetInformationJobObject", "GetCurrentProcess", "AssignProcessToJobObject",
                  "IsProcessInJob", "QueryInformationJobObject", "SetPriorityClass", "GetPriorityClass",
                  "wrong_limit", "wrong_priority", "unassigned", "kill_on_close")
        for fault in faults:
            with self.subTest(fault=fault):
                code, record, kernel, calls, output = self.run_case(fault)
                self.assertEqual(code, 1)
                self.assertEqual(record["status"], "INVALID")
                self.assertFalse(record["control_started"])
                self.assertFalse(calls)
                self.assertFalse(output.exists())
                if fault != "CreateJobObjectW":
                    self.assertEqual(kernel.calls[-1][0], "CloseHandle")

    def test_failed_final_query_keeps_original_failure(self):
        code, record, _, calls, _ = self.run_case("final_query", code=9)
        self.assertEqual(code, 9)
        self.assertEqual(record["status"], "INVALID")
        self.assertTrue(record["handle_closed"])
        self.assertEqual(len(calls), 1)

    def test_failed_final_query_or_close_cannot_make_success(self):
        for fault in ("final_query", "CloseHandle"):
            with self.subTest(fault=fault):
                code, record, _, _, _ = self.run_case(fault)
                self.assertEqual(code, 1)
                self.assertEqual(record["status"], "INVALID")
                self.assertEqual(record["original_control_exit_code"], 0)

    def test_original_exception_recorded_without_raw_message(self):
        with tempfile.TemporaryDirectory() as root:
            output = Path(root) / "evidence"
            def control(path):
                path.mkdir()
                raise ValueError("C:\\private\\secret.png")
            stream = io.StringIO()
            with contextlib.redirect_stdout(stream):
                code = guard.execute(output, guard.NativeApi(FakeKernel()), control)
            self.assertEqual(code, 1)
            self.assertNotIn("secret.png", stream.getvalue())
            record = json.loads(stream.getvalue())
            self.assertIn("control_ValueError", record["errors"])
            self.assertEqual(record["control_exception_stack"]["frames"][-1]["function"], "control")
            self.assertTrue(all("/" not in frame["file"] and "\\" not in frame["file"]
                                for frame in record["control_exception_stack"]["frames"]))

    def test_native_failure_retains_numeric_windows_error(self):
        with tempfile.TemporaryDirectory() as root, contextlib.redirect_stdout(io.StringIO()) as stream:
            api = guard.NativeApi(FakeKernel("AssignProcessToJobObject"), last_error=lambda: 87)
            code = guard.execute(Path(root) / "new", api, lambda _: self.fail("must not run"))
            self.assertEqual(code, 1)
            self.assertIn({"call": "AssignProcessToJobObject", "winerror": 87}, json.loads(stream.getvalue())["errors"])

    def test_fixed_control_arguments_no_fallback_and_restored_argv(self):
        original = guard.sys.argv
        seen = []
        with patch.object(guard.runpy, "run_path", side_effect=lambda *args, **kw: seen.append((args, kw, list(guard.sys.argv)))):
            guard.run_control(Path("new-output"))
        self.assertIs(guard.sys.argv, original)
        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0][1], {"run_name": "__main__"})
        self.assertEqual(seen[0][2][1:], ["--qt-platform", "windows", "--output", "new-output"])
        self.assertEqual(Path(seen[0][0][0]).name, "r3_export_credit_control.py")

    def test_existing_directory_is_not_reused(self):
        with tempfile.TemporaryDirectory() as root, contextlib.redirect_stdout(io.StringIO()) as stream:
            kernel = FakeKernel()
            code = guard.execute(Path(root), guard.NativeApi(kernel), lambda _: self.fail("must not run"))
            self.assertEqual(code, 1)
            self.assertEqual(kernel.calls, [])
            self.assertIn("new_output_directory_required", json.loads(stream.getvalue())["errors"])

    def test_absent_control_output_keeps_nonzero_exit(self):
        with tempfile.TemporaryDirectory() as root, contextlib.redirect_stdout(io.StringIO()) as stream:
            def control(_):
                raise SystemExit(8)
            code = guard.execute(Path(root) / "absent", guard.NativeApi(FakeKernel()), control)
            record = json.loads(stream.getvalue())
            self.assertEqual(code, 8)
            self.assertEqual(record["original_control_exit_code"], 8)
            self.assertEqual(record["status"], "INVALID")
            self.assertIn("control_output_unavailable", record["errors"])

    def test_guard_record_collision_keeps_nonzero_exit(self):
        with tempfile.TemporaryDirectory() as root, contextlib.redirect_stdout(io.StringIO()) as stream:
            def control(path):
                path.mkdir()
                (path / "hosted-guard.json").write_text("do not overwrite")
                raise SystemExit(6)
            output = Path(root) / "new"
            code = guard.execute(output, guard.NativeApi(FakeKernel()), control)
            record = json.loads(stream.getvalue())
            self.assertEqual(code, 6)
            self.assertEqual(record["status"], "INVALID")
            self.assertTrue(record["guard_record_write_failed"])
            self.assertEqual((output / "hosted-guard.json").read_text(), "do not overwrite")


if __name__ == "__main__":
    unittest.main()
