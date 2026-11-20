import multiprocessing
import os
from pathlib import Path
from shutil import copytree
from uuid import uuid4

import pytest

from emo_master.apps.runtime.context.sqlite_store import SqliteStore
from emo_master.apps.runtime.project_config.resource_journal import ResourceJournal
from tests.runtime import test_prepared_resources as prepared

runtime = prepared.runtime
imagePath = prepared.imagePath


def journal(tmp_path, instance=None, *, consumerLeases=False):
    return ResourceJournal(SqliteStore(tmp_path / "runtime.db"), tmp_path / "configuration-resources",
                           instance or uuid4().hex, consumerLeases=consumerLeases)


def reserve(owner, name="model.bin"):
    path = owner.root / uuid4().hex / name
    path.parent.mkdir(parents=True)
    owner.reserve(path)
    path.write_bytes(b"owned input")
    return path


def _crashPreparation(root):
    owner = journal(Path(root), "crashed-preparation")
    reserve(owner, "unfinished.pending")
    os._exit(23)


def testActualPreparationProcessDeathRecoversOnlyJournaledFiles(tmp_path):
    unknown = tmp_path / "configuration-resources" / "unknown" / "keep.bin"
    unknown.parent.mkdir(parents=True)
    unknown.write_bytes(b"not ours")
    child = multiprocessing.get_context("spawn").Process(target=_crashPreparation, args=(str(tmp_path),))
    child.start()
    child.join(15)
    if child.is_alive():
        child.terminate()
        child.join(5)
        pytest.fail("synthetic preparation child did not exit")
    assert child.exitcode == 23
    child.close()
    assert list(unknown.parent.parent.rglob("*.pending"))
    owner = journal(tmp_path)
    owner.recover()
    assert not list(owner.root.rglob("*.pending"))
    assert unknown.read_bytes() == b"not ours"


def testExposedFilesRequireActualRetirementNotJustRestart(tmp_path):
    old = journal(tmp_path)
    path = reserve(old)
    old.expose([path])
    current = journal(tmp_path)
    current.recover()
    assert path.exists()
    assert current.retainedAfterCrash() == [str(path)]
    old.retire()
    current.recover()
    assert not path.exists()
    assert current.retainedAfterCrash() == []


def testFailedUnlinkKeepsOwnershipForRetry(tmp_path, monkeypatch):
    old = journal(tmp_path)
    path = reserve(old)
    current = journal(tmp_path)
    unlink = Path.unlink
    def fail(target, *args, **kwargs):
        if target == path:
            raise PermissionError("still mapped")
        return unlink(target, *args, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(Path, "unlink", fail)
        with pytest.raises(PermissionError, match="still mapped"):
            current.recover()
    assert path.exists()
    current.recover()
    assert not path.exists()


def testUntrustedJournalPathsCannotEscapeCache(tmp_path):
    owner = journal(tmp_path)
    outside = tmp_path / "unrelated.bin"
    outside.write_bytes(b"keep")
    with pytest.raises(ValueError, match="escaped"):
        owner.reserve(outside)
    with pytest.raises(ValueError, match="escaped"):
        owner.reserve(owner.root / "batch" / ".." / ".." / "unrelated.bin")
    assert outside.read_bytes() == b"keep"


def testCopiedJournalReclaimsRetiredFilesOnlyInNewCache(tmp_path):
    source = tmp_path / "source"
    previous = journal(source, consumerLeases=True)
    path = reserve(previous)
    previous.expose([path])
    previous.retire()
    destination = tmp_path / "destination"
    copytree(source, destination)
    current = journal(destination)
    moved = current.root / path.relative_to(previous.root)
    unknown = moved.with_name("keep.txt")
    unknown.write_text("unknown")
    current.recover()
    assert not moved.exists()
    assert path.read_bytes() == b"owned input"
    assert unknown.read_text() == "unknown"
    with current.sqlite.connection() as connection:
        assert connection.execute("SELECT instanceId FROM configurationResourceOwners").fetchall() == [(current.instanceId,)]
        assert connection.execute("SELECT path FROM configurationResourceFiles").fetchall() == []
    current.recover()


@pytest.mark.parametrize("unrelatedRoot", [False, True])
def testCopiedJournalCannotUseUnrelatedFileRecordAsOwnership(tmp_path, unrelatedRoot):
    source = tmp_path / "source"
    previous = journal(source, consumerLeases=True)
    path = reserve(previous)
    previous.retire()
    destination = tmp_path / "destination"
    copytree(source, destination)
    current = journal(destination)
    if unrelatedRoot:
        wrong = tmp_path / "other" / previous.root.name / path.relative_to(previous.root)
        target = current.root / path.relative_to(previous.root)
    else:
        wrong = previous.root / "unmanaged" / path.name
        target = current.root / "unmanaged" / path.name
        target.parent.mkdir()
        target.write_bytes(b"unowned")
    content = target.read_bytes()
    with current.sqlite.connection() as connection:
        connection.execute("UPDATE configurationResourceFiles SET path=?", (str(wrong),))
    with pytest.raises(ValueError, match="escaped"):
        current.recover()
    assert target.read_bytes() == content


@pytest.mark.parametrize("missingMarker", [False, True])
def testCopiedJournalRetainsExposedResourceUntilLocalLeaseRetires(tmp_path, missingMarker):
    from emo_master.apps.runtime.project_config.resource_lease import ResourceLease

    source = tmp_path / "source"
    previous = journal(source, consumerLeases=True)
    path = reserve(previous)
    previous.expose([path])
    previous.lease.close()  # Unclean exit leaves a marker and no retirement record.
    destination = tmp_path / "destination"
    copytree(source, destination)
    current = journal(destination)
    moved = current.root / path.relative_to(previous.root)
    marker = current.root / Path(previous.leasePath).relative_to(previous.root)
    if missingMarker:
        marker.unlink()
    lease = None if missingMarker else ResourceLease(marker)
    try:
        current.recover()
        assert current.retainedAfterCrash() == [str(moved)]
        assert moved.exists()
    finally:
        if lease:
            lease.close()
    if not missingMarker:
        current.recover()
        assert not moved.exists()
    assert path.exists() and Path(previous.leasePath).exists()


def testCleanRuntimeRestartReclaimsOldGenerationAndRebuildsCurrent(runtime):
    from emo_master.apps.runtime.grpc_server.service import RuntimeService
    service, source = runtime
    old = imagePath(service)
    service.close()
    restored = RuntimeService(dbPath=service.sqliteStore.dbPath, workspaceRoot=service.workspaceRoot)
    try:
        assert not restored.onlineRecoveryError
        assert not old.exists()
        assert imagePath(restored).read_bytes() == source.read_bytes()
        assert imagePath(restored) != source
        assert restored.configurationResourceJournal.retainedAfterCrash() == []
    finally:
        restored.close()
