"""Generate separate field and loopback PLC projects; never contact hardware."""
from __future__ import annotations

import json
from copy import deepcopy
from functools import lru_cache
from pathlib import Path


HERE = Path(__file__).resolve().parent
READ = "communication.plc.slmp_read"
WRITE = "communication.plc.slmp_write"
COLLECTION = "plcValueCollection"
RECEIPT = "plcWriteReceipt"
HOSTS = {"50": "192.168.101.50", "51": "192.168.101.51"}
FIELD_FILE = "dual_plc_mc.emoproj"
SIMULATION_FILE = "dual_plc_mc_simulation.emoproj"


@lru_cache(maxsize=1)
def builtin_registry():
    from emo_master import __version__
    from emo_master.core.plugin.registry import PluginRegistry
    scan = PluginRegistry(coreVersion=__version__).scan(HERE.parents[1] / "src/emo_master/plugins")
    if scan.rejectedOperators:
        raise RuntimeError(scan.rejectedOperators)
    return scan.activeOperators


def designer_metadata(payload):
    from emo_master.core.contracts.port_types import normalizePortType
    registry = builtin_registry()
    for flow in payload["workflows"].values():
        for node in flow["nodes"]:
            kind = node["kind"]
            if kind == "operator":
                manifest = registry[node["operatorId"]].manifest
                inputs, outputs = manifest.inputPorts, manifest.outputPorts
                node["paramSchema"] = deepcopy(manifest.paramSchema)
            elif kind == "subflow":
                target = payload["workflows"][node["targetWorkflowId"]]
                inputs, outputs = target["inputs"], target["outputs"]
            elif kind == "workflow_input":
                inputs, outputs = {}, flow["inputs"]
            else:
                inputs, outputs = flow["outputs"], {}
            # Designer prunes boundary wires before Runtime supplies its metadata.
            node["inputPorts"] = {name: normalizePortType(spec) for name, spec in inputs.items()}
            node["outputPorts"] = {name: normalizePortType(spec) for name, spec in outputs.items()}
    return payload


def edge(source, port, target, target_port):
    return dict(fromNode=source, fromPort=port, toNode=target, toPort=target_port)


def op(node_id, operator, params, title):
    return dict(nodeId=node_id, kind="operator", operatorId=operator,
                params=params, displayName=title)


def call(node_id, target, title):
    return dict(nodeId=node_id, kind="subflow", targetWorkflowId=target, displayName=title)


def workflow(name, inputs, outputs, nodes, edges):
    all_nodes = [dict(nodeId="input", kind="workflow_input"), *nodes,
                 dict(nodeId="output", kind="workflow_output")]
    return dict(name=name, inputs=inputs, outputs=outputs, nodes=all_nodes, edges=edges,
                layout={"nodePositions": {n["nodeId"]: {"x": float(i * 500), "y": 100.0}
                                          for i, n in enumerate(all_nodes)}})


def common(host, port, **overrides):
    return dict(host=host, port=port, connectTimeoutMs=2000, responseTimeoutMs=2000,
                retryCount=0, retryDelayMs=0, networkNo=0, pcNo=255,
                moduleIoNo=1023, moduleStationNo=0, monitoringTimer=0, **overrides)


def reading(name, params, inputs=None):
    bit = params.get("dataType") == "bit"
    scalar = "booleanValue" if bit else "numberValue"
    return workflow(name, inputs or {}, {"values": COLLECTION, scalar: "boolean" if bit else "number"},
                    [op("read", READ, params, name)],
                    [edge("read", "values", "output", "values"), edge("read", scalar, "output", scalar)])


