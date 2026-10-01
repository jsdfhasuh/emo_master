"""Pure/native filesystem checks for the failure-only bounded WAL observer."""
import ctypes
import json
import os
from pathlib import Path, PureWindowsPath
import struct
from types import SimpleNamespace

import pytest

from scripts import r3_sqlite_wal_metadata as probe


def walHeader(*, magic=0x377F0682, version=3007000, pageSize=4096, sequence=7, salts=(11, 13)):
    prefix = struct.pack(">6I", magic, version, pageSize, sequence, *salts)
    words = struct.unpack(("<" if magic == 0x377F0682 else ">") + "6I", prefix)
    first, second = 0, 0
    for index in (0, 2, 4):
        first = (first + words[index] + second) % 2**32
        second = (second + words[index + 1] + first) % 2**32
    return prefix + struct.pack(">2I", first, second)


def files(tmp_path, header=None, slots=2, extra=19):
    database = tmp_path / "owned.sqlite"
    database.write_bytes(b"database body must never be read")
    wal = Path(str(database) + "-wal")
    wal.write_bytes((walHeader() if header is None else header) + b"x" * (slots * (4096 + 24) + extra))
    return database, wal


@pytest.mark.parametrize("magic", [0x377F0682, 0x377F0683])
def testReadsOnlyTwoHeadersAndReturnsAdvisoryPhysicalCapacity(tmp_path, monkeypatch, magic):
    database, wal = files(tmp_path, walHeader(magic=magic))
    reads = []
    backend = probe._WindowsBackend(FakeKernel(header=walHeader(magic=magic), fileSize=wal.stat().st_size))
    original = backend.readHeader

    def read(handle):
        reads.append(handle)
        return original(handle)

    monkeypatch.setattr(backend, "readHeader", read)
    monkeypatch.setattr(probe, "_backend", lambda: backend)
    result = probe.captureWalMetadata(database, tmp_path)
    assert result["status"] == "OBSERVED", result
    assert result["advisory"] is True
    assert result["physical_frame_slot_capacity"] == 2
    assert result["before"] == result["after"] == result["path_after"]
    assert result["header_before"] == result["header_after"]
    assert result["header_before"]["checkpoint_sequence"] == 7
    assert result["header_before"]["salt_1"] == 11
    assert result["header_before"]["salt_2"] == 13
    assert result["header_before"]["checksum_byte_order"] == ("little" if magic == 0x377F0682 else "big")
    assert len(reads) == 2 and reads[0] == reads[1]
    assert result["header_reads"] == 2 and result["header_bytes"] == 64
    assert result["limits"]["header_reads"] == 2
    assert result["started"]["monotonic_ns"] <= result["finished"]["monotonic_ns"]
    payload = json.dumps(result)
    assert str(tmp_path) not in payload
    assert "database body" not in payload
    assert "active_frames" not in result
    assert len(payload) < 8192
    assert wal.stat().st_size == 32 + 2 * (4096 + 24) + 19


@pytest.mark.parametrize("pageSize", [512, 1024, 32768, 65536])
def testWalPageSizeIsFullUnsigned32BitValue(tmp_path, monkeypatch, pageSize):
    database, _ = files(tmp_path, walHeader(pageSize=pageSize), slots=0, extra=0)
    monkeypatch.setattr(probe, "_backend", lambda: probe._WindowsBackend(FakeKernel(header=walHeader(pageSize=pageSize))))
    result = probe.captureWalMetadata(database, tmp_path)
    assert result["status"] == "OBSERVED"
    assert result["header_before"]["page_size"] == pageSize
    assert result["physical_frame_slot_capacity"] == 0


@pytest.mark.parametrize(("header", "status", "reason"), [
    (b"", "UNAVAILABLE", "short_header"),
    (b"x" * 31, "UNAVAILABLE", "short_header"),
    (walHeader(magic=42), "UNAVAILABLE", "invalid_magic"),
    (walHeader(version=3007001), "UNSUPPORTED", "unsupported_wal_version"),
    (walHeader(pageSize=1), "UNAVAILABLE", "invalid_page_size"),
    (walHeader(pageSize=511), "UNAVAILABLE", "invalid_page_size"),
    (walHeader(pageSize=513), "UNAVAILABLE", "invalid_page_size"),
    (walHeader(pageSize=131072), "UNAVAILABLE", "invalid_page_size"),
    (walHeader()[:31] + bytes([walHeader()[31] ^ 1]), "UNAVAILABLE", "invalid_header_checksum"),
])
def testInvalidOrPartialHeadersAreUnavailable(tmp_path, monkeypatch, header, status, reason):
    database, _ = files(tmp_path, header, slots=0, extra=0)
    monkeypatch.setattr(probe, "_backend", lambda: probe._WindowsBackend(FakeKernel(header=header, fileSize=len(header))))
    result = probe.captureWalMetadata(database, tmp_path)
    assert (result["status"], result["reason"]) == (status, reason)
    assert "physical_frame_slot_capacity" not in result
    assert result["header_reads"] <= 2 and result["header_bytes"] <= 64


