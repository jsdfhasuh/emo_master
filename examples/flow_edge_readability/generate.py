"""Generate safe, deterministic number/boolean projects for desktop inspection."""
from copy import deepcopy
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]


def operator(nodeId, operatorId, title, **params):
    folder = "number_value" if operatorId == "vision.value.number" else "number_compare"
    manifest = json.loads((ROOT / f"src/emo_master/plugins/builtins/{folder}/manifest.json").read_text(encoding="utf-8"))
    schema = manifest["paramSchema"]
    defaults = {key: spec["default"] for key, spec in schema["properties"].items() if "default" in spec}
    return dict(nodeId=nodeId, kind="operator", operatorId=operatorId, displayName=title,
                params={**defaults, **params}, paramSchema=schema,
                inputPorts={key: spec["type"] for key, spec in manifest["inputPorts"].items()},
                outputPorts={key: spec["type"] for key, spec in manifest["outputPorts"].items()})


def comparisonFlow(count, sourceCount=2):
    sources = [operator("seed" if i == 0 else f"ref{i}", "vision.value.number",
                        "参考数值" if i else "输入数值", value=7 + i) for i in range(sourceCount)]
    checks = [operator(f"check{i}", "vision.compare.number",
                       "数值比较" if i < 2 else "检测结果与参考值比较_长中文名称_" + str(i)) for i in range(count)]
    outputs = {f"result{i}": "boolean" for i in range(count)}
    nodes = [dict(nodeId="input", kind="workflow_input", outputPorts={}), *sources, *checks,
             dict(nodeId="output", kind="workflow_output", inputPorts=outputs)]
    edges = []
    for i in range(count):
        for port, source in (("left", i % sourceCount), ("right", (i + 1) % sourceCount)):
            edges.append(dict(fromNode=sources[source]["nodeId"], fromPort="value", toNode=f"check{i}", toPort=port))
        edges.append(dict(fromNode=f"check{i}", fromPort="result", toNode="output", toPort=f"result{i}"))
    positions = {"input": dict(x=-360, y=30), "output": dict(x=1100, y=30)}
    positions.update({node["nodeId"]: dict(x=20, y=40 + i * 210) for i, node in enumerate(sources)})
    positions.update({f"check{i}": dict(x=550, y=30 + i * 180) for i in range(count)})
    return dict(name="", inputs={}, outputs=outputs, nodes=nodes, edges=edges,
                layout=dict(nodePositions=positions))


def buildProject():
    fanout = comparisonFlow(4)
    # Keep exactly four outgoing seed edges, one per check, to simplify E06-E08.
    for edge in fanout["edges"]:
        if edge["toNode"].startswith("check"):
            edge["fromNode"] = "seed" if edge["toPort"] == "left" else "ref1"
    dense = comparisonFlow(20, 5)
    sparse = comparisonFlow(1)
    crossing = deepcopy(fanout)
    crossing["layout"]["nodePositions"]["check0"]["y"] = 570
    crossing["layout"]["nodePositions"]["check3"]["y"] = 30
    blocked = deepcopy(sparse)
    blocked["layout"]["nodePositions"]["check0"] = dict(x=20, y=40)
    workflows = dict(fanout=fanout, sparse=sparse, dense=dense, crossing=crossing, blocked=blocked)
    for key, workflow in workflows.items():
        workflow["name"] = key
    return dict(schemaVersion="2.1", project=dict(projectId="flow-edge-readability", name="Flow edge readability",
                revision=1, createdAt="2026-10-12T00:00:00Z", updatedAt="2026-10-12T00:00:00Z"),
                entryWorkflowId="fanout", workflowOrder=list(workflows), workflows=workflows)


if __name__ == "__main__":
    (HERE / "project.emoproj").write_text(json.dumps(buildProject(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