def add_cycle(workflows, key, label, host, port, device, address, dtype, values, source):
    width = 2 if dtype in {"uint32", "int32", "float32"} else 1
    words = len(values) * width
    raw = common(host, port, device=device, startAddress=address,
                 dataType="uint16", count=words, outputIndex=0)
    typed = common(host, port, device=device, startAddress=address,
                   dataType=dtype, count=len(values), outputIndex=len(values)-1)
    write = common(host, port, device=device, startAddress=address, dataType=dtype,
                   values=values if source == "params" else [])
    writer_nodes = [op("write", WRITE, write, "写入测试值")]
    writer_edges = [edge("write", "receipt", "output", "receipt")]
    if source == "value":
        writer_nodes.insert(0, op("number", "vision.value.number", {"value": values[0]}, "单值输入"))
        writer_nodes.insert(1, op("integer", "vision.math.exact_integer", {}, "严格转整数"))
        writer_edges += [edge("number", "value", "integer", "value"), edge("integer", "value", "write", "value")]
    elif source == "values":
        assert len(values) == 2
        writer_nodes = [op("first", "vision.value.number", {"value": values[0]}, "列表值 1"),
                        op("second", "vision.value.number", {"value": values[1]}, "列表值 2"),
                        op("first_int", "vision.math.exact_integer", {}, "严格转整数 1"),
                        op("second_int", "vision.math.exact_integer", {}, "严格转整数 2"),
                        op("pair", "vision.collection.number_pair", {}, "数值列表"), *writer_nodes]
        writer_edges += [edge("first", "value", "first_int", "value"), edge("second", "value", "second_int", "value"),
                         edge("first_int", "value", "pair", "first"), edge("second_int", "value", "pair", "second"),
                         edge("pair", "values", "write", "values")]
    # A required subflow input gates even an operator without input ports.
    workflows[key + "_write"] = workflow("内部/" + label + "/写入", {"before": COLLECTION},
        {"receipt": RECEIPT}, writer_nodes, writer_edges)
    workflows[key + "_readback"] = reading("内部/" + label + "/写后读取", typed, {"after": RECEIPT})
    workflows[key + "_restore"] = workflow("内部/" + label + "/恢复原始字", {"before": COLLECTION, "after": COLLECTION},
        {"receipt": RECEIPT}, [op("restore", WRITE, common(host, port, device=device, startAddress=address,
        dataType="uint16", values=[]), "data 输入恢复原始地址和值")],
        [edge("input", "before", "restore", "data"), edge("restore", "receipt", "output", "receipt")])
    workflows[key + "_final"] = reading("内部/" + label + "/恢复后读取", raw, {"after": RECEIPT})
    nodes = [op("before", READ, raw, "1 读取原始字"),
             call("write", key + "_write", "2 写入"),
             call("readback", key + "_readback", "3 回读"),
             call("restore", key + "_restore", "4 恢复原始字"),
             call("final", key + "_final", "5 恢复后回读"),
             op("matches", "vision.compare.number", {"operator": "eq", "rightValue": values[-1]}, "测试值匹配"),
             op("restored", "vision.compare.number", {"operator": "eq"}, "原始首字匹配")]
    edges = [edge("before", "values", "write", "before"), edge("write", "receipt", "readback", "after"),
             edge("before", "values", "restore", "before"), edge("readback", "values", "restore", "after"),
             edge("restore", "receipt", "final", "after"),
             edge("readback", "numberValue", "matches", "left"),
             edge("before", "numberValue", "restored", "right"), edge("final", "numberValue", "restored", "left"),
             edge("before", "values", "output", "original"), edge("readback", "values", "output", "readback"),
             edge("final", "values", "output", "restored"), edge("write", "receipt", "output", "writeReceipt"),
             edge("restore", "receipt", "output", "restoreReceipt"), edge("matches", "result", "output", "selectedValueMatches"),
             edge("restored", "result", "output", "firstWordRestored")]
    workflows[key] = workflow(label, {}, dict(original=COLLECTION, readback=COLLECTION, restored=COLLECTION,
        writeReceipt=RECEIPT, restoreReceipt=RECEIPT, selectedValueMatches="boolean", firstWordRestored="boolean"), nodes, edges)
    positions = {"input": (0, -220), "before": (0, 0), "write": (500, 0), "readback": (1000, 0),
                 "restore": (0, 420), "final": (500, 420), "matches": (1500, 0),
                 "restored": (1000, 420), "output": (1500, 420)}
    workflows[key]["layout"]["nodePositions"] = {
        node: {"x": float(x), "y": float(y)} for node, (x, y) in positions.items()}


