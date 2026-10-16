"""Deadline-aware multiprocessing Pipe framing, with no abandoned I/O threads.

Windows PipeConnection uses message-mode overlapped handles; POSIX Connection
uses a length-prefixed byte stream. Keep their wire formats, but never use the
stdlib's unbounded WriteFile/read wait for communication requests.
"""
from __future__ import annotations

import os
import select
import struct
import time


def _check(cancelled, deadline):
    if cancelled() or time.monotonic() >= deadline:
        raise TimeoutError("通信 IPC 已取消或超过请求时限")


def _windowsOperation(handle, data, cancelled, deadline, *, reading=False):
    import _winapi
    _check(cancelled, deadline)
    operation = _winapi.ReadFile if reading else _winapi.WriteFile
    ov, error = operation(handle, data, overlapped=True)
    try:
        while error == _winapi.ERROR_IO_PENDING:
            _check(cancelled, deadline)
            if _winapi.WaitForMultipleObjects([ov.event], False, 20) == 0:
                break
        count, error = ov.GetOverlappedResult(False)
        if error not in (0, _winapi.ERROR_MORE_DATA):
            raise OSError(error, "通信 IPC 读写失败")
        return bytes(ov.getbuffer()) if reading else count, error
    except BaseException:
        ov.cancel()
        # Retain the OVERLAPPED and buffer until cancellation has completed.
        ov.GetOverlappedResult(True)
        raise


def _readExact(handle, size, cancelled, deadline):
    chunks = bytearray()
    while len(chunks) < size:
        _check(cancelled, deadline)
        if not select.select([handle], [], [], .02)[0]:
            continue
        try:
            data = os.read(handle, min(65536, size - len(chunks)))
        except BlockingIOError:
            continue
        if not data:
            raise EOFError("通信 IPC 对端已关闭")
        chunks.extend(data)
    return bytes(chunks)


def sendBytes(connection, data, cancelled, deadline):
    handle = connection.fileno()
    if os.name == "nt":
        count, _ = _windowsOperation(handle, data, cancelled, deadline)
        if count != len(data):
            raise OSError("通信 IPC 不完整写入")
        return
    os.set_blocking(handle, False)
    frame = memoryview(struct.pack("!i", len(data)) + data)
    while frame:
        _check(cancelled, deadline)
        if not select.select([], [handle], [], .02)[1]:
            continue
        try:
            count = os.write(handle, frame[:65536])
        except BlockingIOError:
            continue
        if not count:
            raise EOFError("通信 IPC 对端已关闭")
        frame = frame[count:]


def receiveBytes(connection, limit, cancelled, deadline):
    handle = connection.fileno()
    if os.name == "nt":
        import _winapi
        chunks = bytearray()
        while True:
            data, error = _windowsOperation(handle, min(65536, limit - len(chunks) + 1),
                                            cancelled, deadline, reading=True)
            chunks.extend(data)
            if len(chunks) > limit:
                raise OSError("通信 IPC 消息超过字节上限")
            if error != _winapi.ERROR_MORE_DATA:
                return bytes(chunks)
    os.set_blocking(handle, False)
    size = struct.unpack("!i", _readExact(handle, 4, cancelled, deadline))[0]
    if size < 0 or size > limit:
        raise OSError("通信 IPC 消息超过字节上限")
    return _readExact(handle, size, cancelled, deadline)
