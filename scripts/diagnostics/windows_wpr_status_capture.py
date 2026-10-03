"""Bounded private capture for the one read-only named WPR status query.

No capture flags come from the caller. A drained pipe EOF and a successful
process wait are separate facts. Raw merged stdout/stderr never leaves here.
"""
from dataclasses import dataclass
import os
import subprocess
import time

from .windows_wpr_status_absence import (
    MAX_CAPTURE_BYTES, Reason, Status, StatusDecision, StatusObservation,
    classify_status_absence,
)


@dataclass(frozen=True, slots=True)
class CaptureResult:
    decision: StatusDecision
    native_exit: int | None


def _pipe_reader(stream):
    descriptor = stream.fileno()
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        import msvcrt

        handle = msvcrt.get_osfhandle(descriptor)
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        peek = kernel.PeekNamedPipe
        peek.argtypes = (wintypes.HANDLE, wintypes.LPVOID, wintypes.DWORD,
                         wintypes.LPDWORD, wintypes.LPDWORD, wintypes.LPDWORD)
        peek.restype = wintypes.BOOL
        read_file = kernel.ReadFile
        read_file.argtypes = (wintypes.HANDLE, wintypes.LPVOID, wintypes.DWORD,
                              wintypes.LPDWORD, wintypes.LPVOID)
        read_file.restype = wintypes.BOOL

        def read(limit):
            available = wintypes.DWORD()
            if not peek(handle, None, 0, None, ctypes.byref(available), None):
                if ctypes.get_last_error() != 109:  # ERROR_BROKEN_PIPE
                    raise OSError("pipe_read_failed")
                # Confirm with ReadFile; include any residual bytes. Its 109
                # error, not Peek alone or a successful zero-byte read, is EOF.
            elif available.value == 0:
                return None
            else:
                limit = min(limit, available.value)
            # One reader, unbuffered pipe: these bytes cannot be consumed by a
            # competing buffered reader between the peek and read.
            buffer = ctypes.create_string_buffer(limit)
            count = wintypes.DWORD()
            success = read_file(handle, buffer, limit, ctypes.byref(count), None)
            if count.value > limit:
                raise OSError("pipe_read_failed")
            if not success:
                if ctypes.get_last_error() == 109 and count.value == 0:
                    return b""
                raise OSError("pipe_read_failed")
            # A successful zero-byte ReadFile can mean a zero-length write.
            return buffer.raw[:count.value] if count.value else None

        return read
    os.set_blocking(descriptor, False)

    def read(limit):
        try:
            return os.read(descriptor, limit)
        except BlockingIOError:
            return None

    return read


def observe_status(argv, timeout=15):
    """One private merged pipe, one child, no retry, and no unbounded readers.

    The production caller supplies its existing scoped read-only command. The
    only bytes retained are at most MAX_CAPTURE_BYTES; one overflow byte can be
    read solely to reject the observation. No raw bytes or error text escape.
    """
    process = None
    body = bytearray()
    native_exit = None
    reason = Reason.COMMAND_NOT_NORMAL
    normal_completion = False
    eof = False
    capture_ok = False
    try:
        deadline = time.monotonic() + timeout
        process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, shell=False, bufsize=0)
        reason = Reason.OUTPUT_INCOMPLETE
        read = _pipe_reader(process.stdout)
        while True:
            if time.monotonic() >= deadline:
                reason = Reason.OUTPUT_INCOMPLETE if normal_completion else Reason.COMMAND_NOT_NORMAL
                break
            if not normal_completion:
                reason = Reason.COMMAND_NOT_NORMAL
                status = process.poll()
                if status is not None:
                    status = process.wait(timeout=0)
                    normal_completion = True
                    if type(status) is int and 0 <= status <= 0xFFFFFFFF:
                        native_exit = status
            if not eof:
                reason = Reason.OUTPUT_INCOMPLETE
                chunk = read(min(1024, MAX_CAPTURE_BYTES - len(body) + 1))
                if chunk == b"":
                    eof = True
                elif chunk is not None:
                    if len(body) + len(chunk) > MAX_CAPTURE_BYTES:
                        reason = Reason.OUTPUT_OVERSIZE
                        break
                    body.extend(chunk)
            if time.monotonic() >= deadline:
                reason = Reason.OUTPUT_INCOMPLETE if normal_completion else Reason.COMMAND_NOT_NORMAL
                break
            if normal_completion and eof:
                capture_ok = True
                break
            time.sleep(0.01)
    except BaseException:
        # The current closed-set phase records failed spawn/wait versus read.
        pass
    finally:
        if process is not None:
            try:
                # Only our immediate status subprocess. An inherited writer in
                # another process is neither inspected nor terminated.
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=1)
            except BaseException:
                capture_ok = False
                reason = Reason.COMMAND_NOT_NORMAL
            try:
                # FileIO (bufsize=0), no blocked thread or buffered-reader lock.
                process.stdout.close()
            except BaseException:
                capture_ok = False
                reason = Reason.OUTPUT_INCOMPLETE
    if not capture_ok:
        body.clear()
        return CaptureResult(StatusDecision(Status.UNKNOWN, reason), native_exit)
    decision = classify_status_absence(StatusObservation(
        body=bytes(body), output_complete=eof, normal_completion=normal_completion,
        private_capture=True, exit_code=native_exit,
    ))
    body.clear()
    return CaptureResult(decision, native_exit)