def build_project(*, simulation=False, ports=(15950, 15951)):
    workflows, public, helpers = {}, [], []
    entry_nodes, entry_edges, entry_outputs = [], [], {}
    for index, (station, physical_host) in enumerate(HOSTS.items()):
        host = "127.0.0.1" if simulation else physical_host
        port = ports[index] if simulation else 4998
        prefix = "plc" + station
        key = prefix + "_read800"
        workflows[key] = reading(f"只读/.{station} D800", common(host, port, device="D", startAddress=800,
            dataType="uint16", count=1, outputIndex=0))
        public.append(key)
        entry_nodes.append(call(prefix, key, f".{station} D800 只读"))
        for output, kind in (("numberValue", "number"), ("values", COLLECTION)):
            name = prefix + "_" + output
            entry_outputs[name] = kind
            entry_edges.append(edge(prefix, output, "output", name))
        key = prefix + "_baseline796"
        workflows[key] = reading(f"只读/.{station} D796 原值记录", common(host, port, device="D", startAddress=796,
            dataType="uint16", count=1, outputIndex=0))
        public.append(key)
        cases = [("uint16", "D", 796, "uint16", [1], "params"),
                 ("int16", "D", 796, "int16", [-1], "params"),
                 ("scalar", "D", 796, "uint16", [2], "value")]
        if simulation:
            cases += [("uint32", "D", 796, "uint32", [4294967295, 65541], "params"),
                      ("int32", "D", 796, "int32", [-2147483648, -1], "params"),
                      ("float32", "D", 796, "float32", [1.5, -2.25], "params"),
                      ("list", "D", 796, "uint16", [11, 22], "values"),
                      ("m_word", "M", 100, "uint16", [9, 65535], "params"),
                      ("m_signed", "M", 100, "int16", [-1, 123], "params")]
            bit_key = prefix + "_m_bit"
            workflows[bit_key] = reading(f"模拟/.{station} M100 单位读取", common(host, port,
                device="M", startAddress=100, dataType="bit", count=1, outputIndex=0))
            public.append(bit_key)
        for case, device, address, dtype, values, source in cases:
            key = prefix + "_cycle_" + case
            label = f"{'模拟' if simulation else '写入'}/.{station} {device}{address} {case} 写读恢复"
            add_cycle(workflows, key, label, host, port, device, address, dtype, values, source)
            public.append(key)
            helpers.extend(key + suffix for suffix in ("_write", "_readback", "_restore", "_final"))
    workflows["read_both"] = workflow("00 两台 D800 只读（默认入口）", {}, entry_outputs, entry_nodes, entry_edges)
    workflows["read_both"]["layout"]["nodePositions"] = {
        "input": {"x": 0.0, "y": 180.0},
        "plc50": {"x": 360.0, "y": 40.0},
        "plc51": {"x": 360.0, "y": 360.0},
        "output": {"x": 900.0, "y": 150.0},
    }
    order = ["read_both", *public, *helpers]
    operators = sorted({node["operatorId"] for flow in workflows.values() for node in flow["nodes"] if "operatorId" in node})
    return designer_metadata(dict(schemaVersion="2.4", project=dict(projectId="dual-mc-simulation" if simulation else "dual-mc-field-20261015",
        name="双 PLC MC 全类型模拟测试" if simulation else "双 PLC MC 真机测试（仅 D796 写入）", revision=2,
        createdAt="2026-10-15T00:00:00Z", updatedAt="2026-10-15T00:00:00Z"), entryWorkflowId="read_both",
        workflowOrder=order, workflows={key: workflows[key] for key in order},
        runtime=dict(maxConcurrentJobs=1, startupWorkflowIds=["read_both"]), devices={"bindings": {}},
        dependencies={"operators": operators}, presentation={}, resources={}, globalVariables={},
        production=dict(autoStart=False, mode="single", cycleIntervalMs=100)))


def write_project(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(HERE.parents[1] / "src"))
    write_project(HERE / FIELD_FILE, build_project())
    write_project(HERE / "simulation" / SIMULATION_FILE, build_project(simulation=True))
    print("Generated field and simulation projects. No network operations performed.")
