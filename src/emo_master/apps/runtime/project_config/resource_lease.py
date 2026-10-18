"""OS-held generation fence, acquired before a child can consume prepared paths."""
from contextlib import contextmanager
import os
from pathlib import Path


class ResourceLease:
    def __init__(self, path, *, create=False, retiring=False):
        self.path = Path(path)
        self.handle = None
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.CreateFileW.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE)
            kernel.CreateFileW.restype = wintypes.HANDLE
            kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
            kernel.CloseHandle.restype = wintypes.BOOL
            self.kernel = kernel
            kernel.DeleteFileW.argtypes = (wintypes.LPCWSTR,)
            kernel.DeleteFileW.restype = wintypes.BOOL
            # The retiring handle permits deletion, but denies new readers.
            # Tombstone only after the caller commits durable retirement.
            handle = kernel.CreateFileW(str(self.path), 0x80000000 | (0x10000 if retiring else 0),
                4 if retiring else 1, None, 4 if create else 3, 0x80, None)
            if handle == ctypes.c_void_p(-1).value:
                raise ctypes.WinError(ctypes.get_last_error())
            self.handle = handle
        else:
            import fcntl
            fd = os.open(self.path, os.O_RDONLY | (os.O_CREAT if create else 0), 0o600)
            try:
                fcntl.flock(fd, (fcntl.LOCK_EX if retiring else fcntl.LOCK_SH) | fcntl.LOCK_NB)
                # A reader opened before unlink must not become a late consumer.
                if not os.fstat(fd).st_nlink or os.fstat(fd).st_ino != self.path.stat().st_ino:
                    raise OSError("resource generation has retired")
                self.handle = fd
            except BaseException:
                os.close(fd)
                raise

    def seal(self):
        if os.name == 'nt':
            import ctypes
            if not self.kernel.DeleteFileW(str(self.path)):
                raise ctypes.WinError(ctypes.get_last_error())
        else:
            self.path.unlink()

    def close(self):
        if self.handle is not None:
            if os.name == "nt":
                self.kernel.CloseHandle(self.handle)
            else:
                os.close(self.handle)
            self.handle = None

    def __del__(self):
        self.close()


@contextmanager
def retirementLease(path):
    try:
        lease = ResourceLease(path, retiring=True)
    except OSError:
        yield False
        return
    try:
        yield True
        lease.seal()
    finally:
        lease.close()


def runWithResourceLease(path, target, args):
    lease = ResourceLease(path)
    try:
        target(*args)
    finally:
        lease.close()
