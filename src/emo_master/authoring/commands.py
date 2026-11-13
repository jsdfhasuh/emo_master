"""Schemas for implemented semantic commands; shared by HTTP and MCP discovery."""
from __future__ import annotations
from copy import deepcopy

STRING = {"type": "string", "minLength": 1}
OBJECT = {"type": "object"}
ARRAY = {"type": "array"}
POSITION = {"type": "object", "properties": {"x": {"type": "number"}, "y": {"type": "number"}},
            "required": ["x", "y"], "additionalProperties": False}


def _schema(command_type, required_fields, **fields):
    return {"type": "object", "properties": {"type": {"const": command_type}, **deepcopy(fields)},
            "required": ["type", *required_fields], "additionalProperties": False}


COMMAND_SCHEMAS = {
    "project.rename": _schema("project.rename", ["name"], name=STRING),
    "workflow.create": _schema("workflow.create", ["name"], name=STRING, workflowId=STRING),
    "workflow.rename": _schema("workflow.rename", ["workflowId", "name"], workflowId=STRING, name=STRING),
    "workflow.copy": _schema("workflow.copy", ["workflowId"], workflowId=STRING),
    "workflow.delete": _schema("workflow.delete", ["workflowId"], workflowId=STRING),
    "workflow.entry": _schema("workflow.entry", ["workflowId"], workflowId=STRING),
    "workflow.interface": _schema("workflow.interface", ["workflowId", "inputs", "outputs"],
                                  workflowId=STRING, inputs=OBJECT, outputs=OBJECT),
    "node.create": _schema("node.create", ["workflowId", "targetId", "operatorId"],
                           workflowId=STRING, targetId=STRING, operatorId=STRING, nodeId=STRING,
                           displayName=STRING, params=OBJECT, position=POSITION),
    "node.configure": _schema("node.configure", ["workflowId", "nodeId", "params"],
                              workflowId=STRING, nodeId=STRING, params=OBJECT,
                              globalVariableBindings={"type": "array", "items": {"type": "object",
                                  "properties": {"parameterPath": {"type": "array", "items": STRING, "minItems": 1},
                                                 "variableId": STRING},
                                  "required": ["parameterPath", "variableId"], "additionalProperties": False}}),
    "node.rename": _schema("node.rename", ["workflowId", "nodeId", "name"], workflowId=STRING, nodeId=STRING, name=STRING),
    "node.move": _schema("node.move", ["workflowId", "nodeId", "position"], workflowId=STRING, nodeId=STRING, position=POSITION),
    "node.copy": _schema("node.copy", ["workflowId", "nodeId"], workflowId=STRING, nodeId=STRING),
    "node.delete": _schema("node.delete", ["workflowId", "nodeId"], workflowId=STRING, nodeId=STRING),
    "edge.connect": _schema("edge.connect", ["workflowId", "fromNode", "fromPort", "toNode", "toPort"],
                            workflowId=STRING, fromNode=STRING, fromPort=STRING, toNode=STRING, toPort=STRING),
    "edge.disconnect": _schema("edge.disconnect", ["workflowId", "fromNode", "fromPort", "toNode", "toPort"],
                               workflowId=STRING, fromNode=STRING, fromPort=STRING, toNode=STRING, toPort=STRING),
    "control.create": _schema("control.create", ["workflowId", "kind", "config"], workflowId=STRING,
                              kind={"enum": ["subflow", "loop", "dispatch", "join"]}, config=OBJECT,
                              nodeId=STRING, displayName=STRING, position=POSITION, inputPorts=OBJECT),
    "control.configure": _schema("control.configure", ["workflowId", "nodeId", "config"],
                                 workflowId=STRING, nodeId=STRING, config=OBJECT, inputPorts=OBJECT),
    "device.put": _schema("device.put", ["targetId", "definition"], targetId=STRING, definition=OBJECT, deviceId=STRING),
    "device.delete": _schema("device.delete", ["deviceId"], deviceId=STRING),
    "node.devices": _schema("node.devices", ["workflowId", "nodeId", "targetId", "deviceBindings"],
                            workflowId=STRING, nodeId=STRING, targetId=STRING, deviceBindings=OBJECT,
                            deviceOverrides=OBJECT, deviceValueBindings=OBJECT),
    "variables.configure": _schema("variables.configure", ["definitions"], definitions=OBJECT),
    "page.create": _schema("page.create", ["name"], name=STRING),
    "page.rename": _schema("page.rename", ["pageId", "name"], pageId=STRING, name=STRING),
    "page.copy": _schema("page.copy", ["pageId"], pageId=STRING, name=STRING),
    "page.delete": _schema("page.delete", ["pageId"], pageId=STRING, repairTo=STRING),
    "page.default": _schema("page.default", ["pageId"], pageId=STRING),
    "page.order": _schema("page.order", ["pageOrder"], pageOrder={"type": "array", "items": STRING}),
    "page.configure": _schema("page.configure", ["pageId", "page"], pageId=STRING, page=OBJECT),
    "component.put": _schema("component.put", ["pageId", "component"], pageId=STRING, component=OBJECT),
    "component.delete": _schema("component.delete", ["pageId", "componentId"], pageId=STRING, componentId=STRING),
    "component.bind": _schema("component.bind", ["pageId", "componentId", "property", "source"],
                              pageId=STRING, componentId=STRING, property=STRING,
                              source={"type": ["object", "null"]}),
    "presentation.configure": _schema("presentation.configure", ["presentation"], presentation=OBJECT),
    "resource.bind": _schema("resource.bind", ["resourceId", "target"], resourceId=STRING, target=OBJECT),
    "resource.unbind": _schema("resource.unbind", ["target"], target=OBJECT),
    "site.bind": _schema("site.bind", ["name", "target", "purpose"], name=STRING, target=OBJECT,
                         purpose=STRING, required={"type": "boolean"}),
    "site.unbind": _schema("site.unbind", ["target"], target=OBJECT),
    "runtime.configure": _schema("runtime.configure", ["settings"], settings=OBJECT),
    "production.configure": _schema("production.configure", ["settings"], settings=OBJECT),
}


