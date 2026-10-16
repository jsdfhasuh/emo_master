from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RuntimeEvent:
    jobId: str
    eventType: str
    message: str
    level: str = "INFO"
    nodeId: str = ""
    code: str = ""
    payloadJson: str = "{}"
    sequence: int = 0
    timestampMs: int = 0
    projectId: str = ""
    workflowId: str = ""
    workflowRunId: str = ""
    parentWorkflowRunId: str = ""
    nodeRunId: str = ""
    iterationPathJson: str = "[]"
