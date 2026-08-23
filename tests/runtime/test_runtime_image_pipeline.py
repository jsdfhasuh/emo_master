from pathlib import Path

from emo_master.apps.runtime.grpc_server.generated import runtime_pb2
from emo_master.apps.runtime.grpc_server.service import RuntimeService


def testRuntimeRejectsDirectImagePathLoad(tmp_path: Path) -> None:
    imagePath = tmp_path / "demo.png"
    imagePath.write_bytes(b"fake")

    runtimeService = RuntimeService()
    loadReply = runtimeService.LoadProject(
        runtime_pb2.LoadProjectRequest(project_path=str(imagePath)),
        None,
    )
    assert loadReply.ok is False
    assert "project.json" in str(loadReply.message)
