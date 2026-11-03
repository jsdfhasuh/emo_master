"""Shared diagnostic categories; event ownership stays with producers."""
from __future__ import annotations


def componentForEvent(eventType: str) -> str:
    if eventType == "node.log":
        return "operator"
    if eventType.startswith(("job.", "node.", "workflow.", "scheduler.", "dispatch.", "loop.")):
        return "execution"
    if eventType.startswith("device."):
        return "device"
    if eventType.startswith("presentation."):
        return "presentation"
    if eventType.startswith(("sqlite.", "runtime.log", "runtime.event")) or eventType in {
            "runtime.maintenance.failed", "operator.storage.resolved", "resource.cleanup.failed"}:
        return "storage"
    return "application"
