"""Offline control-flow demonstration; no model, camera or network activity."""
from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np

from emo_master import __version__
from emo_master.apps.runtime.context.global_variables import ProjectGlobalVariables
from emo_master.apps.runtime.context.sqlite_store import SqliteStore
from emo_master.apps.runtime.workflow.cancellation import CancellationToken
from emo_master.apps.runtime.workflow.context import RunContext
from emo_master.apps.runtime.workflow.runner import WorkflowExecutionError, WorkflowRunner
from emo_master.core.contracts.geometry2d import Point2D
from emo_master.core.plugin.registry import PluginRegistry
from emo_master.core.workflow.compiler import WorkflowCompiler
from emo_master.plugins.builtins._coordinate_operators import CoordinateReaderOperator
from emo_master.plugins.builtins._image_frame import defaultFrame


ROOT = Path(__file__).resolve().parent


def loadProject():
    return json.loads((ROOT / "project.json").read_text(encoding="utf-8"))


def loadReference(station):
    # The demo TXT contains pixel references, not device-unit coordinates.
    frame = defaultFrame(64, 48, sourceId=f"demo-station{station}")
    result = CoordinateReaderOperator().executeNode(
        {"frame": frame.toPayload()},
        {"filePath": f"station{station}_reference.txt", "headerMode": "absent"},
        {"workspacePath": str(ROOT)},
    )
    if result["status"] != "ok":
        raise RuntimeError(result["error"])
    return result["outputs"]["points"], frame.coordinateSpace


def mockInputs(space, fixedPoints, frameNumber):
    def points(x, y):
        return [Point2D(x, y, space).toPayload()]
    return {
        "image": np.full((48, 64, 3), frameNumber, dtype=np.uint8),
        "mockA": points(10, 20), "mockB": points(1, 2),
        "mockHoles": points(5, 6), "fixedPoints": fixedPoints,
    }


def pointValues(payloads):
    return [(point.x, point.y) for point in map(Point2D.fromPayload, payloads)]


def runSimulation():
    project = loadProject()
    scan = PluginRegistry(coreVersion=__version__).scan(ROOT.parents[1] / "src" / "emo_master" / "plugins")
    if scan.rejectedOperators:
        raise RuntimeError(scan.rejectedOperators)
    compiled = WorkflowCompiler(scan.activeOperators).compile(project)
    with TemporaryDirectory(prefix="emo-two-shot-") as temporary:
        store = SqliteStore(Path(temporary) / "state.db")
        store.initialize()
        stations = {}
        for station in (1, 2):
            # This is a content snapshot for this offline session. The live
            # Coordinate Reader remains unchanged; production freezing is separate.
            fixed, space = loadReference(station)
            state = ProjectGlobalVariables(store, compiled.projectId, project["globalVariables"], f"demo-job-{station}")
            state.synchronize()
            state.initializeJob()
            stations[station] = (WorkflowRunner(compiled, scan.activeOperators, globalVariables=state), state, fixed, space)
        rows = []
        # Different schedules deliberately exercise independent job state.
        for station, number in ((1, 1), (2, 1), (2, 2), (1, 2), (1, 3), (1, 4)):
            runner, state, fixed, space = stations[station]
            workflowId = f"station{station}"
            result = runner.run(workflowId, mockInputs(space, fixed, number),
                                RunContext.root(state.jobId, workflowId, str(ROOT)), CancellationToken())
            if "unexpectedShot" in result.outputs:
                raise WorkflowExecutionError("E_SHOT_COUNTER", "unexpected shot count; synchronize before restart")
            output = "plcPoints" if result.outputs["shot"] == 1 else "robotPoints"
            row = dict(station=station, frame=number, shot=result.outputs["shot"],
                       count=state.get(f"station{station}-shot-count"),
                       channel=output, coordinates=pointValues(result.outputs[output]))
            rows.append(row)
        return rows


if __name__ == "__main__":
    for row in runSimulation():
        print(json.dumps(row, ensure_ascii=False))
