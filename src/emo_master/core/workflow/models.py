from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping


@dataclass(frozen=True)
class CompiledNode:
    nodeId: str
    kind: str
    operatorId: str | None
    inputPorts: Mapping[str, str]
    outputPorts: Mapping[str, str]
    params: Mapping[str, object]
    targetWorkflowId: str | None = None
    loop: Mapping[str, object] = MappingProxyType({})


@dataclass(frozen=True)
class CompiledEdge:
    fromNode: str
    fromPort: str
    toNode: str
    toPort: str


@dataclass(frozen=True)
class CompiledWorkflow:
    workflowId: str
    name: str
    inputs: Mapping[str, object]
    outputs: Mapping[str, object]
    nodes: tuple[CompiledNode, ...]
    edges: tuple[CompiledEdge, ...]
    nodeById: Mapping[str, CompiledNode]
    incomingEdges: Mapping[str, tuple[CompiledEdge, ...]]
    outgoingEdges: Mapping[str, tuple[CompiledEdge, ...]]
    topologicalOrder: tuple[str, ...]


@dataclass(frozen=True)
class CompiledProject:
    projectId: str
    revision: int
    entryWorkflowId: str
    workflows: Mapping[str, CompiledWorkflow]
    workflowCallGraph: Mapping[str, tuple[str, ...]]
    runtime: Mapping[str, object]
    pluginRootPaths: tuple[str, ...] = ()


def freezeMapping(value: Mapping[str, Any]) -> Mapping[str, Any]:
    return MappingProxyType(dict(value))
