"""Pure configuration checks shared by the desktop editor and authoring API."""
from emo_master.core.project.global_variables import (VariableError, validateBindings, resolveParams,
    validateEffectiveParams, variablePorts)
from emo_master.core.contracts.port_compatibility import arePortTypesCompatible
from emo_master.core.contracts.port_types import normalizePortType


def validateParameterBindings(workflowId, raw, bindings, schema, variables, resources):
    occupied = [b["target"]["parameterPath"] for b in (*resources.get("parameterBindings", []), *resources.get("siteBindings", []))
                if b["target"].get("workflowId") == workflowId and b["target"].get("nodeId") == raw["nodeId"]]
    if raw.get("operatorId") in {"vision.state.variable_read", "vision.state.variable_write"}:
        occupied.append(["variableId"])
    if raw.get("operatorId") == "vision.state.variable_write":
        occupied.append(["operation"])
    validateBindings(bindings, variables, schema, occupied=occupied)


def validateConfiguration(workflow, raw, params, bindings, schema, variables, resources, *, validateParams=False):
    validateParameterBindings(workflow.workflowId, raw, bindings, schema, variables, resources)
    if raw.get("operatorId") == "vision.io.coordinate_reader" and params.get("readMode") == "session" and bindings:
        raise VariableError("E_COORDINATE_SNAPSHOT_CONFIG", "会话坐标的文件和解析配置必须在准入时固定，不能使用动态变量绑定")
    if bindings or validateParams:
        validateEffectiveParams(resolveParams(params, bindings, {vid: value.initialValue for vid, value in variables.items()}), bindings, schema)
    ports = variablePorts(raw.get("operatorId"), params, variables)
    if ports is not None:
        inputs, outputs = ports
        nodes = {item["nodeId"]: item for item in workflow.nodes}
        incompatible = []
        for edge in workflow.edges:
            if edge["toNode"] == raw["nodeId"]:
                source = nodes.get(edge["fromNode"], {}).get("outputPorts", {}).get(edge["fromPort"])
                target = inputs.get(edge["toPort"])
            elif edge["fromNode"] == raw["nodeId"]:
                source = outputs.get(edge["fromPort"])
                target = nodes.get(edge["toNode"], {}).get("inputPorts", {}).get(edge["toPort"])
            else:
                continue
            if source is None or target is None or not arePortTypesCompatible(normalizePortType(source), normalizePortType(target)):
                incompatible.append(f'{edge["fromNode"]}.{edge["fromPort"]} → {edge["toNode"]}.{edge["toPort"]}')
        if incompatible:
            raise VariableError("E_VARIABLE_INTERFACE_CONFLICT", "操作改变端口，请先断开并重新连接不兼容的线：" + "; ".join(incompatible))
    return ports
