"""Build a synthetic operator-chain fixture, NOT the four field algorithms.

Only tests replace camera SDK, SLMP peer, ONNX backend and gateway peer. Every
intermediate operator runs normally. All formulas/regions and S1's second-shot
circle are explicit test choices; the unknown Blob-inner-center is NOT emulated.
"""
from copy import deepcopy
import json
from pathlib import Path

from emo_master import __version__
from emo_master.core.plugin.registry import PluginRegistry
from emo_master.core.project.models import ProductionSettings
from emo_master.core.presentation.models import Presentation
from emo_master.core.project.resources import ResourcePlan
from emo_master.core.contracts.port_types import normalizePortType


HERE = Path(__file__).resolve().parent


def edge(source, port, target, targetPort):
    return dict(fromNode=source, fromPort=port, toNode=target, toPort=targetPort)


def op(nodeId, operatorId, params=None, title=None):
    return dict(nodeId=nodeId, kind="operator", operatorId=operatorId,
                params=params or {}, displayName=title or nodeId)


def call(nodeId, workflow):
    return dict(nodeId=nodeId, kind="subflow", targetWorkflowId=workflow)


def workflow(name, inputs, outputs, nodes, edges):
    return dict(name=name, inputs=inputs, outputs=outputs,
        nodes=[dict(nodeId="input", kind="workflow_input"), *nodes, dict(nodeId="output", kind="workflow_output")],
        edges=edges, layout={"nodePositions": {n["nodeId"]: {"x": i * 290, "y": 80}
            for i, n in enumerate([dict(nodeId="input"), *nodes, dict(nodeId="output")])}})


