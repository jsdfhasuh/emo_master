"""Invocation-local activation, separate from missing/optional data values."""
from __future__ import annotations

from collections.abc import Mapping

from emo_master.core.contracts.port_types import isPortRequired
from emo_master.core.workflow.models import CompiledNode, CompiledWorkflow


_BRANCH_PORTS = {
    "vision.flow.if": frozenset({"true", "false"}),
    "vision.flow.switch": frozenset({"case0", "case1", "case2", "case3", "default"}),
}


class BranchActivity:
    def __init__(self, workflow: CompiledWorkflow) -> None:
        self.workflow = workflow
        self._active: dict[tuple[str, str], frozenset[str]] = {}
        self._inactive: dict[tuple[str, str], frozenset[str]] = {}

    def blockingBranches(self, node: CompiledNode) -> set[str]:
        # The output boundary collects alternatives; its required output checks
        # still run at workflow completion. It is not a side-effecting action.
        if node.kind == "workflow_output":
            return set()
        incoming = self.workflow.incomingEdges.get(node.nodeId, ())
        optionalAlternatives: set[str] = set()
        for edge in incoming:
            if not isPortRequired(node.inputPorts[edge.toPort], default=True):
                optionalAlternatives.update(self._active.get((edge.fromNode, edge.fromPort), ()))
        blocked: set[str] = set()
        for edge in incoming:
            causes = self._inactive.get((edge.fromNode, edge.fromPort), frozenset())
            if not isPortRequired(node.inputPorts[edge.toPort], default=True):
                # Explicitly optional fan-in can merge alternatives of the SAME
                # decision. Unrelated shared data cannot activate a closed gate.
                causes = causes.difference(optionalAlternatives)
            blocked.update(causes)
        return blocked

    def skipped(self, node: CompiledNode, causes: set[str]) -> None:
        if causes:
            for port in node.outputPorts:
                self._inactive[node.nodeId, port] = frozenset(causes)

    def completed(self, node: CompiledNode, outputs: Mapping[str, object]) -> None:
        inherited: set[str] = set()
        for edge in self.workflow.incomingEdges.get(node.nodeId, ()):
            inherited.update(self._active.get((edge.fromNode, edge.fromPort), ()))
        branchPorts = _BRANCH_PORTS.get(node.operatorId or "", frozenset())
        if node.kind == "operator" and branchPorts:
            inherited.add(node.nodeId)
            for port in branchPorts.intersection(node.outputPorts).difference(outputs):
                self._inactive[node.nodeId, port] = frozenset({node.nodeId})
        for port in outputs:
            self._active[node.nodeId, port] = frozenset(inherited)
