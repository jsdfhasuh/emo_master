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
        exclusiveAddressUse = getattr(socket, "SO_EXCLUSIVEADDRUSE", None)
        if isinstance(exclusiveAddressUse, int):
            server.setsockopt(socket.SOL_SOCKET, exclusiveAddressUse, 1)
        else:
            server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
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
