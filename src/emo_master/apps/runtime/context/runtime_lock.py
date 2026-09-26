from __future__ import annotations

import os
from pathlib import Path
import threading
from typing import Any, BinaryIO

fcntl: Any = None
try:
    import fcntl as _fcntl

    fcntl = _fcntl
except ImportError:  # pragma: no cover - only available on POSIX
    fcntl = None

msvcrt: Any = None
try:
    import msvcrt as _msvcrt

    msvcrt = _msvcrt
except ImportError:  # pragma: no cover - only available on Windows
    msvcrt = None


class RuntimeDataLock:
    """Own one runtime data directory across processes and reentrantly in-process."""

    _registryLock = threading.RLock()
    _held: dict[str, tuple[BinaryIO, int]] = {}

    def __init__(self, path: Path) -> None:
        self.path = path
        self._key = os.path.normcase(str(path.resolve()))
        self._acquired = False
        self._primary = False

    def acquire(self) -> bool:
        with self._registryLock:
            if self._acquired:
                return self._primary
            held = self._held.get(self._key)
            if held is not None:
                handle, count = held
                self._held[self._key] = (handle, count + 1)
                self._acquired = True
                return False

            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.touch(exist_ok=True)
            handle = self.path.open("r+b")
            try:
                _lockHandle(handle)
            except OSError as err:
                handle.close()
                raise RuntimeError(
                    f"runtime data directory is already in use: {self.path.parent}"
                ) from err
            self._held[self._key] = (handle, 1)
            self._acquired = True
            self._primary = True
            return True

    def release(self) -> None:
        with self._registryLock:
            if not self._acquired:
                return
            held = self._held.get(self._key)
            if held is None:
                self._acquired = False
                self._primary = False
                return
            handle, count = held
            if count > 1:
                self._held[self._key] = (handle, count - 1)
            else:
                try:
                    _unlockHandle(handle)
                finally:
                    handle.close()
                    self._held.pop(self._key, None)
            self._acquired = False
            self._primary = False


def _lockHandle(handle: BinaryIO) -> None:
    handle.seek(0, os.SEEK_END)
    if handle.tell() == 0:
        handle.write(b"\0")
        handle.flush()
    handle.seek(0)
    if msvcrt is not None:
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        return
    if fcntl is not None:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return
    raise OSError("no supported file locking implementation")


def _unlockHandle(handle: BinaryIO) -> None:
    handle.seek(0)
    if msvcrt is not None:
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        return
    if fcntl is not None:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
