"""Project-only variable edits; never copy runtime values into definitions."""
from copy import deepcopy

from emo_master.core.project.global_variables import definitions, READ_VARIABLE, WRITE_VARIABLE
from emo_master.core.project.migration import migrateProjectPayload


def referencedVariables(payload):
    found = set()
    for workflow in payload.get("workflows", {}).values():
        for node in workflow.get("nodes", []):
            found.update(item["variableId"] for item in node.get("globalVariableBindings", []))
            if node.get("operatorId") in {READ_VARIABLE, WRITE_VARIABLE}:
                found.add(node.get("params", {}).get("variableId"))
            loop = node.get("loop") or {}
            if loop.get("conditionMode") == "globalVariable":
                found.add(loop.get("conditionVariableId"))
    def visit(value):
        if isinstance(value, dict):
            if value.get("kind") == "global_variable":
                found.add(value.get("variableId"))
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)
    visit(payload.get("presentation", {}))
    return found - {None, ""}


def enableVariables(store):
    if "globalVariables" not in store.projectExtensions:
        payload = migrateProjectPayload(store.toPayload(), enableGlobalVariables=True)
        for key in ("presentation", "resources", "production", "globalVariables"):
            store.projectExtensions[key] = deepcopy(payload[key])


def editDefinitions(store, values):
    checked = definitions(values)
    names = [item.name for item in checked.values()]
    if len(set(names)) != len(names):
        raise ValueError("全局变量名称不能重复")
    previous = definitions(store.projectExtensions.get("globalVariables", {}))
    for key, old in previous.items():
        if key not in checked:
            if old.legacyCounterName or key in referencedVariables(store.toPayload()):
                raise ValueError(f"变量 {old.name} 已被引用或属于兼容计数器，不能删除")
        else:
            new = checked[key]
            for field in ("type", "kind", "lifetime", "legacyCounterName"):
                if getattr(old, field) != getattr(new, field):
                    raise ValueError("既有变量的类型、属性和生命周期不能修改")
    enableVariables(store)
    store.projectExtensions["globalVariables"] = {key: item.model_dump() for key, item in checked.items()}
