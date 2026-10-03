from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ValidationIssue:
    code: str
    message: str
    projectId: str = ""
    workflowId: str = ""
    nodeId: str = ""
    fieldPath: str = ""


class WorkflowCompileError(ValueError):
    def __init__(self, issues: list[ValidationIssue]) -> None:
        self.issues = tuple(issues)
        message = "; ".join(issue.message for issue in self.issues)
        super().__init__(message or "workflow compilation failed")
