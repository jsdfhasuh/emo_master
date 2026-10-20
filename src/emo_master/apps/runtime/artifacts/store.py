from __future__ import annotations

import hashlib
from pathlib import Path
import shutil
from uuid import uuid4
import mimetypes

from emo_master.core.contracts.execution import ArtifactRef


class ArtifactStore:
    def __init__(self, workspacePath: Path) -> None:
        self.root = workspacePath / "artifacts"
        self.root.mkdir(parents=True, exist_ok=True)

    def registerPath(
        self,
        path: str,
        nodeId: str = "",
        workflowRunId: str = "",
        kind: str = "file",
    ) -> ArtifactRef:
        filePath = Path(path)
        artifactId = str(uuid4())
        storedPath = filePath
        if filePath.exists() and filePath.is_file():
            destination = self.root / f"{artifactId}{filePath.suffix}"
            try:
                if filePath.resolve() != destination.resolve():
                    shutil.copy2(filePath, destination)
                storedPath = destination
            except OSError:
                storedPath = filePath
        size = storedPath.stat().st_size if storedPath.exists() and storedPath.is_file() else 0
        checksum = ""
        if size:
            checksum = hashlib.sha256(storedPath.read_bytes()).hexdigest()
        mimeType = mimetypes.guess_type(str(filePath))[0] or "application/octet-stream"
        return ArtifactRef(
            artifactId=artifactId,
            kind=kind,
            path=str(storedPath),
            mimeType=mimeType,
            sizeBytes=size,
            checksum=checksum,
            nodeId=nodeId,
            workflowRunId=workflowRunId,
        )
