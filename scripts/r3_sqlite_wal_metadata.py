"""Bounded, advisory WAL-header evidence, called ONLY after a pytest failure.

The caller supplies the exact owned test-store path and its resolved pytest
temporary root; this module neither discovers stores nor schedules observations.
No SQLite, SHM, mmap, locks, writes, frame contents or background work are used.
Two matching reads are not an atomic snapshot or proof of a file's lifetime.

Format: https://www.sqlite.org/fileformat2.html#wal_format
Sharing: https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-createfilew
"""
from contextlib import ExitStack
import ctypes
import ntpath
import os
from pathlib import Path
import stat
import struct
import time


LIMITS = {"header_reads": 2, "bytes_per_header_read": 32,
          "total_header_bytes": 64, "path_characters": 4096, "path_components": 64}
LIMITATIONS = (
    "Independent read-only filesystem observations are advisory, not atomic. "
    "Matching identities and headers cannot establish lifetime non-recreation. "
    "Physical frame-slot capacity is not active frames, committed transactions, "
    "historical growth or checkpoint completion. No frame contents are inspected. "
    "Native capture is Windows-only; other platforms are unsupported before filesystem inspection. "
    "Fixed operation limits do not bound filesystem-call latency."
)


class _Unavailable(Exception):
    def __init__(self, reason, status="UNAVAILABLE"):
        self.reason, self.status = reason, status


class _Cleanup:
    """Account for every owned handle without retrying ambiguous closes."""
    def __init__(self):
        self.report = {"opened": 0, "close_attempted": 0, "close_failures": 0,
                       "retirement_confirmed": True, "error_types": []}

    def own(self, handle, closer):
        self.report["opened"] += 1
        attempted = False

        def close():
            nonlocal attempted
            if attempted:
                return False  # Never retry an ambiguous numeric descriptor.
            attempted = True
            self.report["close_attempted"] += 1
            try:
                closer(handle)
            except BaseException as error:
                self.report["close_failures"] += 1
                self.report["retirement_confirmed"] = False
                errors = self.report["error_types"]
                if len(errors) < 8:
                    errors.append(type(error).__name__[:80])
                return False
            return True

        return close


def _reading():
    return {"monotonic_ns": time.monotonic_ns(), "perf_counter_ns": time.perf_counter_ns()}


def _normalPath(value):
    value = os.fspath(value)
    if not isinstance(value, str) or not value or len(value) > LIMITS["path_characters"]:
        raise _Unavailable("invalid_path")
    if "\0" in value or not os.path.isabs(value) or os.path.normpath(value) != value:
        raise _Unavailable("non_normal_absolute_path")
    path = Path(value)
    if len(path.parts) > LIMITS["path_components"]:
        raise _Unavailable("path_component_limit")
    if os.name == "nt":
        # Reject UNC/device paths, streams and Win32 spelling ambiguities.
        if len(path.drive) != 2 or path.drive[1] != ":" or not path.drive[0].isalpha():
            raise _Unavailable("unsupported_windows_path", "UNSUPPORTED")
        if any(":" in part or part.rstrip(" .") != part for part in path.parts[1:]):
            raise _Unavailable("ambiguous_windows_path")
    return path


def _checkComponents(path, *, directory=False):
    """Inspect every component without following links, including root parents."""
    for component in reversed((path,) + tuple(path.parents)):
        info = component.lstat()
        if stat.S_ISLNK(info.st_mode):
            raise _Unavailable("symlink_path")
        if os.name == "nt":
            attributes = getattr(info, "st_file_attributes", None)
            if attributes is None:
                raise _Unavailable("unknown_reparse_status", "UNSUPPORTED")
            if attributes & 0x400:
                raise _Unavailable("reparse_path")
        leaf = component == path
        if not (stat.S_ISDIR(info.st_mode) if not leaf or directory else stat.S_ISREG(info.st_mode)):
            raise _Unavailable("unexpected_file_type")
        if leaf and not directory and info.st_nlink != 1:
            # Reject aliases to other stores, even inside an otherwise safe root.
            raise _Unavailable("hardlinked_file")
    if path.resolve(strict=True) != path:
        raise _Unavailable("unresolved_path")


def _safePaths(dbPath, allowedRoot):
    database, root = _normalPath(dbPath), _normalPath(allowedRoot)
    if root == Path(root.anchor) or database == root or root not in database.parents:
        raise _Unavailable("outside_owned_root")
    wal = _normalPath(str(database) + "-wal")
    _checkComponents(root, directory=True)
    _checkComponents(database)
    return database, wal


