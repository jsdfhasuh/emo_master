from __future__ import annotations

import socket
import time


TCP_FRAMING_MODES = frozenset({"newline", "quoted", "idle", "fixedLength"})


class TcpTransportError(Exception):
    """Base exception for bounded TCP transaction failures."""


class TcpConnectError(TcpTransportError):
    """Raised when a client connection or server bind/accept fails."""


class TcpTimeoutError(TcpTransportError):
    """Raised when connect, accept, send, or framed receive times out."""


class TcpIoError(TcpTransportError):
    """Raised when a connected socket fails while sending or receiving."""


class TcpFrameError(TcpTransportError):
    """Raised when TCP bytes do not satisfy the configured message boundary."""


class CancellableSocket:
    """Apply cancellation and an absolute operation ceiling to existing framing helpers."""
    def __init__(self, sock, cancelled, deadline, timeout):
        self.sock, self.cancelled, self.deadline, self.timeout = sock, cancelled, deadline, timeout
        self.bytesSent = 0

    def settimeout(self, timeout):
        self.timeout = timeout

    def gettimeout(self):
        return self.timeout

    def _call(self, method, *args):
        end = min(self.deadline, time.monotonic() + self.timeout)
        while True:
            if self.cancelled():
                from emo_master.core.contracts.device_driver import DeviceError
                raise DeviceError('E_DEVICE_CANCELLED', 'TCP transaction cancelled', outcome='UNKNOWN' if self.bytesSent else 'NOT_SENT')
            remaining = end - time.monotonic()
            if remaining <= 0:
                raise socket.timeout('TCP phase deadline expired')
            self.sock.settimeout(min(.05, remaining))
            try:
                return method(*args)
            except socket.timeout:
                continue

    def recv(self, count):
        return self._call(self.sock.recv, count)

    def sendall(self, data):
        end = min(self.deadline, time.monotonic() + self.timeout)
        view = memoryview(data)
        sent = 0
        while sent < len(view):
            self.timeout = max(.001, end - time.monotonic())
            count = self._call(self.sock.send, view[sent:])
            if not count:
                raise OSError('TCP peer closed while sending')
            sent += count
            self.bytesSent += count


def connectCancellable(host, port, cancelled, deadline):
    """Connect-only phase; never retry a transaction or business send."""
    import errno
    import select
    last = None
    for family, socktype, proto, _, address in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM):
        sock = socket.socket(family, socktype, proto)
        try:
            sock.setblocking(False)
            status = sock.connect_ex(address)
            while status:
                if cancelled():
                    from emo_master.core.contracts.device_driver import DeviceError
                    raise DeviceError('E_DEVICE_CANCELLED', 'TCP connection cancelled')
                if time.monotonic() >= deadline:
                    raise TcpTimeoutError('TCP connection timed out')
                if status not in {errno.EINPROGRESS, errno.EWOULDBLOCK, errno.EALREADY, 10035, 10036, 10037}:
                    raise OSError(status, 'TCP connection failed')
                _, writable, exceptional = select.select([], [sock], [sock], min(.05, max(0, deadline-time.monotonic())))
                if writable or exceptional:
                    status = sock.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR)
                    if not status:
                        break
            sock.setblocking(True)
            return sock
        except OSError as error:
            last = error
            sock.close()
        except BaseException:
            sock.close()
            raise
    raise TcpConnectError(str(last)) from last


def openTcpClient(
    host: str,
    port: int,
    *,
    connectTimeoutSec: float,
    ioTimeoutSec: float,
) -> socket.socket:
    sock: socket.socket | None = None
    try:
        sock = socket.create_connection((host, port), timeout=connectTimeoutSec)
        sock.settimeout(ioTimeoutSec)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        return sock
    except socket.timeout as exc:
        if sock is not None:
            _safeClose(sock)
        raise TcpTimeoutError(f"TCP connect timed out at {host}:{port}") from exc
    except OSError as exc:
        if sock is not None:
            _safeClose(sock)
        raise TcpConnectError(f"cannot connect to TCP peer {host}:{port}: {exc}") from exc


def sendAll(sock: socket.socket, data: bytes) -> None:
    try:
        sock.sendall(data)
    except socket.timeout as exc:
        raise TcpTimeoutError("TCP send timed out") from exc
    except OSError as exc:
        raise TcpIoError(f"TCP send failed: {exc}") from exc


