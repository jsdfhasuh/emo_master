import json
import time

import pytest

from emo_master.core.presentation.results import ClosedSource
from emo_master.apps.runtime.context.sqlite_store import SqliteStore


@pytest.mark.parametrize("value", ["1e309", "-1e999", '{"a":[1e309]}', "NaN", "[" * 14 + "0" + "]" * 14,
                                  json.dumps([0] * 4097), json.dumps("x" * (256 * 1024))], ids=["overflow", "negative", "nested", "nan", "depth", "elements", "bytes"])
def testInvalidValues(value):
    with pytest.raises(ValueError):
        ClosedSource(sourceId="s", state="AVAILABLE", valueJson=value)


@pytest.mark.parametrize("value", ["0", "false", "[]", "null", "1e308"])
def testValidValues(value):
    assert ClosedSource(sourceId="s", state="AVAILABLE", valueJson=value).valueJson == value


def waitResult(channel, job, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        snapshot = channel.store.snapshot(job)
        if snapshot["results"]:
            return snapshot["results"][0]
        time.sleep(.02)
    pytest.fail(f"no closed result; status={channel.runtime.jobRepository.get(job)}")


def testRealSpawnScalarUsesStableInputAndDebugState(channel, tmp_path, sample):
    project = sample(tmp_path, image=False)
    release = channel.prepare(project, tmp_path, mode="release", releaseRevision="approved-test")
    releaseDb = SqliteStore(__import__("pathlib").Path(release.snapshot.runtimeDbPath))
    releaseDb.setGlobalCounter(project.project.projectId, "production", 100)
    debug = channel.prepare(project, tmp_path)
    debugDb = SqliteStore(__import__("pathlib").Path(debug.snapshot.runtimeDbPath))
    debugDb.applyGlobalCounter(project.project.projectId, "production", increment=True)
    debugDb.resetGlobalCounter(project.project.projectId, "production")
    assert releaseDb.getGlobalCounter(project.project.projectId, "production").value == 100
    assert not channel.jobs  # preparation is not execution
    (tmp_path / "input.png").unlink()
    job = channel.start(debug.snapshot.snapshotId)
    result = waitResult(channel, job)
    assert result.status == "COMPLETE"
    assert result.sources[0].valueJson == "2"
    assert result.identity.jobId == job and result.identity.mode == "debug"
    assert len(channel.jobs) == 1


def testPreparationRejectsResourceMutationAndSemanticParameter(channel, tmp_path, sample):
    project = sample(tmp_path, image=False)
    (tmp_path / "input.png").write_bytes(b"changed")
    with pytest.raises(ValueError, match="mismatch"):
        channel.prepare(project, tmp_path)
    project = sample(tmp_path, image=False)
    project.workflows["main"].nodes[2].params["connectivity"] = 6
    with pytest.raises(ValueError):
        channel.prepare(project, tmp_path)
