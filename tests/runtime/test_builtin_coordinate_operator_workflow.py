from __future__ import annotations

from pathlib import Path

import pytest

from emo_master.apps.runtime.workflow.cancellation import CancellationToken
from emo_master.apps.runtime.workflow.context import RunContext
from emo_master.apps.runtime.workflow.runner import WorkflowRunner
from emo_master.core.contracts.geometry2d import Polygon2D, Vector2D
from emo_master.core.plugin.registry import PluginRegistry
from emo_master.core.project.models import ProjectDocument
from emo_master.core.workflow.compiler import WorkflowCompiler


def testRuntimeReadsAndCalculatesCoordinateFile(tmp_path: Path) -> None:
    (tmp_path / "coordinates.csv").write_text(
        "x,y\n0,0\n12,0\n12,5\n0,5\n",
        encoding="utf-8",
    )
    pluginRoot = Path(__file__).resolve().parents[2] / "src" / "emo_master" / "plugins"
    scan = PluginRegistry(coreVersion="0.4.0").scan(pluginRoot)
    assert scan.rejectedOperators == {}
    registry = dict(scan.activeOperators)
    compiled = WorkflowCompiler(operatorRegistry=registry).compile(_project())

    result = WorkflowRunner(compiled, registry).run(
        "main",
        {},
        RunContext.root("coordinate-job", "main", str(tmp_path)),
        CancellationToken(),
    )

    assert result.outputs["area"] == pytest.approx(60.0)
    assert result.outputs["perimeter"] == pytest.approx(34.0)
    polygon = Polygon2D.fromPayload(result.outputs["polygon"])
    assert [(point.x, point.y) for point in polygon.points] == [
        (0.0, 0.0),
        (12.0, 0.0),
        (12.0, 5.0),
        (0.0, 5.0),
    ]


def testRuntimeSubtractsTwoCoordinateFilesAsStronglyTypedVectors(
    tmp_path: Path,
) -> None:
    (tmp_path / "left.csv").write_text("5,7\n1,-2\n", encoding="utf-8")
    (tmp_path / "right.csv").write_text("2,3\n-4,3\n", encoding="utf-8")
    pluginRoot = Path(__file__).resolve().parents[2] / "src" / "emo_master" / "plugins"
    scan = PluginRegistry(coreVersion="0.4.0").scan(pluginRoot)
    assert scan.rejectedOperators == {}
    registry = dict(scan.activeOperators)
    compiled = WorkflowCompiler(operatorRegistry=registry).compile(_subtractProject())

    result = WorkflowRunner(compiled, registry).run(
        "main",
        {},
        RunContext.root("coordinate-subtract-job", "main", str(tmp_path)),
        CancellationToken(),
    )

    vectorPayloads = result.outputs["vectors"]
    magnitudes = result.outputs["magnitudes"]
    assert isinstance(vectorPayloads, list)
    assert isinstance(magnitudes, list)
    vectors = [Vector2D.fromPayload(value) for value in vectorPayloads]
    assert [(vector.dx, vector.dy) for vector in vectors] == pytest.approx(
        [(3.0, 4.0), (5.0, -5.0)]
    )
    assert magnitudes == pytest.approx([5.0, 50.0**0.5])
    assert result.outputs["resultCount"] == 2