def receiveFramed(
    sock: socket.socket,
    *,
    framing: str,
    maxBytes: int,
    expectedBytes: int,
    rejectTrailing: bool = False,
) -> bytes:
    if framing not in TCP_FRAMING_MODES:
        raise ValueError(f"unsupported TCP framing mode: {framing!r}")
    if not 1 <= maxBytes <= 16 * 1024 * 1024:
        raise ValueError("maxBytes must be between 1 and 16777216")
    if framing == "fixedLength":
        if not 1 <= expectedBytes <= maxBytes:
            raise ValueError("expectedBytes must be between 1 and maxBytes")
        return _receiveExact(sock, expectedBytes, deadline=_socketDeadline(sock))

    buffer = bytearray()
    deadline = _socketDeadline(sock)
    while True:
        remainingTime = deadline - time.monotonic()
        if remainingTime <= 0.0:
            if framing == "idle" and buffer:
                return bytes(buffer)
            raise TcpTimeoutError("TCP receive deadline expired before a complete message")
        sock.settimeout(remainingTime)
        delimiterAllowance = 2 if framing == "newline" else 1
        receiveSize = min(4096, maxBytes - len(buffer) + delimiterAllowance)
        try:
            chunk = sock.recv(receiveSize)
        except socket.timeout as exc:
            if framing == "idle" and buffer:
                return bytes(buffer)
            raise TcpTimeoutError("TCP receive timed out before a complete message") from exc
        except OSError as exc:
            raise TcpIoError(f"TCP receive failed: {exc}") from exc
        if not chunk:
            if framing == "idle" and buffer:
                return bytes(buffer)
            raise TcpFrameError("TCP peer closed before a complete framed message")
        buffer.extend(chunk)
        if framing == "newline":
            newlineIndex = buffer.find(b"\n")
            if newlineIndex >= 0:
                if rejectTrailing and len(buffer) != newlineIndex + 1:
                    raise TcpFrameError("unexpected trailing bytes after the response frame")
                message = bytes(buffer[:newlineIndex])
                message = message[:-1] if message.endswith(b"\r") else message
                if len(message) > maxBytes:
                    raise TcpFrameError(f"TCP message exceeds maxBytes={maxBytes}")
                return message
        elif framing == "quoted":
            quotedMessage = _quotedMessage(buffer)
            if quotedMessage is not None:
                if len(quotedMessage) > maxBytes:
                    raise TcpFrameError(f"TCP message exceeds maxBytes={maxBytes}")
                return quotedMessage
        if len(buffer) > maxBytes:
            if (
                framing == "newline"
                and len(buffer) == maxBytes + 1
                and buffer.endswith(b"\r")
            ):
                continue
            raise TcpFrameError(f"TCP message exceeds maxBytes={maxBytes}")


def configureListener(server):
    """Set listener ownership/rebind semantics before bind on either platform."""
    exclusive = getattr(socket, 'SO_EXCLUSIVEADDRUSE', None)
    server.setsockopt(socket.SOL_SOCKET, exclusive if isinstance(exclusive, int) else socket.SO_REUSEADDR, 1)


def listenOnce(
    bindHost: str,
    port: int,
    *,
    acceptTimeoutSec: float,
    ioTimeoutSec: float,
    framing: str,
    maxBytes: int,
    expectedBytes: int,
    ack: bytes | None,
) -> tuple[bytes, tuple[str, int]]:
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        configureListener(server)
        try:
            server.bind((bindHost, port))
            server.listen(1)
            server.settimeout(acceptTimeoutSec)
        except OSError as exc:
            raise TcpConnectError(f"cannot listen on {bindHost}:{port}: {exc}") from exc
        try:
            client, peer = server.accept()
        except socket.timeout as exc:
            raise TcpTimeoutError(
                f"TCP accept timed out on {bindHost}:{port}"
            ) from exc
        except OSError as exc:
            raise TcpConnectError(f"TCP accept failed on {bindHost}:{port}: {exc}") from exc
        with client:
            client.settimeout(ioTimeoutSec)
            message = receiveFramed(
                client,
                framing=framing,
                maxBytes=maxBytes,
                expectedBytes=expectedBytes,
            )
            if ack is not None:
                client.settimeout(ioTimeoutSec)
                sendAll(client, ack)
            return message, (str(peer[0]), int(peer[1]))
    finally:
        _safeClose(server)


def _receiveExact(sock: socket.socket, size: int, *, deadline: float) -> bytes:
    chunks: list[bytes] = []
    remaining = size
    while remaining:
        remainingTime = deadline - time.monotonic()
        if remainingTime <= 0.0:
            raise TcpTimeoutError(
                f"TCP receive deadline expired with {remaining} bytes missing"
            )
        sock.settimeout(remainingTime)
        try:
            chunk = sock.recv(remaining)
        except socket.timeout as exc:
            raise TcpTimeoutError(
                f"TCP receive timed out with {remaining} bytes missing"
            ) from exc
        except OSError as exc:
            raise TcpIoError(f"TCP receive failed: {exc}") from exc
        if not chunk:
            raise TcpFrameError(
                f"TCP peer closed with {remaining} fixed-length bytes missing"
            )
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _socketDeadline(sock: socket.socket) -> float:
    timeout = sock.gettimeout()
    if timeout is None or timeout <= 0.0:
        raise ValueError("TCP socket must have a positive timeout")
    return time.monotonic() + timeout


def _quotedMessage(buffer: bytearray) -> bytes | None:
    start = 0
    while start < len(buffer) and buffer[start] in b" \t\r\n":
        start += 1
    if start == len(buffer):
        return None
    quote = buffer[start]
    if quote not in (ord("'"), ord('"')):
        raise TcpFrameError("quoted framing requires a leading single or double quote")
    end = buffer.find(bytes([quote]), start + 1)
    if end < 0:
        return None
    return bytes(buffer[start : end + 1])


def _safeClose(sock: socket.socket) -> None:
    try:
        sock.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass
    try:
        sock.close()
    except OSError:
        pass
