from pathlib import Path

import pytest

from emo_master.apps.designer.state.project_store import (
    createProjectSkeleton,
    loadProject,
    saveProject,
)


def testCreateProjectSkeletonCreatesRequiredStructure(tmp_path: Path) -> None:
    projectDir = tmp_path / "demo_project"
    createProjectSkeleton(projectDir, "Demo")

    assert projectDir.exists()
    assert (projectDir / "assets").exists()
    assert (projectDir / "outputs").exists()
    assert (projectDir / "project.json").exists()


def testSaveAndLoadProjectRoundTrip(tmp_path: Path) -> None:
    projectDir = tmp_path / "demo_project"
    createProjectSkeleton(projectDir, "Demo")

    payload = {
        "version": "1.0",
        "meta": {"name": "Demo"},
        "runtime": {"sourceImagePath": ""},
        "designer": {
            "nodes": [
                {
                    "nodeId": "node-1",
                    "operatorId": "vision.io.image_loader",
                    "displayName": "Image Loader",
                    "x": 10.0,
                    "y": 20.0,
                    "inputPorts": {},
                    "outputPorts": {"image": "image"},
                    "paramSchema": {},
                    "params": {"imagePath": "C:/tmp/a.png"},
                }
            ],
            "edges": [],
        },
    }
    saveProject(projectDir, payload)
    loaded = loadProject(projectDir)
    assert loaded["version"] == "1.0"
    assert loaded["meta"]["name"] == "Demo"
    assert loaded["designer"]["nodes"][0]["nodeId"] == "node-1"


def testLoadProjectRaisesWhenProjectFileMissing(tmp_path: Path) -> None:
    projectDir = tmp_path / "empty_dir"
    projectDir.mkdir(parents=True, exist_ok=True)
    with pytest.raises(ValueError):
        _ = loadProject(projectDir)