@pytest.mark.parametrize("magic", [0x377F0682, 0x377F0683])
def testChecksumWordsAlwaysStoredBigEndian(tmp_path, monkeypatch, magic):
    header = walHeader(magic=magic)
    invalid = header[:24] + struct.pack("<2I", *struct.unpack(">2I", header[24:]))
    database, _ = files(tmp_path, invalid)
    monkeypatch.setattr(probe, "_backend", lambda: probe._WindowsBackend(FakeKernel(header=invalid)))
    result = probe.captureWalMetadata(database, tmp_path)
    assert result["reason"] == "invalid_header_checksum"


@pytest.mark.parametrize("kind", ["outside", "relative", "dotdot", "missing_root", "whole_filesystem", "missing_database"])
def testUnownedOrUncertainPathsNeverOpen(tmp_path, monkeypatch, kind):
    database, _ = files(tmp_path)
    root = tmp_path
    if kind == "outside":
        root = tmp_path / "other"
        root.mkdir()
    elif kind == "relative":
        database = "owned.sqlite"
    elif kind == "dotdot":
        database = str(tmp_path) + "/../" + tmp_path.name + "/owned.sqlite"
    elif kind == "missing_root":
        root = tmp_path / "missing"
    elif kind == "whole_filesystem":
        root = Path(tmp_path.anchor)
    elif kind == "missing_database":
        database = tmp_path / "missing.sqlite"
    kernel = FakeKernel()
    monkeypatch.setattr(probe, "_backend", lambda: probe._WindowsBackend(kernel))
    result = probe.captureWalMetadata(database, root)
    assert result["status"] == "UNAVAILABLE"
    assert result["header_reads"] == 0
    assert not kernel.paths


@pytest.mark.parametrize("kind", ["database", "wal", "directory", "root"])
def testSymlinkComponentsRejectedWithoutReading(tmp_path, monkeypatch, kind):
    realRoot = tmp_path / "real"
    realRoot.mkdir()
    database, wal = files(realRoot)
    root = realRoot
    if kind in ("database", "wal"):
        target = database if kind == "database" else wal
        target.rename(str(target) + ".real")
        try:
            target.symlink_to(str(target) + ".real")
        except OSError as error:
            if getattr(error, "winerror", None) == 1314:
                pytest.skip("Windows symlink privilege unavailable")
            raise
    else:
        link = tmp_path / "link"
        try:
            link.symlink_to(realRoot, target_is_directory=True)
        except OSError as error:
            if getattr(error, "winerror", None) == 1314:
                pytest.skip("Windows symlink privilege unavailable")
            raise
        database = link / database.name
        root = link if kind == "root" else tmp_path
    backend = probe._WindowsBackend(FakeKernel())
    monkeypatch.setattr(backend, "readHeader", lambda *args: pytest.fail("symlink read"))
    monkeypatch.setattr(probe, "_backend", lambda: backend)
    result = probe.captureWalMetadata(database, root)
    assert result["status"] == "UNAVAILABLE"
    assert result["reason"] == "symlink_path"
    assert result["header_reads"] == 0


@pytest.mark.parametrize("kind", ["database", "wal"])
def testHardlinkedStoreFilesRejected(tmp_path, monkeypatch, kind):
    database, wal = files(tmp_path)
    os.link(database if kind == "database" else wal, tmp_path / "alias")
    monkeypatch.setattr(probe, "_backend", lambda: probe._WindowsBackend(FakeKernel()))
    result = probe.captureWalMetadata(database, tmp_path)
    assert result["reason"] == "hardlinked_file"
    assert result["header_reads"] == 0


def testMissingWalDoesNotCreateIt(tmp_path, monkeypatch):
    database, wal = files(tmp_path)
    wal.unlink()
    monkeypatch.setattr(probe, "_backend", lambda: probe._WindowsBackend(FakeKernel()))
    result = probe.captureWalMetadata(database, tmp_path)
    assert result["status"] == "UNAVAILABLE" and result["reason"] == "path_missing"
    assert not wal.exists()


