import csv
import json
from pathlib import Path

import pytest

from emo_master.core.contracts.geometry2d import (
    BBox2D,
    Circle2D,
    CircleCollection,
    CircleItem,
    ContourCollection,
    ContourItem,
    Detection2D,
    DetectionCollection,
    CoordinateSpace2D,
    Histogram,
    HistogramChannel,
    Line2D,
    LineCollection,
    LineItem,
    Point2D,
    Polygon2D,
    RotatedBox2D,
    ShapeMeasurement,
    ShapeMeasurementCollection,
    TemplateMatch,
    TemplateMatchCollection,
)
from emo_master.plugins.builtins.result_writer import operator as writer_module
from emo_master.plugins.builtins.result_writer.operator import ResultWriterOperator


def _detections() -> DetectionCollection:
    space = CoordinateSpace2D(sourceId="source", imageWidth=20, imageHeight=20)
    detection = Detection2D.fromGeometry(
        "det-1",
        2,
        "零件",
        0.95,
        BBox2D(1, 2, 3, 4, space),
        {"batch": "A"},
    )
    return DetectionCollection((detection,), space)


def _run(tmp_path: Path, formatName: str, relativePath: str):
    return ResultWriterOperator().executeNode(
        {"detections": _detections().toPayload()},
        {"format": formatName, "relativePath": relativePath},
        {"workspacePath": str(tmp_path)},
    )


def testResultWriterWritesJsonJsonlAndCsvInsideWorkspace(tmp_path: Path) -> None:
    jsonResult = _run(tmp_path, "json", "results/data")
    jsonlResult = _run(tmp_path, "jsonl", "results/data-lines")
    csvResult = _run(tmp_path, "csv", "results/data-table")

    jsonPath = Path(jsonResult["outputs"]["result"]["path"])
    jsonlPath = Path(jsonlResult["outputs"]["result"]["path"])
    csvPath = Path(csvResult["outputs"]["result"]["path"])
    assert json.loads(jsonPath.read_text(encoding="utf-8"))["items"][0]["label"] == "零件"
    jsonl = [json.loads(line) for line in jsonlPath.read_text(encoding="utf-8").splitlines()]
    assert jsonl[0]["collectionType"] == "detectionCollection"
    with csvPath.open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    assert rows[0]["label"] == "零件"
    assert jsonResult["outputs"]["result"]["recordCount"] == 1


def testResultWriterProtectsOverwriteAndRejectsTraversal(tmp_path: Path) -> None:
    first = _run(tmp_path, "json", "result")
    second = _run(tmp_path, "json", "result")
    traversal = _run(tmp_path, "json", "../outside")

    assert first["status"] == "ok"
    assert second["error"]["code"] == "E_OUTPUT_EXISTS"
    assert traversal["error"]["code"] == "E_PARAM_INVALID"


def testResultWriterRequiresExactlyOneInputAndSupportsScalar(tmp_path: Path) -> None:
    operator = ResultWriterOperator()
    missing = operator.executeNode(
        {}, {}, {"workspacePath": str(tmp_path)}
    )
    conflict = operator.executeNode(
        {"numberValue": 1, "booleanValue": True},
        {},
        {"workspacePath": str(tmp_path)},
    )
    scalar = operator.executeNode(
        {"numberValue": 3.5},
        {"relativePath": "number", "format": "json"},
        {"workspacePath": str(tmp_path)},
    )

    assert missing["error"]["code"] == "E_INPUT_SHAPE"
    assert conflict["error"]["code"] == "E_INPUT_SHAPE"
    assert json.loads(Path(scalar["outputs"]["result"]["path"]).read_text()) == 3.5


def _classicValues() -> dict[str, object]:
    space = CoordinateSpace2D(sourceId="source", imageWidth=40, imageHeight=30)
    polygon = Polygon2D(
        (
            Point2D(1, 1, space),
            Point2D(8, 1, space),
            Point2D(8, 7, space),
            Point2D(1, 7, space),
        )
    )
    return {
        "contours": ContourCollection(
            (ContourItem("contour-1", polygon, depth=0, isHole=False),), space
        ),
        "measurements": ShapeMeasurementCollection(
            (
                ShapeMeasurement(
                    "measurement-1",
                    "contour-1",
                    "contour",
                    42,
                    26,
                    Point2D(4.5, 4, space),
                    BBox2D(1, 1, 7, 6, space),
                    RotatedBox2D(4.5, 4, 7, 6, 0, space),
                    0.78,
                ),
            ),
            space,
        ),
        "lines": LineCollection(
            (LineItem("line-1", Line2D(Point2D(2, 2, space), Point2D(8, 6, space))),),
            space,
        ),
        "circles": CircleCollection(
            (CircleItem("circle-1", Circle2D(Point2D(15, 10, space), 4)),),
            space,
        ),
        "matches": TemplateMatchCollection(
            "ccoeffNormed",
            "零件",
            5,
            4,
            (TemplateMatch("match-1", BBox2D(20, 10, 5, 4, space), 0.9, 0.95),),
            space,
        ),
        "histogram": Histogram(
            space,
            "GRAY",
            "counts",
            4,
            (0, 128, 256),
            (HistogramChannel("GRAY", (1, 3)),),
        ),
    }


