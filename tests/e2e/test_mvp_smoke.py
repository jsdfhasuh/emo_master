from pathlib import Path
import json

import cv2
import numpy as np

from emo_master.apps.runtime.main import createRuntimeService


def _createProjectWithLoaderSaver(
    projectDir: Path, imageName: str, outputName: str
) -> Path:
    projectDir.mkdir(parents=True, exist_ok=True)
    inputPath = projectDir / "assets" / imageName
    inputPath.parent.mkdir(parents=True, exist_ok=True)
    outputPath = projectDir / "outputs" / outputName
    image = np.zeros((48, 48, 3), dtype=np.uint8)
    image[12:36, 12:36] = (255, 255, 255)
    cv2.imwrite(str(inputPath), image)

    projectPayload = {
        "version": "1.0",
        "meta": {"name": "demo"},
        "runtime": {"sourceImagePath": ""},
        "designer": {
            "nodes": [
                {
                    "nodeId": "loader",
                    "operatorId": "vision.io.image_loader",
                    "params": {
                        "imagePath": str(inputPath),
                        "colorMode": "color",
                    },
                },
                {
                    "nodeId": "saver",
                    "operatorId": "vision.io.image_saver",
                    "params": {
                        "outputPath": str(outputPath),
                        "overwrite": True,
                    },
                },
            ],
            "edges": [
                {
                    "fromNode": "loader",
                    "fromPort": "image",
                    "toNode": "saver",
                    "toPort": "image",
                }
            ],
        },
    }
    (projectDir / "project.json").write_text(
        json.dumps(projectPayload, ensure_ascii=True, indent=2),
        encoding="utf-8",
    )
    return outputPath


def testMvpPipelineSmoke(tmp_path: Path) -> None:
    projectDir = tmp_path / "demo_project"
    outputPath = _createProjectWithLoaderSaver(
        projectDir, "sample.png", "sample.out.png"
    )

    runtimeService = createRuntimeService()
    loadReply = runtimeService.LoadProject(
        type("Req", (), {"project_path": str(projectDir)})(), None
    )
    assert loadReply.ok is True

    startReply = runtimeService.StartJob(
        type("Req", (), {"project_id": str(projectDir)})(), None
    )
    assert startReply.ok is True
    assert startReply.job_id != ""
    assert outputPath.exists()


def testMvpPipelineSmokeWithImage(tmp_path: Path) -> None:
    projectDir = tmp_path / "sample_project"
    outputPath = _createProjectWithLoaderSaver(
        projectDir, "sample.png", "sample.out.png"
    )

    runtimeService = createRuntimeService()
    loadReply = runtimeService.LoadProject(
        type("Req", (), {"project_path": str(projectDir)})(), None
    )
    assert loadReply.ok is True

    startReply = runtimeService.StartJob(
        type("Req", (), {"project_id": str(projectDir)})(), None
    )
    assert startReply.ok is True

    statusReply = runtimeService.GetJobStatus(
        type("Req", (), {"job_id": startReply.job_id})(), None
    )
    assert statusReply.ok is True
    assert statusReply.status == "COMPLETED"
    events = list(
        runtimeService.StreamJobEvents(
            type("Req", (), {"job_id": startReply.job_id})(), None
        )
    )
    assert any(getattr(event, "event_type", "") == "job.completed" for event in events)
    assert outputPath.exists()


def testEndToEndDagWithEventStreamAndPreviewArtifacts(tmp_path: Path) -> None:
    projectDir = tmp_path / "preview_project"
    outputPath = _createProjectWithLoaderSaver(
        projectDir, "preview.png", "preview.out.png"
    )

    runtimeService = createRuntimeService()
    _ = runtimeService.LoadProject(
        type("Req", (), {"project_path": str(projectDir)})(), None
    )
    startReply = runtimeService.StartJob(
        type("Req", (), {"project_id": str(projectDir)})(), None
    )
    assert startReply.ok is True

    events = list(
        runtimeService.StreamJobEvents(
            type("Req", (), {"job_id": startReply.job_id})(), None
        )
    )
    assert any(getattr(event, "event_type", "") == "job.completed" for event in events)
    assert outputPath.exists()
