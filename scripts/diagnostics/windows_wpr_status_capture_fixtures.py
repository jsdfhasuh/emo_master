"""Synthetic subprocess and fake transport checks. Never invokes WPR."""
from contextlib import redirect_stderr, redirect_stdout
import ctypes
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from . import windows_wpr_status_capture as capture
from .windows_wpr_status_absence import MAX_CAPTURE_BYTES, Reason, Status

PHRASE = b"WPR is not recording"
SECRET = "PRIVATE_SESSION_C:\\private\\trace.etl"


class FakeProcess:
    def __init__(self, status=0, poll_error=False, wait_error=False, close_error=False):
        self.status = status
        self.poll_error = poll_error
        self.wait_error = wait_error
        self.killed = False
        self.closed = False
        self.waits = []
        self.close_error = close_error
        self.stdout = SimpleNamespace(close=self.close)

    def close(self):
        self.closed = True
        if self.close_error:
            raise OSError(SECRET)

    def poll(self):
        if self.poll_error:
            raise OSError(SECRET)
        return self.status

    def wait(self, timeout):
        self.waits.append(timeout)
        if self.wait_error:
            raise OSError(SECRET)
        return self.status

    def kill(self):
        self.killed = True
        self.status = 1


class StatusCaptureFixtures(unittest.TestCase):
    def fake(self, chunks, process=None, timeout=0.15):
        process = process or FakeProcess()
        chunks = iter(chunks)
        limits = []
        def read(limit):
            limits.append(limit)
            chunk = next(chunks, None)
            if isinstance(chunk, BaseException):
                raise chunk
            return chunk
        output = io.StringIO()
        with patch.object(capture.subprocess, "Popen", return_value=process) as launch, \
                patch.object(capture, "_pipe_reader", return_value=read), \
                redirect_stdout(output), redirect_stderr(output):
            result = capture.observe_status(["synthetic", "-status", "-instancename", "private"], timeout)
        self.assertEqual(launch.call_count, 1)
        self.assertEqual(launch.call_args.kwargs, dict(stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                                     stderr=subprocess.STDOUT, shell=False, bufsize=0))
        self.assertTrue(process.closed)
        self.assertNotIn(SECRET, output.getvalue() + repr(result))
        self.assertNotIn(PHRASE.decode(), output.getvalue() + repr(result))
        self.assertTrue(all(1 <= limit <= 1024 for limit in limits))
        return result, process

    def real(self, script, timeout=15):
        output = io.StringIO()
        with redirect_stdout(output), redirect_stderr(output):
            result = capture.observe_status([sys.executable, "-c", script], timeout)
        self.assertEqual(output.getvalue(), "")
        return result

    def test_fake_complete_stdout_stderr_and_allowed_exits(self):
        for status in (0, 0xC5583000):
            result, process = self.fake([b" \t", PHRASE[:8], PHRASE[8:], b"\r\n", b""], FakeProcess(status))
            self.assertIs(result.decision.status, Status.ABSENT)
            self.assertEqual(result.native_exit, status)
            self.assertEqual(process.waits, [0])
            self.assertFalse(process.killed)

    def test_fake_conflicting_stderr_cannot_be_filtered(self):
        result, _ = self.fake([PHRASE, b"\n" + SECRET.encode(), b""])
        self.assertIs(result.decision.status, Status.UNKNOWN)
        self.assertIs(result.decision.reason, Reason.BODY_NOT_EXACT)

    def test_fake_exit_before_eof_does_not_accept_prefix(self):
        started = time.monotonic()
        result, process = self.fake([PHRASE])
        self.assertIs(result.decision.status, Status.UNKNOWN)
        self.assertIs(result.decision.reason, Reason.OUTPUT_INCOMPLETE)
        self.assertLess(time.monotonic() - started, 2)
        self.assertFalse(process.killed)

    def test_fake_eof_without_child_completion_is_unknown(self):
        result, process = self.fake([PHRASE, b""], FakeProcess(None))
        self.assertIs(result.decision.status, Status.UNKNOWN)
        self.assertTrue(process.killed)
        self.assertEqual(process.waits, [1])

    def test_fake_read_error_after_exact_body_is_unknown(self):
        result, _ = self.fake([PHRASE, OSError(SECRET)])
        self.assertIs(result.decision.status, Status.UNKNOWN)
        self.assertIs(result.decision.reason, Reason.OUTPUT_INCOMPLETE)

    def test_fake_poll_and_wait_errors_are_unknown(self):
        for process in (FakeProcess(poll_error=True), FakeProcess(wait_error=True)):
            result, _ = self.fake([PHRASE, b""], process)
            self.assertIs(result.decision.status, Status.UNKNOWN)
            self.assertIs(result.decision.reason, Reason.COMMAND_NOT_NORMAL)

    def test_fake_close_error_invalidates_complete_body(self):
        result, _ = self.fake([PHRASE, b""], FakeProcess(close_error=True))
        self.assertIs(result.decision.status, Status.UNKNOWN)
        self.assertIs(result.decision.reason, Reason.OUTPUT_INCOMPLETE)

    def test_fake_overflow_is_irreversible(self):
        chunks = [b" " * 1024] * 4 + [b" ", PHRASE, b""]
        result, _ = self.fake(chunks)
        self.assertIs(result.decision.status, Status.UNKNOWN)
        self.assertIs(result.decision.reason, Reason.OUTPUT_OVERSIZE)

    def test_fake_nonallowed_and_signed_exits_are_unknown(self):
        for status in (1, -1, 0xC5583000 - (1 << 32), (1 << 32), True):
            result, _ = self.fake([PHRASE, b""], FakeProcess(status))
            self.assertIs(result.decision.status, Status.UNKNOWN)
            self.assertIs(result.decision.reason, Reason.EXIT_NOT_ALLOWED)

    def test_fake_spawn_error_is_private_and_unknown(self):
        output = io.StringIO()
        with patch.object(capture.subprocess, "Popen", side_effect=OSError(SECRET)) as launch, \
                redirect_stdout(output), redirect_stderr(output):
            result = capture.observe_status([SECRET])
        self.assertEqual(launch.call_count, 1)
        self.assertIs(result.decision.status, Status.UNKNOWN)
        self.assertIs(result.decision.reason, Reason.COMMAND_NOT_NORMAL)
        self.assertNotIn(SECRET, output.getvalue() + repr(result))

    def test_real_stdout_and_stderr_are_both_captured(self):
        for fd in (1, 2):
            result = self.real(f"import os; os.write({fd}, {PHRASE!r})")
            self.assertIs(result.decision.status, Status.ABSENT)
        result = self.real(f"import os; os.write(1, {PHRASE!r}); os.write(2, b'\\nextra')")
        self.assertIs(result.decision.status, Status.UNKNOWN)
        self.assertIs(result.decision.reason, Reason.BODY_NOT_EXACT)

    def test_real_delayed_tail_is_not_ignored(self):
        result = self.real(f"import os,time; os.write(1,{PHRASE!r}); time.sleep(.08); os.write(2,b'\\nextra')")
        self.assertIs(result.decision.status, Status.UNKNOWN)
        self.assertIs(result.decision.reason, Reason.BODY_NOT_EXACT)

    def test_real_bom_encodings_and_invalid_encoding(self):
        for raw in (b"\xef\xbb\xbf" + PHRASE, b"\xff\xfe" + PHRASE.decode().encode("utf-16-le"),
                    b"\xfe\xff" + PHRASE.decode().encode("utf-16-be")):
            self.assertIs(self.real(f"import os; os.write(1,{raw!r})").decision.status, Status.ABSENT)
        result = self.real(f"import os; os.write(1,{PHRASE!r}+b'\\xff')")
        self.assertIs(result.decision.status, Status.UNKNOWN)
        self.assertIs(result.decision.reason, Reason.ENCODING_INVALID)

    def test_real_exact_cap_and_over_cap(self):
        for extra, expected in ((0, Status.ABSENT), (1, Status.UNKNOWN)):
            result = self.real(f"import os; os.write(1,{PHRASE!r}+b' '*{MAX_CAPTURE_BYTES-len(PHRASE)+extra})")
            self.assertIs(result.decision.status, expected)
            if extra:
                self.assertIs(result.decision.reason, Reason.OUTPUT_OVERSIZE)

    def test_real_timeout_kills_only_owned_child(self):
        started = time.monotonic()
        result = self.real(f"import os,time; os.write(1,{PHRASE!r}); time.sleep(5)", timeout=.2)
        self.assertIs(result.decision.status, Status.UNKNOWN)
        self.assertLess(time.monotonic() - started, 2)

    def test_real_inherited_writer_prevents_eof(self):
        # Explicit readiness removes Python startup from the branch under test.
        # The real parent has exited and its real descendant holds the pipe open
        # before the adapter's short drain deadline begins. Release is test-owned.
        with tempfile.TemporaryDirectory() as root:
            ready = Path(root) / "ready"
            release = Path(root) / "release"
            done = Path(root) / "done"
            child_script = (
                "from pathlib import Path; import time; "
                f"ready=Path({str(ready)!r}); release=Path({str(release)!r}); done=Path({str(done)!r}); "
                "deadline=time.monotonic()+30; ready.touch();\n"
                "while not release.exists() and time.monotonic()<deadline: time.sleep(.01)\n"
                "done.touch()"
            )
            script = ("import os,subprocess,sys; "
                      f"subprocess.Popen([sys.executable,'-c',{child_script!r}], "
                      "stdout=sys.stdout,stderr=sys.stderr,close_fds=False); "
                      f"os.write(1,{PHRASE!r})")
            process = subprocess.Popen([sys.executable, "-c", script], stdin=subprocess.DEVNULL,
                                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                       shell=False, bufsize=0)
            try:
                self.assertEqual(process.wait(timeout=15), 0)
                ready_deadline = time.monotonic() + 15
                while not ready.exists() and time.monotonic() < ready_deadline:
                    time.sleep(.01)
                self.assertTrue(ready.exists())
                started = time.monotonic()
                with patch.object(capture.subprocess, "Popen", return_value=process):
                    result = capture.observe_status(["synthetic-ready-process"], timeout=.2)
                self.assertIs(result.decision.status, Status.UNKNOWN)
                self.assertIs(result.decision.reason, Reason.OUTPUT_INCOMPLETE)
                self.assertEqual(result.native_exit, 0)
                self.assertLess(time.monotonic() - started, 3)
            finally:
                release.touch()
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=1)
                process.stdout.close()
                done_deadline = time.monotonic() + 15
                while not done.exists() and time.monotonic() < done_deadline:
                    time.sleep(.01)
                self.assertTrue(done.exists())

    def test_real_windows_exit_dword_or_portable_nonzero(self):
        script = (f"import os; os.write(1,{PHRASE!r}); ")
        if os.name == "nt":
            script += "import ctypes; ctypes.windll.kernel32.ExitProcess(0xC5583000)"
            result = self.real(script)
            self.assertIs(result.decision.status, Status.ABSENT)
            self.assertEqual(result.native_exit, 0xC5583000)
        else:
            result = self.real(script + "raise SystemExit(1)")
            self.assertIs(result.decision.status, Status.UNKNOWN)

    def test_both_cli_import_entrypoints_fail_closed_off_windows(self):
        repo = Path(__file__).resolve().parents[2]
        environment = dict(os.environ)
        environment.pop("PYTHONPATH", None)
        environment.pop("GITHUB_OUTPUT", None)
        environment["GITHUB_ACTIONS"] = "false"
        with tempfile.TemporaryDirectory() as root:
            for args in (("scripts/r3_windows_wpr_metadata_preflight.py",),
                         ("-m", "scripts.r3_windows_wpr_metadata_preflight")):
                result = subprocess.run([sys.executable, *args, root], cwd=repo, env=environment,
                                        capture_output=True, timeout=5, check=False)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stderr, b"")
                self.assertIn(b'"reason":"wrong_environment"', result.stdout)
            self.assertEqual(list(Path(root).iterdir()), [])

    def test_windows_readfile_eof_and_zero_write_contract(self):
        # Exercise the exact Windows adapter on any platform, including both
        # pipe APIs and their error codes, without a Windows/ETW query.
        from ctypes import wintypes
        state = {"error": 0, "peeks": 0, "reads": 0}
        def peek(_handle, _buffer, _size, _read, available, _left):
            state["peeks"] += 1
            available._obj.value = 5
            if state["peeks"] >= 3:
                state["error"] = 109
                return 0
            return 1
        def read(_handle, buffer, _size, count, _overlapped):
            state["reads"] += 1
            if state["reads"] == 1:
                count._obj.value = 0  # zero-length write is not EOF
                return 1
            if state["reads"] in (2, 3):
                ctypes.memmove(buffer, b"extra", 5)
                count._obj.value = 5
                return 1
            state["error"] = 109
            return 0
        library = SimpleNamespace(PeekNamedPipe=peek, ReadFile=read)
        with patch.object(capture, "os", SimpleNamespace(name="nt")), \
                patch.dict(sys.modules, {"msvcrt": SimpleNamespace(get_osfhandle=lambda fd: 42)}), \
                patch.object(ctypes, "WinDLL", return_value=library, create=True), \
                patch.object(ctypes, "get_last_error", side_effect=lambda: state["error"], create=True):
            reader = capture._pipe_reader(SimpleNamespace(fileno=lambda: 7))
            self.assertIsNone(reader(9))
            self.assertEqual(reader(9), b"extra")
            self.assertEqual(reader(9), b"extra")
            self.assertEqual(reader(9), b"")
        self.assertEqual(read.argtypes[2], wintypes.DWORD)

    def test_windows_open_empty_pipe_and_api_errors_are_not_eof(self):
        def check(peek_ok, error, count):
            calls = []
            def peek(_handle, _buffer, _size, _read, available, _left):
                available._obj.value = count
                return peek_ok
            def read(*args):
                calls.append(1)
                return 0
            library = SimpleNamespace(PeekNamedPipe=peek, ReadFile=read)
            with patch.object(capture, "os", SimpleNamespace(name="nt")), \
                    patch.dict(sys.modules, {"msvcrt": SimpleNamespace(get_osfhandle=lambda fd: 42)}), \
                    patch.object(ctypes, "WinDLL", return_value=library, create=True), \
                    patch.object(ctypes, "get_last_error", return_value=error, create=True):
                reader = capture._pipe_reader(SimpleNamespace(fileno=lambda: 7))
                if peek_ok:
                    self.assertIsNone(reader(4))
                else:
                    with self.assertRaises(OSError):
                        reader(4)
            self.assertEqual(calls, [])
        check(1, 0, 0)
        check(0, 5, 0)

    def test_windows_inconsistent_read_count_is_rejected(self):
        for success, count in ((1, 5), (0, 1)):
            def peek(_handle, _buffer, _size, _read, available, _left):
                available._obj.value = 4
                return 1
            def read(_handle, _buffer, _size, returned, _overlapped):
                returned._obj.value = count
                return success
            with patch.object(capture, "os", SimpleNamespace(name="nt")), \
                    patch.dict(sys.modules, {"msvcrt": SimpleNamespace(get_osfhandle=lambda fd: 42)}), \
                    patch.object(ctypes, "WinDLL", return_value=SimpleNamespace(PeekNamedPipe=peek, ReadFile=read), create=True), \
                    patch.object(ctypes, "get_last_error", return_value=109, create=True):
                reader = capture._pipe_reader(SimpleNamespace(fileno=lambda: 7))
                with self.assertRaises(OSError):
                    reader(4)

    def test_final_read_crossing_deadline_cannot_accept(self):
        process = FakeProcess()
        chunks = iter((PHRASE, b""))
        clock = iter((0, 0, 0, 0, 2))
        with patch.object(capture.subprocess, "Popen", return_value=process), \
                patch.object(capture, "_pipe_reader", return_value=lambda limit: next(chunks)), \
                patch.object(capture.time, "monotonic", side_effect=lambda: next(clock)), \
                patch.object(capture.time, "sleep"):
            result = capture.observe_status(["synthetic"], timeout=1)
        self.assertIs(result.decision.status, Status.UNKNOWN)
        self.assertIs(result.decision.reason, Reason.OUTPUT_INCOMPLETE)

    def test_public_success_requires_full_case_inventory(self):
        from . import windows_wpr_metadata_preflight_fixtures as fixtures
        from . import windows_wpr_fixture_report as report
        expected = [{"case_id": ident, "outcome": "PASS", "error": "NONE"}
                    for ident, _cls, _method in report.CASES]
        for rows in (expected[:-1], expected + expected[:1], expected[:-1] + expected[:1]):
            result = report.FixedResult(fixtures)
            result.testsRun = len(expected)
            result.rows = rows
            self.assertEqual(result.records()[1]["reason"], "report_invalid")
        result = report.FixedResult(fixtures)
        result.testsRun = len(expected)
        result.rows = expected
        self.assertEqual(result.records()[1]["reason"], "ok")
