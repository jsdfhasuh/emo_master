"""Private preview writer and workspace retirement must agree on Win32 paths."""
from pathlib import Path
from types import SimpleNamespace

import pytest

from emo_master.apps.runtime.grpc_server.service import RuntimeService
from emo_master.apps.runtime.preview.store import _ioPath


@pytest.mark.parametrize("operation", ["retired", "stale"])
def testOwnedWorkspaceRetiresLongPreviewPathsWithoutRemovingLiveWorkspace(tmp_path, operation):
    runtime = object.__new__(RuntimeService)
    runtime.workspaceRoot = tmp_path / "jobs"
    workspace = runtime.workspaceRoot / "retired-job"
    runtime._workspacePaths = {"retired-job": workspace}
    runtime.jobRepository = SimpleNamespace(get=lambda job: SimpleNamespace(isTerminal=job == "retired-job"))
    runtime.jobSupervisor = SimpleNamespace(ownsJobResources=lambda job: False)
    runtime.deviceManager = SimpleNamespace(dispatchGroups={})
    preview = workspace / "preview_staging" / "assets"
    # Keep each component legal while deterministically exceeding MAX_PATH.
    while len(str(preview.resolve())) < 280:
        preview /= "private-preview-segment"
    image = preview / ("a" * 64 + ".png")
    assert workspace.resolve().is_relative_to(tmp_path.resolve())
    _ioPath(image.parent).mkdir(parents=True)
    _ioPath(image).write_bytes(b"private fixture, not an actual image")
    live = runtime.workspaceRoot / "live-job"
    live.mkdir()
    (live / "keep.txt").write_text("still owned", encoding="utf-8")
    assert len(str(image.resolve())) > 300
    assert _ioPath(image).is_file()
    if operation == "retired":
        runtime._removeWorkspace("retired-job")
    else:
        runtime._cleanupStaleWorkspaces()
    assert not _ioPath(workspace).exists(), "retirement must delete files created through the extended-path writer"
    assert (live / "keep.txt").read_text(encoding="utf-8") == "still owned"
    assert isinstance(runtime.workspaceRoot, Path)