def _formal_models():
    """Reuse official models as wire input documentation, not a second model."""
    from pydantic import TypeAdapter
    from emo_master.core.project.devices import DeviceDefinition, DeviceBinding, DeviceValueBinding
    from emo_master.core.project.global_variables import VariableDefinition
    from emo_master.core.project.models import ProductionSettings, RuntimeSettings
    from emo_master.core.project.resources import ParameterTarget, DeviceParameterTarget
    from emo_master.core.presentation.models import Component, DataSource, Page, Presentation

    definitions = {}
    def install(command_type, field, model):
        generated = (model.model_json_schema() if hasattr(model, "model_json_schema") else TypeAdapter(model).json_schema())
        local_definitions = generated.pop("$defs", {})
        definitions.update(local_definitions)
        COMMAND_SCHEMAS[command_type].setdefault("$defs", {}).update(local_definitions)
        COMMAND_SCHEMAS[command_type]["properties"][field] = generated
    install("device.put", "definition", DeviceDefinition)
    install("node.devices", "deviceBindings", dict[str, DeviceBinding])
    install("node.devices", "deviceValueBindings", dict[str, dict[str, DeviceValueBinding]])
    install("variables.configure", "definitions", dict[str, VariableDefinition])
    install("component.put", "component", Component)
    install("component.bind", "source", DataSource | None)
    install("page.configure", "page", Page)
    install("presentation.configure", "presentation", Presentation)
    install("runtime.configure", "settings", RuntimeSettings)
    install("production.configure", "settings", ProductionSettings)
    for kind in ("resource.bind", "resource.unbind", "site.bind", "site.unbind"):
        install(kind, "target", ParameterTarget | DeviceParameterTarget)
    return definitions


_MODEL_DEFINITIONS = _formal_models()

