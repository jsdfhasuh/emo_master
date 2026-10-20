from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np

from emo_master.apps.runtime.context.global_counters import ProjectGlobalCounters
from emo_master.apps.runtime.context.sqlite_store import SqliteStore
from emo_master.apps.runtime.workflow.cancellation import CancellationToken
from emo_master.apps.runtime.workflow.context import RunContext
from emo_master.apps.runtime.workflow.runner import WorkflowRunner
from emo_master.core.contracts.communication import PlcValueCollection, PlcWriteReceipt
from emo_master.core.contracts.geometry2d import BBox2D, CoordinateSpace2D
from emo_master.core.plugin.registry import PluginRegistry
from emo_master.core.project.models import ProjectDocument
from emo_master.core.workflow.compiler import WorkflowCompiler


PROJECT_ID = "global-counter-field-hardware"


class _FakeHardwareState:
    increment = False
    reset = False
    plcCount = 0
    blockId = 0


class _FakeCameraOperator:
    def __init__(self, state: _FakeHardwareState) -> None:
        self.state = state

    def executeNode(
        self,
        inputs: dict[str, object],
        params: dict[str, object],
        runtimeContext: dict[str, object],
    ) -> dict[str, Any]:
        _ = inputs, params, runtimeContext
        self.state.blockId += 1
        image = np.zeros((6, 8, 3), dtype=np.uint8)
        frame = BBox2D(
            0,
            0,
            8,
            6,
            CoordinateSpace2D(
                sourceId="fake-camera",
                imageWidth=8,
                imageHeight=6,
            ),
        )
        return {
            "status": "ok",
            "outputs": {
                "image": image,
                "frame": frame.toPayload(),
                "blockId": self.state.blockId,
                "deviceTimestamp": self.state.blockId * 1000,
                "actualExposureUs": 10000.0,
            },
        }


class _FakePlcReadOperator:
    def __init__(self, state: _FakeHardwareState) -> None:
        self.state = state

    def executeNode(
        self,
        inputs: dict[str, object],
        params: dict[str, object],
        runtimeContext: dict[str, object],
    ) -> dict[str, Any]:
        _ = inputs, runtimeContext
        device = str(params["device"]).upper()
        startAddress = int(params["startAddress"])
        dataType = str(params["dataType"])
        if dataType == "bit":
            value = self.state.increment if startAddress == 500 else self.state.reset
            collection = PlcValueCollection(
                device,
                startAddress,
                dataType,
                (value,),
                1,
            )
            return {
                "status": "ok",
                "outputs": {
                    "values": collection.toPayload(),
                    "booleanValue": value,
                },
            }
        collection = PlcValueCollection(
            device,
            startAddress,
            dataType,
            (self.state.plcCount,),
            2,
        )
        return {
            "status": "ok",
            "outputs": {
                "values": collection.toPayload(),
                "numberValue": self.state.plcCount,
            },
        }


class _FakePlcWriteOperator:
    def __init__(self, state: _FakeHardwareState) -> None:
        self.state = state

    def executeNode(
        self,
        inputs: dict[str, object],
        params: dict[str, object],
        runtimeContext: dict[str, object],
    ) -> dict[str, Any]:
        _ = runtimeContext
        value = int(inputs["value"])
        self.state.plcCount = value
        receipt = PlcWriteReceipt(
            str(params["device"]),
            int(params["startAddress"]),
            str(params["dataType"]),
            1,
            2,
            1,
        )
        return {"status": "ok", "outputs": {"receipt": receipt.toPayload()}}


def testHardwareFieldProjectCompilesAndRunsWithSimulatedDevices(
    tmp_path: Path,
) -> None:
    repositoryRoot = Path(__file__).resolve().parents[2]
    projectPath = repositoryRoot / "field_tests" / "global_counter" / "project.json"
    document = ProjectDocument.model_validate(
        json.loads(projectPath.read_text(encoding="utf-8"))
    )
    pluginRoot = repositoryRoot / "src" / "emo_master" / "plugins"
    activeOperators = PluginRegistry(coreVersion="0.6.1").scan(pluginRoot).activeOperators
    compiled = WorkflowCompiler(operatorRegistry=activeOperators).compile(document)

    state = _FakeHardwareState()
    runtimeOperators = dict(activeOperators)
    runtimeOperators["vision.io.huaray_camera"] = _FakeCameraOperator(state)
    runtimeOperators["communication.plc.slmp_read"] = _FakePlcReadOperator(state)
    runtimeOperators["communication.plc.slmp_write"] = _FakePlcWriteOperator(state)
    store = SqliteStore(tmp_path / "runtime.db")
    store.initialize()
    runner = WorkflowRunner(
        compiled,
        runtimeOperators,
        globalCounters=ProjectGlobalCounters(store, PROJECT_ID),
    )

    def run(workflowId: str):
        return runner.run(
            workflowId,
            {},
            RunContext.root(
                f"job-{workflowId}",
                workflowId,
                str(tmp_path),
                projectId=PROJECT_ID,
            ),
            CancellationToken(),
        )

    capture = run("camera_capture")
    assert capture.outputs["image"].shape == (6, 8, 3)
    assert capture.outputs["blockId"] == 1

    readStatus = run("plc_read_status")
    assert readStatus.outputs == {
        "incrementSignal": False,
        "resetSignal": False,
        "plcCount": 0,
    }

    probe = run("plc_write_probe")
    assert probe.outputs["writtenValue"] == 123.0
    assert probe.outputs["receipt"]["startAddress"] == 798
    assert state.plcCount == 123

    idle = run("plc_signal_counter")
    assert idle.outputs["count"] == 0
    assert state.plcCount == 0

    state.increment = True
    incremented = run("plc_signal_counter")
    assert incremented.outputs["count"] == 1
    assert state.plcCount == 1

    state.reset = True
    reset = run("plc_signal_counter")
    assert reset.outputs["count"] == 0
    assert state.plcCount == 0

    state.increment = False
    state.reset = False
    freeRun = run("camera_counter_freerun")
    assert freeRun.outputs["count"] == 1
    assert state.plcCount == 1

    hardware = run("camera_counter_hardware")
    assert hardware.outputs["count"] == 2
    assert state.plcCount == 2

    readCounter = run("counter_read")
    assert readCounter.outputs == {"count": 2}