def _project() -> ProjectDocument:
    return ProjectDocument.model_validate(
        {
            "schemaVersion": "2.1",
            "project": {
                "projectId": "coordinate-reader-calculator",
                "name": "Coordinate Reader Calculator",
                "revision": 1,
                "createdAt": "2026-01-01T00:00:00Z",
                "updatedAt": "2026-01-01T00:00:00Z",
            },
            "entryWorkflowId": "main",
            "workflowOrder": ["main"],
            "workflows": {
                "main": {
                    "name": "Main",
                    "inputs": {},
                    "outputs": {
                        "area": "number",
                        "perimeter": "number",
                        "polygon": {
                            "type": "polygon2d",
                            "schemaVersion": "1.x",
                        },
                    },
                    "nodes": [
                        {"nodeId": "input", "kind": "workflow_input"},
                        {
                            "nodeId": "reader",
                            "kind": "operator",
                            "operatorId": "vision.io.coordinate_reader",
                            "params": {
                                "filePath": "coordinates.csv",
                                "headerMode": "present",
                                "sourceId": "coordinate-fixture",
                            },
                        },
                        {
                            "nodeId": "calculator",
                            "kind": "operator",
                            "operatorId": "vision.geometry.coordinate_calculator",
                            "params": {"closed": True},
                        },
                        {"nodeId": "output", "kind": "workflow_output"},
                    ],
                    "edges": [
                        {
                            "fromNode": "reader",
                            "fromPort": "points",
                            "toNode": "calculator",
                            "toPort": "points",
                        },
                        {
                            "fromNode": "calculator",
                            "fromPort": "area",
                            "toNode": "output",
                            "toPort": "area",
                        },
                        {
                            "fromNode": "calculator",
                            "fromPort": "perimeter",
                            "toNode": "output",
                            "toPort": "perimeter",
                        },
                        {
                            "fromNode": "calculator",
                            "fromPort": "polygon",
                            "toNode": "output",
                            "toPort": "polygon",
                        },
                    ],
                    "layout": {"nodePositions": {}},
                }
            },
            "runtime": {},
            "dependencies": {"operators": []},
            "devices": {"bindings": {}},
        }
    )


def _subtractProject() -> ProjectDocument:
    return ProjectDocument.model_validate(
        {
            "schemaVersion": "2.1",
            "project": {
                "projectId": "coordinate-subtract",
                "name": "Coordinate Subtract",
                "revision": 1,
                "createdAt": "2026-01-01T00:00:00Z",
                "updatedAt": "2026-01-01T00:00:00Z",
            },
            "entryWorkflowId": "main",
            "workflowOrder": ["main"],
            "workflows": {
                "main": {
                    "name": "Main",
                    "inputs": {},
                    "outputs": {
                        "vectors": {
                            "type": "list<vector2d>",
                            "schemaVersion": "1.x",
                        },
                        "magnitudes": "list<number>",
                        "resultCount": "integer",
                    },
                    "nodes": [
                        {"nodeId": "input", "kind": "workflow_input"},
                        {
                            "nodeId": "left",
                            "kind": "operator",
                            "operatorId": "vision.io.coordinate_reader",
                            "params": {
                                "filePath": "left.csv",
                                "headerMode": "absent",
                                "sourceId": "coordinate-fixture",
                            },
                        },
                        {
                            "nodeId": "right",
                            "kind": "operator",
                            "operatorId": "vision.io.coordinate_reader",
                            "params": {
                                "filePath": "right.csv",
                                "headerMode": "absent",
                                "sourceId": "coordinate-fixture",
                            },
                        },
                        {
                            "nodeId": "calculator",
                            "kind": "operator",
                            "operatorId": "vision.geometry.coordinate_calculator",
                            "params": {"mode": "subtract"},
                        },
                        {"nodeId": "output", "kind": "workflow_output"},
                    ],
                    "edges": [
                        {
                            "fromNode": "left",
                            "fromPort": "points",
                            "toNode": "calculator",
                            "toPort": "points",
                        },
                        {
                            "fromNode": "right",
                            "fromPort": "points",
                            "toNode": "calculator",
                            "toPort": "otherPoints",
                        },
                        {
                            "fromNode": "calculator",
                            "fromPort": "vectors",
                            "toNode": "output",
                            "toPort": "vectors",
                        },
                        {
                            "fromNode": "calculator",
                            "fromPort": "magnitudes",
                            "toNode": "output",
                            "toPort": "magnitudes",
                        },
                        {
                            "fromNode": "calculator",
                            "fromPort": "resultCount",
                            "toNode": "output",
                            "toPort": "resultCount",
                        },
                    ],
                    "layout": {"nodePositions": {}},
                }
            },
            "runtime": {},
            "dependencies": {"operators": []},
            "devices": {"bindings": {}},
        }
    )
