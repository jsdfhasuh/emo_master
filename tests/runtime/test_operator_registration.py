import json
from pathlib import Path

from emo_master.apps.runtime.grpc_server.generated import runtime_pb2
from emo_master.apps.runtime.grpc_server.service import RuntimeService


def testRuntimeRegistersBuiltinsAfterStrictValidation(tmp_path: Path) -> None:
    service = RuntimeService(
        dbPath=tmp_path / "runtime.db",
        workspaceRoot=tmp_path / "jobs",
    )
    try:
        activeReply = service.ListOperators(
            runtime_pb2.ListOperatorsRequest(),
            None,
        )
        rejectedReply = service.ListRejectedOperators(
            runtime_pb2.ListRejectedOperatorsRequest(),
            None,
        )
    finally:
        service.close()

    operatorIds = {operator.operator_id for operator in activeReply.operators}
    assert {
        "communication.plc.slmp_read",
        "communication.plc.slmp_write",
        "communication.tcp.client",
        "communication.tcp.receive_once",
        "vision.analysis.contour",
        "vision.analysis.histogram",
        "vision.analysis.hough_circle",
        "vision.analysis.hough_line",
        "vision.analysis.shape_measurement",
        "vision.analysis.template_match",
        "vision.analysis.blob",
        "vision.collection.count",
        "vision.collection.filter",
        "vision.collection.select",
        "vision.collection.sort",
        "vision.compare.number",
        "vision.color.rgb_statistics",
        "vision.edge.canny",
        "vision.flow.if",
        "vision.flow.switch",
        "vision.geometry.coordinate_calculator",
        "vision.inference.yolo",
        "vision.io.image_loader",
        "vision.io.image_saver",
        "vision.io.huaray_camera",
        "vision.io.coordinate_reader",
        "vision.io.result_writer",
        "vision.image.absdiff",
        "vision.image.add_weighted",
        "vision.mask.apply",
        "vision.mask.logic",
        "vision.preprocess.affine",
        "vision.preprocess.blur",
        "vision.preprocess.clahe",
        "vision.preprocess.color_convert",
        "vision.preprocess.crop",
        "vision.preprocess.equalize",
        "vision.preprocess.flip",
        "vision.preprocess.morphology",
        "vision.preprocess.perspective",
        "vision.preprocess.resize",
        "vision.preprocess.roi",
        "vision.preprocess.rotate",
        "vision.preprocess.threshold",
        "vision.segment.in_range",
        "vision.render.annotate",
        "vision.state.counter",
        "vision.value.number",
    } <= operatorIds
    assert len(operatorIds) == 49
    assert list(rejectedReply.rejected) == []


def testRuntimeReportsCrossRootOperatorConflicts(tmp_path: Path) -> None:
    firstRoot = tmp_path / "plugins-a"
    secondRoot = tmp_path / "plugins-b"
    _writeManifest(firstRoot, "first")
    _writeManifest(secondRoot, "second")

    service = RuntimeService(
        dbPath=tmp_path / "runtime.db",
        workspaceRoot=tmp_path / "jobs",
        pluginRootPaths=(str(firstRoot), str(secondRoot)),
    )
    try:
        activeReply = service.ListOperators(
            runtime_pb2.ListOperatorsRequest(),
            None,
        )
        rejectedReply = service.ListRejectedOperators(
            runtime_pb2.ListRejectedOperatorsRequest(),
            None,
        )
    finally:
        service.close()

    assert "vision.demo.duplicate" not in {
        operator.operator_id for operator in activeReply.operators
    }
    duplicateIssues = [
        issue
        for issue in rejectedReply.rejected
        if issue.operator_id == "vision.demo.duplicate"
    ]
    assert len(duplicateIssues) == 1
    assert duplicateIssues[0].code == "E_OPERATOR_ID_DUPLICATED"
    assert str(firstRoot) in duplicateIssues[0].message
    assert str(secondRoot) in duplicateIssues[0].message


def _writeManifest(pluginRoot: Path, directoryName: str) -> None:
    pluginDir = pluginRoot / directoryName
    pluginDir.mkdir(parents=True)
    payload = {
        "operatorId": "vision.demo.duplicate",
        "displayName": "Duplicate",
        "version": "0.1.0",
        "entry": "unused.module:UnusedOperator",
        "inputPorts": {},
        "outputPorts": {},
        "paramSchema": {"type": "object"},
        "minCoreVersion": "0.1.0",
        "maxCoreVersion": "1.x",
    }
    (pluginDir / "manifest.json").write_text(
        json.dumps(payload),
        encoding="utf-8",
    )
