from pathlib import Path
import json

import cv2
import numpy as np

from emo_master.apps.runtime.grpc_server.generated import runtime_pb2
from emo_master.apps.runtime.grpc_server.service import RuntimeService
from tests.runtime.runtime_test_utils import waitForTerminal


def testRuntimeExecutesSavedProjectGraph(tmp_path: Path) -> None:
    projectDir = tmp_path / "demo_project"
    projectDir.mkdir(parents=True, exist_ok=True)

    inputImagePath = projectDir / "assets" / "input.png"
    inputImagePath.parent.mkdir(parents=True, exist_ok=True)
    image = np.zeros((48, 48, 3), dtype=np.uint8)
    image[8:40, 8:40] = (255, 255, 255)
    cv2.imwrite(str(inputImagePath), image)

    outputImagePath = projectDir / "outputs" / "saved.png"
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
                        "imagePath": str(inputImagePath),
                        "colorMode": "color",
                    },
                },
                {
                    "nodeId": "saver",
                    "operatorId": "vision.io.image_saver",
                    "params": {
                        "outputPath": str(outputImagePath),
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

    runtimeService = RuntimeService()
    loadReply = runtimeService.LoadProject(
        runtime_pb2.LoadProjectRequest(project_path=str(projectDir)),
        None,
    )
    assert loadReply.ok is True

    startReply = runtimeService.StartJob(
        runtime_pb2.StartJobRequest(project_id=str(projectDir)),
        None,
    )
    assert startReply.ok is True
    waitForTerminal(runtimeService, startReply.job_id)
    assert outputImagePath.exists()


def testRuntimeExecutesIfTrueBranchAndSkipsFalseBranch(tmp_path: Path) -> None:
    projectDir = tmp_path / "if_project"
    projectDir.mkdir(parents=True, exist_ok=True)

    inputImagePath = projectDir / "assets" / "input.png"
    inputImagePath.parent.mkdir(parents=True, exist_ok=True)
    image = np.zeros((32, 32, 3), dtype=np.uint8)
    image[8:24, 8:24] = (255, 255, 255)
    cv2.imwrite(str(inputImagePath), image)

    trueOutputPath = projectDir / "outputs" / "true.png"
    falseOutputPath = projectDir / "outputs" / "false.png"
    projectPayload = {
        "version": "1.0",
        "meta": {"name": "if"},
        "runtime": {"sourceImagePath": ""},
        "designer": {
            "nodes": [
                {
                    "nodeId": "loader",
                    "operatorId": "vision.io.image_loader",
                    "params": {
                        "imagePath": str(inputImagePath),
                        "colorMode": "color",
                    },
                },
                {
                    "nodeId": "if1",
                    "operatorId": "vision.flow.if",
                    "params": {
                        "mode": "bool",
                        "compareValue": "",
                    },
                },
                {
                    "nodeId": "save_true",
                    "operatorId": "vision.io.image_saver",
                    "params": {
                        "outputPath": str(trueOutputPath),
                        "overwrite": True,
                    },
                },
                {
                    "nodeId": "save_false",
                    "operatorId": "vision.io.image_saver",
                    "params": {
                        "outputPath": str(falseOutputPath),
                        "overwrite": True,
                    },
                },
            ],
            "edges": [
                {
                    "fromNode": "loader",
                    "fromPort": "image",
                    "toNode": "if1",
                    "toPort": "value",
                },
                {
                    "fromNode": "if1",
                    "fromPort": "true",
                    "toNode": "save_true",
                    "toPort": "image",
                },
                {
                    "fromNode": "if1",
                    "fromPort": "false",
                    "toNode": "save_false",
                    "toPort": "image",
                },
            ],
        },
    }
    (projectDir / "project.json").write_text(
        json.dumps(projectPayload, ensure_ascii=True, indent=2),
        encoding="utf-8",
    )

    runtimeService = RuntimeService()
    loadReply = runtimeService.LoadProject(
        runtime_pb2.LoadProjectRequest(project_path=str(projectDir)),
        None,
    )
    assert loadReply.ok is True

    startReply = runtimeService.StartJob(
        runtime_pb2.StartJobRequest(project_id=str(projectDir)),
        None,
    )
    assert startReply.ok is True
    waitForTerminal(runtimeService, startReply.job_id)
    assert trueOutputPath.exists()
    assert not falseOutputPath.exists()


def testRuntimeExecutesSwitchMatchedCaseAndSkipsOthers(tmp_path: Path) -> None:
    projectDir = tmp_path / "switch_project"
    projectDir.mkdir(parents=True, exist_ok=True)

    inputImagePath = projectDir / "assets" / "input.png"
    inputImagePath.parent.mkdir(parents=True, exist_ok=True)
    image = np.zeros((24, 24, 3), dtype=np.uint8)
    image[6:18, 6:18] = (255, 255, 255)
    cv2.imwrite(str(inputImagePath), image)
    loadedImage = cv2.imread(str(inputImagePath), cv2.IMREAD_COLOR)
    assert loadedImage is not None

    case1OutputPath = projectDir / "outputs" / "case1.png"
    defaultOutputPath = projectDir / "outputs" / "default.png"
    projectPayload = {
        "version": "1.0",
        "meta": {"name": "switch"},
        "runtime": {"sourceImagePath": ""},
        "designer": {
            "nodes": [
                {
                    "nodeId": "loader",
                    "operatorId": "vision.io.image_loader",
                    "params": {
                        "imagePath": str(inputImagePath),
                        "colorMode": "color",
                    },
                },
                {
                    "nodeId": "switch1",
                    "operatorId": "vision.flow.switch",
                    "params": {
                        "case0Value": "A",
                        "case1Value": str(loadedImage),
                        "case2Value": "C",
                        "case3Value": "D",
                    },
                },
                {
                    "nodeId": "save_case1",
                    "operatorId": "vision.io.image_saver",
                    "params": {
                        "outputPath": str(case1OutputPath),
                        "overwrite": True,
                    },
                },
                {
                    "nodeId": "save_default",
                    "operatorId": "vision.io.image_saver",
                    "params": {
                        "outputPath": str(defaultOutputPath),
                        "overwrite": True,
                    },
                },
            ],
            "edges": [
                {
                    "fromNode": "loader",
                    "fromPort": "image",
                    "toNode": "switch1",
                    "toPort": "value",
                },
                {
                    "fromNode": "switch1",
                    "fromPort": "case1",
                    "toNode": "save_case1",
                    "toPort": "image",
                },
                {
                    "fromNode": "switch1",
                    "fromPort": "default",
                    "toNode": "save_default",
                    "toPort": "image",
                },
            ],
        },
    }
    (projectDir / "project.json").write_text(
        json.dumps(projectPayload, ensure_ascii=True, indent=2),
        encoding="utf-8",
    )

    runtimeService = RuntimeService()
    loadReply = runtimeService.LoadProject(
        runtime_pb2.LoadProjectRequest(project_path=str(projectDir)),
        None,
    )
    assert loadReply.ok is True

    startReply = runtimeService.StartJob(
        runtime_pb2.StartJobRequest(project_id=str(projectDir)),
        None,
    )
    assert startReply.ok is True
    waitForTerminal(runtimeService, startReply.job_id)
    assert case1OutputPath.exists()
    assert not defaultOutputPath.exists()