class ScriptedBackend:
    name = "test_only"

    def __init__(self, *, action=None, error=None, closeError=False, closeFailures=(), metadataError=None):
        self.opened, self.closed, self.reads = [], [], 0
        self.action, self.error, self.closeError = action, error, closeError
        self.closeFailures, self.metadataError = closeFailures, metadataError

    def open(self, path, *, metadataOnly=False):
        self.opened.append((path, metadataOnly))
        return len(self.opened)

    def metadata(self, handle, path):
        if handle == 4 and self.metadataError:
            raise self.metadataError
        identity = [1, 2 if str(path).endswith("-wal") else 1]
        size = 32
        if self.action == "replace" and handle == 3:
            identity[1] += 1
        if self.action == "database_replace" and handle == 4:
            identity[1] += 1
        if self.action == "resize" and self.reads == 2:
            size += 4096
        return {"identity": identity, "size_bytes": size, "modified_ns": 1}

    def readHeader(self, handle):
        self.reads += 1
        if self.error:
            raise self.error
        if self.action == "delete":
            raise FileNotFoundError("secret/path")
        return walHeader(sequence=8 if self.action == "header" and self.reads == 2 else 7)

    def close(self, handle):
        self.closed.append(handle)
        if self.closeError or handle in self.closeFailures:
            raise InterruptedError("secret/path")


@pytest.mark.parametrize(("action", "status"), [
    ("header", "UNSTABLE"), ("resize", "UNSTABLE"), ("replace", "REPLACED"),
    ("database_replace", "REPLACED"), ("delete", "DELETED"),
])
def testConcurrentChangesAreExplicitAndAllHandlesClose(tmp_path, monkeypatch, action, status):
    database, _ = files(tmp_path)
    backend = ScriptedBackend(action=action)
    monkeypatch.setattr(probe, "_backend", lambda: backend)
    result = probe.captureWalMetadata(database, tmp_path)
    assert result["status"] == status
    assert len(backend.closed) == len(backend.opened)
    assert len(set(backend.closed)) == len(backend.closed)
    assert backend.reads <= 2
    assert "physical_frame_slot_capacity" not in result
    assert "secret/path" not in json.dumps(result)


@pytest.mark.parametrize("error", [OSError("secret/path"), RuntimeError("secret/path"), KeyboardInterrupt("secret/path")])
@pytest.mark.parametrize("closeError", [False, True])
def testReadAndCleanupErrorsCannotEscapeOrLeakPaths(tmp_path, monkeypatch, error, closeError):
    database, _ = files(tmp_path)
    backend = ScriptedBackend(error=error, closeError=closeError)
    monkeypatch.setattr(probe, "_backend", lambda: backend)
    result = probe.captureWalMetadata(database, tmp_path)
    assert result["status"] == "UNAVAILABLE"
    assert len(backend.closed) == len(backend.opened) == 2
    assert "secret/path" not in json.dumps(result)
    assert result["finished"]


class NativeFunction:
    def __init__(self, action):
        self.action = action

    def __call__(self, *args):
        return self.action(*args)


class FakeKernel:
    def __init__(self, *, fail=None, attributes=0, finalPath=None, identity=23, header=None,
                 fileSize=32, closeFailures=()):
        self.calls, self.closed, self.paths = [], [], {}
        self.fail, self.attributes, self.finalPath, self.identity = fail, attributes, finalPath, identity
        self.header, self.fileSize, self.closeFailures = walHeader() if header is None else header, fileSize, closeFailures
        for name in ("CreateFileW", "GetFileType", "GetFileInformationByHandle", "GetFileInformationByHandleEx",
                     "GetFinalPathNameByHandleW", "SetFilePointerEx", "ReadFile", "CloseHandle"):
            setattr(self, name, NativeFunction(lambda *args, name=name: self.call(name, *args)))

    def call(self, name, *args):
        self.calls.append((name, args))
        if name == self.fail:
            return probe._WindowsBackend.INVALID_HANDLE if name == "CreateFileW" else 0
        if name == "CreateFileW":
            handle = len(self.paths) + 1
            self.paths[handle] = args[0]
            return handle
        if name == "GetFileType":
            return 1
        if name == "GetFileInformationByHandle":
            info = ctypes.cast(args[1], ctypes.POINTER(probe._FileInformation)).contents
            info.attributes, info.links, info.sizeLow = self.attributes, 1, self.fileSize
            return 1
        if name == "GetFileInformationByHandleEx":
            info = ctypes.cast(args[2], ctypes.POINTER(probe._FileIdInformation)).contents
            info.volume, info.identifier[0] = 9, self.identity
            return 1
        if name == "GetFinalPathNameByHandleW":
            value = self.finalPath or "\\\\?\\" + self.paths[args[0]]
            args[1].value = value
            return len(value)
        if name == "ReadFile":
            assert args[2] == 32
            ctypes.memmove(args[1], self.header[:32], min(len(self.header), 32))
            ctypes.cast(args[3], ctypes.POINTER(ctypes.c_uint32)).contents.value = min(len(self.header), 32)
            return 1
        if name == "CloseHandle":
            self.closed.append(args[0])
            if args[0] in self.closeFailures:
                return 0
        return 1


