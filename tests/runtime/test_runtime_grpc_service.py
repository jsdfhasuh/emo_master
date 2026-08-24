import json
from pathlib import Path

import cv2
import numpy as np

from emo_master.apps.runtime.grpc_server.generated import runtime_pb2
from emo_master.apps.runtime.grpc_server.service import RuntimeService
from tests.runtime.runtime_test_utils import waitForTerminal


def testLoadProjectReturnsReadyStatus(tmp_path: Path) -> None:
    projectDir = tmp_path / "demo_project"
    projectDir.mkdir(parents=True, exist_ok=True)
    (projectDir / "project.json").write_text(
        json.dumps(
            {
                "version": "1.0",
                "meta": {"name": "demo"},
                "runtime": {"sourceImagePath": ""},
                "designer": {"nodes": [], "edges": []},
            },
            ensure_ascii=True,
            indent=2,
        ),
        encoding="utf-8",
    )

    service = RuntimeService()
    reply = service.LoadProject(
        runtime_pb2.LoadProjectRequest(project_path=str(projectDir)), None
    )
    assert reply.ok is True
    assert reply.status == "READY"


def testListOperatorsReturnsRegisteredEntries() -> None:
    service = RuntimeService()
    reply = service.ListOperators(runtime_pb2.ListOperatorsRequest(), None)
    assert len(reply.operators) >= 1
    operatorIds = {operator.operator_id for operator in reply.operators}
    operatorsById = {operator.operator_id: operator for operator in reply.operators}
    assert "vision.io.image_loader" in operatorIds
    assert "vision.io.image_saver" in operatorIds
    assert "vision.flow.if" in operatorIds
    assert "vision.flow.switch" in operatorIds
    assert operatorsById["vision.flow.if"].category == "控制流"
    assert operatorsById["vision.flow.switch"].category == "控制流"
    operatorInfo = reply.operators[0]
    assert isinstance(dict(operatorInfo.input_ports), dict)
    assert isinstance(dict(operatorInfo.output_ports), dict)
    assert operatorInfo.param_schema_json.startswith("{")
    assert operatorInfo.category != ""
    assert operatorInfo.icon_key != ""


def testStreamJobEventsReturnsLifecycleEvents(tmp_path: Path) -> None:
    projectDir = tmp_path / "event_project"
    projectDir.mkdir(parents=True, exist_ok=True)
    inputPath = projectDir / "assets" / "in.png"
    inputPath.parent.mkdir(parents=True, exist_ok=True)
    image = np.zeros((16, 16, 3), dtype=np.uint8)
    image[4:12, 4:12] = (255, 255, 255)
    cv2.imwrite(str(inputPath), image)
    outputPath = projectDir / "outputs" / "out.png"
    (projectDir / "project.json").write_text(
        json.dumps(
            {
                "version": "1.0",
                "meta": {"name": "event"},
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
            },
            ensure_ascii=True,
            indent=2,
        ),
        encoding="utf-8",
    )

    service = RuntimeService()
    _ = service.LoadProject(
        runtime_pb2.LoadProjectRequest(project_path=str(projectDir)), None
    )
    startReply = service.StartJob(
        runtime_pb2.StartJobRequest(project_id=str(projectDir)), None
    )
    assert startReply.job_id != ""
    waitForTerminal(service, startReply.job_id)

    events = list(
        service.StreamJobEvents(
            runtime_pb2.StreamJobEventsRequest(job_id=startReply.job_id, follow=True), None
        )
    )
    assert len(events) >= 2
    eventTypes = [event.event_type for event in events]
    assert "job.started" in eventTypes


def testStreamJobEventsIncludeSwitchBranchPayload(tmp_path: Path) -> None:
    projectDir = tmp_path / "switch_event_project"
    projectDir.mkdir(parents=True, exist_ok=True)
    inputPath = projectDir / "assets" / "in.png"
    inputPath.parent.mkdir(parents=True, exist_ok=True)
    image = np.zeros((12, 12, 3), dtype=np.uint8)
    image[3:9, 3:9] = (255, 255, 255)
    cv2.imwrite(str(inputPath), image)
    loadedImage = cv2.imread(str(inputPath), cv2.IMREAD_COLOR)
    assert loadedImage is not None
    outputPath = projectDir / "outputs" / "case1.png"
    (projectDir / "project.json").write_text(
        json.dumps(
            {
                "version": "1.0",
                "meta": {"name": "switch-event"},
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
                            "nodeId": "switch1",
                            "operatorId": "vision.flow.switch",
                            "params": {
                                "case1Value": str(loadedImage),
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
                            "toNode": "switch1",
                            "toPort": "value",
                        },
                        {
                            "fromNode": "switch1",
                            "fromPort": "case1",
                            "toNode": "saver",
                            "toPort": "image",
                        },
                    ],
                },
            },
            ensure_ascii=True,
            indent=2,
        ),
        encoding="utf-8",
    )

    service = RuntimeService()
    _ = service.LoadProject(
        runtime_pb2.LoadProjectRequest(project_path=str(projectDir)), None
    )
    startReply = service.StartJob(
        runtime_pb2.StartJobRequest(project_id=str(projectDir)), None
    )
    waitForTerminal(service, startReply.job_id)
    events = list(
        service.StreamJobEvents(
            runtime_pb2.StreamJobEventsRequest(job_id=startReply.job_id, follow=True), None
        )
    )
    switchEvents = [
        event
        for event in events
        if event.node_id == "switch1" and event.event_type == "node.completed"
    ]
    assert len(switchEvents) == 1
    payload = json.loads(switchEvents[0].payload_json)
    assert payload["status"] == "COMPLETED"
    assert payload["branch"] == "case1"
