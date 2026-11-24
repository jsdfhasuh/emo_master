import os
import select
import sys


def workerHasExited(pid: int | None) -> bool:
    """For old workspaces without a lease, only a proven exit permits cleanup."""
    if pid is None or pid <= 0:
        return False
    if sys.platform == "win32":
        import _winapi
        try:
            handle = _winapi.OpenProcess(_winapi.SYNCHRONIZE, False, pid)
        except OSError as error:
            return error.winerror == 87  # ERROR_INVALID_PARAMETER for a nonexistent PID.
        try:
            return _winapi.WaitForSingleObject(handle, 0) == _winapi.WAIT_OBJECT_0
        finally:
            _winapi.CloseHandle(handle)
    if sys.platform == "linux" and hasattr(os, "pidfd_open"):
        try:
            fd = os.pidfd_open(pid)
        except ProcessLookupError:
            return True
        except OSError:
            pass  # Older kernels can still check PID existence below.
        else:
            try:
                # An exited orphan may still have a PID until its adopter reaps it.
                poll = select.poll()
                poll.register(fd, select.POLLIN)
                return bool(poll.poll(0))
            finally:
                os.close(fd)
    try:
        os.kill(pid, 0)  # Existence check on POSIX. A reused PID stays protected.
    except ProcessLookupError:
        return True
    except PermissionError:
        pass
    return False
