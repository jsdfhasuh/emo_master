import multiprocessing
import os
from pathlib import Path
from uuid import uuid4

import pytest

from emo_master.apps.runtime.context.sqlite_store import SqliteStore
from emo_master.apps.runtime.project_config.resource_journal import ResourceJournal
from tests.runtime import test_prepared_resources as prepared

runtime = prepared.runtime
imagePath = prepared.imagePath


def journal(tmp_path, instance=None):
    return ResourceJournal(SqliteStore(tmp_path / "runtime.db"), tmp_path / "configuration-resources", instance or uuid4().hex)


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
