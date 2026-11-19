import json
from pathlib import Path

import numpy as np
import pytest

from emo_master import __version__
from emo_master.core.contracts.geometry2d import BBox2D, LineCollection, Point2D
from emo_master.core.plugin.registry import PluginRegistry
from emo_master.plugins.builtins._geometry_bridges import GeometryPointsOperator, LineFromPointsOperator
from emo_master.plugins.builtins._image_frame import defaultFrame
from emo_master.plugins.builtins.annotate.operator import AnnotateOperator


def testSelectedCentersProduceExistingDashedOverlayWithoutMutatingInputs():
    frame = defaultFrame(100, 60, sourceId="current-image")
    space = frame.coordinateSpace
    extract = GeometryPointsOperator()
    points = [extract.executeNode({"bbox": BBox2D(x, 20, 10, 20, space).toPayload()},
                                 {"feature": "bboxCenter"}, {})["outputs"]["points"]
              for x in (5, 85)]
    inputs = {"startPoints": points[0], "endPoints": points[1], "frame": frame.toPayload()}
    original = json.dumps(inputs, sort_keys=True)
    result = LineFromPointsOperator().executeNode(inputs, {}, {})
    assert result["status"] == "ok" and result["outputs"]["lineCount"] == 1
    line = LineCollection.fromPayload(result["outputs"]["lines"]).items[0].line
    assert (line.start.x, line.start.y, line.end.x, line.end.y) == (10, 30, 90, 30)
    assert line.coordinateSpace == space
    image = np.zeros((60, 100, 3), dtype=np.uint8)
    painted = AnnotateOperator().executeNode(
        {"image": image, "frame": frame.toPayload(), "lines": result["outputs"]["lines"]},
        {"lineStyle": "dashed", "endpointMarker": "dot", "lineColor": [255, 255, 255]}, {})
    assert painted["status"] == "ok" and painted["outputs"]["drawnCount"] == 1
    strip = painted["outputs"]["overlay"][30, 20:80]
    assert np.any(np.all(strip == 255, axis=1)) and np.any(np.all(strip == 0, axis=1))
    assert np.count_nonzero(image) == 0
    assert json.dumps(inputs, sort_keys=True) == original


@pytest.mark.parametrize("missing", [(True, False), (False, True), (True, True)])
def testCurrentEmptySelectionDoesNotRetainPreviousLine(missing):
    frame = defaultFrame(100, 60, sourceId="current-image")
    inputs = {"startPoints": [Point2D(10, 30, frame.coordinateSpace).toPayload()],
              "endPoints": [Point2D(90, 30, frame.coordinateSpace).toPayload()], "frame": frame.toPayload()}
    operator = LineFromPointsOperator()
    assert operator.executeNode(inputs, {}, {})["outputs"]["lineCount"] == 1
    for name, empty in zip(("startPoints", "endPoints"), missing):
        if empty:
            inputs[name] = []
    result = operator.executeNode(inputs, {}, {})
    assert result["status"] == "ok" and result["outputs"]["lineCount"] == 0
    lines = LineCollection.fromPayload(result["outputs"]["lines"])
    assert lines.items == () and lines.coordinateSpace == frame.coordinateSpace
    image = np.zeros((60, 100, 3), dtype=np.uint8)
    painted = AnnotateOperator().executeNode(
        {"image": image, "frame": frame.toPayload(), "lines": lines.toPayload()}, {}, {})
    assert painted["outputs"]["drawnCount"] == 0
    np.testing.assert_array_equal(painted["outputs"]["overlay"], image)


@pytest.mark.parametrize("problem", ["multiple", "different-space", "same-point"])
def testLineRequiresSelectedDistinctPointsInTheCurrentFrame(problem):
    frame = defaultFrame(100, 60, sourceId="current-image")
    point = Point2D(10, 30, frame.coordinateSpace).toPayload()
    inputs = {"startPoints": [point], "endPoints": [point], "frame": frame.toPayload()}
    if problem == "multiple":
        inputs["startPoints"] = [point, point]
    elif problem == "different-space":
        inputs["endPoints"] = [Point2D(90, 30, defaultFrame(100, 60, sourceId="other").coordinateSpace).toPayload()]
    result = LineFromPointsOperator().executeNode(inputs, {}, {})
    assert result["status"] == "error" and result["error"]["code"] == "E_INPUT_SHAPE"


def testLineOperatorIsRegisteredWithItsTypedManifestAndDebugSupport():
    root = Path(__file__).resolve().parents[2] / "src/emo_master/plugins/builtins"
    registry = PluginRegistry(__version__).scan(root)
    descriptor = registry.activeOperators[LineFromPointsOperator.meta.operatorId]
    assert descriptor.operatorClass is LineFromPointsOperator
    manifest = json.loads((root / "line_from_points/manifest.json").read_text(encoding="utf-8"))
    for key in ("inputPorts", "outputPorts", "paramSchema"):
        assert manifest[key] == getattr(LineFromPointsOperator.meta, key)
    from emo_master.apps.runtime.operator_debug.contracts import trustedEntries
    assert LineFromPointsOperator.meta.operatorId in trustedEntries(registry.activeOperators)