def testWindowsUsesReadOnlyExistingSharedHandlesAndHeaderSizedReads():
    kernel = FakeKernel()
    backend = probe._WindowsBackend(kernel)
    path = PureWindowsPath(r"C:\pytest\owned.sqlite-wal")
    handle = backend.open(path)
    try:
        metadata = backend.metadata(handle, path)
        assert metadata["identity"] == [9, 23]
        assert metadata["size_bytes"] == 32
        assert backend.readHeader(handle) == walHeader()
        assert backend.readHeader(handle) == walHeader()
    finally:
        backend.close(handle)
    _, create = kernel.calls[0]
    assert create[1:] == (0x80000000, 0x7, None, 3, 0x00200000, None)
    assert [row[1][2] for row in kernel.calls if row[0] == "ReadFile"] == [32, 32]
    assert kernel.closed == [handle]
    metadataHandle = backend.open(path, metadataOnly=True)
    backend.close(metadataHandle)
    create = [row[1] for row in kernel.calls if row[0] == "CreateFileW"][-1]
    assert create[1] == 0x80 and create[2] == 7 and create[4] == 3


@pytest.mark.parametrize(("settings", "status", "reason"), [
    ({"attributes": 0x400}, "UNAVAILABLE", "unsafe_open_file"),
    ({"attributes": 0x10}, "UNAVAILABLE", "unsafe_open_file"),
    ({"identity": 0}, "UNSUPPORTED", "unknown_file_identity"),
    ({"fail": "GetFileInformationByHandleEx"}, "UNSUPPORTED", "unknown_file_identity"),
    ({"fail": "GetFinalPathNameByHandleW"}, "UNSUPPORTED", "unknown_final_path"),
    ({"finalPath": r"\\?\C:\outside\owned.sqlite-wal"}, "REPLACED", "path_identity_changed"),
])
def testWindowsRejectsReparseUnknownIdentityAndChangedFinalPath(tmp_path, monkeypatch, settings, status, reason):
    database, _ = files(tmp_path)
    kernel = FakeKernel(**settings)
    monkeypatch.setattr(probe, "_backend", lambda: probe._WindowsBackend(kernel))
    result = probe.captureWalMetadata(database, tmp_path)
    assert (result["status"], result["reason"]) == (status, reason)
    assert result["header_reads"] == 0
    assert len(kernel.closed) == len(kernel.paths)


@pytest.mark.parametrize("failure", ["CreateFileW", "GetFileInformationByHandle", "SetFilePointerEx", "ReadFile"])
def testWindowsNativeFailuresCloseEverySuccessfullyOpenedHandle(tmp_path, monkeypatch, failure):
    database, _ = files(tmp_path)
    kernel = FakeKernel(fail=failure)
    monkeypatch.setattr(probe, "_backend", lambda: probe._WindowsBackend(kernel))
    result = probe.captureWalMetadata(database, tmp_path)
    assert result["status"] == "UNAVAILABLE"
    assert len(kernel.closed) == len(kernel.paths)
    assert result["header_reads"] <= 2


def testUnsupportedPlatformAndClockErrorsAreSafe(tmp_path, monkeypatch):
    database, _ = files(tmp_path)
    monkeypatch.setattr(probe, "_backend", lambda: (_ for _ in ()).throw(
        probe._Unavailable("unsupported_platform", "UNSUPPORTED")))
    result = probe.captureWalMetadata(database, tmp_path)
    assert result["status"] == "UNSUPPORTED"
    monkeypatch.setattr(probe, "_reading", lambda: (_ for _ in ()).throw(ValueError("private")))
    result = probe.captureWalMetadata(database, tmp_path)
    assert result["status"] == "UNAVAILABLE"
    assert result["error_types"] == ["ValueError", "ValueError"]
    assert "private" not in json.dumps(result)