def testResultWriterWritesFixedCsvSchemasForClassicPayloads(tmp_path: Path) -> None:
    expectedColumns = {
        "contours": {"id", "parent_id", "polygon"},
        "measurements": {"id", "source_id", "min_area_rect"},
        "lines": {"id", "start_x", "length", "angle"},
        "circles": {"id", "center_x", "radius"},
        "matches": {"id", "method", "quality", "bbox_x"},
        "histogram": {"channel", "bin_index", "bin_start", "value"},
    }
    for portName, value in _classicValues().items():
        result = ResultWriterOperator().executeNode(
            {portName: value},
            {"format": "csv", "relativePath": f"classic/{portName}"},
            {"workspacePath": str(tmp_path)},
        )
        assert result["status"] == "ok"
        target = Path(result["outputs"]["result"]["path"])
        with target.open(encoding="utf-8", newline="") as stream:
            reader = csv.DictReader(stream)
            rows = list(reader)
        assert expectedColumns[portName] <= set(reader.fieldnames or [])
        assert rows
        if portName == "histogram":
            assert len(rows) == 2
            assert result["outputs"]["result"]["recordCount"] == 2
        else:
            assert len(rows) == 1


def testResultWriterJsonlCarriesCollectionMetadataAndSupportsEmptyCollection(
    tmp_path: Path,
) -> None:
    matches = _classicValues()["matches"]
    result = ResultWriterOperator().executeNode(
        {"matches": matches},
        {"format": "jsonl", "relativePath": "matches"},
        {"workspacePath": str(tmp_path)},
    )
    record = json.loads(
        Path(result["outputs"]["result"]["path"]).read_text(encoding="utf-8")
    )
    assert record["collectionType"] == "templateMatchCollection"
    assert record["collectionMetadata"] == {
        "method": "ccoeffNormed",
        "label": "零件",
        "templateWidth": 5,
        "templateHeight": 4,
    }
    assert record["item"]["id"] == "match-1"

    space = matches.coordinateSpace  # type: ignore[attr-defined]
    empty = TemplateMatchCollection("ccoeffNormed", "零件", 5, 4, (), space)
    emptyResult = ResultWriterOperator().executeNode(
        {"matches": empty},
        {"format": "jsonl", "relativePath": "empty-matches"},
        {"workspacePath": str(tmp_path)},
    )
    assert Path(emptyResult["outputs"]["result"]["path"]).read_text() == ""
    assert emptyResult["outputs"]["result"]["recordCount"] == 0


def testResultWriterAcceptsLineCircleGeometryObjectsAndCleansFailedTempFile(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    space = CoordinateSpace2D(sourceId="source")
    line = Line2D(Point2D(1, 2, space), Point2D(4, 6, space))
    circle = Circle2D(Point2D(8, 9, space), 3)
    lineResult = ResultWriterOperator().executeNode(
        {"geometry": line},
        {"format": "json", "relativePath": "geometry-line"},
        {"workspacePath": str(tmp_path)},
    )
    circleResult = ResultWriterOperator().executeNode(
        {"geometry": circle},
        {"format": "json", "relativePath": "geometry-circle"},
        {"workspacePath": str(tmp_path)},
    )
    assert json.loads(Path(lineResult["outputs"]["result"]["path"]).read_text())["type"] == "line2d"
    assert json.loads(Path(circleResult["outputs"]["result"]["path"]).read_text())["type"] == "circle2d"

    def failReplace(source: object, target: object) -> None:
        _ = source, target
        raise OSError("simulated atomic replacement failure")

    monkeypatch.setattr(writer_module.os, "replace", failReplace)
    failed = ResultWriterOperator().executeNode(
        {"numberValue": 1},
        {"format": "json", "relativePath": "failed"},
        {"workspacePath": str(tmp_path)},
    )
    assert failed["error"]["code"] == "E_OUTPUT_WRITE_FAILED"
    assert list(tmp_path.glob(".failed.json.*.tmp")) == []