def _header(raw):
    if len(raw) != 32:
        raise _Unavailable("short_header")
    magic, version, pageSize, sequence, salt1, salt2, stored0, stored1 = struct.unpack(">8I", raw)
    if magic not in (0x377F0682, 0x377F0683):
        raise _Unavailable("invalid_magic")
    if version != 3007000:
        raise _Unavailable("unsupported_wal_version", "UNSUPPORTED")
    if pageSize < 512 or pageSize > 65536 or pageSize & (pageSize - 1):
        raise _Unavailable("invalid_page_size")
    words = struct.unpack(("<" if magic == 0x377F0682 else ">") + "6I", raw[:24])
    checksum0 = checksum1 = 0
    for index in range(0, 6, 2):
        checksum0 = (checksum0 + words[index] + checksum1) & 0xFFFFFFFF
        checksum1 = (checksum1 + words[index + 1] + checksum0) & 0xFFFFFFFF
    if (checksum0, checksum1) != (stored0, stored1):
        raise _Unavailable("invalid_header_checksum")
    return {"magic": magic, "version": version, "page_size": pageSize,
            "checkpoint_sequence": sequence, "salt_1": salt1, "salt_2": salt2,
            "checksum_byte_order": "little" if magic == 0x377F0682 else "big",
            "checksum_valid": True}


class _FileTime(ctypes.Structure):
    _fields_ = [("low", ctypes.c_uint32), ("high", ctypes.c_uint32)]


class _FileInformation(ctypes.Structure):
    _fields_ = [("attributes", ctypes.c_uint32), ("creation", _FileTime),
                ("access", _FileTime), ("write", _FileTime),
                ("volume", ctypes.c_uint32), ("sizeHigh", ctypes.c_uint32),
                ("sizeLow", ctypes.c_uint32), ("links", ctypes.c_uint32),
                ("indexHigh", ctypes.c_uint32), ("indexLow", ctypes.c_uint32)]


class _FileIdInformation(ctypes.Structure):
    _fields_ = [("volume", ctypes.c_uint64), ("identifier", ctypes.c_ubyte * 16)]


class _WindowsBackend:
    """Native calls are injectable; ordinary Python open() is never used on Windows."""
    name = "windows_read_only_shared"
    GENERIC_READ = 0x80000000
    FILE_READ_ATTRIBUTES = 0x80
    SHARE_ALL = 0x1 | 0x2 | 0x4
    OPEN_EXISTING = 3
    OPEN_REPARSE_POINT = 0x00200000
    INVALID_HANDLE = ctypes.c_void_p(-1).value

    def __init__(self, kernel=None):
        self.kernel = kernel if kernel is not None else ctypes.WinDLL("kernel32", use_last_error=True)
        signatures = {
            "CreateFileW": ([ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p,
                             ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p], ctypes.c_void_p),
            "GetFileType": ([ctypes.c_void_p], ctypes.c_uint32),
            "GetFileInformationByHandle": ([ctypes.c_void_p, ctypes.POINTER(_FileInformation)], ctypes.c_int),
            "GetFileInformationByHandleEx": ([ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p,
                                               ctypes.c_uint32], ctypes.c_int),
            "GetFinalPathNameByHandleW": ([ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_uint32,
                                           ctypes.c_uint32], ctypes.c_uint32),
            "SetFilePointerEx": ([ctypes.c_void_p, ctypes.c_int64, ctypes.c_void_p, ctypes.c_uint32], ctypes.c_int),
            "ReadFile": ([ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32,
                          ctypes.POINTER(ctypes.c_uint32), ctypes.c_void_p], ctypes.c_int),
            "CloseHandle": ([ctypes.c_void_p], ctypes.c_int),
        }
        for name, (arguments, result) in signatures.items():
            function = getattr(self.kernel, name)
            function.argtypes, function.restype = arguments, result

    def _failed(self):
        error = ctypes.get_last_error() if hasattr(ctypes, "get_last_error") else 0
        if error in (2, 3):
            raise FileNotFoundError()
        if error == 5:
            raise PermissionError()
        raise OSError()  # Deliberately do not retain native text or a filename.

    def open(self, path, *, metadataOnly=False):
        handle = self.kernel.CreateFileW(str(path), self.FILE_READ_ATTRIBUTES if metadataOnly else self.GENERIC_READ,
                                         self.SHARE_ALL, None, self.OPEN_EXISTING, self.OPEN_REPARSE_POINT, None)
        if handle in (None, self.INVALID_HANDLE):
            self._failed()
        return handle

    def metadata(self, handle, path):
        if self.kernel.GetFileType(handle) != 1:  # FILE_TYPE_DISK
            raise _Unavailable("unsupported_file_type", "UNSUPPORTED")
        info, identity = _FileInformation(), _FileIdInformation()
        if not self.kernel.GetFileInformationByHandle(handle, ctypes.byref(info)):
            self._failed()
        if info.attributes & (0x400 | 0x10) or info.links != 1:
            raise _Unavailable("unsafe_open_file")
        if not self.kernel.GetFileInformationByHandleEx(handle, 18, ctypes.byref(identity), ctypes.sizeof(identity)):
            raise _Unavailable("unknown_file_identity", "UNSUPPORTED")
        fileId = int.from_bytes(bytes(identity.identifier), "little")
        if not fileId:
            raise _Unavailable("unknown_file_identity", "UNSUPPORTED")
        buffer = ctypes.create_unicode_buffer(LIMITS["path_characters"] + 1)
        length = self.kernel.GetFinalPathNameByHandleW(handle, buffer, len(buffer), 0)
        if not length or length >= len(buffer):
            raise _Unavailable("unknown_final_path", "UNSUPPORTED")
        final = buffer.value
        if final.startswith("\\\\?\\"):
            final = final[4:]
        if ntpath.normcase(final) != ntpath.normcase(str(path)):
            raise _Unavailable("path_identity_changed", "REPLACED")
        return {"identity": [int(identity.volume), fileId],
                "size_bytes": (int(info.sizeHigh) << 32) | int(info.sizeLow),
                "modified_ns": (((int(info.write.high) << 32) | int(info.write.low)) - 116444736000000000) * 100}

    def readHeader(self, handle):
        if not self.kernel.SetFilePointerEx(handle, 0, None, 0):
            self._failed()
        buffer, received = ctypes.create_string_buffer(32), ctypes.c_uint32()
        if not self.kernel.ReadFile(handle, buffer, 32, ctypes.byref(received), None):
            self._failed()
        if received.value > 32:
            raise _Unavailable("invalid_read_length")
        return buffer.raw[:received.value]

    def close(self, handle):
        if not self.kernel.CloseHandle(handle):
            self._failed()