@pytest.mark.skipif(os.name == "nt", reason="Non-Windows platform rejection")
def testUnsupportedPlatformNeverInspectsOrOpensDatabaseOrWal(tmp_path, monkeypatch):
    database, _ = files(tmp_path)
    def forbidden(*args, **kwargs):
        raise AssertionError("unsupported platform touched filesystem")
    monkeypatch.setattr(os, "open", forbidden)
    monkeypatch.setattr(os, "pread", forbidden)
    monkeypatch.setattr(probe, "_safePaths", forbidden)
    monkeypatch.setattr(probe, "_WindowsBackend", forbidden)
    result = probe.captureWalMetadata(database, tmp_path)
    assert result["status"] == "UNSUPPORTED"
    assert result["reason"] == "unsupported_platform"
    assert result["header_reads"] == 0
    assert result["cleanup"] == {"opened": 0, "close_attempted": 0, "close_failures": 0,
                                 "retirement_confirmed": True, "error_types": []}


@pytest.mark.parametrize(("action", "status"), [("delete", "DELETED"), ("replace", "REPLACED"), ("grow", "UNSTABLE")])
@pytest.mark.skipif(os.name != "nt", reason="Actual native capture is Windows-only")
def testNativeWalChangesAreDetected(tmp_path, monkeypatch, action, status):
    database, wal = files(tmp_path)
    backend = probe._backend()
    original = backend.readHeader
    reads = []

    def read(handle):
        header = original(handle)
        reads.append(handle)
        if len(reads) == 1:
            if action == "delete":
                wal.unlink()
            elif action == "replace":
                replacement = tmp_path / "replacement"
                replacement.write_bytes(walHeader())
                replacement.replace(wal)
            else:
                with wal.open("ab") as stream:
                    stream.write(b"growth")
        return header

    monkeypatch.setattr(backend, "readHeader", read)
    monkeypatch.setattr(probe, "_backend", lambda: backend)
    result = probe.captureWalMetadata(database, tmp_path)
    assert result["status"] == status, result
    assert "physical_frame_slot_capacity" not in result


def testUnknownWindowsReparseMetadataIsUnsupported(tmp_path, monkeypatch):
    # Swap this module's OS reference without changing pathlib's host platform.
    monkeypatch.setattr(probe, "os", SimpleNamespace(name="nt"))
    fake = SimpleNamespace(st_mode=0o040755)
    monkeypatch.setattr(Path, "lstat", lambda path: fake)
    with pytest.raises(probe._Unavailable) as raised:
        probe._checkComponents(tmp_path, directory=True)
    assert raised.value.status == "UNSUPPORTED"
    assert raised.value.reason == "unknown_reparse_status"


@pytest.mark.parametrize("closeFailures", [{4}, {2}, {1, 2, 3, 4}])
@pytest.mark.parametrize("metadataError", [None, ValueError("secret metadata path")])
def testAmbiguousClosesAreNeverRetriedAndPreserveObservationError(tmp_path, monkeypatch, closeFailures, metadataError):
    database, _ = files(tmp_path)
    backend = ScriptedBackend(closeFailures=closeFailures, metadataError=metadataError)
    monkeypatch.setattr(probe, "_backend", lambda: backend)
    result = probe.captureWalMetadata(database, tmp_path)
    assert result["status"] == "UNAVAILABLE"
    assert backend.closed == [4, 3, 2, 1]
    assert result["cleanup"] == {"opened": 4, "close_attempted": 4,
        "close_failures": len(closeFailures), "retirement_confirmed": False,
        "error_types": ["InterruptedError"] * len(closeFailures)}
    assert result["reason"] == ("capture_error" if metadataError else "cleanup_unconfirmed")
    assert result["error_types"] == (["ValueError"] if metadataError else [])
    assert "secret" not in json.dumps(result)


@pytest.mark.parametrize("closeFailures", [{4}, {2}, {1, 2, 3, 4}])
def testWindowsFailedCloseIsUnconfirmedAndDoesNotPreventOtherCloses(tmp_path, monkeypatch, closeFailures):
    database, _ = files(tmp_path)
    kernel = FakeKernel(closeFailures=closeFailures)
    monkeypatch.setattr(probe, "_backend", lambda: probe._WindowsBackend(kernel))
    result = probe.captureWalMetadata(database, tmp_path)
    assert kernel.closed == [4, 3, 2, 1]
    assert result["status"] == "UNAVAILABLE"
    assert result["reason"] == "cleanup_unconfirmed"
    assert result["cleanup"]["retirement_confirmed"] is False
    assert result["cleanup"]["opened"] == result["cleanup"]["close_attempted"] == 4
    assert result["cleanup"]["close_failures"] == len(closeFailures)