def buildProject():
    workflows = {}
    common = dict(shot="integer", image="image", frame="bbox2d", plcSample="plcValueCollection")
    for station in (1, 2):
        root = f"station{station}"
        first, second = root + "_first", root + "_second"
        sample = root + "_sample"
        nodes = [op("camera", "vision.io.huaray_camera", dict(selectionMode="cameraKey", cameraKey=f"fixture-camera-{station}",
                    triggerMode="hardware", waitMode="hardware", sequencePolicy="contiguous", retryCount=0), "硬件取图（本地夹具配置）"),
                 call("sample", sample),
                 op("increment", "vision.state.variable_write", dict(variableId=root + "-shot-count", operation="increment")),
                 op("switch", "vision.flow.switch", dict(case1Value="1", case2Value="2")),
                 call("first", first), call("second", second),
                 op("invalid", "vision.flow.error", dict(code="E_SHOT_COUNTER", message="拍次非法，停止并重新同步"))]
        edges = [edge("camera", "image", "sample", "image"), edge("camera", "frame", "sample", "frame"),
                 edge("sample", "plcSample", "increment", "after"), edge("increment", "value", "switch", "value"),
                 edge("increment", "value", "output", "shot"), edge("camera", "blockId", "output", "blockId"),
                 edge("sample", "plcSample", "output", "plcSample"), edge("switch", "case1", "first", "shot"),
                 edge("switch", "case2", "second", "shot"), edge("switch", "default", "invalid", "after"),
                 edge("first", "ack", "output", "plcAck"), edge("second", "ack", "output", "robotAck")]
        for branch in ("first", "second"):
            edges.extend(edge("sample", key, branch, key) for key in ("image", "frame", "plcSample"))
        workflows[root] = workflow(f"工位 {station} 基础链路夹具（非现场算法）", {},
            dict(shot="integer", blockId="integer", plcSample="plcValueCollection",
                 plcAck={"type": "boolean", "required": False}, robotAck={"type": "boolean", "required": False}), nodes, edges)
        # The required image/frame subflow boundary gates the original inputless
        # PLC operator. Its values gate count increment and travel with this frame.
        workflows[sample] = workflow("本帧到达后 PLC 采样", dict(image="image", frame="bbox2d"),
            dict(image="image", frame="bbox2d", plcSample="plcValueCollection"),
            [op("plc", "communication.plc.slmp_read", dict(host="127.0.0.1", port=15000 + station,
                  device="D", startAddress=0, dataType="uint16", count=1, retryCount=0))],
            [edge("input", "image", "output", "image"), edge("input", "frame", "output", "frame"),
             edge("plc", "values", "output", "plcSample")])
        for kind, target in (("plc", first), ("robot", second)):
            isSecond = kind == "robot"
            measurementA = root + ("_holes" if isSecond else "_a")
            measurementB = root + "_b"
            nodes, edges = [], []
            if isSecond:
                nodes.append(op("reset", "vision.state.variable_write", dict(variableId=root + "-shot-count", operation="reset")))
                edges.append(edge("input", "shot", "reset", "after"))
            # S2 first has no fixed reader. S2 second's reader explicitly precedes
            # inference via the child workflow's required `after` boundary.
            hasFixed = station == 1 or isSecond
            fixedBefore = station == 2 and isSecond
            if hasFixed:
                # Reader contents freeze at admission, but node execution still
                # follows the path's declared position (before or after vision).
                prepared = target + "_fixed"
                fixed = op("fixed", "vision.io.coordinate_reader", dict(filePath=f"station{station}_reference.txt", readMode="session", headerMode="absent"), "模拟像素固定坐标，不是设备单位标定")
                workflows[prepared] = workflow("显式依赖后引入固定坐标", dict(after="any", frame="bbox2d"), dict(points="list<point2d>"),
                    [fixed], [edge("input", "frame", "fixed", "frame"), edge("fixed", "points", "output", "points")])
                nodes.append(call("fixed", prepared))
                edges.append(edge("input", "frame", "fixed", "frame"))
                if fixedBefore:
                    edges.append(edge("reset", "value", "fixed", "after"))
            nodes.append(call("a", measurementA))
            edges.append(edge("fixed", "points", "a", "after") if fixedBefore else
                         edge("reset", "value", "a", "after") if isSecond else edge("input", "shot", "a", "after"))
            for key in ("image", "frame"):
                edges.append(edge("input", key, "a", key))
            source = "a"
            if not isSecond:
                nodes.extend([call("b", measurementB), op("test_formula", "vision.geometry.coordinate_calculator", dict(mode="add"), "U03：仅夹具公式，两路分量相加，不是旧业务公式")])
                edges.append(edge("input", "shot", "b", "after"))
                edges.extend(edge("input", key, "b", key) for key in ("image", "frame"))
                edges.extend([edge("a", "points", "test_formula", "points"), edge("b", "points", "test_formula", "otherPoints")])
                source = "test_formula"
            if hasFixed:
                if not fixedBefore:
                    edges.append(edge(source, "points", "fixed", "after"))
                nodes.append(op("test_reference_formula", "vision.geometry.coordinate_calculator", dict(mode="add"), "U03：仅夹具公式，引入像素参考点后相加"))
                edges.extend([edge(source, "points", "test_reference_formula", "points"), edge("fixed", "points", "test_reference_formula", "otherPoints")])
                source = "test_reference_formula"
            transforms = [dict(offsetX=2, offsetY=3)] if isSecond else [dict(rotationDegrees=90, offsetX=7, offsetY=5), dict(scaleX=2, offsetX=100)]
            for index, params in enumerate(transforms, 1):
                name = f"transform{index}"
                nodes.append(op(name, "vision.geometry.coordinate_calculator", params, "U03：非交换的测试 2D 参数"))
                edges.append(edge(source, "points", name, "points"))
                source = name
            nodes.extend([op("format", "communication.gateway.coordinate_format", dict(channel=kind, plcRounding="halfAwayFromZero")),
                op("exchange", "communication.tcp.client", dict(host="127.0.0.1", port=16000 + station * 10 + (1 if kind == "robot" else 3),
                    operation="exchange", responseFraming="newline", rejectTrailingResponse=True, responseTimeoutMs=1000)),
                op("ack", "communication.gateway.ack_validate", dict(channel=kind))])
            edges.extend([edge(source, "points", "format", "points"), edge("format", "message", "exchange", "message"),
                edge("format", "message", "ack", "request"), edge("exchange", "response", "ack", "response"),
                edge("ack", "acknowledged", "output", "ack")])
            workflows[target] = workflow(f"{target}：合成图和测试公式，不作旧算法等价声明", common, dict(ack="boolean"), nodes, edges)
        for route, roi, feature, model in (
            ("a", dict(x=5, y=5, width=60, height=80), "circleCenter", "a"),
            ("b", dict(x=85, y=5, width=60, height=80), "circleCenter" if station == 1 else "minAreaRectCenter", "b"),
            ("holes", dict(x=45, y=55, width=50, height=50), "circleCenter", "holes")):
            target = root + "_" + route
            nodes = [op("mask", "vision.preprocess.roi", roi),
                     op("yolo", "vision.inference.yolo", dict(modelPath=model + ".onnx", drawOverlay=True)),
                     op("bbox", "vision.geometry.detection_bbox"), op("crop", "vision.preprocess.crop"),
                     op("binary", "vision.preprocess.threshold", dict(mode="fixed", threshold=127)),
                     op("contours", "vision.analysis.contour", dict(retrievalMode="external")),
                     op("measure", "vision.analysis.minimum_enclosing_circle" if feature == "circleCenter" else "vision.analysis.shape_measurement"),
                     op("points", "vision.geometry.extract_points", dict(feature=feature)),
                     op("source", "vision.geometry.reframe_points")]
            if station == 1 and route == "holes":
                nodes[6]["displayName"] = "U04：能力夹具明确用圆，不替代旧 Blob 内心"
            edges = [edge("input", "image", "mask", "image"), edge("input", "frame", "mask", "frame"),
                     edge("mask", "maskedImage", "yolo", "image"), edge("mask", "maskedFrame", "yolo", "frame"),
                     edge("yolo", "detections", "bbox", "detections"), edge("bbox", "bbox", "crop", "roi"),
                     edge("mask", "maskedImage", "crop", "image"), edge("mask", "maskedFrame", "crop", "frame"),
                     edge("crop", "image", "binary", "image"), edge("crop", "frame", "binary", "frame"),
                     edge("binary", "mask", "contours", "mask"), edge("binary", "frame", "contours", "frame"),
                     edge("contours", "contours", "measure", "contours"),
                     edge("measure", "circles" if feature == "circleCenter" else "measurements", "points", "circles" if feature == "circleCenter" else "measurements"),
                     edge("points", "points", "source", "points"), edge("input", "frame", "source", "frame"),
                     edge("source", "points", "output", "points")]
            workflows[target] = workflow(f"{target}：真实算子测量区域链", dict(after="any", image="image", frame="bbox2d"), dict(points="list<point2d>"), nodes, edges)
    payload = dict(schemaVersion="2.4", project=dict(projectId="hardware-normal-capability-fixture", name="双工位硬件正常基础链路夹具（非现场算法）",
        revision=1, createdAt="2026-10-09T00:00:00Z", updatedAt="2026-10-09T00:00:00Z"),
        entryWorkflowId="station1-run", workflowOrder=list(workflows), workflows=workflows,
        resources=ResourcePlan().model_dump(), presentation=Presentation().model_dump(), production=ProductionSettings().model_dump())
    payload["globalVariables"] = {f"station{s}-shot-count": dict(name=f"工位{s}拍次", type="integer", kind="variable", lifetime="job", initialValue=0) for s in (1, 2)}
    payload["production"]["mode"] = "single"
    payload["globalVariables"]["run-enabled"] = dict(name="继续等待触发", type="boolean", kind="constant",
        lifetime="job", initialValue=True)
    scopes, sources, pages = {}, {}, {}
    for s in (1, 2):
        root = f"station{s}"
        cycle, entry = root + "-cycle", root + "-run"
        workflows[root]["name"] = f"工位 {s} 单帧两拍处理（非现场算法）"
        workflows[cycle] = workflow(f"工位 {s} 单帧循环体", {}, {}, [call("process", root)], [])
        workflows[entry] = workflow(f"工位 {s} 持续运行入口", {}, {}, [dict(nodeId="wait", kind="loop", loop=dict(
            mode="while", contractVersion=2, bodyWorkflowId=cycle, conditionMode="globalVariable",
            conditionVariableId="run-enabled", unlimited=True, maxIterations=100, timeoutMs=0))], [])
        payload["workflowOrder"].insert(0, entry)
        payload["workflowOrder"].append(cycle)
        path = [dict(nodeId="wait", relation="loop_body"), dict(nodeId="process", relation="subflow")]
        scopes[root] = dict(entryWorkflowId=entry, scopeWorkflowId=root, callPath=path)
        sources[root + "-image"] = dict(kind="node_output", resultScopeId=root, workflowId=root,
                                        nodeId="camera", port="image", expectedType="image", callPath=path)
        sources[root + "-shot"] = dict(kind="workflow_output", resultScopeId=root, workflowId=root,
                                       port="shot", expectedType="integer", callPath=path)
        sources[root + "-count"] = dict(kind="global_variable", resultScopeId=root,
                                        variableId=root + "-shot-count", expectedType="integer")
        pages[root] = dict(name=f"工位 {s}", resultScopeIds=[root], components=[
            dict(componentId=root + "-shot", type="number", bindings={"value": root + "-shot"},
                 props={"title": "本帧拍次 n（不可变输出）"}),
            dict(componentId=root + "-count", type="number", layout={"row": 1}, bindings={"value": root + "-count"},
                 props={"title": "本帧结束时全局拍次（Job 隔离）"}),
            dict(componentId=root + "-image", type="image", layout={"row": 2}, bindings={"image": root + "-image"})])
    payload["presentation"] = Presentation(defaultPageId="station1", pageOrder=["station1", "station2"],
        resultScopes=scopes, dataSources=sources, pages=pages).model_dump()
    return payload


def writeProject():
    payload = buildProject()
    scan = PluginRegistry(coreVersion=__version__).scan(HERE.parents[1] / "src/emo_master/plugins")
    if scan.rejectedOperators:
        raise RuntimeError(scan.rejectedOperators)
    # Save real metadata so Designer opens the fixture without a custom driver.
    for flow in payload["workflows"].values():
        for node in flow["nodes"]:
            if node.get("kind") == "operator":
                descriptor = scan.activeOperators[node["operatorId"]]
                for key in ("inputPorts", "outputPorts", "paramSchema"):
                    value = getattr(descriptor.manifest, key)
                    node[key] = deepcopy(value) if key == "paramSchema" else {name: normalizePortType(spec) for name, spec in value.items()}
    (HERE / "project.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    writeProject()
