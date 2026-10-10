"""Independent, persistent SLMP 3E loopback peer for debug regressions."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import socket
import struct
import threading
import time
from typing import Callable


@dataclass(frozen=True)
class Request:
    connection: int
    raw: bytes

    @property
    def command(self) -> int:
        return struct.unpack_from("<H", self.raw, 11)[0]

    @property
    def subcommand(self) -> int:
        return struct.unpack_from("<H", self.raw, 13)[0]

    @property
    def start(self) -> int:
        return int.from_bytes(self.raw[15:18], "little")

    @property
    def device(self) -> str:
        return {0xA8: "D", 0x90: "M"}[self.raw[18]]

    @property
    def count(self) -> int:
        return struct.unpack_from("<H", self.raw, 19)[0]

    @property
    def payload(self) -> bytes:
        return self.raw[21:]


def response(request: Request, data: bytes = b"", *, end_code: int = 0,
             route: bytes | None = None, length: int | None = None,
             subheader: bytes = b"\xd0\x00") -> bytes:
    body = struct.pack("<H", end_code) + data
    return (subheader + (request.raw[2:7] if route is None else route)
            + struct.pack("<H", len(body) if length is None else length) + body)


class BlockResponse:
    """Hold a response until release or peer cancellation, without sleeping."""

    def __init__(self) -> None:
        self.entered = threading.Event()
        self.release = threading.Event()
        self.peer_closed = threading.Event()

    def __call__(self, server: "PlcDebugServer", request: Request,
                 sock: socket.socket) -> bytes | None:
        self.entered.set()
        while not self.release.is_set() and not server.stopped.is_set():
            try:
                sock.settimeout(0.02)
                data = sock.recv(1, socket.MSG_PEEK)
                if not data:
                    self.peer_closed.set()
                    return None
                raise AssertionError("another request arrived before the held response")
            except socket.timeout:
                continue
            except OSError:
                self.peer_closed.set()
                return None
        return response(request) if request.command == 0x1401 else server.memory_response(request)


Action = bytes | None | Callable[["PlcDebugServer", Request, socket.socket], bytes | None]


class PlcDebugServer:
    """One or more sockets, many exchanges per socket, and scriptable failures.

    The in-memory PLC implements word D/M access and nibble-packed M bits
    independently of the production encoder. Requests expose connection IDs so
    reconnects and accidental retries cannot masquerade as a persistent session.
    """

    def __init__(self, actions: list[Action] | None = None, *, fragment: bool = False) -> None:
        self.actions = deque(actions or [])
        self.fragment = fragment
        self.words: dict[int, int] = {}
        self.bits: dict[int, bool] = {}
        self.requests: list[Request] = []
        self.errors: list[BaseException] = []
        self.connections = 0
        self.stopped = threading.Event()
        self.changed = threading.Condition()
        self._sockets: list[socket.socket] = []
        self._workers: list[threading.Thread] = []
        self.listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(8)
        self.listener.settimeout(0.05)
        self.port = self.listener.getsockname()[1]
        self.thread = threading.Thread(target=self._accept, name="test-plc-loopback", daemon=True)

    def __enter__(self) -> "PlcDebugServer":
        self.thread.start()
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()
        if exc_type is None:
            assert not self.errors, self.errors

    def params(self, **overrides) -> dict:
        return {"host": "127.0.0.1", "port": self.port,
                "connectTimeoutMs": 500, "responseTimeoutMs": 500, **overrides}

    def wait_requests(self, count: int, timeout: float = 2.0) -> None:
        deadline = time.monotonic() + timeout
        with self.changed:
            while len(self.requests) < count and not self.errors:
                remaining = deadline - time.monotonic()
                assert remaining > 0, f"only {len(self.requests)} of {count} requests received"
                self.changed.wait(remaining)
            assert not self.errors, self.errors

    def wait_connections(self, count: int, timeout: float = 2.0) -> None:
        deadline = time.monotonic() + timeout
        with self.changed:
            while self.connections < count and not self.errors:
                remaining = deadline - time.monotonic()
                assert remaining > 0, f"only {self.connections} of {count} sockets accepted"
                self.changed.wait(remaining)
            assert not self.errors, self.errors

    def _accept(self) -> None:
        try:
            while not self.stopped.is_set():
                try:
                    sock, _ = self.listener.accept()
                except socket.timeout:
                    continue
                with self.changed:
                    self.connections += 1
                    connection = self.connections
                    self._sockets.append(sock)
                    self.changed.notify_all()
                worker = threading.Thread(target=self._serve, args=(sock, connection),
                                          name=f"test-plc-peer-{connection}", daemon=True)
                self._workers.append(worker)
                worker.start()
        except OSError as error:
            if not self.stopped.is_set():
                self._record_error(error)

    def _record_error(self, error: BaseException) -> None:
        with self.changed:
            self.errors.append(error)
            self.changed.notify_all()

    def _recv_exact(self, sock: socket.socket, count: int) -> bytes | None:
        result = bytearray()
        while len(result) < count and not self.stopped.is_set():
            try:
                chunk = sock.recv(count - len(result))
            except socket.timeout:
                continue
            if not chunk:
                return None
            result.extend(chunk)
        return bytes(result) if len(result) == count else None

    def _serve(self, sock: socket.socket, connection: int) -> None:
        try:
            with sock:
                sock.settimeout(0.05)
                while not self.stopped.is_set():
                    header = self._recv_exact(sock, 9)
                    if header is None:
                        return
                    assert header[:2] == b"\x50\x00", header.hex()
                    size = struct.unpack_from("<H", header, 7)[0]
                    assert 12 <= size <= 1932, size
                    body = self._recv_exact(sock, size)
                    if body is None:
                        return
                    request = Request(connection, header + body)
                    with self.changed:
                        self.requests.append(request)
                        action = self.actions.popleft() if self.actions else self._memory_action
                        self.changed.notify_all()
                    reply = action(self, request, sock) if callable(action) else action
                    if reply is None:
                        return
                    if self.fragment:
                        for byte in reply:
                            sock.sendall(bytes([byte]))
                    else:
                        sock.sendall(reply)
        except OSError:
            # Disconnects, shutdowns and malformed-response retirement are expected.
            return
        except BaseException as error:
            self._record_error(error)

    @staticmethod
    def _memory_action(server: "PlcDebugServer", request: Request, _sock) -> bytes:
        return server.memory_response(request)

    def memory_response(self, request: Request) -> bytes:
        assert request.command in {0x0401, 0x1401}
        assert request.subcommand in {0, 1}
        assert 1 <= request.count <= 960
        writing = request.command == 0x1401
        if request.subcommand == 1:
            assert request.device == "M"
            if writing:
                assert len(request.payload) == (request.count + 1) // 2
                for index in range(request.count):
                    byte = request.payload[index // 2]
                    nibble = byte >> 4 if index % 2 == 0 else byte & 0xF
                    assert nibble in {0, 1}
                    self.bits[request.start + index] = bool(nibble)
                if request.count % 2:
                    assert request.payload[-1] & 0xF == 0
                return response(request)
            assert request.payload == b""
            values = [self.bits.get(request.start + i, False) for i in range(request.count)]
            data = bytes((int(values[i]) << 4) | (int(values[i + 1]) if i + 1 < len(values) else 0)
                         for i in range(0, len(values), 2))
            return response(request, data)
        if writing:
            assert len(request.payload) == request.count * 2
            values = struct.unpack("<" + "H" * request.count, request.payload)
            for index, value in enumerate(values):
                if request.device == "D":
                    self.words[request.start + index] = value
                else:
                    for bit in range(16):
                        self.bits[request.start + index * 16 + bit] = bool(value & (1 << bit))
            return response(request)
        assert request.payload == b""
        values = [self.words.get(request.start + index, 0) if request.device == "D" else
                  sum(int(self.bits.get(request.start + index * 16 + bit, False)) << bit
                      for bit in range(16)) for index in range(request.count)]
        return response(request, struct.pack("<" + "H" * request.count, *values))

    def close(self) -> None:
        self.stopped.set()
        for sock in (self.listener, *self._sockets):
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            sock.close()
        self.thread.join(2.0)
        for worker in self._workers:
            worker.join(2.0)
            assert not worker.is_alive(), "loopback peer did not retire"
        assert not self.thread.is_alive(), "loopback listener did not retire"