def _backend():
    if os.name == "nt":
        return _WindowsBackend()
    raise _Unavailable("unsupported_platform", "UNSUPPORTED")


def captureWalMetadata(dbPath, allowedRoot):
    """Observe only the supplied test WAL. Never raise or return raw header/path data.

    Invocation belongs strictly at an already-established pytest failure boundary.
    The allowed root must be the caller's resolved owned temporary directory, not
    a repository, user data directory, current directory or guessed fallback.
    """
    cleanup = _Cleanup()
    result = {"schema": 1, "status": "UNAVAILABLE", "advisory": True,
              "limits": dict(LIMITS), "limitations": LIMITATIONS,
              "header_reads": 0, "header_bytes": 0, "error_types": [], "cleanup": cleanup.report}
    try:
        result["started"] = _reading()
        backend = _backend()
        database, wal = _safePaths(dbPath, allowedRoot)
        result["backend"] = backend.name
        with ExitStack() as handles:
            def opened(path, metadataOnly=False):
                _checkComponents(path)
                handle = backend.open(path, metadataOnly=metadataOnly)
                handles.callback(cleanup.own(handle, backend.close))
                _checkComponents(path)
                return handle

            def databaseMetadata():
                _checkComponents(database)
                result["database_identity_method"] = "shared_handle"
                return backend.metadata(opened(database, True), database)

            databaseBefore = databaseMetadata()
            result["database_identity"] = databaseBefore["identity"]
            walHandle = opened(wal)
            result["before"] = backend.metadata(walHandle, wal)
            headers = []
            for index in range(2):
                result["header_reads"] += 1
                raw = backend.readHeader(walHandle)
                result["header_bytes"] += len(raw)
                headers.append(raw)
                result["header_before" if index == 0 else "header_after"] = _header(raw)
            result["after"] = backend.metadata(walHandle, wal)
            # Keep the first handle live while resolving the name again; do not
            # read the reopened file. Identity is still only an observation.
            reopened = opened(wal, True)
            result["path_after"] = backend.metadata(reopened, wal)
            databaseAfter = databaseMetadata()
            if databaseBefore["identity"] != databaseAfter["identity"]:
                raise _Unavailable("database_identity_changed", "REPLACED")
            if any(result["before"]["identity"] != result[key]["identity"] for key in ("after", "path_after")):
                raise _Unavailable("wal_identity_changed", "REPLACED")
            if result["before"] != result["after"] or result["after"] != result["path_after"] or headers[0] != headers[1]:
                raise _Unavailable("wal_changed_during_capture", "UNSTABLE")
            size = result["after"]["size_bytes"]
            if size < 32:
                raise _Unavailable("short_file")
            result["physical_frame_slot_capacity"] = (size - 32) // (result["header_after"]["page_size"] + 24)
            result["status"] = "OBSERVED"
    except _Unavailable as error:
        result.update(status=error.status, reason=error.reason)
    except FileNotFoundError as error:
        result.update(status="DELETED" if "before" in result else "UNAVAILABLE", reason="path_missing")
        result["error_types"].append(type(error).__name__[:80])
    except BaseException as error:
        result.update(status="UNAVAILABLE", reason="capture_error")
        result["error_types"].append(type(error).__name__[:80])
    finally:
        if not cleanup.report["retirement_confirmed"] and result["status"] == "OBSERVED":
            result.update(status="UNAVAILABLE", reason="cleanup_unconfirmed")
            result.pop("physical_frame_slot_capacity", None)
        try:
            result["finished"] = _reading()
        except BaseException as error:
            result.update(status="UNAVAILABLE", reason="clock_error")
            result["error_types"].append(type(error).__name__[:80])
    return result