_LOOP_CONFIG = {"type": "object", "properties": {
    "mode": {"enum": ["repeat", "foreach", "while"]}, "bodyWorkflowId": STRING,
    "conditionWorkflowId": STRING, "conditionMode": {"enum": ["workflow", "boolean", "globalVariable"]},
    "conditionPort": STRING, "conditionVariableId": STRING, "itemInputPort": STRING, "indexInputPort": STRING,
    "contractVersion": {"const": 2}, "repeatCount": {"type": "integer", "minimum": 0},
    "maxIterations": {"type": "integer", "minimum": 0}, "timeoutMs": {"type": "integer", "minimum": 0},
    "unlimited": {"type": "boolean"}, "cycleBoundary": {"enum": ["inherit", "iteration"]}},
    "required": ["mode", "bodyWorkflowId", "maxIterations", "timeoutMs"],
    "additionalProperties": False}
_DISPATCH_CONFIG = {"type": "object", "properties": {
    "targets": {"type": "array", "minItems": 1, "maxItems": 32, "items": {"type": "object", "properties": {
        "targetSlotId": {**STRING, "maxLength": 80}, "workflowId": STRING, "inputs": {"type": "object", "additionalProperties": STRING},
        "queueCapacity": {"type": "integer", "minimum": 1, "maximum": 64}},
        "required": ["targetSlotId", "workflowId", "inputs"], "additionalProperties": False}},
    "queueFullPolicy": {"enum": ["error", "wait", "skip"]}, "waitTimeoutMs": {"type": "integer", "minimum": 0, "maximum": 2147483647}},
    "required": ["targets"], "additionalProperties": False}
_SUBFLOW_CONFIG = {"type": "object", "properties": {"targetWorkflowId": STRING},
                   "required": ["targetWorkflowId"], "additionalProperties": False}
_JOIN_CONFIG = {"type": "object", "properties": {"dispatchNodeId": STRING,
                "timeoutMs": {"type": "integer", "minimum": 1, "maximum": 1200000}},
                "required": ["dispatchNodeId"], "additionalProperties": False}
for _kind in ("control.create", "control.configure"):
    COMMAND_SCHEMAS[_kind]["properties"]["config"] = {"anyOf": [deepcopy(config) for config in (
        _SUBFLOW_CONFIG, _LOOP_CONFIG, _DISPATCH_CONFIG, _JOIN_CONFIG)]}

def command_schema() -> dict:
    choices = []
    for schema in COMMAND_SCHEMAS.values():
        choice = deepcopy(schema)
        choice.pop("$defs", None)
        choices.append(choice)
    return {"oneOf": choices, "$defs": deepcopy(_MODEL_DEFINITIONS)}


def validate_command(command):
    from emo_master.authoring.errors import AuthoringError
    if not isinstance(command, dict) or command.get("type") not in COMMAND_SCHEMAS:
        raise AuthoringError("COMMAND_UNKNOWN", "Unknown semantic command")
    schema = COMMAND_SCHEMAS[command["type"]]
    missing = set(schema["required"]) - set(command)
    extra = set(command) - set(schema["properties"])
    if missing or extra:
        raise AuthoringError("COMMAND_INVALID", "Invalid command fields", missingFields=sorted(missing), unknownFields=sorted(extra))
    for field, value in command.items():
        spec = schema["properties"][field]
        if "enum" in spec and value not in spec["enum"]:
            raise AuthoringError("COMMAND_INVALID", "Invalid command choice", fieldPath=field)
        expected = spec.get("type")
        allowed = expected if isinstance(expected, list) else [expected]
        if expected and not any({"string": isinstance(value, str), "object": isinstance(value, dict),
                                 "array": isinstance(value, list), "boolean": type(value) is bool,
                                 "null": value is None}.get(kind, True) for kind in allowed):
            raise AuthoringError("COMMAND_INVALID", "Invalid command value type", fieldPath=field)
        if expected == "string" and not value:
            raise AuthoringError("COMMAND_INVALID", "Empty identifier or name", fieldPath=field)
