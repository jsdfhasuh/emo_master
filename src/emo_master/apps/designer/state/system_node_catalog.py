from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SystemNodeDefinition:
    kind: str
    displayName: str
    category: str
    canvasVisible: bool = True


SYSTEM_NODE_CATALOG: tuple[SystemNodeDefinition, ...] = (
    SystemNodeDefinition("workflow_input", "Workflow Input", "边界", False),
    SystemNodeDefinition("workflow_output", "Workflow Output", "边界", False),
    SystemNodeDefinition("subflow", "Subflow", "控制流"),
    SystemNodeDefinition("loop:repeat", "Repeat", "控制流"),
    SystemNodeDefinition("loop:foreach", "ForEach", "控制流"),
    SystemNodeDefinition("loop:while", "While", "控制流"),
)


def getSystemNodeDefinition(kind: str) -> SystemNodeDefinition | None:
    for definition in SYSTEM_NODE_CATALOG:
        if definition.kind == kind:
            return definition
    return None
