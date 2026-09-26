from pathlib import Path

from emo_master.apps.runtime.artifacts.store import ArtifactStore


def testArtifactStoreCopiesReferenceIntoJobWorkspace(tmp_path: Path) -> None:
    source = tmp_path / "project-output.png"
    source.write_bytes(b"artifact")
    store = ArtifactStore(tmp_path / "job")

    reference = store.registerPath(str(source), nodeId="save", workflowRunId="run")

    assert Path(reference.path).exists()
    assert Path(reference.path).parent == tmp_path / "job" / "artifacts"
    assert Path(reference.path).read_bytes() == b"artifact"
    assert reference.nodeId == "save"
    assert reference.workflowRunId == "run"
    assert reference.checksum != ""
